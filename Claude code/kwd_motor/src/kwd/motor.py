"""Orquestación: Top 1/2/3, plan de referencia, explicación (qué / por qué / impacto) y alertas."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .baseline import plan_referencia
from .config import CELULA_LOGISTICA, FLAGS, NOMBRES_TURNO, RECURSOS, RECURSOS_R
from .datos import Escenario, stock_inicial, ss_por_celula, tabla_celulas
from .horizonte import Horizonte, construir_horizonte
from .modelo import ModeloInfactible, preparar, resolver
from .plan import Plan


@dataclass
class Recomendacion:
    """Resultado de `recomendar`: Top-K viables, contingencia (si no hay viable), baseline y explicación."""
    inicio: pd.Timestamp
    horizonte: Horizonte
    top: list
    contingencia: Plan | None
    baseline: Plan
    explicacion: dict
    alertas: list
    flags: dict = field(default_factory=lambda: dict(FLAGS))
    tiempo_total_s: float = 0.0

    @property
    def mejor(self) -> Plan | None:
        """Plan principal: Top 1 o, si no hay viables, el de contingencia."""
        return self.top[0] if self.top else self.contingencia


# ---------------------------------------------------------------------------------------------
def franjas(hz: Horizonte, slots: list[int]) -> str:
    """Texto con las franjas horarias contiguas, p. ej. '06:00-11:00, 13:00-15:00'."""
    if not slots:
        return "-"
    slots = sorted(slots)
    grupos, ini, prev = [], slots[0], slots[0]
    for h in slots[1:]:
        if h != prev + 1:
            grupos.append((ini, prev))
            ini = h
        prev = h
    grupos.append((ini, prev))
    s = hz.slots
    return ", ".join(f"{s['inicio'].iloc[i]:%H:%M}-{s['fin'].iloc[j]:%H:%M}" for i, j in grupos)


def _primer_incumplimiento_sin_produccion(d, hz, ic: int):
    """Primer slot en que el stock de la pieza caería por debajo del SS sin producir (o None)."""
    st = d.i0[ic] - np.cumsum(d.env[:, ic])
    malos = np.where(st < d.ss[ic] - 1e-9)[0]
    return int(malos[0]) if len(malos) else None


def _explicar(esc: Escenario, hz: Horizonte, plan: Plan, top: list, baseline: Plan) -> dict:
    d = preparar(esc, hz)
    t = tabla_celulas(esc)
    s = hz.slots
    turno_idx = hz.slots_turno_actual()
    nombre_turno = NOMBRES_TURNO.get(s["turno"].iloc[0], s["turno"].iloc[0]) if len(s) else ""
    solar = (s["hora"] >= d.solar[0]) & (s["hora"] < d.solar[1])
    filas, porque = [], []
    for c in d.todas:
        horas = [h for h in turno_idx if plan.activacion.at[h, c] > 0.5]
        if not horas:
            continue
        h_solar = [h for h in horas if solar.iloc[h]]
        filas.append({
            "celula": c, "tipo": t.loc[c, "tipo"], "horas_activas": len(horas), "franja": franjas(hz, horas),
            "piezas": float(plan.produccion.loc[horas, c].sum()) if c in plan.produccion.columns else 0.0,
            **{r: float(t.loc[c, r]) for r in RECURSOS},
            "horas_franja_solar": len(h_solar),
        })
    que = pd.DataFrame(filas, columns=["celula", "tipo", "horas_activas", "franja", "piezas"] + RECURSOS +
                       ["horas_franja_solar"])

    for c in d.todas:
        horas = [h for h in turno_idx if plan.activacion.at[h, c] > 0.5]
        if c == CELULA_LOGISTICA:
            if horas:
                porque.append("Célula 10: servicio logístico, obligatoria en todas las horas laborables.")
            continue
        ic = d.prods.index(c)
        bloq_turno = [h for h in turno_idx if h in hz.bloqueos.get(c, set())]
        if horas:
            h1 = _primer_incumplimiento_sin_produccion(d, hz, ic)
            if h1 is not None:
                motivo = (f"su pieza caería por debajo del stock de seguridad a las "
                          f"{s['inicio'].iloc[h1]:%H:%M} ({s['inicio'].iloc[h1]:%d/%m}) sin producir")
            else:
                motivo = "repone el colchón de stock de seguridad (objetivo +10 %)"
            bloq_futuro = [h for h in hz.bloqueos.get(c, set()) if h > turno_idx[-1]]
            if bloq_futuro:  # producción anticipada por mantenimiento/baja posterior
                tb = s["turno"].iloc[min(bloq_futuro)]
                es_mto = len(esc.mantenimientos) > 0 and bool((esc.mantenimientos["celula"] == c).any())
                motivo += (f". Se adelanta producción de la célula {c} por "
                           f"{'mantenimiento' if es_mto else 'baja'} en turno {tb}")
            hs = [h for h in horas if solar.iloc[h]]
            solar_txt = (f"; colocada en franja solar ({len(hs)} de {len(horas)} h)" if hs else
                         "; fuera de franja solar")
            porque.append(f"Célula {c} activa {franjas(hz, horas)}: {motivo}{solar_txt}.")
        elif bloq_turno:
            porque.append(f"Célula {c} inactiva: BAJA/MANTENIMIENTO en el turno.")
        else:
            h1 = _primer_incumplimiento_sin_produccion(d, hz, ic)
            hasta = (f"{s['inicio'].iloc[h1]:%H:%M} ({s['inicio'].iloc[h1]:%d/%m})" if h1 is not None
                     else "todo el horizonte")
            porque.append(f"Célula {c} inactiva: stock suficiente hasta {hasta}.")

    def resumen(p: Plan) -> dict:
        k = p.kpis
        return {"puntuacion": p.puntuacion, "idoneidad": p.idoneidad, "estado": p.estado,
                "horas_operario": k["operarios_horas"], "horas_picking": k["picking_horas"],
                "horas_carretillero": k["carretilleros_horas"], "m2_medio": k["m2_medio"],
                "m2_pico": k["m2_pico"], "kwh_total": k["kwh_total"], "kwh_bruto": k["kwh_bruto"],
                "kwh_solar_pct": k["kwh_solar_pct"], "demanda_cubierta_pct": k["demanda_cubierta_pct"]}

    base = resumen(baseline)
    impacto = {"plan": resumen(plan), "baseline": base, "alternativas": [resumen(p) for p in top[1:]]}
    r = impacto["plan"]
    impacto["delta_vs_baseline"] = {
        "horas_operario": r["horas_operario"] - base["horas_operario"],
        "m2_medio": r["m2_medio"] - base["m2_medio"],
        "kwh_total": r["kwh_total"] - base["kwh_total"],
        "puntuacion": r["puntuacion"] - base["puntuacion"],
    }
    impacto["delta_vs_alternativas"] = [
        {"nombre": p.nombre, "puntuacion": plan.puntuacion - p.puntuacion} for p in top[1:]]
    return {"que": que, "porque": porque, "impacto": impacto, "turno_actual": nombre_turno}


def _alertas(esc: Escenario, hz: Horizonte, plan: Plan | None, top: list, contingencia: Plan | None,
             top_k: int) -> list[str]:
    al = list(hz.alertas)
    if plan is None:
        return al
    d = preparar(esc, hz)
    s = hz.slots
    if contingencia is not None:
        al.append("PLAN INVIABLE: no existe configuración que cumpla todas las reglas obligatorias; se muestra "
                  "un plan de contingencia (no recomendable).")
        al.extend(f"Incumplimiento: {m}" for m in contingencia.incumplimientos[:8])
        if len(contingencia.incumplimientos) > 8:
            al.append(f"... y {len(contingencia.incumplimientos) - 8} incumplimientos más.")
    elif len(top) < top_k:
        al.append(f"Sólo se han encontrado {len(top)} configuraciones viables distintas (se pedían {top_k}).")
    al.extend(plan.avisos)  # FLAG F18: condición terminal de stock
    if (plan.stock.to_numpy() < (1 + d.k) * d.ss[None, :] - 1e-6).any():
        malas = [c for c, v in zip(d.prods, (plan.stock.to_numpy() < (1 + d.k) * d.ss[None, :] - 1e-6).any(axis=0))
                 if v]
        al.append(f"Stock por debajo del colchón objetivo (SS +{d.k:.0%}) en las células {malas}.")
    if plan.espacio.max() > 0.9 * d.A:
        al.append(f"Ocupación de almacén de producto terminado {100 * plan.espacio.max() / d.A:.0f} % "
                  f"(> 90 %).")
    for r in RECURSOS_R:
        u_, dp = plan.recursos[f"{r}_usado"], plan.recursos[f"{r}_disp"]
        n = int(((u_ >= dp - 1e-9) & (dp > 0) & s["laborable"]).sum())
        if n:
            al.append(f"Recurso {r} al 100 % en {n} hora(s) del horizonte.")
    return al


def recomendar(esc: Escenario, inicio, horas=None, top_k: int = 3) -> Recomendacion:
    """Calcula el Top-K de configuraciones viables, el plan de referencia y su explicación."""
    t0 = time.perf_counter()
    hz = construir_horizonte(esc, inicio, horas)
    top: list[Plan] = []
    contingencia: Plan | None = None
    cortes: list = []
    baseline = plan_referencia(esc, hz)
    for j in range(top_k):
        try:
            plan = resolver(esc, hz, cortes, inicial=baseline.activacion if not cortes else None)
        except ModeloInfactible:
            break
        plan.nombre = f"Top {j + 1}"
        if not plan.viable:
            if not top:
                plan.nombre = "Plan de contingencia"
                contingencia = plan
            break
        top.append(plan)
        cortes.append(frozenset(plan.config_turno_actual))
    # KWD define Top 1 como la alternativa viable con mejor puntuación global: el objetivo del MILP
    # incluye penalizaciones auxiliares (arranques, cobertura final), así que se reordena por puntuación.
    top.sort(key=lambda p: p.puntuacion, reverse=True)
    for j, p in enumerate(top):
        p.nombre = f"Top {j + 1}"
    if not top and contingencia is None:
        # Ni siquiera hay solución con holguras (p. ej. recursos obligatorios imposibles): se usa el
        # plan de referencia como contingencia.
        baseline.estado = "INVIABLE"
        baseline.idoneidad = baseline.gap = None
        contingencia = baseline
        if not baseline.incumplimientos:
            baseline.incumplimientos = ["El modelo no tiene solución con las reglas obligatorias."]
    principal = top[0] if top else contingencia
    expl = _explicar(esc, hz, principal, top, baseline)
    al = _alertas(esc, hz, principal, top, contingencia, top_k)
    return Recomendacion(inicio=hz.inicio, horizonte=hz, top=top, contingencia=contingencia,
                         baseline=baseline, explicacion=expl, alertas=al, tiempo_total_s=time.perf_counter() - t0)
