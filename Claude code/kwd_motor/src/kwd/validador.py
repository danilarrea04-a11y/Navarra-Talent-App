"""Validador independiente: recalcula las reglas obligatorias y la puntuación a partir del plan (v2).

No reutiliza el modelo MILP ni `modelo.evaluar`; recalcula con sus propias fórmulas sobre
`plan.activacion` y `plan.uso`. Una lista vacía significa que el plan cumple todas las reglas
(certificado de factibilidad). Bajar del stock de seguridad es un AVISO (los camiones pueden llevárselo);
stock < 0 (pedido no servido) es un incumplimiento.
"""
from __future__ import annotations

import math

import numpy as np

from .config import (CELULA_LOGISTICA, CELULAS_PAREJA, COMPONENTES, PESO_PARAM, PESO_PLANTILLA, RECURSOS,
                     RECURSOS_R)
from .datos import celulas_productivas, ss_por_celula, stock_inicial, tabla_celulas

TOL_STOCK = 1e-3   # piezas
TOL_ESPACIO = 1e-3  # m²
TOL_BIN = 1e-6
TOL_PUNT = 1e-4


def validar(esc, hz, plan) -> list[str]:
    """Devuelve la lista de incumplimientos (vacía si el plan cumple todas las reglas obligatorias)."""
    inc: list[str] = []
    t = tabla_celulas(esc)
    s = hz.slots
    H = len(s)
    celdas = sorted(t.index.tolist())
    prods = celulas_productivas(esc)
    i0 = stock_inicial(esc)
    A = esc.area_producto_terminado()
    W = s["laborable"].to_numpy(dtype=bool)
    inicio = s["inicio"].dt.strftime("%d/%m %H:%M").tolist()
    A_ = plan.activacion.reindex(columns=celdas).to_numpy(dtype=float)
    U_ = plan.uso.reindex(columns=celdas).to_numpy(dtype=float)
    ic = {c: i for i, c in enumerate(celdas)}

    # Regla 1: u <= a, a binaria, u en [0,1]
    for c in celdas:
        for h in range(H):
            av, uv = A_[h, ic[c]], U_[h, ic[c]]
            if min(abs(av), abs(av - 1)) > TOL_BIN:
                inc.append(f"Regla 1: activación no binaria en célula {c}, {inicio[h]} (a={av:.3f}).")
            if uv < -TOL_BIN or uv > 1 + TOL_BIN or uv > av + TOL_BIN:
                inc.append(f"Regla 1: uso u={uv:.3f} incompatible con activación a={av:.0f} en célula {c}, {inicio[h]}.")

    # Regla 2: célula 10 activa en horas laborables; nada activo fuera de horas laborables
    for h in range(H):
        if hz.logistica_exigida[h] and CELULA_LOGISTICA in ic and A_[h, ic[CELULA_LOGISTICA]] < 0.5:
            inc.append(f"Regla 2: la célula 10 (servicio logístico) debe estar activa en {inicio[h]}.")
        if not W[h]:
            act = [c for c in celdas if A_[h, ic[c]] > 0.5]
            if act:
                inc.append(f"Regla 2: células {act} activas en hora no laborable {inicio[h]}.")

    # Regla 3: 11 y 12 con la misma activación y uso
    if all(c in ic for c in CELULAS_PAREJA):
        c1, c2 = CELULAS_PAREJA
        for h in range(H):
            if abs(A_[h, ic[c1]] - A_[h, ic[c2]]) > TOL_BIN or abs(U_[h, ic[c1]] - U_[h, ic[c2]]) > TOL_BIN:
                inc.append(f"Regla 3: células {c1} y {c2} con distinta activación/uso en {inicio[h]}.")

    # Regla 4: paradas y averías
    for c, hs in hz.bloqueos.items():
        for h in sorted(hs):
            if h < H and A_[h, ic[c]] > 0.5:
                inc.append(f"Regla 4: célula {c} activa en {inicio[h]} estando parada/averiada.")

    # Regla 5: personal entero N >= suma de cargas, N <= disponibles (horas laborables)
    REQ = np.array([[float(t.loc[c, r]) for r in RECURSOS] for c in celdas])
    usado = A_ @ REQ
    DISP = np.stack([s[f"{r}_disp"].to_numpy(dtype=float) for r in RECURSOS], axis=1)
    NN = plan.personas[RECURSOS].to_numpy(dtype=float) if len(plan.personas) else None
    for h in range(H):
        if not W[h]:
            continue
        for j, r in enumerate(RECURSOS):
            if usado[h, j] > DISP[h, j] + 1e-6:
                inc.append(f"Regla 5: {r} usados {usado[h, j]:.2f} > disponibles {DISP[h, j]:.2f} en {inicio[h]}.")
            if NN is not None:
                n = NN[h, j]
                if abs(n - round(n)) > TOL_BIN:
                    inc.append(f"Regla 5: personas de {r} no enteras ({n:.2f}) en {inicio[h]}.")
                if n < usado[h, j] - 1e-6:
                    inc.append(f"Regla 5: personas {r} N={n:.0f} < carga {usado[h, j]:.2f} en {inicio[h]}.")
                if n > DISP[h, j] + 1e-6:
                    inc.append(f"Regla 5: personas {r} N={n:.0f} > disponibles {DISP[h, j]:.2f} en {inicio[h]}.")

    # Reglas 6 y 8: stock (>= 0, un camión puede llevarse el SS) y espacio
    cap = np.array([float(t.loc[c, "cap_h"]) for c in prods])
    dens = np.array([float(t.loc[c, "piezas_m2"]) for c in prods])
    pu = np.array([ic[c] for c in prods])
    env = hz.envios[prods].to_numpy(dtype=float)
    st = np.array([i0[c] for c in prods]) + np.cumsum(U_[:, pu] * cap[None, :] - env, axis=0)
    pst = plan.stock[prods].to_numpy(dtype=float)
    for h in range(H):
        for k, c in enumerate(prods):
            if abs(st[h, k] - pst[h, k]) > TOL_STOCK:
                inc.append(f"Regla 6: balance de stock inconsistente en célula {c}, {inicio[h]} "
                           f"(plan {pst[h, k]:.2f} vs recalculado {st[h, k]:.2f}).")
            if st[h, k] < -TOL_STOCK:
                inc.append(f"Regla 7: pedido no servido en la pieza {c}, {inicio[h]} (stock {st[h, k]:.1f} < 0).")
    esp = (st / np.where(dens > 0, dens, np.inf)[None, :]).sum(axis=1)
    for h in range(H):
        if esp[h] > A + TOL_ESPACIO:
            inc.append(f"Regla 8: espacio de producto terminado {esp[h]:.1f} m² > {A:.0f} m² en {inicio[h]}.")

    inc.extend(_validar_personal(esc, hz, plan, REQ, A_, ic, W))
    inc.extend(_validar_puntuacion(esc, hz, plan, t, celdas, prods, ss_por_celula(esc), stock_inicial(esc), A, W,
                                   usado, A_, U_, st, esp))
    return inc


