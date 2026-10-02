"""Horizonte rodante: eventos, reconfiguración, estado en tiempo real, contingencia y simulación semanal (v3)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import (CELULA_LOGISTICA, HOJAS_COLUMNAS, RECURSOS, ROL_DE_CODIGO, TURNOS)
from .datos import (Escenario, _limites_turno, bajas_efectivas, ss_por_celula, stock_inicial, tabla_celulas)
from .horizonte import turno_de_hora
from .motor import Recomendacion, recomendar
from .personal import celulas_de, previo_de_plan
from .validador import agotamiento as _agotamiento

TIPOS_EVENTO = ("parada_celula", "fin_parada", "baja_personal", "alta_personal", "correccion_demanda",
                "expedicion_real", "stock_real",
                # compatibilidad v1
                "baja_celula", "alta_celula", "recursos_reales", "mantenimiento")


@dataclass
class Evento:
    """Incidencia que modifica el escenario (ver `aplicar_evento` para las claves de `datos`)."""
    tipo: str
    datos: dict = field(default_factory=dict)


def _anadir(df: pd.DataFrame, fila: dict) -> pd.DataFrame:
    nueva = pd.DataFrame([fila]).reindex(columns=df.columns)
    if df.empty:
        return nueva
    return pd.concat([df, nueva], ignore_index=True)


def _fijar_stock(esc: Escenario, valores: dict) -> None:
    """Sustituye el stock de las células indicadas en la hoja StockActual (modifica `esc`)."""
    st = stock_inicial(esc)
    st.update({int(k): float(v) for k, v in valores.items() if int(k) in st})
    esc.stock_actual = pd.DataFrame({"celula": list(st.keys()), "piezas": list(st.values())})


def _fecha_turno_de(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return (ts - pd.Timedelta(days=1)).normalize() if ts.hour < 6 else ts.normalize()


def _sumar_bajas(e: Escenario, fecha, turno: str, rol: str, delta: float) -> None:
    fecha = pd.Timestamp(fecha).normalize()
    base = bajas_efectivas(e, fecha, turno, rol)
    nuevo = max(0.0, base + delta)
    b = e.bajas
    m = (b["fecha"] == fecha) & (b["turno"] == turno) if len(b) else None
    if m is not None and m.any():
        e.bajas.loc[m, rol] = nuevo
    else:
        fila = {"fecha": fecha, "turno": turno, **{r: np.nan for r in RECURSOS}}
        fila[rol] = nuevo
        e.bajas = _anadir(b, fila)


def aplicar_evento(esc: Escenario, evento: Evento, ahora=None) -> Escenario:
    """Devuelve una copia del escenario con el evento aplicado.

    parada_celula {celula, desde, hasta, tipo, tecnicos}; fin_parada {celula, ahora};
    baja_personal / alta_personal {fecha, turno, rol, cantidad} o {trabajador, [fecha]};
    correccion_demanda {fecha, piezas_ve, piezas_comb}; expedicion_real {fecha_hora, piezas_ve, piezas_comb};
    stock_real {celula: piezas}. Se ignoran (con aviso) las paradas de la célula 10.
    """
    e = esc.copiar()
    d = evento.datos
    tipo = evento.tipo
    if tipo in ("parada_celula", "baja_celula", "mantenimiento"):
        if tipo == "mantenimiento":
            desde, hasta = _limites_turno(d["fecha"], str(d["turno"]).upper())
            tp, tec = "PROGRAMADA", float(d.get("tecnicos", 0))
        else:
            desde = pd.Timestamp(d["desde"]) if d.get("desde") is not None else pd.Timestamp(ahora)
            hasta = pd.Timestamp(d["hasta"]) if d.get("hasta") is not None else pd.NaT
            tp = str(d.get("tipo", "AVERIA")).upper().replace("Í", "I")
            tec = float(d.get("tecnicos", 0) or 0)
        if int(d["celula"]) == CELULA_LOGISTICA:
            e.avisos.append("Se ignora la parada/baja de la célula 10: el servicio logístico no puede pararse.")
            return e
        e.paradas = _anadir(e.paradas, {"celula": int(d["celula"]), "desde": desde, "hasta": hasta,
                                        "tipo": tp, "tecnicos": tec})
    elif tipo in ("fin_parada", "alta_celula"):
        t = pd.Timestamp(d.get("ahora", ahora)) if (d.get("ahora", ahora) is not None) else None
        p = e.paradas
        c = int(d["celula"])
        if len(p):
            keep = []
            for _, f in p.iterrows():
                if int(f["celula"]) != c:
                    keep.append(f)
                    continue
                if t is None:
                    continue
                hasta = f["hasta"] if pd.notna(f["hasta"]) else pd.Timestamp.max
                if f["desde"] <= t < hasta:
                    if f["desde"] < t:
                        g = f.copy()
                        g["hasta"] = t
                        keep.append(g)
                else:
                    keep.append(f)
            e.paradas = pd.DataFrame(keep, columns=p.columns).reset_index(drop=True)
    elif tipo in ("baja_personal", "alta_personal"):
        signo = 1.0 if tipo == "baja_personal" else -1.0
        if d.get("trabajador"):
            w = str(d["trabajador"])
            turno, resto = w.split("-")[0], w.split("-")[1]
            rol = ROL_DE_CODIGO[resto[:2]]
            fecha = d.get("fecha")
            if fecha is None:
                fecha = _fecha_turno_de(ahora) if ahora is not None else pd.Timestamp.now().normalize()
                ft_ahora = turno_de_hora(pd.Timestamp(ahora).hour) if ahora is not None else turno
                if ft_ahora != turno:  # próximo turno de ese código
                    fecha = _proxima_fecha_turno(ahora, turno)
            fecha = pd.Timestamp(fecha).normalize()
            bt = e.bajas_trabajadores
            if signo > 0:
                if not ((bt["fecha"] == fecha) & (bt["trabajador"] == w)).any() if len(bt) else True:
                    e.bajas_trabajadores = _anadir(bt, {"fecha": fecha, "trabajador": w})
                    _sumar_bajas(e, fecha, turno, rol, 1.0)
            else:
                if len(bt) and ((bt["fecha"] == fecha) & (bt["trabajador"] == w)).any():
                    e.bajas_trabajadores = bt[~((bt["fecha"] == fecha) & (bt["trabajador"] == w))].reset_index(drop=True)
                    _sumar_bajas(e, fecha, turno, rol, -1.0)
        else:
            _sumar_bajas(e, d["fecha"], str(d["turno"]).upper(), d["rol"], signo * float(d.get("cantidad", 1)))
    elif tipo == "recursos_reales":  # v1: valores absolutos disponibles
        fecha = pd.Timestamp(d["fecha"]).normalize()
        turno = str(d["turno"]).upper()
        for r, v in d.get("valores", {}).items():
            if r in RECURSOS and v is not None:
                std = e.disp_estandar(r, turno)
                _sumar_bajas(e, fecha, turno, r, 0.0)
                m = (e.bajas["fecha"] == fecha) & (e.bajas["turno"] == turno)
                e.bajas.loc[m, r] = max(0.0, std - float(v))
    elif tipo == "correccion_demanda":
        fecha = pd.Timestamp(d["fecha"]).normalize()
        ve = float(d.get("piezas_ve", d.get("ve", 0)))
        cb = float(d.get("piezas_comb", d.get("comb", 0)))
        cd = e.correccion_diaria
        cd = cd[cd["fecha"] != fecha]
        e.correccion_diaria = _anadir(cd, {"fecha": fecha, "piezas_ve": ve, "piezas_comb": cb})
    elif tipo == "expedicion_real":
        e.expediciones = _anadir(e.expediciones, {
            "fecha_hora": pd.Timestamp(d["fecha_hora"]), "piezas_ve": float(d.get("piezas_ve", d.get("ve", 0))),
            "piezas_comb": float(d.get("piezas_comb", d.get("comb", 0)))})
    elif tipo == "stock_real":
        _fijar_stock(e, d)
    else:
        raise ValueError(f"Tipo de evento desconocido: {tipo}")
    return e


def _proxima_fecha_turno(ahora, turno: str) -> pd.Timestamp:
    t = pd.Timestamp(ahora).floor("h")
    for _ in range(72):
        if turno_de_hora(t.hour) == turno:
            return _fecha_turno_de(t)
        t += pd.Timedelta(hours=1)
    return _fecha_turno_de(ahora)


def _slot_de(rec: Recomendacion, ahora) -> int:
    n = int((pd.Timestamp(ahora).floor("h") - rec.horizonte.inicio) / pd.Timedelta(hours=1))
    return max(0, min(n, rec.horizonte.horas - 1))


def stock_en(rec: Recomendacion, ahora) -> dict:
    """Stock por célula al comienzo de la hora `ahora` según el plan vigente (stock inicial si ahora = inicio)."""
    plan = rec.mejor
    n = int((pd.Timestamp(ahora).floor("h") - rec.horizonte.inicio) / pd.Timedelta(hours=1))
    if plan is None or n <= 0:
        return dict(rec.horizonte.stock_inicial)  # H1: stock del propio horizonte, no del escenario externo
    n = min(n, len(plan.stock))
    return {int(c): float(v) for c, v in plan.stock.iloc[n - 1].items()}


def reconfigurar(esc: Escenario, rec: Recomendacion, evento: Evento, ahora):
    """Aplica un evento y recalcula 24 h desde `ahora`. Devuelve (escenario actualizado, recomendación).

    El stock en `ahora` se toma siempre del plan vigente (también si `ahora` es el inicio del plan) salvo
    `stock_real`, que lo fija explícitamente; el escenario devuelto ya lleva ese stock (eventos encadenados).
    """
    ahora = pd.Timestamp(ahora).floor("h")
    e2 = esc.copiar()
    _fijar_stock(e2, stock_en(rec, ahora))
    e2 = aplicar_evento(e2, evento, ahora)
    n = int((ahora - rec.horizonte.inicio) / pd.Timedelta(hours=1))
    previo = previo_de_plan(rec.horizonte, rec.mejor, max(0, min(n - 1, rec.horizonte.horas - 1)) if n > 0 else 0)
    rec2 = recomendar(e2, ahora, top_k=3, previo=previo)
    return e2, rec2


# --- estado en tiempo real -------------------------------------------------------------------
# Nombre de cada rol en singular y plural para los textos de la contingencia.
_NOMBRE_ROL = {"operarios": ("operario", "operarios"), "picking": ("trabajador de picking", "trabajadores de picking"),
               "carretilleros": ("carretillero", "carretilleros"), "mto": ("técnico de mantenimiento",
                                                                          "técnicos de mantenimiento"),
               "calidad": ("trabajador de calidad", "trabajadores de calidad")}


def _fmt_h(ts) -> str:
    return f"{pd.Timestamp(ts):%H:%M}"


def estado_en(esc: Escenario, rec: Recomendacion, ahora) -> pd.DataFrame:
    """Estado de cada célula en la hora `ahora`: estado, personas, stock, SS, cobertura y próxima activación."""
    hz, plan = rec.horizonte, rec.mejor
    n = _slot_de(rec, ahora)
    t = tabla_celulas(esc)
    ss = ss_por_celula(esc)
    stk = stock_en(rec, hz.slots["inicio"].iloc[n])
    s = hz.slots
    filas = []
    for c in sorted(t.index):
        activa = bool(plan is not None and plan.activacion.at[n, c] > 0.5)
        bloq = n in hz.bloqueos.get(c, set())
        tipo_p = None
        if bloq and len(esc.paradas):
            p = esc.paradas[(esc.paradas["celula"] == c) & (esc.paradas["desde"] < s["fin"].iloc[n]) &
                            (esc.paradas["hasta"].fillna(pd.Timestamp.max) > s["inicio"].iloc[n])]
            tipo_p = "AVERIA" if (p["tipo"] == "AVERIA").any() or not len(p) else "PROGRAMADA"
        if bloq:
            estado = "AVERÍA" if tipo_p == "AVERIA" else "PARADA PROGRAMADA"
        elif activa:
            estado = "PRODUCIENDO"
        else:
            estado = "EN ESPERA"
        pers = ""
        if plan is not None and len(plan.personal):
            g = plan.personal[(plan.personal["slot"] == n) & (plan.personal["estado"] == "ASIGNADO")]
            partes = []
            for _, f in g.iterrows():
                if c in celulas_de(f["celulas"]):
                    partes.append(f["trabajador"] if f["celulas"] == f"C{c}" else f"{f['trabajador']} ({f['detalle']})")
            pers = ", ".join(partes)
        cob = None
        prox = None
        if c != CELULA_LOGISTICA:
            st = stk.get(c, 0.0)
            env = hz.envios[c].to_numpy()
            acum = np.cumsum(env[n:])
            agot = np.where(acum > st + 1e-9)[0]
            if len(agot):
                cob = float(agot[0])
            else:
                tasa = env[env > 0].mean() * (env > 0).sum() / max(1, len(env)) if (env > 0).any() else 0.0
                cob = float(len(acum) + (st - (acum[-1] if len(acum) else 0)) / tasa) if tasa > 0 else None
        if plan is not None:
            a = plan.activacion[c].to_numpy()
            for h in range(n + 1, len(a)):
                if a[h] > 0.5 and a[h - 1] < 0.5:
                    prox = s["inicio"].iloc[h]
                    break
        filas.append({"celula": c, "tipo": t.loc[c, "tipo"], "estado": estado, "personas": pers,
                      "stock": None if c == CELULA_LOGISTICA else float(stk.get(c, 0.0)),
                      "ss": None if c == CELULA_LOGISTICA else float(ss[c]), "cobertura_h": cob,
                      "proxima_activacion": prox})
    return pd.DataFrame(filas)


# --- contingencia ----------------------------------------------------------------------------
def _asignacion_por_slot(plan, hz) -> dict:
    """{(trabajador, hora_absoluta): (rol, celulas_str)} de las filas ASIGNADO/LIBRE."""
    res = {}
    if plan is None or not len(plan.personal):
        return res
    pe = plan.personal
    for w, r, ini, cel, est in zip(pe["trabajador"], pe["rol"], pe["inicio"], pe["celulas"], pe["estado"]):
        res[(w, pd.Timestamp(ini))] = (r, cel if est == "ASIGNADO" else est)
    return res


def _franjas(horas: list) -> str:
    if not horas:
        return ""
    horas = sorted(horas)
    grupos, ini, prev = [], horas[0], horas[0]
    for h in horas[1:]:
        if h - prev != pd.Timedelta(hours=1):
            grupos.append((ini, prev))
            ini = h
        prev = h
    grupos.append((ini, prev))
    return ", ".join(f"{_fmt_h(a)} a {_fmt_h(b + pd.Timedelta(hours=1))}" for a, b in grupos)


def _cambios_asignacion(p1, hz1, p2, hz2, horas, relevantes: set, excluir: set) -> pd.DataFrame:
    """Reubicación: trabajadores cuya célula cambia entre el plan anterior y el nuevo (en las horas comunes)."""
    w1, w2 = _asignacion_por_slot(p1, hz1), _asignacion_por_slot(p2, hz2)
    por_persona: dict = {}
    for (w, h), (r, cel2) in w2.items():
        if h not in horas or (w, h) not in w1 or w in excluir:
            continue
        cel1 = w1[(w, h)][1]
        if cel1 == cel2:
            continue
        cs1, cs2 = set(celulas_de(cel1)), set(celulas_de(cel2))
        if (cs1 & relevantes) or (cs2 & relevantes):
            por_persona.setdefault((w, r), []).append((h, cel1, cel2))
    filas = []
    for (w, r), lst in sorted(por_persona.items()):
        lst.sort()
        ini, c1, c2, prev = lst[0][0], lst[0][1], lst[0][2], lst[0][0]
        for h, x1, x2 in lst[1:] + [(None, None, None)]:
            if h is not None and x1 == c1 and x2 == c2 and h - prev == pd.Timedelta(hours=1):
                prev = h
                continue
            filas.append({"persona": w, "rol": r, "de_celula": c1, "a_celulas": c2,
                          "desde": ini, "hasta": prev + pd.Timedelta(hours=1)})
            if h is not None:
                ini, c1, c2, prev = h, x1, x2, h
    return pd.DataFrame(filas, columns=["persona", "rol", "de_celula", "a_celulas", "desde", "hasta"])


def contingencia(esc: Escenario, rec: Recomendacion, ahora, bajas_celulas: list | None = None,
                 bajas_personas: list | None = None, bajas_rol: dict | None = None) -> dict:
    """Contingencia con células averiadas y/o personas de baja: recalcula desde `ahora` (stock del plan vigente).

    bajas_celulas: [{celula, desde, hasta}] (hasta None = hasta nuevo aviso; la célula 10 no puede averiarse);
    bajas_personas: ids de trabajador ("T-OP04") o dicts {trabajador, fecha}; bajas_rol: {rol: cantidad} que se
    restan a todos los turnos del horizonte. Devuelve rec_antes, rec_despues, reubicacion, maquinas, agotamiento
    (DataFrame por pieza), desabastecimiento (DataFrame pieza, ciclo, piezas_no_servidas), aviso_direccion (str|None)
    y resumen (list[str]); además `escenario` (con las incidencias aplicadas).
    """
    ahora = pd.Timestamp(ahora).floor("h")
    bajas_celulas = list(bajas_celulas or [])
    bajas_personas = list(bajas_personas or [])
    for b in bajas_celulas:
        if int(b["celula"]) == CELULA_LOGISTICA:
            raise ValueError("La célula 10 no puede averiarse ni pararse.")
    e2 = esc.copiar()
    _fijar_stock(e2, stock_en(rec, ahora))
    for b in bajas_celulas:
        d0 = pd.Timestamp(b["desde"]).floor("h") if b.get("desde") is not None else ahora
        e2 = aplicar_evento(e2, Evento("parada_celula", {
            "celula": int(b["celula"]), "desde": d0,
            "hasta": pd.Timestamp(b["hasta"]) if b.get("hasta") is not None else None,
            "tipo": "AVERIA", "tecnicos": 0}), ahora)
    for w in bajas_personas:
        dat = dict(w) if isinstance(w, dict) else {"trabajador": str(w)}
        e2 = aplicar_evento(e2, Evento("baja_personal", dat), ahora)
    if bajas_rol:
        t_ini = ahora
        for i in range(int(esc.parametros["horas_horizonte"])):
            t = t_ini + pd.Timedelta(hours=i)
            if i == 0 or t.hour in (6, 14, 22):
                for rol, cant in bajas_rol.items():
                    if cant:
                        e2 = aplicar_evento(e2, Evento("baja_personal", {
                            "fecha": _fecha_turno_de(t), "turno": turno_de_hora(t.hour), "rol": rol,
                            "cantidad": cant}), ahora)
    n = int((ahora - rec.horizonte.inicio) / pd.Timedelta(hours=1))
    previo = previo_de_plan(rec.horizonte, rec.mejor, max(0, min(n - 1, rec.horizonte.horas - 1)) if n > 0 else 0)
    rec2 = recomendar(e2, ahora, top_k=1, previo=previo)
    hz1, hz2 = rec.horizonte, rec2.horizonte
    p1, p2 = rec.mejor, rec2.mejor
    fin_v = min(hz1.slots["fin"].iloc[-1], hz2.slots["fin"].iloc[-1])
    horas = [h for h in hz2.slots["inicio"] if h < fin_v]
    celulas_rotas = sorted({int(b["celula"]) for b in bajas_celulas})

    def act(plan, hz):
        return {pd.Timestamp(i): plan.activacion.iloc[k] for k, i in enumerate(hz.slots["inicio"])}
    a1, a2 = act(p1, hz1), act(p2, hz2)
    filas = []
    for c in sorted(p2.activacion.columns):
        b = [h for h in horas if h in a1 and a1[h][c] > 0.5]
        d_ = [h for h in horas if h in a2 and a2[h][c] > 0.5]
        nuevas = [h for h in d_ if h not in b]
        if c in celulas_rotas or len(b) != len(d_):
            filas.append({"celula": c, "horas_antes": len(b), "horas_despues": len(d_),
                          "delta": len(d_) - len(b), "franjas_nuevas": _franjas(nuevas)})
    maquinas = pd.DataFrame(filas, columns=["celula", "horas_antes", "horas_despues", "delta", "franjas_nuevas"])
    activadas = {int(f["celula"]) for _, f in maquinas.iterrows() if f["delta"] > 0}
    # células que cubría cada persona de baja (según el plan anterior)
    ausentes = {}
    w1 = _asignacion_por_slot(p1, hz1)
    for w in bajas_personas:
        wid = w["trabajador"] if isinstance(w, dict) else str(w)
        cels = set()
        for (x, h), (r, cel) in w1.items():
            if x == wid and h in horas:
                cels |= set(celulas_de(cel))
        ausentes[wid] = cels
    relevantes = set(celulas_rotas) | activadas | {c for cs in ausentes.values() for c in cs}
    reubic = _cambios_asignacion(p1, hz1, p2, hz2, horas, relevantes, set(ausentes))
    # agotamiento / desabastecimiento (del plan nuevo)
    ag = p2.agotamiento.copy()
    ag = ag.rename(columns={"pieza": "celula"}) if False else ag
    des = p2.desabastecimiento.copy()
    aviso = p2.aviso_direccion
    # resumen en lenguaje de planta
    res = []
    for b in bajas_celulas:
        hasta_txt = f"hasta {pd.Timestamp(b['hasta']):%d/%m %H:%M}" if b.get("hasta") is not None else "hasta nuevo aviso"
        d0 = pd.Timestamp(b["desde"]) if b.get("desde") is not None else ahora
        res.append(f"Avería de la célula {int(b['celula'])} desde {d0:%d/%m %H:%M} {hasta_txt}.")
    for wid in ausentes:
        cs = ", ".join(f"C{c}" for c in sorted(ausentes[wid])) or "sin célula asignada"
        res.append(f"Baja de {wid} (cubría: {cs}).")
    for rol, cant in (bajas_rol or {}).items():
        if cant:
            res.append(f"Baja de {cant:g} {_NOMBRE_ROL.get(rol, (rol, rol))[1]} en los turnos del horizonte.")
    if len(reubic):
        for c in celulas_rotas:
            g_c = reubic[reubic["de_celula"].str.contains(f"C{c}(?!\\d)", regex=True)]
            for r, g in g_c.groupby("rol"):
                pers = g["persona"].unique()
                filas_d = [f"{x['persona']} → {x['a_celulas'] or 'LIBRE'} ({_fmt_h(x['desde'])}-{_fmt_h(x['hasta'])})"
                           for _, x in g.iterrows()]
                destinos = "; ".join(filas_d[:4]) + (f"; y {len(filas_d) - 4} cambios más" if len(filas_d) > 4 else "")
                singular, plural = _NOMBRE_ROL.get(r, (f"trabajador de {r}", f"trabajadores de {r}"))
                res.append(f"El {singular} de la C{c} pasa a: {destinos}." if len(pers) == 1
                           else f"Los {len(pers)} {plural} de la C{c} pasan a: {destinos}.")
        patron = "|".join(f"C{c}(?!\\d)" for c in celulas_rotas)
        otros = reubic[~reubic["de_celula"].str.contains(patron, regex=True)] if patron else reubic
        if len(otros):
            res.append(f"Otros {len(otros)} cambios de puesto de {otros['persona'].nunique()} personas "
                       f"(detalle en la tabla de reubicación).")
    else:
        res.append("No hay personas que reubicar.")
    for _, m in maquinas.iterrows():
        if int(m["celula"]) in celulas_rotas:
            continue
        if m["franjas_nuevas"]:
            verbo = "Activar" if m["horas_antes"] == 0 else "Ampliar"
            res.append(f"{verbo} C{int(m['celula'])} de {m['franjas_nuevas']}.")
    if len(ag):
        for _, x in ag.iterrows():
            t = f"La pieza {int(x['pieza'])} consume stock de seguridad desde las {_fmt_h(x['hora_bajo_ss'])}"
            if pd.notna(x["hora_sin_stock"]):
                t += f" y se queda sin stock a las {_fmt_h(x['hora_sin_stock'])}"
            if pd.notna(x["hora_repone_ss"]):
                t += f"; repone el SS a las {_fmt_h(x['hora_repone_ss'])}"
            else:
                t += "; no repone el SS en el horizonte"
            res.append(t + ".")
    else:
        res.append("El stock de seguridad no se consume: todas las piezas se mantienen por encima del SS.")
    k2 = p2.kpis
    if k2.get("stock_opt_dev_media_pct") is not None:
        res.append(f"Stock vs óptimo al cierre de turno: desviación media {k2['stock_opt_dev_media_pct']:.0f} %, "
                   f"máxima {k2['stock_opt_dev_max_pct']:.0f} %.")
    if aviso:
        res.append(aviso)
    if rec2.contingencia is not None:
        res.append("Aviso: el plan resultante es INVIABLE (almacén o reglas duras).")
    return {"rec_antes": rec, "rec_despues": rec2, "reubicacion": reubic, "maquinas": maquinas,
            "agotamiento": ag, "desabastecimiento": des, "aviso_direccion": aviso, "resumen": res,
            "escenario": e2}


def contingencia_celula(esc: Escenario, rec: Recomendacion, celula: int, desde, hasta=None) -> dict:
    """Atajo: avería de una célula (≠ 10) desde `desde`."""
    return contingencia(esc, rec, desde, bajas_celulas=[{"celula": int(celula), "desde": desde, "hasta": hasta}])


def simular_semana(esc: Escenario, lunes, iteraciones: int = 15) -> pd.DataFrame:
    """Simula una semana con horizonte rodante: 15 turnos desde el lunes 06:00 (`iteraciones` para acotar)."""
    inicio = pd.Timestamp(lunes).normalize() + pd.Timedelta(hours=6)
    e = esc.copiar()
    e.parametros["tiempo_limite_s"] = min(float(e.parametros["tiempo_limite_s"]), 6.0)
    e.parametros["gap_relativo"] = max(float(e.parametros.get("gap_relativo", 0.001)), 0.005)
    filas = []
    for it in range(iteraciones):
        rec = recomendar(e, inicio, top_k=1)
        plan = rec.mejor
        hz = rec.horizonte
        idx = hz.slots_turno_actual()
        fila = plan.resumen_turnos.iloc[0].to_dict()
        fila.update({
            "iteracion": it + 1, "estado": plan.estado, "idoneidad": plan.idoneidad,
            "puntuacion_plan_24h": plan.puntuacion, "configuracion": list(plan.config_turno_actual),
            "tiempo_s": rec.tiempo_total_s,
        })
        filas.append(fila)
        _fijar_stock(e, {int(c): float(v) for c, v in plan.stock.iloc[idx[-1]].items()})
        inicio = inicio + pd.Timedelta(hours=len(idx))
    return pd.DataFrame(filas)
