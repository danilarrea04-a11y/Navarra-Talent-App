"""Horizonte rodante: eventos, reconfiguración y simulación semanal."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import HOJAS_COLUMNAS, RECURSOS, TURNOS
from .datos import Escenario, stock_inicial
from .motor import Recomendacion, recomendar

TIPOS_EVENTO = ("baja_celula", "alta_celula", "recursos_reales", "correccion_demanda",
                "expedicion_real", "mantenimiento", "stock_real")


@dataclass
class Evento:
    """Incidencia que modifica el escenario (ver `aplicar_evento` para las claves de `datos`)."""
    tipo: str
    datos: dict = field(default_factory=dict)


def _anadir(df: pd.DataFrame, fila: dict) -> pd.DataFrame:
    nueva = pd.DataFrame([fila])
    if df.empty:
        return nueva.reindex(columns=df.columns)
    return pd.concat([df, nueva.reindex(columns=df.columns)], ignore_index=True)


def aplicar_evento(esc: Escenario, evento: Evento) -> Escenario:
    """Devuelve una copia del escenario con el evento aplicado.

    Tipos y `datos`: baja_celula {celula, desde, hasta}; alta_celula {celula}; recursos_reales
    {fecha, turno, valores}; correccion_demanda {fecha, ve, comb}; expedicion_real {fecha_hora, ve, comb};
    mantenimiento {fecha, turno, celula, tecnicos}; stock_real {celula: piezas}.
    """
    e = esc.copiar()
    d = evento.datos
    tipo = evento.tipo
    if tipo == "baja_celula":
        e.disponibilidad = _anadir(e.disponibilidad, {"celula": int(d["celula"]), "estado": "BAJA",
                                                      "desde": pd.Timestamp(d["desde"]),
                                                      "hasta": pd.Timestamp(d["hasta"])})
    elif tipo == "alta_celula":
        dis = e.disponibilidad
        e.disponibilidad = dis[dis["celula"] != int(d["celula"])].reset_index(drop=True)
    elif tipo == "recursos_reales":
        fecha = pd.Timestamp(d["fecha"]).normalize()
        turno = str(d["turno"]).upper()
        rr = e.recursos_reales
        fila = {"fecha": fecha, "turno": turno, **{r: pd.NA for r in RECURSOS}}
        previa = rr[(rr["fecha"] == fecha) & (rr["turno"] == turno)]
        if len(previa):
            fila.update({r: previa.iloc[-1][r] for r in RECURSOS})
        fila.update({k: v for k, v in d.get("valores", {}).items() if k in RECURSOS})
        rr = rr[~((rr["fecha"] == fecha) & (rr["turno"] == turno))]
        e.recursos_reales = _anadir(rr, fila)
    elif tipo == "correccion_demanda":
        fecha = pd.Timestamp(d["fecha"]).normalize()
        cd = e.correccion_diaria
        cd = cd[cd["fecha"] != fecha]
        e.correccion_diaria = _anadir(cd, {"fecha": fecha, "chasis_ve": float(d["ve"]),
                                           "chasis_comb": float(d["comb"])})
    elif tipo == "expedicion_real":
        e.expediciones = _anadir(e.expediciones, {"fecha_hora": pd.Timestamp(d["fecha_hora"]),
                                                  "chasis_ve": float(d["ve"]), "chasis_comb": float(d["comb"])})
    elif tipo == "mantenimiento":
        e.mantenimientos = _anadir(e.mantenimientos, {"fecha": pd.Timestamp(d["fecha"]).normalize(),
                                                      "turno": str(d["turno"]).upper(),
                                                      "celula": int(d["celula"]), "tecnicos": float(d.get("tecnicos", 0))})
    elif tipo == "stock_real":
        _fijar_stock(e, {int(k): float(v) for k, v in d.items()})
    else:
        raise ValueError(f"Tipo de evento desconocido: {tipo}")
    return e


def _fijar_stock(esc: Escenario, valores: dict) -> None:
    """Sustituye el stock de las células indicadas en la hoja StockActual (modifica `esc`)."""
    st = stock_inicial(esc)
    st.update(valores)
    esc.stock_actual = pd.DataFrame({"celula": list(st.keys()), "piezas": list(st.values())})


def reconfigurar(esc: Escenario, rec: Recomendacion, evento: Evento, ahora) -> Recomendacion:
    """Aplica un evento y recalcula 24 h desde `ahora`.

    El stock en `ahora` se toma del plan vigente (stock al final de la hora anterior), salvo que el
    evento sea `stock_real`, que lo fija explícitamente.
    """
    ahora = pd.Timestamp(ahora).floor("h")
    plan = rec.mejor
    hz = rec.horizonte
    e2 = esc.copiar()
    if plan is not None:
        n = int((ahora - hz.inicio) / pd.Timedelta(hours=1))
        if n >= 1:
            n = min(n, len(plan.stock))
            _fijar_stock(e2, {int(c): float(v) for c, v in plan.stock.iloc[n - 1].items()})
        # n <= 0: el stock de partida del escenario ya corresponde al inicio del plan vigente
    e2 = aplicar_evento(e2, evento)
    return recomendar(e2, ahora, top_k=3)


def simular_semana(esc: Escenario, lunes) -> pd.DataFrame:
    """Simula una semana con horizonte rodante: 15 turnos desde el lunes 06:00.

    En cada iteración se resuelve 24 h, se consolida el turno actual (8 h) y el stock evoluciona.
    Devuelve una fila por turno con KPIs y configuración.
    """
    inicio = pd.Timestamp(lunes).normalize() + pd.Timedelta(hours=6)
    e = esc.copiar()
    # Para la simulación (15 resoluciones) se acota el tiempo y se acepta un gap algo mayor (0,5 %).
    e.parametros["tiempo_limite_s"] = min(float(e.parametros["tiempo_limite_s"]), 10.0)
    e.parametros["gap_relativo"] = max(float(e.parametros.get("gap_relativo", 0.001)), 0.005)
    filas = []
    for it in range(15):
        rec = recomendar(e, inicio, top_k=1)
        plan = rec.mejor
        hz = rec.horizonte
        idx = hz.slots_turno_actual()
        fila = plan.resumen_turnos.iloc[0].to_dict()
        fila.update({
            "iteracion": it + 1, "estado": plan.estado, "idoneidad": plan.idoneidad,
            "puntuacion_plan_24h": plan.puntuacion,
            "configuracion": list(plan.config_turno_actual),
            "puntuacion_baseline": rec.baseline.puntuacion, "tiempo_s": rec.tiempo_total_s,
        })
        filas.append(fila)
        _fijar_stock(e, {int(c): float(v) for c, v in plan.stock.iloc[idx[-1]].items()})
        inicio = inicio + pd.Timedelta(hours=len(idx))
    return pd.DataFrame(filas)

