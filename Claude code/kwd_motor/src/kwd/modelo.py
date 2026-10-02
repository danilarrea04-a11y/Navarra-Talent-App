"""Modelo MILP (PuLP + HiGHS) y evaluación de planes (KPIs y puntuación) (v3)."""
from __future__ import annotations

import math
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pulp

from .config import CELULA_LOGISTICA, CELULAS_PAREJA, COMPONENTES, PESO_PARAM, RECURSOS, TOL
from .datos import (Escenario, celulas_productivas, ss_por_celula, stock_inicial, tabla_celulas)
from .horizonte import Horizonte
from .personal import aplicar_personal, personas_enteras
from .plan import Plan
from .validador import (agotamiento, aviso_direccion, avisos_plan, consumos_ss, desabastecimiento, validar)

PENALIZACION = 1000.0  # holgura del espacio de almacén (> 800 m²): vuelve INVIABLE el plan (el pedido no servido usa penalizacion_pedido)
PESO_ARRANQUES = 1e-4  # estabilidad: penalización por arranque
TOL_HOLGURA = 1e-6
ESCALA_OBJ = 1000.0  # escala del objetivo (evita costes ~1e-8 por debajo de la tolerancia del solver)
FRACCION_FASE1 = 0.5 # fracción del tiempo límite para la fase 1 (N continuo); 0 = un solo modelo entero
GAP_FASE1 = 0.02      # gap aceptado en la fase 1
OPCIONES_SOLVER: dict = {}  # opciones extra de HiGHS (p. ej. mip_heuristic_effort)


def _var(prob, nombre, lo=0, up=None, cat="Continuous"):
    "Crea una variable compatible con PuLP 4 (add_variable) y con versiones anteriores."
    if hasattr(prob, "add_variable"):
        return prob.add_variable(nombre, lo, up, cat)
    return pulp.LpVariable(nombre, lo, up, cat)


class ModeloInfactible(RuntimeError):
    """El solver no encontró solución (modelo infactible o sin solución dentro del tiempo límite)."""


# ---------------------------------------------------------------------------------------------
def preparar(esc: Escenario, hz: Horizonte) -> SimpleNamespace:
    """Arrays numpy con los parámetros del modelo (índices de célula en orden creciente)."""
    t = tabla_celulas(esc)
    todas = sorted(t.index.tolist())
    prods = celulas_productivas(esc)
    p = esc.parametros
    ss = ss_por_celula(esc)
    i0 = stock_inicial(esc)
    s = hz.slots
    fmax = max(float(p["factor_solar"]), float(p["factor_noche"]), 1.0)
    disp = np.stack([s[f"{r}_disp"].to_numpy(dtype=float) for r in RECURSOS], axis=1)
    d = SimpleNamespace(
        esc=esc, hz=hz, todas=todas, prods=prods, H=len(s),
        ipos={c: i for i, c in enumerate(todas)},
        req=np.array([[float(t.loc[c, r]) for r in RECURSOS] for c in todas]),
        dens=np.array([float(t.loc[c, "piezas_m2"]) for c in prods]),
        ppc=np.ones(len(prods)),
        cap=np.array([float(t.loc[c, "cap_h"]) for c in prods]),
        kw=np.array([float(t.loc[c, "kw"]) for c in todas]),
        ss=np.array([ss[c] for c in prods]),
        i0=np.array([i0[c] for c in prods]),
        es_ve=np.array([bool(t.loc[c, "es_ve"]) for c in prods]),
        env=hz.envios[prods].to_numpy(dtype=float),
        disp=disp,
        cierres=list(hz.cierres),
        opt=(hz.stock_optimo[prods].to_numpy(dtype=float) if len(hz.cierres) else np.zeros((0, len(prods)))),
        W=s["laborable"].to_numpy(dtype=bool),
        f=s["factor_energia"].to_numpy(dtype=float),
        hora=s["hora"].to_numpy(dtype=int),
        A=esc.area_producto_terminado(),
        fmax=fmax,
        pesos={c: float(p[PESO_PARAM[c]]) for c in COMPONENTES},
        solar=(float(p["solar_ini"]), float(p["solar_fin"])),
        pen_ss=float(p.get("penalizacion_ss", 50.0)),
        pen_pedido=float(p.get("penalizacion_pedido", 1000.0)),
    )
    d.mascara_densa = d.dens > 0
    return d


