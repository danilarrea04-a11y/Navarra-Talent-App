"""Horizonte temporal: slots horarios, disponibilidades, bloqueos, camiones y envíos de piezas."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import CELULA_LOGISTICA, DIAS_LABORABLES, RECURSOS, TURNOS
from .datos import Escenario, celulas_productivas, demanda_dia, tabla_celulas


@dataclass
class Horizonte:
    """Horizonte de planificación.

    - `slots`: una fila por hora (índice 0..H-1) con inicio, fin, hora, turno, fecha_turno, laborable,
      factor_energia, `<recurso>_disp`, camiones, chasis_ve, chasis_comb.
    - `envios`: piezas que salen en cada slot; índice = slot, columnas = células productivas.
    - `bloqueos`: célula -> conjunto de slots en los que no puede activarse (bajas + mantenimiento).
    - `camiones`: detalle de camiones del horizonte.
    - `logistica_exigida`: por slot, si la célula 10 es obligatoria (F11/F14).
    """
    slots: pd.DataFrame
    envios: pd.DataFrame
    bloqueos: dict
    alertas: list = field(default_factory=list)
    camiones: pd.DataFrame = field(default_factory=pd.DataFrame)
    logistica_exigida: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    inicio: pd.Timestamp = None
    envio_final: pd.Series = None  # piezas previstas a expedir en las `cobertura_final_h` horas tras el horizonte (F18)

    @property
    def horas(self) -> int:
        return len(self.slots)

    def slots_turno_actual(self) -> list[int]:
        """Slots del turno actual (turno del slot 0) dentro del horizonte."""
        s = self.slots
        clave = (s["fecha_turno"] == s["fecha_turno"].iloc[0]) & (s["turno"] == s["turno"].iloc[0])
        # sólo el primer tramo contiguo
        idx = []
        for h in range(len(s)):
            if clave.iloc[h]:
                idx.append(h)
            else:
                break
        return idx


def redondeo_comercial(x: float) -> int:
    """Redondeo 'half up' (FLAG F17), no el bancario de Python."""
    return int(math.floor(x + 0.5))


def turno_de_hora(hora: int) -> str:
    """Código de turno (M/T/N) de una hora del día. FLAG F4."""
    for cod, (ini, fin) in TURNOS.items():
        if ini < fin and ini <= hora < fin:
            return cod
    return "N"


def _valor(fila, col):
    v = fila[col]
    return None if pd.isna(v) else float(v)


def _camiones_previstos(esc: Escenario, t0: pd.Timestamp, t1: pd.Timestamp) -> list[dict]:
    """Lista de camiones (previstos o reales) con salida en [t0 - 1 día, t1) para poder casar reales."""
    p = esc.parametros
    n = int(p["camiones_dia"])
    intervalo = float(p["intervalo_camion_h"])
    primero = float(p["primer_camion_h"])
    camiones = []
    d = (t0 - pd.Timedelta(days=1)).normalize()
    fin = t1.normalize()
    while d <= fin:
        if d.weekday() in DIAS_LABORABLES:  # FLAG F4/F6: sin expediciones el fin de semana
            ve, comb = demanda_dia(esc, d)
            for i in range(n):
                t = d + pd.Timedelta(hours=primero + i * intervalo)
                camiones.append({"hora": t, "dia": d, "ve": ve / n, "comb": comb / n, "real": False})
        d += pd.Timedelta(days=1)
    # Expediciones reales: sustituyen al camión previsto más cercano (FLAG F15)
    for _, f in esc.expediciones.iterrows():
        if pd.isna(f["fecha_hora"]):
            continue
        th = pd.Timestamp(f["fecha_hora"])
        dia = th.normalize() if th.hour >= int(primero) else th.normalize() - pd.Timedelta(days=1)
        cand = [c for c in camiones if c["dia"] == dia and not c["real"]]
        ve = float(f["chasis_ve"]) if pd.notna(f["chasis_ve"]) else 0.0
        comb = float(f["chasis_comb"]) if pd.notna(f["chasis_comb"]) else 0.0
        if cand:
            mejor = min(cand, key=lambda c: abs((c["hora"] - th).total_seconds()))
            if abs((mejor["hora"] - th).total_seconds()) <= 45 * 60:
                mejor.update({"hora": th, "ve": ve, "comb": comb, "real": True})
                continue
        camiones.append({"hora": th, "dia": dia, "ve": ve, "comb": comb, "real": True})
    return camiones


def construir_horizonte(esc: Escenario, inicio, horas=None) -> Horizonte:
    """Construye el horizonte de `horas` slots horarios a partir de `inicio` (redondeado a la hora)."""
    p = esc.parametros
    H = int(horas if horas is not None else p["horas_horizonte"])
    t0 = pd.Timestamp(inicio).floor("h")
    t1 = t0 + pd.Timedelta(hours=H)
    alertas: list[str] = []

    horas_ini = [t0 + pd.Timedelta(hours=h) for h in range(H)]
    slots = pd.DataFrame({"inicio": horas_ini})
    slots["fin"] = slots["inicio"] + pd.Timedelta(hours=1)
    slots["hora"] = slots["inicio"].dt.hour
    slots["turno"] = [turno_de_hora(h) for h in slots["hora"]]
    # fecha del turno: la noche de 00-06 pertenece al día anterior
    slots["fecha_turno"] = (slots["inicio"] - pd.to_timedelta(np.where(slots["hora"] < 6, 1, 0), unit="D")
                            ).dt.normalize()
    slots["laborable"] = slots["fecha_turno"].dt.weekday.isin(DIAS_LABORABLES)  # FLAG F4

    # FLAG F5: factor energético horario
    s_ini, s_fin = float(p["solar_ini"]), float(p["solar_fin"])
    f = np.ones(H)
    h = slots["hora"].to_numpy()
    f[(h >= s_ini) & (h < s_fin)] = float(p["factor_solar"])
    f[(h >= 22) | (h < 6)] = float(p["factor_noche"])
    slots["factor_energia"] = f

    # Disponibilidad de recursos: reales del turno o estándar con absentismo (FLAG F7)
    rr = esc.recursos_reales
    mant = esc.mantenimientos
    disp = {r: np.zeros(H) for r in RECURSOS}
    tecnicos = np.zeros(H)
    for i, fila in slots.iterrows():
        real = None
        if len(rr):
            m = rr[(rr["fecha"] == fila["fecha_turno"]) & (rr["turno"] == fila["turno"])]
            if len(m):
                real = m.iloc[-1]
        for r in RECURSOS:
            base = esc.disp_estandar(r, fila["turno"])
            v = _valor(real, r) if real is not None else None
            if v is None:
                v = base - redondeo_comercial(base * float(p["absentismo"]))  # FLAG F7
            disp[r][i] = max(0.0, v)
        if len(mant):
            m = mant[(mant["fecha"] == fila["fecha_turno"]) & (mant["turno"].isin([fila["turno"], "DIA"]))]
            tecnicos[i] = float(pd.to_numeric(m["tecnicos"]).fillna(0).sum()) if len(m) else 0.0
    # FLAG F3: los técnicos de mantenimiento planificado se restan de la disponibilidad de Mto
    disp["mto"] = np.maximum(0.0, disp["mto"] - tecnicos)
    for r in RECURSOS:
        slots[f"{r}_disp"] = disp[r]

    # Bloqueos por baja y por mantenimiento (FLAG F13)
    todas = [int(c) for c in esc.celulas["celula"]]
    bloqueos: dict[int, set] = {c: set() for c in todas}
    if len(esc.disponibilidad):
        for _, fila in esc.disponibilidad.iterrows():
            if pd.isna(fila["celula"]) or str(fila["estado"]).strip().upper() != "BAJA":
                continue
            c = int(fila["celula"])
            if c not in bloqueos:
                continue
            desde = fila["desde"] if pd.notna(fila["desde"]) else pd.Timestamp.min
            hasta = fila["hasta"] if pd.notna(fila["hasta"]) else pd.Timestamp.max
            sel = (slots["inicio"] < hasta) & (slots["fin"] > desde)
            bloqueos[c] |= set(slots.index[sel].tolist())
    if len(mant):
        for _, fila in mant.iterrows():
            if pd.isna(fila["celula"]):
                continue
            c = int(fila["celula"])
            if c not in bloqueos:
                continue
            sel = (slots["fecha_turno"] == fila["fecha"]) & (slots["turno"].eq(fila["turno"]) |
                                                              (fila["turno"] == "DIA"))
            bloqueos[c] |= set(slots.index[sel].tolist())

    # Camiones y envíos (FLAG F6)
    prods = celulas_productivas(esc)
    t = tabla_celulas(esc)
    dens = t["piezas_m2"].astype(float)
    ppc = t["ppc"]
    m2_ve = float(sum(ppc[c] / dens[c] for c in prods if t.loc[c, "es_ve"] and dens[c] > 0))
    m2_comb = float(sum(ppc[c] / dens[c] for c in prods if (not t.loc[c, "es_ve"]) and dens[c] > 0))
    m2max = float(p["m2_max_camion"])

    # FLAG F18: envíos previstos tras el horizonte (para la condición terminal de stock)
    cov = float(p.get("cobertura_final_h", 8))
    t2 = t1 + pd.Timedelta(hours=cov)
    lista = _camiones_previstos(esc, t0, t2)
    post_ve = post_comb = 0.0
    filas = []
    for c in lista:
        m2 = c["ve"] * m2_ve + c["comb"] * m2_comb
        esc_f = 1.0
        if m2 > m2max + 1e-9:
            esc_f = m2max / m2
        if t1 <= c["hora"] < t2:
            post_ve += c["ve"] * esc_f
            post_comb += c["comb"] * esc_f
        if not (t0 <= c["hora"] < t1):
            continue
        filas.append({"hora": c["hora"], "dia": c["dia"], "slot": int((c["hora"] - t0) // pd.Timedelta(hours=1)),
                      "ve_demanda": c["ve"], "comb_demanda": c["comb"], "ve": c["ve"] * esc_f,
                      "comb": c["comb"] * esc_f, "m2": m2 * esc_f, "escalado": esc_f < 1.0,
                      "real": c["real"]})
    cam = pd.DataFrame(filas, columns=["hora", "dia", "slot", "ve_demanda", "comb_demanda", "ve", "comb",
                                       "m2", "escalado", "real"])
    if len(cam) and cam["escalado"].any():
        e = cam[cam["escalado"]].copy()
        e["no_exp"] = (e["ve_demanda"] + e["comb_demanda"]) - (e["ve"] + e["comb"])
        for dia, g in e.groupby("dia"):
            alertas.append(f"Capacidad de expedición insuficiente: {g['no_exp'].sum():.0f} chasis no "
                           f"expedibles el {pd.Timestamp(dia):%d/%m/%Y} (camiones limitados a {m2max:g} m²).")

    ve_slot = np.zeros(H)
    comb_slot = np.zeros(H)
    n_cam = np.zeros(H, dtype=int)
    for _, c in cam.iterrows():
        ve_slot[int(c["slot"])] += c["ve"]
        comb_slot[int(c["slot"])] += c["comb"]
        n_cam[int(c["slot"])] += 1
    slots["camiones"] = n_cam
    slots["chasis_ve"] = ve_slot
    slots["chasis_comb"] = comb_slot
    envios = pd.DataFrame(index=slots.index, columns=prods, dtype=float)
    for c in prods:
        envios[c] = (ve_slot if t.loc[c, "es_ve"] else comb_slot) * ppc[c]
    envios.index.name = "slot"

    # Célula 10 obligatoria en horas laborables salvo bloqueo o recursos insuficientes (FLAG F11/F14)
    log = np.zeros(H, dtype=bool)
    if CELULA_LOGISTICA in t.index:
        req = t.loc[CELULA_LOGISTICA]
        for i in range(H):
            ok = bool(slots.at[i, "laborable"]) and i not in bloqueos.get(CELULA_LOGISTICA, set())
            if ok:
                for r in ("operarios", "picking", "carretilleros", "mto", "calidad"):
                    if req[r] > slots.at[i, f"{r}_disp"] + 1e-9:
                        ok = False
                        break
            log[i] = ok
        faltan = [i for i in range(H) if slots.at[i, "laborable"] and not log[i]]
        if faltan:
            alertas.append(f"La célula 10 (servicio logístico) no puede garantizarse en {len(faltan)} hora(s) "
                           f"laborable(s) por bloqueo o falta de recursos.")
    envio_final = pd.Series({c: (post_ve if t.loc[c, "es_ve"] else post_comb) * ppc[c] for c in prods}, dtype=float)
    return Horizonte(slots=slots, envios=envios, envio_final=envio_final, bloqueos=bloqueos, alertas=alertas, camiones=cam,
                     logistica_exigida=log, inicio=t0)

