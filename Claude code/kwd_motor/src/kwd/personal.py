"""Personal: trabajadores enumerados y asignación puesto a puesto.

Post-proceso del plan: dado N (personas ocupadas por hora y rol, entero) numera a los trabajadores presentes
(M-OP01, T-CA02 ...) y reparte cada hora las cargas de las células activas entre ellos ("first-fit decreasing"
estable). Un trabajador puede cubrir varias células cuya suma de cargas sea <= 1; el resto está LIBRE.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import RECURSOS
from .datos import tabla_celulas, trabajadores_disponibles

EPS = 1e-9


def personas_enteras(req_frac: np.ndarray) -> np.ndarray:
    """N = personas ocupadas = techo de la suma de cargas (con tolerancia numérica)."""
    return np.ceil(np.asarray(req_frac, dtype=float) - 1e-6)


def celulas_de(texto) -> list[int]:
    """'C8+C9' -> [8, 9]."""
    if not isinstance(texto, str) or not texto.strip():
        return []
    return [int(x.strip()[1:]) for x in texto.split("+") if x.strip().startswith("C") and x.strip()[1:].isdigit()]


def _fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _items(req_col: dict) -> list[tuple]:
    """Descompone las cargas por célula en unidades enteras (1,0) y un resto fraccionario."""
    items = []
    for c, q in req_col.items():
        k = int(math.floor(q + 1e-9))
        for _ in range(k):
            items.append((1.0, c))
        fr = q - k
        if fr > 1e-9:
            items.append((round(fr, 6), c))
    items.sort(key=lambda x: (-x[0], x[1]))
    return items


def _asignar_hora(items, crew: list[str], n_h: int, prev: dict) -> dict:
    """Reparte `items` entre los trabajadores de `crew`. Devuelve {trabajador: [(celula, carga), ...]}."""
    asig: dict[str, list] = {}
    carga: dict[str, float] = {}

    def room(w):
        return 1.0 - carga.get(w, 0.0)

    pend = []
    # Fase 1: estabilidad (el trabajador sigue en la célula donde estaba mientras siga activa)
    for size, c in items:
        for w in crew:
            if c in prev.get(w, ()) and room(w) >= size - EPS:
                asig.setdefault(w, []).append((c, size))
                carga[w] = carga.get(w, 0.0) + size
                break
        else:
            pend.append((size, c))
    # Fase 2: first-fit decreasing entre los ya ocupados; si no cabe, abrir uno nuevo (primero los que
    # se quedaron sin célula, que pasan a cubrir células que arrancan, antes que coger a otro libre)
    for size, c in pend:
        dest = None
        for w in crew:
            if w in asig and room(w) >= size - EPS:
                dest = w
                break
        if dest is None:
            libres = [(0 if prev.get(w) else 1, i, w) for i, w in enumerate(crew) if w not in asig]
            if libres:
                dest = min(libres)[2]
        if dest is None:  # plantilla insuficiente (no debería ocurrir): se reparte fraccionando
            for w in crew:
                if room(w) > EPS:
                    parte = min(size, room(w))
                    asig.setdefault(w, []).append((c, parte))
                    carga[w] = carga.get(w, 0.0) + parte
                    size -= parte
                    if size <= EPS:
                        break
            continue
        asig.setdefault(dest, []).append((c, size))
        carga[dest] = carga.get(dest, 0.0) + size
    # Fase 3: si hay más personas ocupadas que N (empaquetado), se vacían las menos cargadas fraccionando
    while len(asig) > n_h:
        cand = [w for w in asig if carga[w] < 1.0 - EPS]
        if not cand:
            break
        w = min(cand, key=lambda x: (carga[x], -crew.index(x)))
        otros = [o for o in crew if o in asig and o != w]
        libre = sum(room(o) for o in otros)
        if libre < carga[w] - 1e-7:
            break
        for c, size in asig.pop(w):
            for o in otros:
                if size <= EPS:
                    break
                parte = min(size, room(o))
                if parte > EPS:
                    asig[o].append((c, parte))
                    carga[o] += parte
                    size -= parte
        carga.pop(w)
    return asig


def _texto(asig_w: list) -> tuple[str, str]:
    """(celulas 'C8+C9', detalle 'C8 (0,5) + C9 (0,5)')."""
    por = {}
    for c, s in asig_w:
        por[c] = por.get(c, 0.0) + s
    cs = sorted(por)
    cel = "+".join(f"C{c}" for c in cs)
    det = " + ".join(f"C{c}" if abs(por[c] - 1.0) < 1e-6 else f"C{c} ({_fmt(por[c])})" for c in cs)
    return cel, det


def asignacion_personal(esc, hz, plan, previo: tuple | None = None):
    """Asignación nominal por hora, resumen por trabajador y presentes por turno.

    Todo el personal presente (estándar - bajas) está ASIGNADO a una o varias células, LIBRE o, si es técnico
    ocupado en una parada, PARADA. `previo`: (clave_turno=(fecha_turno, turno), {trabajador: {celulas}}) con la
    asignación de la hora anterior (para mantener a cada trabajador en su puesto al reconfigurar).
    Devuelve (personal, trabajadores, plantilla); `plantilla` = presentes por turno y rol y horas libres.
    """
    t = tabla_celulas(esc)
    s = hz.slots
    cols_cel = list(plan.activacion.columns)
    A_ = plan.activacion.to_numpy(dtype=float)
    reqr = {r: {c: float(t.loc[c, r]) for c in cols_cel} for r in RECURSOS}
    N_ = plan.personas[RECURSOS].to_numpy(dtype=float)
    ridx = {r: j for j, r in enumerate(RECURSOS)}
    hora_ = s["hora"].to_numpy()
    inicio_ = s["inicio"].tolist()
    filas, plant = [], []
    for ft, tn, slots in hz.turnos_trabajo():
        clave = (pd.Timestamp(ft), tn)
        for r in RECURSOS:
            ids = trabajadores_disponibles(esc, ft, tn, r)
            disp = s[f"{r}_disp"].to_numpy(dtype=float)
            prev = {}
            if previo is not None and (pd.Timestamp(previo[0][0]), previo[0][1]) == clave:
                prev = {w: set(cs) for w, cs in previo[1].items()}
            horas_libres = 0
            for h in slots:
                n_h = int(round(N_[h, ridx[r]]))
                n_act = min(len(ids), int(math.floor(disp[h] + 1e-9)))
                crew = ids[:n_act]          # presentes y disponibles esta hora
                parados = ids[n_act:]       # técnicos ocupados en una parada programada
                act = [c for j, c in enumerate(cols_cel) if A_[h, j] > 0.5 and reqr[r][c] > EPS]
                items = _items({c: reqr[r][c] for c in act})
                asig = _asignar_hora(items, crew, n_h, prev)
                base = {"slot": h, "hora": int(hora_[h]), "turno": tn, "rol": r,
                        "fecha_turno": clave[0], "inicio": inicio_[h]}
                nuevo_prev = {}
                for w in crew:
                    if w in asig:
                        cel, det = _texto(asig[w])
                        cg = round(sum(x for _, x in asig[w]), 6)
                        filas.append({**base, "trabajador": w, "celulas": cel, "carga": cg, "estado": "ASIGNADO",
                                      "detalle": det})
                        nuevo_prev[w] = {c for c, _ in asig[w]}
                    else:
                        filas.append({**base, "trabajador": w, "celulas": "", "carga": 0.0, "estado": "LIBRE",
                                      "detalle": ""})
                        horas_libres += 1
                        nuevo_prev[w] = set()
                for w in parados:
                    filas.append({**base, "trabajador": w, "celulas": "", "carga": 0.0, "estado": "PARADA",
                                  "detalle": "En parada programada"})
                prev = nuevo_prev
            plant.append({"fecha_turno": clave[0], "turno": tn, "rol": r, "plantilla": len(ids),
                          "disponibles": len(ids), "excedente": 0,
                          "horas_libres": int(round(sum(disp[h] - N_[h, ridx[r]] for h in slots)))})
    cols = ["slot", "hora", "turno", "rol", "trabajador", "celulas", "carga", "estado", "fecha_turno", "inicio",
            "detalle"]
    personal = pd.DataFrame(filas, columns=cols)
    plantilla = pd.DataFrame(plant, columns=["fecha_turno", "turno", "rol", "plantilla", "disponibles", "excedente",
                                             "horas_libres"])
    return personal, resumen_trabajadores(personal), plantilla


def resumen_trabajadores(personal: pd.DataFrame) -> pd.DataFrame:
    """Resumen por trabajador del horizonte: horas asignado/libre y recorrido 'C3 06–10 → C14 10–14'."""
    cols = ["trabajador", "turno", "rol", "horas_asignado", "horas_libre", "celulas", "fecha_turno",
            "horas_excedente"]
    if not len(personal):
        return pd.DataFrame(columns=cols)
    filas = []
    for (ft, w), g in personal.groupby(["fecha_turno", "trabajador"], sort=False):
        g = g.sort_values("slot")
        tramos, ini, fin, cur = [], None, None, None
        for _, f in g.iterrows():
            c = f["celulas"] if f["estado"] == "ASIGNADO" else ""
            if cur is not None and c == cur and f["slot"] == fin + 1:
                fin = f["slot"]
            else:
                if cur:
                    tramos.append((cur, ini, fin))
                cur, ini, fin = c, f["slot"], f["slot"]
        if cur:
            tramos.append((cur, ini, fin))
        hora_de = dict(zip(g["slot"], g["hora"]))
        txt = " → ".join(f"{c} {hora_de[a]:02d}–{(hora_de[b] + 1) % 24:02d}" for c, a, b in tramos)
        filas.append({"trabajador": w, "turno": g["turno"].iloc[0], "rol": g["rol"].iloc[0],
                      "horas_asignado": int((g["estado"] == "ASIGNADO").sum()),
                      "horas_libre": int((g["estado"] == "LIBRE").sum()),
                      "celulas": txt, "fecha_turno": ft,
                      "horas_excedente": int((g["estado"] == "PARADA").sum())})
    return pd.DataFrame(filas, columns=cols)


def aplicar_personal(esc, hz, plan, previo=None) -> None:
    """Calcula y guarda `personal`, `trabajadores` y `plantilla` en el plan."""
    plan.personal, plan.trabajadores, plan.plantilla = asignacion_personal(esc, hz, plan, previo)


def previo_de_plan(hz, plan, slot: int):
    """Asignación del slot `slot` de un plan como `previo` ((fecha_turno, turno), {trabajador: {celulas}})."""
    if plan is None or plan.personal is None or not len(plan.personal) or slot < 0:
        return None
    slot = min(slot, len(hz.slots) - 1)
    g = plan.personal[(plan.personal["slot"] == slot) & (plan.personal["estado"] == "ASIGNADO")]
    if not len(g):
        g = plan.personal[plan.personal["slot"] == slot]
    clave = (pd.Timestamp(hz.slots["fecha_turno"].iloc[slot]), hz.slots["turno"].iloc[slot])
    return clave, {r["trabajador"]: set(celulas_de(r["celulas"])) for _, r in g.iterrows()}