def _trabajo(a: np.ndarray, u: np.ndarray, d) -> np.ndarray:
    """Horas-persona productivas por hora y rol: carga × fracción de la hora produciendo (H x 5).

    Las células productivas cuentan su fracción de uso u; las no productivas (célula 10, servicio logístico
    permanente) cuentan la hora completa mientras están activas.
    """
    efectivo = a.astype(float).copy()
    idx_p = [d.ipos[c] for c in d.prods]
    efectivo[:, idx_p] = u[:, idx_p]
    return efectivo @ d.req


def _componentes_RQ(N: np.ndarray, d, trabajo: np.ndarray) -> tuple[float, float]:
    """(R, Q) a partir del trabajo productivo y de las personas ocupadas N (H x 5).

    R = Σ (Disp - trabajo) / Σ Disp en horas laborables: fracción de tiempo muerto de TODO el personal presente,
    donde trabajo = carga de cada célula × fracción de la hora en que produce (célula 10: hora completa).
    Q = media sobre horas laborables de la ocupación media de mto y calidad (N/disp).
    """
    iw = np.where(d.W)[0]
    den = float(d.disp[iw].sum())
    R = float((d.disp[iw] - trabajo[iw]).sum() / den) if den > 1e-9 else 0.0
    im, ic = RECURSOS.index("mto"), RECURSOS.index("calidad")
    q = []
    for h in iw:
        tt = [N[h, k] / d.disp[h, k] for k in (im, ic) if d.disp[h, k] > 1e-9]
        if tt:
            q.append(sum(tt) / len(tt))
    Q = float(np.mean(q)) if q else 0.0
    return R, Q


def _tabla_optimo(d, stock: np.ndarray) -> pd.DataFrame:
    """Stock vs óptimo en cada cierre de turno: cierre, slot, celula, stock, optimo, desviacion, desviacion_pct."""
    filas = []
    s = d.hz.slots
    for k, h in enumerate(d.cierres):
        for j, c in enumerate(d.prods):
            o = float(d.opt[k, j])
            dv = float(stock[h, j] - o)
            filas.append({"cierre": s["fin"].iloc[h], "slot": int(h), "celula": int(c), "stock": float(stock[h, j]),
                          "optimo": o, "desviacion": dv, "desviacion_pct": 100.0 * abs(dv) / o if o > 0 else 0.0})
    return pd.DataFrame(filas, columns=["cierre", "slot", "celula", "stock", "optimo", "desviacion",
                                        "desviacion_pct"])


