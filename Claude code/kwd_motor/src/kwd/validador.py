"""Validador independiente: recalcula las reglas obligatorias y la puntuación a partir del plan.

No reutiliza el modelo MILP ni `modelo.evaluar`; trabaja con bucles explícitos sobre
`plan.activacion` y `plan.uso`. Una lista vacía significa que el plan cumple todas las reglas
(certificado de factibilidad).
"""
from __future__ import annotations

from .config import (CELULA_LOGISTICA, CELULAS_PAREJA, COMPONENTES, PESO_PARAM, RECURSOS, RECURSOS_R)
from .datos import celulas_productivas, ss_por_celula, stock_inicial, tabla_celulas

TOL_STOCK = 1e-3   # piezas
TOL_ESPACIO = 1e-3  # m²
TOL_BIN = 1e-6
TOL_PUNT = 1e-4


def validar(esc, hz, plan) -> list[str]:
    """Devuelve la lista de incumplimientos (vacía si el plan cumple todas las reglas obligatorias)."""
    inc: list[str] = []
    t = tabla_celulas(esc)
    p = esc.parametros
    s = hz.slots
    H = len(s)
    celdas = sorted(t.index.tolist())
    prods = celulas_productivas(esc)
    ss = ss_por_celula(esc)
    i0 = stock_inicial(esc)
    A = esc.area_producto_terminado()
    W = [bool(x) for x in s["laborable"]]
    a = plan.activacion
    u = plan.uso

    def fmt(h):
        return f"{s['inicio'].iloc[h]:%d/%m %H:%M}"

    # Regla 1: u <= a, a binaria, u en [0,1]
    for c in celdas:
        for h in range(H):
            av = float(a.at[h, c])
            uv = float(u.at[h, c]) if c in u.columns else 0.0
            if min(abs(av), abs(av - 1)) > TOL_BIN:
                inc.append(f"Regla 1: activación no binaria en célula {c}, {fmt(h)} (a={av:.3f}).")
            if uv < -TOL_BIN or uv > 1 + TOL_BIN or uv > av + TOL_BIN:
                inc.append(f"Regla 1: uso u={uv:.3f} incompatible con activación a={av:.0f} en célula {c}, {fmt(h)}.")

    # Regla 2: célula 10 activa en horas laborables; nada activo fuera de horas laborables
    for h in range(H):
        if hz.logistica_exigida[h] and CELULA_LOGISTICA in celdas and a.at[h, CELULA_LOGISTICA] < 0.5:
            inc.append(f"Regla 2: la célula 10 (servicio logístico) debe estar activa en {fmt(h)}.")
        if not W[h]:
            act = [c for c in celdas if a.at[h, c] > 0.5]
            if act:
                inc.append(f"Regla 2: células {act} activas en hora no laborable {fmt(h)}.")

    # Regla 3: 11 y 12 con la misma activación y uso
    if all(c in celdas for c in CELULAS_PAREJA):
        c1, c2 = CELULAS_PAREJA
        for h in range(H):
            if abs(a.at[h, c1] - a.at[h, c2]) > TOL_BIN or abs(u.at[h, c1] - u.at[h, c2]) > TOL_BIN:
                inc.append(f"Regla 3: células {c1} y {c2} con distinta activación/uso en {fmt(h)}.")

    # Regla 4: bajas y mantenimientos
    for c, hs in hz.bloqueos.items():
        for h in sorted(hs):
            if h < H and a.at[h, c] > 0.5:
                inc.append(f"Regla 4: célula {c} activa en {fmt(h)} estando en baja/mantenimiento.")

    # Regla 5: recursos en horas laborables
    for h in range(H):
        if not W[h]:
            continue
        for r in RECURSOS:
            usado = sum(float(t.loc[c, r]) * float(a.at[h, c]) for c in celdas)
            disp = float(s[f"{r}_disp"].iloc[h])
            if usado > disp + 1e-6:
                inc.append(f"Regla 5: {r} usados {usado:.2f} > disponibles {disp:.2f} en {fmt(h)}.")

    # Reglas 6, 7, 8: stock, stock de seguridad y espacio
    stock = {c: i0[c] for c in prods}
    for h in range(H):
        esp = 0.0
        for c in prods:
            prod = float(t.loc[c, "cap_h"]) * float(u.at[h, c])
            stock[c] = stock[c] + prod - float(hz.envios.at[h, c])
            if abs(stock[c] - float(plan.stock.at[h, c])) > TOL_STOCK:
                inc.append(f"Regla 6: balance de stock inconsistente en célula {c}, {fmt(h)} "
                           f"(plan {plan.stock.at[h, c]:.2f} vs recalculado {stock[c]:.2f}).")
            if stock[c] < ss[c] - TOL_STOCK:
                inc.append(f"Regla 7: stock de seguridad incumplido en célula {c}, {fmt(h)} "
                           f"({stock[c]:.1f} < SS {ss[c]:.0f}).")
            dens = float(t.loc[c, "piezas_m2"])
            if dens > 0:
                esp += stock[c] / dens
        if esp > A + TOL_ESPACIO:
            inc.append(f"Regla 8: espacio de producto terminado {esp:.1f} m² > {A:.0f} m² en {fmt(h)}.")

    inc.extend(_validar_puntuacion(esc, hz, plan, t, celdas, prods, ss, stock_inicial(esc), A, W))
    return inc