def _validar_personal(esc, hz, plan, REQ, A_, ic, W) -> list[str]:
    """Comprueba la asignación nominal: cargas <= 1, suma de cargas = requisitos, plantilla e ids estables."""
    inc: list[str] = []
    if plan.personal is None or not len(plan.personal):
        return inc
    s = hz.slots
    pe = plan.personal
    mal = pe[pe["carga"] > 1 + 1e-6]
    for _, f in mal.iterrows():
        inc.append(f"Personal: {f['trabajador']} con carga {f['carga']:.2f} > 1 en slot {f['slot']}.")
    suma = pe.groupby(["slot", "rol"])["carga"].sum()
    usado = A_ @ REQ
    for h in range(len(s)):
        if not W[h]:
            continue
        for j, r in enumerate(RECURSOS):
            got = float(suma.get((h, r), 0.0))
            if abs(usado[h, j] - got) > 1e-4:
                inc.append(f"Personal: carga asignada {r} {got:.3f} != requisito {usado[h, j]:.3f} en slot {h}.")
    # plantilla <= disponibles; ids estables dentro del turno
    crew = pe[pe["estado"].isin(["ASIGNADO", "LIBRE"])]
    for ft, tn, slots in hz.turnos_trabajo():
        for r in RECURSOS:
            g = crew[(crew["fecha_turno"] == ft) & (crew["turno"] == tn) & (crew["rol"] == r)]
            if not len(g):
                continue
            por_slot = g.groupby("slot")["trabajador"].apply(frozenset)
            conjuntos = {h: por_slot.get(h, frozenset()) for h in slots}
            if len(set(conjuntos.values())) > 1:
                inc.append(f"Personal: plantilla {r} del turno {tn} no estable entre horas.")
            disp_min = min(float(s[f"{r}_disp"].iloc[h]) for h in slots)
            if len(conjuntos[slots[0]]) > disp_min + 1e-6:
                inc.append(f"Personal: plantilla {r} del turno {tn} supera los disponibles.")
            for h in slots:
                if float(plan.personas.at[h, r]) > len(conjuntos[h]) + 1e-6:
                    inc.append(f"Personal: plantilla {r} < N en slot {h}.")
    return inc