# ---------------------------------------------------------------------------------------------
def evaluar(esc: Escenario, hz: Horizonte, activacion: pd.DataFrame, uso: pd.DataFrame,
            estado: str = "EVALUADO", gap: float = 0.0, holguras: dict | None = None,
            previo=None) -> Plan:
    """Calcula stock, personal, energía, KPIs y puntuación de una activación dada (u en [0,1])."""
    d = preparar(esc, hz)
    H = d.H
    a = activacion.reindex(columns=d.todas).fillna(0).to_numpy(dtype=float)
    u = uso.reindex(columns=d.todas).fillna(0).to_numpy(dtype=float)
    a = a.reshape(H, len(d.todas))
    idx_p = [d.ipos[c] for c in d.prods]
    up = u[:, idx_p]
    prod = up * d.cap[None, :]
    stock = d.i0[None, :] + np.cumsum(prod - d.env, axis=0)
    dens_ok = np.where(d.mascara_densa, d.dens, np.inf)
    espacio = (stock / dens_ok[None, :]).sum(axis=1)  # m² de producto terminado
    req = a @ d.req                       # cargas fraccionarias por hora y rol
    N = personas_enteras(req)             # personas ocupadas por hora y rol
    trabajo = _trabajo(a, u, d)           # horas-persona realmente produciendo por hora y rol
    energia_bruta = (u * d.kw[None, :]).sum(axis=1)
    energia_red = energia_bruta * d.f

    # Componentes de la puntuación (menor = mejor)
    R, Q = _componentes_RQ(N, d, trabajo)
    S = float(np.mean(espacio / d.A)) if H else 0.0
    if len(d.cierres) and len(d.prods):  # B = media de |I - óptimo| / óptimo en los cierres de turno
        B = float(np.mean(np.abs(stock[d.cierres] - d.opt) / np.where(d.opt > 0, d.opt, np.inf)))
    else:
        B = 0.0
    den_e = d.W.sum() * d.kw.sum() * d.fmax
    E = float((d.f * (u * d.kw[None, :]).sum(axis=1)).sum() / den_e) if den_e > 0 else 0.0
    comp = {"R": R, "S": S, "Q": Q, "B": B, "E": E}
    contrib = {c: 100.0 * d.pesos[c] * (1.0 - comp[c]) for c in COMPONENTES}
    puntuacion = float(sum(contrib.values()))

    cols_a = pd.DataFrame(a, index=hz.slots.index, columns=d.todas).astype(int)
    cols_u = pd.DataFrame(u, index=hz.slots.index, columns=d.todas)
    idx = hz.slots.index
    rec = pd.DataFrame(index=idx)
    for j, r in enumerate(RECURSOS):
        rec[f"{r}_usado"] = N[:, j]
        rec[f"{r}_req"] = req[:, j]
        rec[f"{r}_trabajo"] = trabajo[:, j]
        rec[f"{r}_disp"] = d.disp[:, j]
    turno_idx = hz.slots_turno_actual()
    config = [c for c in d.todas if cols_a.loc[turno_idx, c].sum() > 0] if turno_idx else []

    plan = Plan(
        estado=estado, viable=True, gap=float(gap), idoneidad=100.0 * (1.0 - min(max(gap, 0.0), 1.0)),
        puntuacion=puntuacion, componentes=comp, contribuciones=contrib, activacion=cols_a, uso=cols_u,
        produccion=pd.DataFrame(prod, index=idx, columns=d.prods),
        stock=pd.DataFrame(stock, index=idx, columns=d.prods),
        espacio=pd.Series(espacio, index=idx, name="m2"), recursos=rec,
        energia_kwh=pd.Series(energia_red, index=idx, name="kwh"), config_turno_actual=config,
        holguras=holguras or {},
    )
    plan.personas = pd.DataFrame(N, index=idx, columns=RECURSOS)
    aplicar_personal(esc, hz, plan, previo)
    plan.stock_vs_optimo = _tabla_optimo(d, stock)
    plan.kpis, plan.resumen_turnos = _kpis(d, hz, plan, energia_bruta)
    plan.incumplimientos = validar(esc, hz, plan)
    plan.viable = len(plan.incumplimientos) == 0
    plan.desabastecimiento = desabastecimiento(esc, hz, plan)
    plan.agotamiento = agotamiento(esc, hz, plan)
    plan.aviso_direccion = aviso_direccion(esc, hz, plan)
    if not plan.viable:
        plan.estado = "INVIABLE"
    elif plan.aviso_direccion is not None and plan.estado in ("OPTIMO", "FACTIBLE", "EVALUADO"):
        plan.estado = "CRITICO"  # pedidos sin servir: ejecutable pero con aviso para dirección
    plan.avisos = avisos_plan(esc, hz, plan)
    plan.kpis["ss_consumos"] = len(consumos_ss(esc, hz, plan))
    plan.kpis["piezas_no_servidas_total"] = float(plan.desabastecimiento["piezas_no_servidas"].sum())
    if plan.estado == "INVIABLE":
        # No aplica: plan sin garantía de óptimo
        plan.idoneidad = None
        plan.gap = None
        plan.kpis["idoneidad"] = None
    return plan


def reasignar_personal(esc: Escenario, hz: Horizonte, plan: Plan, previo=None) -> None:
    """Recalcula la asignación nominal de personal de un plan (p. ej. partiendo de la del plan anterior)."""
    aplicar_personal(esc, hz, plan, previo)
    plan.incumplimientos = validar(esc, hz, plan)


