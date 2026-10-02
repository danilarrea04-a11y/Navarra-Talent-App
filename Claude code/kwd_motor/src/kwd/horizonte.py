"""Horizonte temporal: slots horarios, disponibilidades, bloqueos, ciclos de expedición y envíos de piezas (v2)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import CELULA_LOGISTICA, CIERRES_TURNO, DIAS_LABORABLES, RECURSOS, TURNOS
from .datos import (Escenario, celulas_productivas, demanda_dia, disp_base, stock_inicial, stock_optimo,
                    tabla_celulas)


@dataclass
class Horizonte:
    """Horizonte de planificación.

    - `slots`: una fila por hora (índice 0..H-1) con inicio, fin, hora, turno, fecha_turno, laborable,
      factor_energia, `<recurso>_disp` (tras restar técnicos en paradas), `<recurso>_disp_base` (estándar - bajas),
      `tecnicos` (técnicos ocupados en paradas), camiones, piezas_ve, piezas_comb.
    - `envios`: piezas que salen en cada slot; índice = slot, columnas = células productivas.
    - `bloqueos`: célula -> conjunto de slots en los que no puede activarse (paradas y averías).
    - `camiones`: DataFrame `slot, fecha_hora, piezas_ve, piezas_comb, m2, n_camiones, real` (un ciclo por fila).
    - `logistica_exigida`: por slot, si la célula 10 es obligatoria.
    - `stock_inicial`: stock de partida por célula usado al construir el horizonte (para encadenar eventos).
    - `cierres`: slots cuyo final coincide con un cierre de turno laborable (06:00, 14:00, 22:00) dentro del horizonte;
      `stock_optimo`: stock óptimo (SS + demanda diaria / 3 del día del turno que cierra) en cada cierre.
    """
    slots: pd.DataFrame
    envios: pd.DataFrame
    bloqueos: dict
    alertas: list = field(default_factory=list)
    camiones: pd.DataFrame = field(default_factory=pd.DataFrame)
    logistica_exigida: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    inicio: pd.Timestamp = None
    stock_inicial: dict = field(default_factory=dict)
    cierres: list = field(default_factory=list)  # slots cuyo final es un cierre de turno laborable (06, 14, 22 h)
    stock_optimo: pd.DataFrame = field(default_factory=pd.DataFrame)  # óptimo por cierre: índice = slot, cols = piezas

    @property
    def horas(self) -> int:
        return len(self.slots)

    def slots_turno_actual(self) -> list[int]:
        """Slots del turno actual (turno del slot 0) dentro del horizonte."""
        s = self.slots
        clave = (s["fecha_turno"] == s["fecha_turno"].iloc[0]) & (s["turno"] == s["turno"].iloc[0])
        idx = []
        for h in range(len(s)):
            if clave.iloc[h]:
                idx.append(h)
            else:
                break
        return idx

    def turnos_trabajo(self) -> list[tuple]:
        """Turnos laborables del horizonte: lista de (fecha_turno, turno, [slots laborables])."""
        s = self.slots
        res: list[tuple] = []
        for h in range(len(s)):
            if not bool(s["laborable"].iloc[h]):
                continue
            clave = (s["fecha_turno"].iloc[h], s["turno"].iloc[h])
            if res and res[-1][0] == clave[0] and res[-1][1] == clave[1] and res[-1][2][-1] == h - 1:
                res[-1][2].append(h)
            else:
                res.append((clave[0], clave[1], [h]))
        return res


def redondeo_comercial(x: float) -> int:
    """Redondeo 'half up', no el bancario de Python."""
    return int(math.floor(x + 0.5))


def turno_de_hora(hora: int) -> str:
    """Código de turno (M/T/N) de una hora del día. ."""
    for cod, (ini, fin) in TURNOS.items():
        if ini < fin and ini <= hora < fin:
            return cod
    return "N"


def _ciclos(esc: Escenario, t0: pd.Timestamp, t1: pd.Timestamp) -> list[dict]:
    """Ciclos de expedición (previstos o reales) con salida entre t0 - 1 día y t1."""
    p = esc.parametros
    n = int(p["ciclos_dia"])
    intervalo = float(p["intervalo_camion_h"])
    primero = float(p["primer_camion_h"])
    ciclos = []
    d = (t0 - pd.Timedelta(days=1)).normalize()
    fin = t1.normalize()
    while d <= fin:
        if d.weekday() in DIAS_LABORABLES:  # sin expediciones el fin de semana
            ve, comb = demanda_dia(esc, d)
            for i in range(n):
                t = d + pd.Timedelta(hours=primero + i * intervalo)
                ciclos.append({"hora": t, "dia": d, "ve": ve / n, "comb": comb / n, "real": False})
        d += pd.Timedelta(days=1)
    # Expediciones reales: sustituyen al ciclo previsto más cercano
    for _, f in esc.expediciones.iterrows():
        if pd.isna(f["fecha_hora"]):
            continue
        th = pd.Timestamp(f["fecha_hora"])
        dia = th.normalize() if th.hour >= int(primero) else th.normalize() - pd.Timedelta(days=1)
        cand = [c for c in ciclos if c["dia"] == dia and not c["real"]]
        ve = float(f["piezas_ve"]) if pd.notna(f["piezas_ve"]) else 0.0
        comb = float(f["piezas_comb"]) if pd.notna(f["piezas_comb"]) else 0.0
        if cand:
            mejor = min(cand, key=lambda c: abs((c["hora"] - th).total_seconds()))
            if abs((mejor["hora"] - th).total_seconds()) <= 45 * 60:
                mejor.update({"hora": th, "ve": ve, "comb": comb, "real": True})
                continue
        ciclos.append({"hora": th, "dia": dia, "ve": ve, "comb": comb, "real": True})
    return ciclos


def construir_horizonte(esc: Escenario, inicio, horas=None) -> Horizonte:
    """Construye el horizonte de `horas` slots horarios a partir de `inicio` (redondeado a la hora)."""
    p = esc.parametros
    H = int(horas if horas is not None else p["horas_horizonte"])
    t0 = pd.Timestamp(inicio).floor("h")
    t1 = t0 + pd.Timedelta(hours=H)
    alertas: list[str] = list(getattr(esc, "avisos", []) or [])

    horas_ini = [t0 + pd.Timedelta(hours=h) for h in range(H)]
    slots = pd.DataFrame({"inicio": horas_ini})
    slots["fin"] = slots["inicio"] + pd.Timedelta(hours=1)
    slots["hora"] = slots["inicio"].dt.hour
    slots["turno"] = [turno_de_hora(h) for h in slots["hora"]]
    # fecha del turno: la noche de 00-06 pertenece al día anterior
    slots["fecha_turno"] = (slots["inicio"] - pd.to_timedelta(np.where(slots["hora"] < 6, 1, 0), unit="D")
                            ).dt.normalize()
    slots["laborable"] = slots["fecha_turno"].dt.weekday.isin(DIAS_LABORABLES)  # # factor energético horario
    s_ini, s_fin = float(p["solar_ini"]), float(p["solar_fin"])
    f = np.ones(H)
    h = slots["hora"].to_numpy()
    f[(h >= s_ini) & (h < s_fin)] = float(p["factor_solar"])
    f[(h >= 22) | (h < 6)] = float(p["factor_noche"])
    slots["factor_energia"] = f

    # Bloqueos y técnicos por paradas; la célula 10 no puede pararse
    todas = [int(c) for c in esc.celulas["celula"]]
    bloqueos: dict[int, set] = {c: set() for c in todas}
    tecnicos = np.zeros(H)
    ignorada10 = False
    for _, fila in esc.paradas.iterrows():
        if pd.isna(fila["celula"]):
            continue
        c = int(fila["celula"])
        if c == CELULA_LOGISTICA:
            ignorada10 = True
            continue
        if c not in bloqueos:
            continue
        desde = fila["desde"] if pd.notna(fila["desde"]) else pd.Timestamp.min
        hasta = fila["hasta"] if pd.notna(fila["hasta"]) else pd.Timestamp.max
        sel = ((slots["inicio"] < hasta) & (slots["fin"] > desde)).to_numpy()
        bloqueos[c] |= set(np.where(sel)[0].tolist())
        tec = float(pd.to_numeric(pd.Series([fila["tecnicos"]]), errors="coerce").fillna(0.0).iloc[0])
        tecnicos[sel] += tec
    if ignorada10 and not any("célula 10" in a and "ignora" in a for a in alertas):
        alertas.append("Se ignora la parada/baja de la célula 10: el servicio logístico no puede pararse.")

    # Disponibilidad de recursos: estándar - bajas (o absentismo F7); los técnicos de paradas se restan de mto
    disp = {r: np.zeros(H) for r in RECURSOS}
    base = {r: np.zeros(H) for r in RECURSOS}
    for i in range(H):
        ft, tn = slots["fecha_turno"].iloc[i], slots["turno"].iloc[i]
        for r in RECURSOS:
            base[r][i] = disp_base(esc, ft, tn, r)
            disp[r][i] = base[r][i]
    disp["mto"] = np.maximum(0.0, disp["mto"] - tecnicos)
    for r in RECURSOS:
        slots[f"{r}_disp"] = disp[r]
        slots[f"{r}_disp_base"] = base[r]
    slots["tecnicos"] = tecnicos

    # Ciclos de expedición y envíos
    prods = celulas_productivas(esc)
    t = tabla_celulas(esc)
    dens = t["piezas_m2"].astype(float)
    m2_ve = float(sum(1.0 / dens[c] for c in prods if t.loc[c, "es_ve"] and dens[c] > 0))
    m2_comb = float(sum(1.0 / dens[c] for c in prods if (not t.loc[c, "es_ve"]) and dens[c] > 0))
    m2max = float(p["m2_max_camion"])

    lista = _ciclos(esc, t0, t1)
    filas = []
    for c in lista:
        m2 = c["ve"] * m2_ve + c["comb"] * m2_comb
        if not (t0 <= c["hora"] < t1):
            continue
        n_cam = int(math.ceil(m2 / m2max - 1e-9)) if m2 > 1e-9 else 0  # techo de m²/15: los camiones que hagan falta
        filas.append({"slot": int((c["hora"] - t0) // pd.Timedelta(hours=1)), "fecha_hora": c["hora"],
                      "piezas_ve": c["ve"], "piezas_comb": c["comb"], "m2": m2, "n_camiones": n_cam,
                      "real": c["real"]})
    cam = pd.DataFrame(filas, columns=["slot", "fecha_hora", "piezas_ve", "piezas_comb", "m2", "n_camiones",
                                       "real"])
    if len(cam):
        cam = cam.sort_values("fecha_hora").reset_index(drop=True)

    ve_slot = np.zeros(H)
    comb_slot = np.zeros(H)
    n_cam = np.zeros(H, dtype=int)
    n_ciclo = np.zeros(H, dtype=int)
    for _, c in cam.iterrows():
        k = int(c["slot"])
        ve_slot[k] += c["piezas_ve"]
        comb_slot[k] += c["piezas_comb"]
        n_cam[k] += int(c["n_camiones"])
        n_ciclo[k] += 1
    slots["camiones"] = n_cam
    slots["ciclos"] = n_ciclo
    slots["piezas_ve"] = ve_slot
    slots["piezas_comb"] = comb_slot
    envios = pd.DataFrame(index=slots.index, columns=prods, dtype=float)
    for c in prods:
        envios[c] = ve_slot if t.loc[c, "es_ve"] else comb_slot
    envios.index.name = "slot"

    # Célula 10 obligatoria en horas laborables salvo recursos insuficientes
    log = np.zeros(H, dtype=bool)
    if CELULA_LOGISTICA in t.index:
        req = t.loc[CELULA_LOGISTICA]
        for i in range(H):
            ok = bool(slots.at[i, "laborable"])
            if ok:
                for r in RECURSOS:
                    if req[r] > slots.at[i, f"{r}_disp"] + 1e-9:
                        ok = False
                        break
            log[i] = ok
        faltan = [i for i in range(H) if slots.at[i, "laborable"] and not log[i]]
        if faltan:
            alertas.append(f"La célula 10 (servicio logístico) no puede garantizarse en {len(faltan)} hora(s) "
                           f"laborable(s) por falta de recursos.")

    # Cierres de turno laborables dentro del horizonte y stock óptimo en cada uno
    cierres = [int(i) for i in range(H) if int(slots["fin"].iloc[i].hour) in CIERRES_TURNO
               and bool(slots["laborable"].iloc[i]) and slots["fin"].iloc[i].minute == 0]
    opt = pd.DataFrame(index=pd.Index(cierres, name="slot"), columns=prods, dtype=float)
    for i in cierres:
        o = stock_optimo(esc, slots["fecha_turno"].iloc[i])
        for c in prods:
            opt.at[i, c] = o[c]
    return Horizonte(slots=slots, envios=envios, bloqueos=bloqueos, alertas=alertas,
                     camiones=cam, logistica_exigida=log, inicio=t0, stock_inicial=stock_inicial(esc),
                     cierres=cierres, stock_optimo=opt)
