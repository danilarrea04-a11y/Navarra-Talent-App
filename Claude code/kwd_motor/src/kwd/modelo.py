"""Modelo MILP (PuLP + HiGHS) y evaluación de planes (KPIs y puntuación) (v2)."""
from __future__ import annotations

import math
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pulp

from .config import (CELULA_LOGISTICA, CELULAS_PAREJA, COMPONENTES, PESO_IDLE, PESO_PARAM, PESO_PLANTILLA,
                     RECURSOS, RECURSOS_R, TOL)
from .datos import (Escenario, celulas_productivas, ss_por_celula, stock_inicial, tabla_celulas)
from .horizonte import Horizonte
from .personal import aplicar_personal, personas_enteras
from .plan import Plan
from .validador import avisos_plan, consumos_ss, validar

PENALIZACION = 1000.0  # penalización de las holguras duras (stock < 0, espacio): vuelven INVIABLE el plan
PENALIZACION_FINAL = 10.0  # FLAG F18: holgura blanda de la condición terminal de stock
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
    # turnos laborables del horizonte con la disponibilidad mínima de cada rol (cota de la plantilla P)
    turnos = []
    for ft, tn, slots in hz.turnos_trabajo():
        turnos.append((np.array(slots, dtype=int), disp[slots].min(axis=0), tn, ft))
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
        env_final=(hz.envio_final.reindex(prods).fillna(0.0).to_numpy(dtype=float)
                   if hz.envio_final is not None else np.zeros(len(prods))),
        disp=disp, turnos=turnos,
        W=s["laborable"].to_numpy(dtype=bool),
        f=s["factor_energia"].to_numpy(dtype=float),
        hora=s["hora"].to_numpy(dtype=int),
        A=esc.area_producto_terminado(),
        k=float(p["colchon_ss"]),  # FLAG F9
        fmax=fmax,
        pesos={c: float(p[PESO_PARAM[c]]) for c in COMPONENTES},
        solar=(float(p["solar_ini"]), float(p["solar_fin"])),
        pen_ss=float(p.get("penalizacion_ss", 50.0)),  # FLAG F20
    )
    d.mascara_densa = d.dens > 0
    return d


def _componentes_RQ(N: np.ndarray, d) -> tuple[float, float]:
    """(R, Q) a partir de las personas enteras N (H x 5).

    R = 0,7 media_{k,turno}(P/disp_turno) + 0,3 media_{k,h}(N/disp_h) con P = pico de N en el turno (FLAG F21);
    Q = media sobre horas laborables de la ocupación media de mto y calidad (N/disp).
    """
    ir = [RECURSOS.index(r) for r in RECURSOS_R]
    iw = np.where(d.W)[0]
    rp = [N[slots, k].max() / dmin[k] for slots, dmin, _, _ in d.turnos for k in ir if dmin[k] > 1e-9]
    rn = [N[h, k] / d.disp[h, k] for h in iw for k in ir if d.disp[h, k] > 1e-9]
    mp = float(np.mean(rp)) if rp else 0.0
    mn = float(np.mean(rn)) if rn else 0.0
    R = PESO_PLANTILLA * mp + (1 - PESO_PLANTILLA) * mn
    im, ic = RECURSOS.index("mto"), RECURSOS.index("calidad")
    q = []
    for h in iw:
        tt = [N[h, k] / d.disp[h, k] for k in (im, ic) if d.disp[h, k] > 1e-9]
        if tt:
            q.append(sum(tt) / len(tt))
    Q = float(np.mean(q)) if q else 0.0
    return R, Q


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
    N = personas_enteras(req)             # personas enteras necesarias por hora y rol (FLAG F21)
    energia_bruta = (u * d.kw[None, :]).sum(axis=1)
    energia_red = energia_bruta * d.f  # FLAG F16

    # Componentes de la puntuación (menor = mejor)
    R, Q = _componentes_RQ(N, d)
    S = float(np.mean(espacio / d.A)) if H else 0.0
    if d.k > 0 and len(d.prods):
        corto = np.maximum(0.0, (1 + d.k) * d.ss[None, :] - stock)
        B = float(np.mean(corto / (d.k * d.ss[None, :])))
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
    plan.kpis, plan.resumen_turnos = _kpis(d, hz, plan, energia_bruta)
    _kpis_personal(plan)
    plan.incumplimientos = validar(esc, hz, plan)
    plan.viable = len(plan.incumplimientos) == 0
    if not plan.viable and plan.estado in ("OPTIMO", "FACTIBLE"):
        plan.estado = "INVIABLE"
    plan.avisos = avisos_plan(esc, hz, plan)
    plan.kpis["ss_consumos"] = len(consumos_ss(esc, hz, plan))
    if plan.estado in ("INVIABLE", "REFERENCIA"):
        # No aplica: plan de contingencia o heurístico (sin garantía de óptimo)
        plan.idoneidad = None
        plan.gap = None
        plan.kpis["idoneidad"] = None
    return plan