# ---------------------------------------------------------------------------------------------
def _kpis_tramo(d, hz, plan: Plan, idx: np.ndarray, bruta: np.ndarray) -> dict:
    """KPIs de un tramo de slots `idx` (plan completo o un turno)."""
    s = hz.slots
    A = d.A
    W = d.W[idx]
    rec = plan.recursos
    k = {}
    k["horas"] = int(len(idx))
    k["horas_laborables"] = int(W.sum())
    ve = float(s["piezas_ve"].to_numpy()[idx].sum())
    cb = float(s["piezas_comb"].to_numpy()[idx].sum())
    st = plan.stock.to_numpy()[idx]
    falt_ve = falt_cb = 0.0
    if st.size:
        falt = np.maximum(0.0, -st.min(axis=0))
        falt_ve = float(falt[d.es_ve].max()) if d.es_ve.any() else 0.0
        falt_cb = float(falt[~d.es_ve].max()) if (~d.es_ve).any() else 0.0
    k["piezas_ve_a_expedir"] = ve
    k["piezas_comb_a_expedir"] = cb
    k["piezas_ve_cubiertas"] = ve - min(ve, falt_ve)
    k["piezas_comb_cubiertas"] = cb - min(cb, falt_cb)
    # alias de la v1
    k["chasis_ve_a_expedir"], k["chasis_comb_a_expedir"] = k["piezas_ve_a_expedir"], k["piezas_comb_a_expedir"]
    k["chasis_ve_cubiertos"], k["chasis_comb_cubiertos"] = k["piezas_ve_cubiertas"], k["piezas_comb_cubiertas"]
    k["demanda_cubierta_pct"] = (100.0 * (k["piezas_ve_cubiertas"] + k["piezas_comb_cubiertas"]) / (ve + cb)
                                 if (ve + cb) > 1e-9 else 100.0)
    n_ve, n_cb = int(d.es_ve.sum()), int((~d.es_ve).sum())
    k["piezas_total_a_expedir"] = ve * n_ve + cb * n_cb
    # camiones (A2): los que hagan falta en cada ciclo, 15 m² como máximo cada uno
    k["camiones"] = int(s["camiones"].to_numpy()[idx].sum())
    k["ciclos"] = int(s["ciclos"].to_numpy()[idx].sum())
    k["camiones_por_ciclo_medio"] = k["camiones"] / k["ciclos"] if k["ciclos"] else 0.0
    cam = hz.camiones
    k["camiones_por_ciclo_max"] = int(cam.loc[cam["slot"].isin(idx), "n_camiones"].max()) if len(cam) and \
        cam["slot"].isin(idx).any() else 0
    for r in RECURSOS:
        u_ = rec[f"{r}_usado"].to_numpy()[idx]   # personas enteras N
        rq = rec[f"{r}_req"].to_numpy()[idx]
        dp = rec[f"{r}_disp"].to_numpy()[idx]
        sel = W & (dp > 1e-9)
        frac = u_[sel] / dp[sel] if sel.any() else np.array([0.0])
        k[f"{r}_horas"] = float(u_[W].sum())
        k[f"{r}_horas_req"] = float(rq[W].sum())
        k[f"desperdicio_{r}_h"] = float((u_[W] - rq[W]).sum())  # A7: Σ_h (N - Σ req·a)
        k[f"{r}_ocup_media_pct"] = float(100 * frac.mean())
        k[f"{r}_ocup_pico_pct"] = float(100 * frac.max())
    k["desperdicio_personal_h"] = float(sum(k[f"desperdicio_{r}_h"] for r in RECURSOS))
    # horas libres (tiempo muerto) = Σ (presentes - trabajo productivo) en horas laborables (KPI principal)
    for r in RECURSOS:
        dp = rec[f"{r}_disp"].to_numpy()[idx][W]
        un = rec[f"{r}_trabajo"].to_numpy()[idx][W]
        k[f"horas_libres_{r}"] = float((dp - un).sum())
        k[f"ocupacion_{r}_pct"] = float(100 * un.sum() / dp.sum()) if dp.sum() > 1e-9 else 0.0
    k["horas_libres_total"] = float(sum(k[f"horas_libres_{r}"] for r in RECURSOS))
    tot = sum(float(rec[f"{r}_disp"].to_numpy()[idx][W].sum()) for r in RECURSOS)
    k["ocupacion_total_pct"] = float(100 * (1 - k["horas_libres_total"] / tot)) if tot > 1e-9 else 0.0
    # stock vs óptimo en los cierres de turno de este tramo
    cs = [h for h in d.cierres if h in set(idx.tolist())]
    if cs:
        sv = plan.stock_vs_optimo
        g = sv[sv["slot"].isin(cs)]
        k["stock_opt_dev_media_pct"] = float(g["desviacion_pct"].mean())
        k["stock_opt_dev_max_pct"] = float(g["desviacion_pct"].max())
        k["stock_opt_dev_media_pzs"] = float(g["desviacion"].abs().mean())
    else:
        k["stock_opt_dev_media_pct"] = k["stock_opt_dev_max_pct"] = k["stock_opt_dev_media_pzs"] = None
    esp = plan.espacio.to_numpy()[idx]
    k["m2_medio"] = float(esp.mean()) if esp.size else 0.0
    k["m2_pico"] = float(esp.max()) if esp.size else 0.0
    k["m2_medio_pct"] = 100 * k["m2_medio"] / A
    k["m2_pico_pct"] = 100 * k["m2_pico"] / A
    smin = st.min(axis=0) if st.size else d.i0
    k["stock_min"] = {c: float(v) for c, v in zip(d.prods, smin)}
    k["stock_min_vs_ss"] = {c: float(v / ss) for c, v, ss in zip(d.prods, smin, d.ss)}
    envm = d.env[idx].mean(axis=0) if st.size else np.zeros(len(d.prods))
    k["cobertura_horas"] = {c: (float(v / e) if e > 1e-9 else None) for c, v, e in zip(d.prods, smin, envm)}
    hora = d.hora[idx]
    b = bruta[idx]
    solar = (hora >= d.solar[0]) & (hora < d.solar[1])
    noche = (hora >= 22) | (hora < 6)
    k["kwh_total"] = float(plan.energia_kwh.to_numpy()[idx].sum())  # de red (con factor F5)
    k["kwh_bruto"] = float(b.sum())
    k["kwh_solar"] = float(b[solar].sum())
    k["kwh_solar_pct"] = float(100 * b[solar].sum() / b.sum()) if b.sum() > 1e-9 else 0.0
    k["kwh_noche"] = float(b[noche].sum())
    k["piezas_producidas"] = float(plan.produccion.to_numpy()[idx].sum())
    a = plan.activacion.to_numpy()[idx]
    k["horas_celula"] = float(a.sum())
    k["celulas_activas"] = [c for c, v in zip(d.todas, a.sum(axis=0)) if v > 0]
    return k


