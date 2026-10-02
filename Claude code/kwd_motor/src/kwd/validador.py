"""Validador independiente: recalcula las reglas obligatorias y la puntuación a partir del plan (v3).

No reutiliza el modelo MILP ni `modelo.evaluar`; recalcula con sus propias fórmulas sobre
`plan.activacion` y `plan.uso`. Una lista vacía significa que el plan cumple todas las reglas
(certificado de factibilidad). Bajar del stock de seguridad y el stock < 0 (pedido no servido) NO son
incumplimientos sino avisos (con pedidos sin servir el plan es CRITICO). Incumplimientos duros: almacén > 800 m²,
reglas de células y de recursos.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import CELULA_LOGISTICA, CELULAS_PAREJA, COMPONENTES, PESO_PARAM, RECURSOS, penalizacion_tramos
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
                if n > math.ceil(usado[h, j] - 1e-6) + 1e-6:
                    inc.append(f"Regla 5: personas {r} N={n:.0f} > techo de la carga {usado[h, j]:.2f} en {inicio[h]}.")
                if n > DISP[h, j] + 1e-6:
                    inc.append(f"Regla 5: personas {r} N={n:.0f} > disponibles {DISP[h, j]:.2f} en {inicio[h]}.")

    # Reglas 6 y 8: balance de stock y espacio (stock < 0 = pedido no servido: aviso, no incumplimiento)
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
    # todo el personal presente está ASIGNADO o LIBRE (o PARADA): presentes = disponibles de la hora
    crew = pe[pe["estado"].isin(["ASIGNADO", "LIBRE"])].groupby(["slot", "rol"]).size()
    for h in range(len(s)):
        if not W[h]:
            continue
        for r in RECURSOS:
            n = int(crew.get((h, r), 0))
            if n > math.floor(float(s[f"{r}_disp"].iloc[h]) + 1e-6):
                inc.append(f"Personal: {n} presentes de {r} > disponibles en slot {h}.")
            if float(plan.personas.at[h, r]) > n + 1e-6:
                inc.append(f"Personal: N de {r} > personas presentes en slot {h}.")
    return inc


def _validar_puntuacion(esc, hz, plan, t, celdas, prods, ss, i0, A, W, usado, A_, U_, st, esp) -> list[str]:
    """Recalcula R, S, Q, B, E y la puntuación; compara con los valores del plan."""
    inc = []
    p = esc.parametros
    s = hz.slots
    H = len(s)
    N = np.ceil(usado - 1e-6)
    DISP = np.stack([s[f"{r}_disp"].to_numpy(dtype=float) for r in RECURSOS], axis=1)

    # R = tiempo muerto del personal presente = Σ (Disp - trabajo productivo) / Σ Disp en horas laborables;
    # trabajo = carga × fracción de la hora produciendo (células no productivas: hora completa si activas)
    efectivo = A_.copy()
    for j, c in enumerate(celdas):
        if c in prods:
            efectivo[:, j] = U_[:, j]
    REQ = np.array([[float(t.loc[c, r]) for r in RECURSOS] for c in celdas])
    trabajo = efectivo @ REQ
    den = float(DISP[W].sum())
    R = float((DISP[W] - trabajo[W]).sum() / den) if den > 1e-9 else 0.0

    S = float(np.mean(esp / A)) if H else 0.0
    # B = media sobre piezas y cierres de turno de la penalización por tramos de (I − óptimo) / óptimo
    B = 0.0
    if len(hz.cierres) and prods:
        opt = hz.stock_optimo[prods].to_numpy(dtype=float)
        stc = st[list(hz.cierres)]
        turno = opt - np.array([ss[c] for c in prods], dtype=float)[None, :]
        B = float((penalizacion_tramos(stc - opt, turno) / np.where(opt > 0, opt, np.inf)).mean())

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


def _instante(hz, slot: int):
    """Hora del ciclo de expedición del slot (si lo hay) o fin del slot."""
    cam = hz.camiones
    if len(cam):
        m = cam[cam["slot"] == slot]
        if len(m):
            return pd.Timestamp(m["fecha_hora"].iloc[0])
    return pd.Timestamp(hz.slots["fin"].iloc[slot])


def agotamiento(esc, hz, plan) -> pd.DataFrame:
    """Por pieza: hora en que baja del SS, hora en que se agota el stock (<0), hora en que repone el SS y mínimo.

    Sólo incluye las piezas que bajan del SS en el horizonte.
    """
    ss = ss_por_celula(esc)
    cols = ["pieza", "hora_bajo_ss", "hora_sin_stock", "hora_repone_ss", "stock_min", "ss"]
    filas = []
    for c in plan.stock.columns:
        v = plan.stock[c].to_numpy(dtype=float)
        bajo = np.where(v < ss[c] - TOL_STOCK)[0]
        if not len(bajo):
            continue
        cero = np.where(v < -TOL_STOCK)[0]
        rep = [h for h in range(int(bajo[0]), len(v)) if v[h] >= ss[c] - TOL_STOCK]
        filas.append({"pieza": int(c), "hora_bajo_ss": _instante(hz, int(bajo[0])),
                      "hora_sin_stock": _instante(hz, int(cero[0])) if len(cero) else None,
                      "hora_repone_ss": pd.Timestamp(hz.slots["fin"].iloc[rep[0]]) if rep else None,
                      "stock_min": float(v.min()), "ss": float(ss[c])})
    return pd.DataFrame(filas, columns=cols)


def desabastecimiento(esc, hz, plan) -> pd.DataFrame:
    """Piezas no servidas por pieza y ciclo de expedición: DataFrame `pieza, ciclo, piezas_no_servidas`.

    Se considera no servido el incremento del pedido pendiente (stock negativo) en la hora del ciclo.
    """
    filas = []
    for c in plan.stock.columns:
        pend = np.maximum(0.0, -plan.stock[c].to_numpy(dtype=float))
        prev = 0.0
        for h in range(len(pend)):
            nuevo = pend[h] - prev
            prev = pend[h]
            if nuevo > TOL_STOCK:
                filas.append({"pieza": int(c), "ciclo": _instante(hz, h), "piezas_no_servidas": float(nuevo)})
    return pd.DataFrame(filas, columns=["pieza", "ciclo", "piezas_no_servidas"])


def aviso_direccion(esc, hz, plan) -> str | None:
    """Aviso para dirección si hay pedidos sin servir: pieza, hora en que se agota el SS y el stock, piezas no
    servidas por ciclo y total. None si todo se sirve."""
    des = plan.desabastecimiento if len(plan.desabastecimiento) else desabastecimiento(esc, hz, plan)
    if not len(des):
        return None
    ag = plan.agotamiento if len(plan.agotamiento) else agotamiento(esc, hz, plan)

    def f(ts):
        return f"{pd.Timestamp(ts):%H:%M} del {pd.Timestamp(ts):%d/%m}"
    partes = []
    for pieza, g in des.groupby("pieza"):
        a = ag[ag["pieza"] == pieza]
        txt = f"Pieza {pieza}: "
        if len(a):
            if pd.notna(a["hora_bajo_ss"].iloc[0]):
                txt += f"agota el stock de seguridad a las {f(a['hora_bajo_ss'].iloc[0])}; "
            if pd.notna(a["hora_sin_stock"].iloc[0]):
                txt += f"se queda sin stock a las {f(a['hora_sin_stock'].iloc[0])}; "
        ciclos = ", ".join(f"{pd.Timestamp(x['ciclo']):%d/%m %H:%M} -> {x['piezas_no_servidas']:.0f}"
                           for _, x in g.iterrows())
        txt += f"piezas no servidas por ciclo ({ciclos}); total {g['piezas_no_servidas'].sum():.0f} piezas."
        partes.append(txt)
    total = float(des["piezas_no_servidas"].sum())
    return f"AVISO PARA DIRECCIÓN: habrá pedidos sin servir ({total:.0f} piezas en total). " + " ".join(partes)


def avisos_plan(esc, hz, plan) -> list[str]:
    """Avisos (no violaciones): stock de seguridad consumido por una expedición, con su reposición."""
    out = []
    if plan.stock.empty:
        return out
    for d in consumos_ss(esc, hz, plan):
        rep = (f"repuesto a {d['repuesto']:%H:%M}" if d["repuesto"] is not None
               else "no se repone en el horizonte")
        out.append(f"Stock de seguridad de la pieza {d['celula']} consumido por expedición de "
                   f"{d['desde']:%H:%M} a {d['hasta']:%H:%M}; {rep}.")
    return out