def reasignar_personal(esc: Escenario, hz: Horizonte, plan: Plan, previo=None) -> None:
    """Recalcula la asignación nominal de personal de un plan (p. ej. partiendo de la del plan anterior)."""
    aplicar_personal(esc, hz, plan, previo)
    _kpis_personal(plan)
    plan.incumplimientos = validar(esc, hz, plan)


def _kpis_personal(plan: Plan) -> None:
    """KPIs de plantilla: plantilla por turno y rol, horas libres de plantilla y excedente."""
    k = plan.kpis
    pl = plan.plantilla
    k["plantilla"] = {}
    for _, f in pl.iterrows():
        k["plantilla"].setdefault(f"{f['turno']} {pd.Timestamp(f['fecha_turno']):%d/%m}", {})[f["rol"]] = int(f["plantilla"])
    for r in RECURSOS:
        g = pl[pl["rol"] == r]
        k[f"horas_libres_{r}"] = int(g["horas_libres"].sum()) if len(g) else 0
        k[f"excedente_{r}"] = int(g["excedente"].sum()) if len(g) else 0
    k["horas_libres_total"] = int(sum(k[f"horas_libres_{r}"] for r in RECURSOS))
    k["excedente_total"] = int(sum(k[f"excedente_{r}"] for r in RECURSOS))


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
    a, u, I, sh, sn, st, sho = {}, {}, {}, {}, {}, {}, {}
    for c in todas:
        for h in range(H):
            bloqueado = (h in hz.bloqueos.get(c, set())) or (not d.W[h])  # reglas 2 y 4
            # regla 2 / FLAG F11: célula 10 siempre activa en horas laborables (salvo F14)
            lo = 1 if (c == CELULA_LOGISTICA and hz.logistica_exigida[h]) else 0
            up = 0 if bloqueado else 1
            a[c, h] = _var(prob, f"a_{c}_{h}", lo, up, "Binary")
            if c in prods:
                u[c, h] = _var(prob, f"u_{c}_{h}", 0, 1)
                I[c, h] = _var(prob, f"I_{c}_{h}", None, None)
                sh[c, h] = _var(prob, f"s_{c}_{h}", 0)   # SS consumido (prioridad máxima, F20)
                sn[c, h] = _var(prob, f"sn_{c}_{h}", 0)  # pedido no servido (stock < 0): INVIABLE
                st[c, h] = _var(prob, f"st_{c}_{h}", 0)
                if d.k > 0:
                    sho[c, h] = _var(prob, f"short_{c}_{h}", 0)
    sa = {h: _var(prob, f"sa_{h}", 0) for h in range(H)}
    sf = {c: _var(prob, f"sf_{c}", 0) for c in prods}  # holgura terminal (F18)

    # personal entero: N por hora y rol, plantilla P por turno y rol (FLAG F21)
    iw = np.where(d.W)[0]
    Nv, Pv = {}, {}
    for h in iw:
        for j, r in enumerate(RECURSOS):
            Nv[j, h] = _var(prob, f"N_{r}_{h}", 0, math.floor(d.disp[h, j] + 1e-9),
                              "Integer" if entero else "Continuous")
    for si, (slots, dmin, tn, ft) in enumerate(d.turnos):
        for j, r in enumerate(RECURSOS):
            Pv[j, si] = _var(prob, f"P_{r}_{si}", 0, math.floor(dmin[j] + 1e-9))  # continua: P = max N es entero al óptimo

    # --- restricciones ---
    for c in prods:
        for h in range(H):
            prob += u[c, h] <= a[c, h], f"r1_{c}_{h}"
    par = [c for c in CELULAS_PAREJA if c in todas]
    if len(par) == 2:  # regla 3
        for h in range(H):
            prob += (a[par[0], h] - a[par[1], h] == 0), f"r3a_{h}"
            prob += (u[par[0], h] - u[par[1], h] == 0), f"r3u_{h}"
    for h in iw:  # regla 5: personas enteras N >= carga, N <= disponibles
        for j, r in enumerate(RECURSOS):
            prob += (pulp.lpSum(d.req[d.ipos[c], j] * a[c, h] for c in todas if d.req[d.ipos[c], j] > 0)
                     <= Nv[j, h]), f"r5_{r}_{h}"
    # cortes válidos: si una célula con carga fraccionaria está activa, hace falta al menos ceil(carga) personas
    for h in iw:
        for j, r in enumerate(RECURSOS):
            for c in todas:
                q = d.req[d.ipos[c], j]
                if q > 1e-9 and math.ceil(q - 1e-9) > q + 1e-9:
                    prob += Nv[j, h] >= math.ceil(q - 1e-9) * a[c, h], f"rN_{r}_{h}_{c}"
    for si, (slots, dmin, tn, ft) in enumerate(d.turnos):  # P >= N en el turno
        for j, r in enumerate(RECURSOS):
            for h in slots:
                prob += Pv[j, si] >= Nv[j, h], f"rP_{r}_{si}_{h}"
    for ic, c in enumerate(prods):
        for h in range(H):
            prev = d.i0[ic] if h == 0 else I[c, h - 1]
            prob += I[c, h] == prev + d.cap[ic] * u[c, h] - d.env[h, ic], f"r6_{c}_{h}"  # regla 6
            prob += I[c, h] + sn[c, h] >= 0, f"r6b_{c}_{h}"  # stock >= 0 (pedido servido)
            prob += I[c, h] >= d.ss[ic] - sh[c, h], f"r7_{c}_{h}"  # SS: consumible con penalización (F20)
            if d.k > 0:
                prob += sho[c, h] >= (1 + d.k) * d.ss[ic] - I[c, h], f"r9_{c}_{h}"  # regla 9
            prev_a = 0 if h == 0 else a[c, h - 1]
            prob += st[c, h] >= a[c, h] - prev_a, f"r10_{c}_{h}"  # regla 10
    # FLAG F18: condición terminal blanda: stock final >= SS + envíos de las horas siguientes
    for ic, c in enumerate(prods):
        prob += I[c, H - 1] >= d.ss[ic] + d.env_final[ic] - sf[c], f"term_{c}"
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
    ir = [RECURSOS.index(r) for r in RECURSOS_R]
    im, ic_ = RECURSOS.index("mto"), RECURSOS.index("calidad")
    # R = 0,7 media(P/disp turno) + 0,3 media(N/disp hora)
    tp = [(j, si) for si, (slots, dmin, _, _) in enumerate(d.turnos) for j in ir if dmin[j] > 1e-9]
    tn_ = [(j, h) for h in iw for j in ir if d.disp[h, j] > 1e-9]
    expr_R = 0
    if tp:
        expr_R += PESO_PLANTILLA / len(tp) * pulp.lpSum(Pv[j, si] * (1.0 / d.turnos[si][1][j]) for j, si in tp)
    if tn_:
        expr_R += (1 - PESO_PLANTILLA) / len(tn_) * pulp.lpSum(Nv[j, h] * (1.0 / d.disp[h, j]) for j, h in tn_)
    terminos_q = []
    for h in iw:
        ks = [k for k in (im, ic_) if d.disp[h, k] > 1e-9]
        for k in ks:
            terminos_q.append(Nv[k, h] * (1.0 / d.disp[h, k] / len(ks)))
    expr_Q = pulp.lpSum(terminos_q) * (1.0 / len(iw)) if len(iw) else 0
    # término auxiliar: horas libres dentro de la plantilla, Σ (P - N) / disp (nivela la carga)
    idle_terms = [(j, si, h) for si, (slots, dmin, _, _) in enumerate(d.turnos) for j in range(len(RECURSOS))
                  if dmin[j] > 1e-9 for h in slots]
    expr_idle = (pulp.lpSum((Pv[j, si] - Nv[j, h]) * (1.0 / d.turnos[si][1][j]) for j, si, h in idle_terms)
                 * (PESO_IDLE / len(idle_terms)) if idle_terms else 0)
    expr_S = pulp.lpSum((1.0 / d.dens[ic]) / d.A / H * I[c, h]
                        for ic, c in enumerate(prods) for h in range(H) if d.dens[ic] > 0)
    if d.k > 0 and prods:
        expr_B = pulp.lpSum(sho[c, h] / (d.k * d.ss[ic]) / (len(prods) * H)
                            for ic, c in enumerate(prods) for h in range(H))
    else:
        expr_B = 0
    den_e = d.W.sum() * d.kw.sum() * d.fmax
    expr_E = (pulp.lpSum(d.f[h] * d.kw[d.ipos[c]] / den_e * u[c, h] for c in prods for h in range(H)
                         if d.kw[d.ipos[c]] > 0) if den_e > 0 else 0)
    pen_hard = pulp.lpSum(sn[c, h] / d.ss[ic] for ic, c in enumerate(prods) for h in range(H)) + \
        pulp.lpSum(sa[h] / d.A for h in range(H))
    pen_ss = pulp.lpSum(sh[c, h] / d.ss[ic] for ic, c in enumerate(prods) for h in range(H))
    w = d.pesos
    prob += ESCALA_OBJ * (w["R"] * expr_R + w["S"] * expr_S + w["Q"] * expr_Q + w["B"] * expr_B + w["E"] * expr_E +
                          expr_idle + PENALIZACION * pen_hard + d.pen_ss * pen_ss +
                          PENALIZACION_FINAL * pulp.lpSum(sf[c] / d.ss[ic] for ic, c in enumerate(prods)) +
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
    if hol_n > TOL_HOLGURA or hol_a > TOL_HOLGURA:  # sólo stock < 0 o almacén > 800 m² descartan el plan
        estado = "INVIABLE"
    plan = evaluar(esc, hz, pd.DataFrame(av, index=hz.slots.index, columns=todas),
                   pd.DataFrame(uv, index=hz.slots.index, columns=todas), estado=estado, gap=gap,
                   holguras={"stock": hol_n, "ss": hol_s, "espacio": hol_a}, previo=previo)
    plan.objetivo = float(info.objective_function_value) / ESCALA_OBJ
    plan.tiempo_s = time.perf_counter() - t_ini
    return plan