def _kpis(d, hz, plan: Plan, bruta: np.ndarray):
    """KPIs globales del plan y tabla resumen por turno del horizonte."""
    H = d.H
    k = _kpis_tramo(d, hz, plan, np.arange(H), bruta)
    k["puntuacion"] = plan.puntuacion
    k["idoneidad"] = plan.idoneidad
    k["camiones_dia"] = k["camiones"] * 24.0 / H if H else 0.0
    act = plan.activacion.to_numpy()
    arr = np.maximum(0, np.diff(np.vstack([np.zeros(act.shape[1]), act]), axis=0)).sum()
    k["arranques"] = int(arr)
    k["horas_celula_productivas"] = float(plan.activacion[d.prods].to_numpy().sum())
    sv = plan.stock_vs_optimo
    k["stock_opt_por_cierre"] = [] if sv.empty else [
        {"cierre": c, "dev_media_pct": float(g["desviacion_pct"].mean()), "dev_max_pct": float(g["desviacion_pct"].max())}
        for c, g in sv.groupby("cierre", sort=True)]
    s = hz.slots
    filas = []
    clave = list(zip(s["fecha_turno"], s["turno"]))
    inicio_t = 0
    for h in range(1, H + 1):
        if h == H or clave[h] != clave[inicio_t]:
            idx = np.arange(inicio_t, h)
            kt = _kpis_tramo(d, hz, plan, idx, bruta)
            fila = {"turno": clave[inicio_t][1], "fecha_turno": clave[inicio_t][0],
                    "inicio": s["inicio"].iloc[inicio_t], "fin": s["fin"].iloc[h - 1],
                    "laborable": bool(s["laborable"].iloc[inicio_t])}
            for kk, vv in kt.items():
                if not isinstance(vv, dict):
                    fila[kk] = vv
            fila["stock_min_ratio_ss"] = min(kt["stock_min_vs_ss"].values()) if kt["stock_min_vs_ss"] else None
            filas.append(fila)
            inicio_t = h
    return k, pd.DataFrame(filas)


# ---------------------------------------------------------------------------------------------
def resolver(esc: Escenario, hz: Horizonte, cortes: list | None = None, tiempo_limite=None,
             inicial: pd.DataFrame | None = None, previo=None) -> Plan:
    """Resuelve el MILP y devuelve el plan evaluado.

    `cortes`: lista de configuraciones (frozenset de células) del turno actual ya obtenidas, que se
    excluyen con cortes de no-buen (sección 4 de la especificación).

    Dos fases (personal entero): fase 1 con N continuo (FRACCION_FASE1 del tiempo) para hallar una buena activación;
    fase 2 con N entero arrancando en caliente con esa activación. `tiempo_limite` (por defecto el parámetro
    `tiempo_limite_s`) es el tiempo total por plan.
    """
    t_ini = time.perf_counter()
    tl = float(tiempo_limite if tiempo_limite is not None else esc.parametros["tiempo_limite_s"])
    semilla = inicial
    if FRACCION_FASE1 > 0:
        gap_rel = float(esc.parametros.get("gap_relativo", 0.001))
        try:
            p1 = _resolver_uno(esc, hz, cortes, max(1.0, tl * FRACCION_FASE1), inicial, previo, False,
                               max(gap_rel, GAP_FASE1))
            semilla = p1.activacion
        except ModeloInfactible:
            pass
        tl = max(1.0, tl * (1 - FRACCION_FASE1))
    plan = _resolver_uno(esc, hz, cortes, tl, semilla, previo, True, None)
    plan.tiempo_s = time.perf_counter() - t_ini
    return plan