def _validar_puntuacion(esc, hz, plan, t, celdas, prods, ss, i0, A, W) -> list[str]:
    """Recalcula R, S, Q, B, E y la puntuación; compara con los valores del plan."""
    inc = []
    p = esc.parametros
    s = hz.slots
    H = len(s)
    k = float(p["colchon_ss"])
    a, u = plan.activacion, plan.uso
    usado = {(h, r): sum(float(t.loc[c, r]) * float(a.at[h, c]) for c in celdas)
             for h in range(H) for r in RECURSOS}

    terminos = [usado[h, r] / float(s[f"{r}_disp"].iloc[h]) for h in range(H) if W[h]
                for r in RECURSOS_R if float(s[f"{r}_disp"].iloc[h]) > 1e-9]
    R = sum(terminos) / len(terminos) if terminos else 0.0

    esp = []
    stock = dict(i0)
    cortos = 0.0
    for h in range(H):
        e = 0.0
        for c in prods:
            stock[c] += float(t.loc[c, "cap_h"]) * float(u.at[h, c]) - float(hz.envios.at[h, c])
            dens = float(t.loc[c, "piezas_m2"])
            if dens > 0:
                e += stock[c] / dens
            if k > 0:
                cortos += max(0.0, (1 + k) * ss[c] - stock[c]) / (k * ss[c])
        esp.append(e)
    S = sum(x / A for x in esp) / H if H else 0.0
    B = cortos / (len(prods) * H) if (k > 0 and prods and H) else 0.0

    qs = []
    for h in range(H):
        if not W[h]:
            continue
        tt = [usado[h, r] / float(s[f"{r}_disp"].iloc[h]) for r in ("mto", "calidad")
              if float(s[f"{r}_disp"].iloc[h]) > 1e-9]
        if tt:
            qs.append(sum(tt) / len(tt))
    Q = sum(qs) / len(qs) if qs else 0.0

    fmax = max(float(p["factor_solar"]), float(p["factor_noche"]), 1.0)
    num = sum(float(s["factor_energia"].iloc[h]) * float(t.loc[c, "kw"]) * float(u.at[h, c])
              for h in range(H) for c in celdas)
    den = sum(W) * fmax * sum(float(t.loc[c, "kw"]) for c in celdas)
    E = num / den if den > 0 else 0.0

    comp = {"R": R, "S": S, "Q": Q, "B": B, "E": E}
    punt = 100.0 * (1.0 - sum(float(p[PESO_PARAM[c]]) * comp[c] for c in COMPONENTES))
    for c in COMPONENTES:
        if abs(comp[c] - plan.componentes[c]) > TOL_PUNT:
            inc.append(f"Puntuación: componente {c} recalculado {comp[c]:.6f} != plan {plan.componentes[c]:.6f}.")
    if abs(punt - plan.puntuacion) > TOL_PUNT * 100:
        inc.append(f"Puntuación: recalculada {punt:.4f} != plan {plan.puntuacion:.4f}.")
    return inc


def avisos_plan(esc, hz, plan) -> list[str]:
    """Avisos (no violaciones): stock final por debajo del objetivo de cobertura (FLAG F18)."""
    if hz.envio_final is None or plan.stock.empty:
        return []
    ss = ss_por_celula(esc)
    final = plan.stock.iloc[-1]
    return [f"Stock final por debajo del objetivo de cobertura (célula {c}): {final[c]:.0f} < "
            f"{ss[c] + float(hz.envio_final.get(c, 0.0)):.0f} piezas."
            for c in plan.stock.columns if final[c] < ss[c] + float(hz.envio_final.get(c, 0.0)) - TOL_STOCK]