def _validar_puntuacion(esc, hz, plan, t, celdas, prods, ss, i0, A, W, usado, A_, U_, st, esp) -> list[str]:
    """Recalcula R, S, Q, B, E y la puntuación; compara con los valores del plan."""
    inc = []
    p = esc.parametros
    s = hz.slots
    H = len(s)
    k = float(p["colchon_ss"])
    N = np.ceil(usado - 1e-6)
    DISP = np.stack([s[f"{r}_disp"].to_numpy(dtype=float) for r in RECURSOS], axis=1)
    ir = [RECURSOS.index(r) for r in RECURSOS_R]

    # R = 0,7 media(P/disp turno) + 0,3 media(N/disp hora)  (roles de RECURSOS_R); P = pico de N en el turno
    ratios_p = []
    for ft, tn, slots in hz.turnos_trabajo():
        for j in ir:
            d = DISP[slots, j].min()
            if d > 1e-9:
                ratios_p.append(N[slots, j].max() / d)
    ratios_n = [N[h, j] / DISP[h, j] for h in range(H) if W[h] for j in ir if DISP[h, j] > 1e-9]
    mp = sum(ratios_p) / len(ratios_p) if ratios_p else 0.0
    mn = sum(ratios_n) / len(ratios_n) if ratios_n else 0.0
    R = PESO_PLANTILLA * mp + (1 - PESO_PLANTILLA) * mn

    S = float(np.mean(esp / A)) if H else 0.0
    ssv = np.array([ss[c] for c in prods])
    B = 0.0
    if k > 0 and prods and H:
        B = float((np.maximum(0.0, (1 + k) * ssv[None, :] - st) / (k * ssv[None, :])).sum() / (len(prods) * H))

    im, icc = RECURSOS.index("mto"), RECURSOS.index("calidad")
    qs = []
    for h in range(H):
        if not W[h]:
            continue
        tt = [N[h, j] / DISP[h, j] for j in (im, icc) if DISP[h, j] > 1e-9]
        if tt:
            qs.append(sum(tt) / len(tt))
    Q = sum(qs) / len(qs) if qs else 0.0

    kw = np.array([float(t.loc[c, "kw"]) for c in celdas])
    fmax = max(float(p["factor_solar"]), float(p["factor_noche"]), 1.0)
    f = s["factor_energia"].to_numpy(dtype=float)
    num = float((f[:, None] * kw[None, :] * U_).sum())
    den = float(W.sum()) * fmax * float(kw.sum())
    E = num / den if den > 0 else 0.0

    comp = {"R": R, "S": S, "Q": Q, "B": B, "E": E}
    punt = 100.0 * (1.0 - sum(float(p[PESO_PARAM[c]]) * comp[c] for c in COMPONENTES))
    for c in COMPONENTES:
        if abs(comp[c] - plan.componentes[c]) > TOL_PUNT:
            inc.append(f"Puntuación: componente {c} recalculado {comp[c]:.6f} != plan {plan.componentes[c]:.6f}.")
    if abs(punt - plan.puntuacion) > TOL_PUNT * 100:
        inc.append(f"Puntuación: recalculada {punt:.4f} != plan {plan.puntuacion:.4f}.")
    return inc


def consumos_ss(esc, hz, plan) -> list[dict]:
    """Tramos en que el stock de una pieza baja del SS: {celula, desde, hasta, repuesto (Timestamp|None)}."""
    ss = ss_por_celula(esc)
    s = hz.slots
    H = len(s)
    res = []
    for c in plan.stock.columns:
        v = plan.stock[c].to_numpy(dtype=float)
        h = 0
        while h < H:
            if v[h] < ss[c] - TOL_STOCK:
                ini = h
                while h + 1 < H and v[h + 1] < ss[c] - TOL_STOCK:
                    h += 1
                fin = h
                rep = s["fin"].iloc[fin + 1] if fin + 1 < H else None
                res.append({"celula": int(c), "desde": s["inicio"].iloc[ini], "hasta": s["fin"].iloc[fin],
                            "repuesto": rep, "slot_ini": ini, "slot_fin": fin,
                            "minimo": float(v[ini:fin + 1].min())})
            h += 1
    return res


def avisos_plan(esc, hz, plan) -> list[str]:
    """Avisos (no violaciones): SS consumido por una expedición (F20) y stock final bajo el objetivo (F18)."""
    out = []
    if plan.stock.empty:
        return out
    for d in consumos_ss(esc, hz, plan):
        rep = (f"repuesto a {d['repuesto']:%H:%M}" if d["repuesto"] is not None
               else "no se repone en el horizonte")
        out.append(f"Stock de seguridad de la pieza {d['celula']} consumido por expedición de "
                   f"{d['desde']:%H:%M} a {d['hasta']:%H:%M}; {rep}.")
    if hz.envio_final is not None:
        ss = ss_por_celula(esc)
        final = plan.stock.iloc[-1]
        out += [f"Stock final por debajo del objetivo de cobertura (célula {c}): {final[c]:.0f} < "
                f"{ss[c] + float(hz.envio_final.get(c, 0.0)):.0f} piezas."
                for c in plan.stock.columns if final[c] < ss[c] + float(hz.envio_final.get(c, 0.0)) - TOL_STOCK
                and final[c] >= ss[c] - TOL_STOCK]
    return out