def _resolver_uno(esc: Escenario, hz: Horizonte, cortes, tl: float, inicial, previo, entero: bool,
                  gap_f) -> Plan:
    t_ini = time.perf_counter()
    cortes = cortes or []
    d = preparar(esc, hz)
    H, todas, prods = d.H, d.todas, d.prods
    prob = pulp.LpProblem("kwd_plan", pulp.LpMinimize)

    # --- variables ---
    a, u, I, sh, sn, st = {}, {}, {}, {}, {}, {}
    dpos, dneg = {}, {}
    for c in todas:
        for h in range(H):
            bloqueado = (h in hz.bloqueos.get(c, set())) or (not d.W[h])  # reglas 2 y 4
            # regla 2 / célula 10 siempre activa en horas laborables (salvo F14)
            lo = 1 if (c == CELULA_LOGISTICA and hz.logistica_exigida[h]) else 0
            up = 0 if bloqueado else 1
            a[c, h] = _var(prob, f"a_{c}_{h}", lo, up, "Binary")
            if c in prods:
                u[c, h] = _var(prob, f"u_{c}_{h}", 0, 1)
                I[c, h] = _var(prob, f"I_{c}_{h}", None, None)
                sh[c, h] = _var(prob, f"s_{c}_{h}", 0)   # SS consumido (penalización alta)
                sn[c, h] = _var(prob, f"sn_{c}_{h}", 0)  # pedido no servido (stock < 0): penalización máxima
                st[c, h] = _var(prob, f"st_{c}_{h}", 0)
    sa = {h: _var(prob, f"sa_{h}", 0) for h in range(H)}
    for k_, h in enumerate(d.cierres):   # desviación |I - óptimo| en cada cierre de turno
        for c in prods:
            dpos[c, k_] = _var(prob, f"dp_{c}_{k_}", 0)
            dneg[c, k_] = _var(prob, f"dn_{c}_{k_}", 0)

    # personas ocupadas N por hora y rol (sólo en la fase entera): N = techo de la carga
    iw = np.where(d.W)[0]
    Nv = {}
    if entero:
        for h in iw:
            for j, r in enumerate(RECURSOS):
                Nv[j, h] = _var(prob, f"N_{r}_{h}", 0, math.floor(d.disp[h, j] + 1e-9), "Integer")

    # --- restricciones ---
    for c in prods:
        for h in range(H):
            prob += u[c, h] <= a[c, h], f"r1_{c}_{h}"
    par = [c for c in CELULAS_PAREJA if c in todas]
    if len(par) == 2:  # regla 3
        for h in range(H):
            prob += (a[par[0], h] - a[par[1], h] == 0), f"r3a_{h}"
            prob += (u[par[0], h] - u[par[1], h] == 0), f"r3u_{h}"
    carga = {(j, h): pulp.lpSum(d.req[d.ipos[c], j] * a[c, h] for c in todas if d.req[d.ipos[c], j] > 0)
             for h in iw for j in range(len(RECURSOS))}
    for h in iw:  # regla 5: la carga cabe en los presentes; N = techo(carga) (fase entera)
        for j, r in enumerate(RECURSOS):
            prob += carga[j, h] <= float(d.disp[h, j]), f"r5d_{r}_{h}"
            if entero:
                prob += carga[j, h] <= Nv[j, h], f"r5_{r}_{h}"
                prob += Nv[j, h] <= carga[j, h] + 0.999, f"r5u_{r}_{h}"  # N no supera el techo de la carga
                for c in todas:  # corte válido: carga fraccionaria activa => al menos ceil(carga) personas
                    q = d.req[d.ipos[c], j]
                    if q > 1e-9 and math.ceil(q - 1e-9) > q + 1e-9:
                        prob += Nv[j, h] >= math.ceil(q - 1e-9) * a[c, h], f"rN_{r}_{h}_{c}"
    for ic, c in enumerate(prods):
        for h in range(H):
            prev = d.i0[ic] if h == 0 else I[c, h - 1]
            prob += I[c, h] == prev + d.cap[ic] * u[c, h] - d.env[h, ic], f"r6_{c}_{h}"  # regla 6
            prob += I[c, h] + sn[c, h] >= 0, f"r6b_{c}_{h}"  # stock >= 0 (pedido servido)
            prob += I[c, h] >= d.ss[ic] - sh[c, h], f"r7_{c}_{h}"  # SS: consumible con penalización
            prev_a = 0 if h == 0 else a[c, h - 1]
            prob += st[c, h] >= a[c, h] - prev_a, f"r10_{c}_{h}"  # regla 10
    for k_, h in enumerate(d.cierres):  # I - óptimo = d+ - d-
        for ic, c in enumerate(prods):
            prob += I[c, h] - float(d.opt[k_, ic]) == dpos[c, k_] - dneg[c, k_], f"opt_{c}_{k_}"
    for h in range(H):  # regla 8
        prob += (pulp.lpSum(I[c, h] * (1.0 / d.dens[ic]) for ic, c in enumerate(prods) if d.dens[ic] > 0)
                 <= d.A + sa[h]), f"r8_{h}"

    # --- Top-K: configuración del turno actual y cortes (sección 4) ---
    turno_idx = hz.slots_turno_actual()
    if cortes:
        y = {c: _var(prob, f"y_{c}", 0, 1, "Binary") for c in todas}
        for c in todas:
            for h in turno_idx:
                prob += y[c] >= a[c, h], f"y1_{c}_{h}"
            prob += y[c] <= pulp.lpSum(a[c, h] for h in turno_idx), f"y2_{c}"
        for j, S in enumerate(cortes):
            prob += (pulp.lpSum(1 - y[c] for c in todas if c in S) +
                     pulp.lpSum(y[c] for c in todas if c not in S)) >= 1, f"corte_{j}"

    # --- objetivo ---
    im, ic_ = RECURSOS.index("mto"), RECURSOS.index("calidad")
    den_r = float(d.disp[iw].sum()) if len(iw) else 0.0
    # R = tiempo muerto del personal presente = (ΣDisp - Σ trabajo productivo) / ΣDisp. El trabajo cuenta la
    # fracción de la hora que cada célula produce (u), así que activar una célula sin producir no reduce R.
    if den_r > 1e-9:
        prods_set = set(prods)
        trabajo = pulp.lpSum(d.req[d.ipos[c], j] * (u[c, h] if c in prods_set else a[c, h])
                             for c in todas for h in iw for j in range(len(RECURSOS)) if d.req[d.ipos[c], j] > 0)
        expr_R = 1.0 - trabajo * (1.0 / den_r)
    else:
        expr_R = 0
    terminos_q = []
    for h in iw:
        ks = [k for k in (im, ic_) if d.disp[h, k] > 1e-9]
        for k in ks:
            terminos_q.append((Nv[k, h] if entero else carga[k, h]) * (1.0 / d.disp[h, k] / len(ks)))
    expr_Q = pulp.lpSum(terminos_q) * (1.0 / len(iw)) if len(iw) else 0
    expr_S = pulp.lpSum((1.0 / d.dens[ic]) / d.A / H * I[c, h]
                        for ic, c in enumerate(prods) for h in range(H) if d.dens[ic] > 0)
    if len(d.cierres) and prods:
        expr_B = pulp.lpSum((dpos[c, k_] + dneg[c, k_]) * (1.0 / max(float(d.opt[k_, ic]), 1.0))
                            / (len(prods) * len(d.cierres))
                            for k_ in range(len(d.cierres)) for ic, c in enumerate(prods))
    else:
        expr_B = 0
    den_e = d.W.sum() * d.kw.sum() * d.fmax
    expr_E = (pulp.lpSum(d.f[h] * d.kw[d.ipos[c]] / den_e * u[c, h] for c in prods for h in range(H)
                         if d.kw[d.ipos[c]] > 0) if den_e > 0 else 0)
    pen_espacio = pulp.lpSum(sa[h] / d.A for h in range(H))
    pen_pedido = pulp.lpSum(sn[c, h] / d.ss[ic] for ic, c in enumerate(prods) for h in range(H))
    pen_ss = pulp.lpSum(sh[c, h] / d.ss[ic] for ic, c in enumerate(prods) for h in range(H))
    w = d.pesos
    # jerarquía: pedido no servido >> stock bajo SS >> criterios (R, S, Q, B, E)
    prob += ESCALA_OBJ * (w["R"] * expr_R + w["S"] * expr_S + w["Q"] * expr_Q + w["B"] * expr_B + w["E"] * expr_E +
                          PENALIZACION * pen_espacio + d.pen_pedido * pen_pedido + d.pen_ss * pen_ss +
                          PESO_ARRANQUES * pulp.lpSum(st.values())), "objetivo"

    # --- resolver (HiGHS vía highspy; gap y estado se leen del modelo nativo) ---
    # DESVIACIÓN de la spec (mip_rel_gap = 0): se acepta un gap relativo pequeño (parámetro gap_relativo).
    # Estrategia en dos fases por el personal entero: (1) modelo con N continuo (rápido) para encontrar una buena
    # activación; (2) modelo entero arrancado en caliente con esa activación.
    import highspy
    gap_rel = float(esc.parametros.get("gap_relativo", 0.001))

    def _resolver_fase(tl_f, gap_f, inicio_a):
        caliente = False
        if inicio_a is not None:
            try:
                for c in todas:
                    for h in range(H):
                        a[c, h].setInitialValue(float(inicio_a.at[h, c]))
                caliente = True
            except Exception:  # versión de PuLP sin setInitialValue
                caliente = False
        # gap ABSOLUTO equivalente a gap_f sobre un objetivo típico de ~0,5 (escala ESCALA_OBJ): si hay penalizaciones grandes
        # (SS consumido) un gap relativo pararía demasiado pronto con soluciones malas.
        solver = pulp.HiGHS(msg=False, timeLimit=tl_f, gapRel=0.0, gapAbs=gap_f * 0.5 * ESCALA_OBJ,
                            warmStart=caliente, **OPCIONES_SOLVER)
        prob.solve(solver)
        hm = prob.solverModel
        ms_ = hm.getModelStatus()
        info_ = hm.getInfo()
        optimo_f = ms_ == highspy.HighsModelStatus.kOptimal
        hay = optimo_f or (ms_ in (highspy.HighsModelStatus.kTimeLimit, highspy.HighsModelStatus.kIterationLimit,
                                   highspy.HighsModelStatus.kSolutionLimit, highspy.HighsModelStatus.kInterrupt)
                           and info_.primal_solution_status == 2)
        return hay, optimo_f, info_, ms_

    hay_sol, optimo, info, ms = _resolver_fase(tl, gap_f if gap_f is not None else gap_rel, inicial)
    if not hay_sol:
        raise ModeloInfactible(f"HiGHS terminó sin solución (estado {ms}).")
    g = info.mip_gap
    if g is not None and math.isfinite(g):
        gap = max(0.0, float(g))
    else:
        gap = 0.0 if optimo else 1.0

    av = np.array([[round(a[c, h].value() or 0) for c in todas] for h in range(H)], dtype=float)
    if not entero:  # fase 1: sólo interesa la activación (semilla de la fase 2)
        return SimpleNamespace(activacion=pd.DataFrame(av, index=hz.slots.index, columns=todas))
    uv = np.zeros((H, len(todas)))
    for c in prods:
        for h in range(H):
            val = float(u[c, h].value() or 0.0)
            val = min(max(val, 0.0), av[h, d.ipos[c]])
            uv[h, d.ipos[c]] = 0.0 if val < 1e-7 else val
    hol_s = float(sum((sh[c, h].value() or 0.0) for c in prods for h in range(H)))
    hol_n = float(sum((sn[c, h].value() or 0.0) for c in prods for h in range(H)))
    hol_a = float(sum((sa[h].value() or 0.0) for h in range(H)))
    estado = "OPTIMO" if optimo else "FACTIBLE"
    if hol_a > TOL_HOLGURA:  # sólo almacén > 800 m² (o reglas duras) descarta el plan; stock < 0 => CRITICO
        estado = "INVIABLE"
    plan = evaluar(esc, hz, pd.DataFrame(av, index=hz.slots.index, columns=todas),
                   pd.DataFrame(uv, index=hz.slots.index, columns=todas), estado=estado, gap=gap,
                   holguras={"stock": hol_n, "ss": hol_s, "espacio": hol_a}, previo=previo)
    plan.objetivo = float(info.objective_function_value) / ESCALA_OBJ
    plan.tiempo_s = time.perf_counter() - t_ini
    if plan.viable and plan.gap is not None:
        # Margen máximo de mejora en puntos = 100 × (objetivo − cota del solver). Aproximado: supone que los
        # término de arranques se mantiene.
        margen = 100.0 * plan.objetivo * plan.gap
        plan.kpis["margen_mejora_max"] = margen
        plan.kpis["puntuacion_max_teorica"] = min(100.0, plan.puntuacion + margen)
    return plan
