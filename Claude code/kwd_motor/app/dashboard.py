"""Dashboard Streamlit del motor de decisión KWD (español)."""
from __future__ import annotations

import copy
import io
import sys
import tempfile
import traceback
from datetime import date, datetime, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for p in (str(RAIZ / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import graficos as G  # noqa: E402
from kwd import config, datos, informes, motor, rolling  # noqa: E402

st.set_page_config(layout="wide", page_title="KWD · Motor de decisión", page_icon="🏭")

NAVY = G.NAVY
EJEMPLO = RAIZ / "data" / "entrada_ejemplo.xlsx"
CONTINGENCIA = RAIZ / "data" / "escenario_contingencia.xlsx"
NOMBRE_TURNO = {"M": "Mañana (06–14)", "T": "Tarde (14–22)", "N": "Noche (22–06)"}

st.markdown(f"""
<style>
.block-container {{padding-top: 1.2rem;}}
.kwd-header {{background:{NAVY};color:white;padding:14px 22px;border-radius:10px;margin-bottom:12px;}}
.kwd-header h1 {{color:white;font-size:1.5rem;margin:0;}}
.kwd-header span {{opacity:.8;font-size:.9rem;}}
div[data-testid="stMetric"] {{background:#F4F6FC;border:1px solid #DDE2F3;border-left:5px solid {NAVY};
   padding:8px 12px;border-radius:8px;}}
/* Evitar textos cortados con "…" en etiquetas y valores de las tarjetas */
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{
   white-space:normal !important;overflow:visible !important;text-overflow:clip !important;
   overflow-wrap:anywhere;}}
[data-testid="stMetricValue"] {{font-size:clamp(1.15rem, 2.1vw, 1.9rem) !important;line-height:1.2;}}
.estado {{display:inline-block;padding:4px 14px;border-radius:16px;color:white;font-weight:700;}}
.caja {{border:1px solid #DDE2F3;border-radius:10px;padding:12px 16px;background:white;height:100%;}}
.caja h4 {{color:{NAVY};margin-top:0;}}
.contingencia {{background:#FDECEA;border:2px solid {G.ROJO};color:{G.ROJO};padding:12px 16px;border-radius:10px;
   font-weight:600;}}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ utilidades
def _norm(s: str) -> str:
    return s.replace("_", "").lower()


def hoja_attr(esc, nombre: str) -> str:
    """Resuelve el nombre del atributo del Escenario correspondiente a una hoja."""
    nombres = list(getattr(esc, "__dataclass_fields__", {}).keys()) or list(vars(esc).keys())
    for n in nombres:
        if _norm(n) == _norm(nombre):
            return n
    for n in nombres:
        if _norm(nombre) in _norm(n) or _norm(n) in _norm(nombre):
            return n
    raise KeyError(nombre)


def fnum(x, d=1, suf=""):
    try:
        return f"{float(x):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") + suf
    except Exception:
        return str(x)


def kget(plan, *subs, default=None):
    """Busca un KPI por subcadenas en el nombre de la clave (tolerante a variaciones)."""
    k = plan.kpis or {}
    for key, v in k.items():
        kn = _norm(str(key))
        if all(_norm(s) in kn for s in subs):
            return v
    return default


def plan_principal(rec):
    if rec.top:
        return rec.top[0], False
    return rec.contingencia, True


def turno_de(rec):
    return str(rec.horizonte.slots["turno"].iloc[0])


_pc_n = [0]


def pc(fig, **kw):
    _pc_n[0] += 1
    st.plotly_chart(fig, width="stretch", key=f"pc{_pc_n[0]}")


def init_state():
    for k, v in dict(esc=None, rec=None, historial=[], semana=None, pdf=None, error=None, origen="").items():
        st.session_state.setdefault(k, v)


init_state()


def cargar_ejemplo():
    if not EJEMPLO.exists():
        EJEMPLO.parent.mkdir(parents=True, exist_ok=True)
        datos.crear_plantilla_ejemplo(str(EJEMPLO))
    return datos.cargar_entrada(str(EJEMPLO))


def calcular(esc, inicio):
    with st.spinner("Resolviendo el modelo de optimización…"):
        rec = motor.recomendar(esc, pd.Timestamp(inicio))
    st.session_state.rec = rec
    st.session_state.esc = esc
    st.session_state.historial.append({"etiqueta": f"{pd.Timestamp(inicio):%d/%m %H:%M} · cálculo", "rec": rec})
    st.session_state.pdf = None
    return rec


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.markdown(f"<h3 style='color:{NAVY}'>KWD · Motor de decisión</h3>", unsafe_allow_html=True)
    st.caption("Planificación de células de soldadura: qué activar, por qué y con qué impacto.")
    fuente = st.radio("Datos de entrada",
                      ["Escenario de ejemplo", "Escenario de contingencia (célula 14 de baja)", "Cargar Excel"],
                      index=0)
    subido = None
    if fuente == "Cargar Excel":
        subido = st.file_uploader("Plantilla Excel (.xlsx)", type=["xlsx"])
    f_ini = st.date_input("Fecha de inicio", value=date(2026, 10, 2))
    h_ini = st.time_input("Hora de inicio", value=time(6, 0), step=3600)
    calcular_btn = st.button("Calcular plan", type="primary", width="stretch")
    if st.button("Reiniciar sesión", width="stretch"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

inicio = datetime.combine(f_ini, time(h_ini.hour, 0))

if calcular_btn:
    try:
        if fuente == "Cargar Excel":
            if subido is None:
                st.sidebar.error("Sube un archivo Excel primero.")
                st.stop()
            tmp = Path(tempfile.mkdtemp()) / "entrada.xlsx"
            tmp.write_bytes(subido.getvalue())
            esc = datos.cargar_entrada(str(tmp))
            st.session_state.origen = subido.name
        elif fuente.startswith("Escenario de contingencia"):
            esc = datos.cargar_entrada(str(CONTINGENCIA))
            st.session_state.origen = "contingencia"
        else:
            # usar el escenario editado si ya hay uno cargado del ejemplo
            if st.session_state.esc is not None and st.session_state.origen == "ejemplo":
                esc = st.session_state.esc
            else:
                esc = cargar_ejemplo()
                st.session_state.origen = "ejemplo"
        st.session_state.historial = []
        st.session_state.semana = None
        calcular(esc, inicio)
        st.session_state.error = None
    except Exception as e:  # noqa: BLE001
        st.session_state.error = f"{e}\n\n{traceback.format_exc()}"

st.markdown(f"""<div class="kwd-header"><h1>KWD Automotive · Motor de decisión de producción</h1>
<span>Soldadura · horizonte 24 h · VE en verde, combustión en azul</span></div>""", unsafe_allow_html=True)

if st.session_state.error:
    st.error("No se ha podido calcular el plan.")
    with st.expander("Detalle técnico"):
        st.code(st.session_state.error)

rec = st.session_state.rec
esc = st.session_state.esc

tabs = st.tabs(["Recomendación", "KPIs", "Overview 24 h", "Alternativas", "Incidencias / Reconfigurar",
                "Datos de entrada", "Semana", "Informe"])

if rec is None:
    with tabs[0]:
        st.info("Elige el origen de datos y la hora de inicio en la barra lateral y pulsa **Calcular plan**.")
    # Datos de entrada accesibles sin plan
    for i in (1, 2, 3, 4, 6, 7):
        with tabs[i]:
            st.info("Calcula primero un plan para ver esta sección.")
    with tabs[5]:
        if esc is None:
            st.info("Calcula primero un plan, o carga el ejemplo desde la barra lateral.")
    st.stop()

plan, es_contingencia = plan_principal(rec)
ss = datos.ss_por_celula(esc)
k_colchon = float(esc.parametros.get("colchon_ss", 0.10))
estado_txt = "CONTINGENCIA" if es_contingencia else str(plan.estado)
col_estado = G.COLOR_ESTADO.get(str(plan.estado), G.GRIS)
tp = G.tipos(esc)


def badge(estado, color):
    return f'<span class="estado" style="background:{color}">{estado}</span>'


# ------------------------------------------------------------------ 1. Recomendación
with tabs[0]:
    inicio_rec = pd.Timestamp(rec.inicio)
    st.subheader(f"Turno {NOMBRE_TURNO.get(turno_de(rec), turno_de(rec))} · desde {inicio_rec:%d/%m/%Y %H:%M}")
    if es_contingencia:
        st.markdown(f'<div class="contingencia">Plan de contingencia — incumple: '
                    f'{"; ".join(map(str, plan.incumplimientos)) or "ver alertas"}<br>'
                    f'<small>No existe ningún plan viable: este es el menos malo y NO debe tomarse como recomendación '
                    f'viable sin decisión de la dirección.</small></div>', unsafe_allow_html=True)
        st.write("")
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 2])
    c1.markdown("**Estado**<br>" + badge(estado_txt if es_contingencia else str(plan.estado), col_estado),
                unsafe_allow_html=True)
    c2.metric("Puntuación", f"{plan.puntuacion:.1f} / 100")
    c3.metric("Idoneidad (óptimo garantizado ±gap)", "—" if plan.idoneidad is None else f"{plan.idoneidad:.1f} %",
              help=("No aplica: plan de contingencia" if plan.idoneidad is None else
                    f"Gap relativo del solver: {100 * (plan.gap or 0):.2f} %"))
    if plan.idoneidad is None:
        c3.caption("No aplica: plan de contingencia")
    c4.markdown("**Células a activar en este turno**<br>" +
                " ".join(f'<span class="estado" style="background:{G.COLOR_TIPO.get(tp.get(int(c)), G.AZUL)}">C{int(c)}</span>'
                         for c in (plan.config_turno_actual or [])) or "ninguna", unsafe_allow_html=True)
    st.write("")
    exp = rec.explicacion or {}
    cq, cp, ci = st.columns(3)
    with cq:
        st.markdown('<div class="caja"><h4>Qué activar</h4></div>', unsafe_allow_html=True)
        que = exp.get("que")
        if isinstance(que, pd.DataFrame) and len(que):
            st.dataframe(que.rename(columns={"celula": "Célula", "tipo": "Tipo", "horas_activas": "Horas", "franja": "Franja",
                                             "piezas": "Piezas", "operarios": "Operarios", "picking": "Picking",
                                             "carretilleros": "Carretill.", "mto": "Mto", "calidad": "Calidad",
                                             "horas_franja_solar": "H. solar"}).round(1),
                         width="stretch", hide_index=True)
        else:
            st.write("Sin células activas en el turno actual.")
    with cp:
        st.markdown('<div class="caja"><h4>Por qué</h4></div>', unsafe_allow_html=True)
        for linea in exp.get("porque", []) or []:
            st.markdown(f"- {linea}")
    with ci:
        st.markdown('<div class="caja"><h4>Con qué impacto</h4></div>', unsafe_allow_html=True)
        fi = informes.filas_impacto(rec)
        if fi:
            st.dataframe(pd.DataFrame(fi, columns=["Indicador", "Plan", "Referencia manual", "Diferencia"]),
                         width="stretch", hide_index=True)
            st.caption("Referencia manual = heurística sin optimizar. Diferencia negativa en horas / m² / kWh = ahorro.")
            for dd in (exp.get("impacto", {}) or {}).get("delta_vs_alternativas", []) or []:
                st.markdown(f"- Frente a **{dd['nombre']}**: {fnum(dd['puntuacion'], 2)} puntos de diferencia")
        else:
            st.write("Sin datos de comparación.")
    pc(G.contribuciones(plan), width="stretch")
    st.markdown("#### Alertas")
    if rec.alertas:
        for a in rec.alertas:
            (st.error if es_contingencia and "INVIABLE" in str(a).upper() else st.warning)(str(a))
    else:
        st.success("Sin alertas.")
    if plan.incumplimientos and not es_contingencia:
        st.warning("Incumplimientos: " + "; ".join(map(str, plan.incumplimientos)))

# ------------------------------------------------------------------ 2. KPIs
with tabs[1]:
    st.subheader("Indicadores clave del plan recomendado")
    lista = informes.kpi_lista(plan)
    for grupo in dict.fromkeys(g for _, _, g in lista):
        st.markdown(f"**{grupo}**")
        items = [(a, b) for a, b, g in lista if g == grupo]
        for n0 in range(0, len(items), 4):
            cols = st.columns(4)
            for col, (n, v) in zip(cols, items[n0:n0 + 4]):
                col.metric(n, v)
    pc(G.recursos(rec, plan), width="stretch")
    ca, cb = st.columns(2)
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.energia(rec, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### KPIs por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")

# ------------------------------------------------------------------ 3. Overview 24 h
with tabs[2]:
    st.subheader("Overview de 24 horas")
    pc(G.gantt(rec, esc, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### Resumen por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")
    pc(G.stock(rec, esc, plan, ss, k_colchon), width="stretch")
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.recursos(rec, plan), width="stretch")

# ------------------------------------------------------------------ 4. Alternativas
with tabs[3]:
    st.subheader("Top 1/2/3 y plan de referencia manual")
    opciones = [(f"Top {i + 1}", p) for i, p in enumerate(rec.top)]
    if rec.contingencia is not None and not rec.top:
        opciones.append(("Contingencia", rec.contingencia))
    if rec.baseline is not None:
        opciones.append(("Referencia manual", rec.baseline))
    filas = []
    for n, p in opciones:
        filas.append({"Plan": n, "Estado": str(p.estado), "Puntuación": round(p.puntuacion, 2),
                      "Idoneidad %": "—" if p.idoneidad is None else round(p.idoneidad, 1),
                      "Células turno actual": ", ".join(str(int(c)) for c in (p.config_turno_actual or [])) or "—",
                      "m² medios": round(float(np.mean(p.espacio.values)), 1),
                      "kWh": round(float(np.sum(p.energia_kwh.values)), 1)})
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
    cols = st.columns(2)
    colores = [G.VERDE if n.startswith("Top") else (G.ROJO if n == "Contingencia" else G.GRIS) for n, _ in opciones]
    pc(G.comparar_barras([n for n, _ in opciones], [p.puntuacion for _, p in opciones],
                                           "Puntuación", colores), width="stretch")
    pc(G.comparar_barras([n for n, _ in opciones], [float(np.sum(p.energia_kwh.values)) for _, p in opciones],
                                           "kWh totales", colores), width="stretch")
    # KPIs lado a lado y diferencias respecto a baseline
    nombres = [n for n, _ in opciones]
    todas = []
    for _, p in opciones:
        for a in (p.kpis or {}):
            if a not in todas and isinstance(p.kpis[a], (int, float, np.floating, np.integer)):
                todas.append(a)
    comp = pd.DataFrame({n: [p.kpis.get(a) for a in todas] for n, p in opciones},
                        index=[str(a).replace("_", " ").capitalize() for a in todas])
    if rec.baseline is not None and "Referencia manual" in comp.columns:
        for n in nombres:
            if n != "Referencia manual":
                comp[f"Δ {n} − referencia"] = comp[n] - comp["Referencia manual"]
    st.markdown("#### KPIs lado a lado")
    st.dataframe(comp.round(2), width="stretch")
    sel = st.selectbox("Ver Gantt de", nombres)
    pc(G.gantt(rec, esc, dict(opciones)[sel]), width="stretch")

# ------------------------------------------------------------------ 5. Incidencias
with tabs[4]:
    st.subheader("Incidencias y reconfiguración")
    st.caption("Registra un evento: el motor recalcula desde la hora indicada, con el stock del plan vigente, y muestra el antes / después.")
    ahora_def = pd.Timestamp(rec.inicio)
    ahora = st.datetime_input("Instante de reconfiguración (hora en punto)", value=ahora_def.to_pydatetime()) \
        if hasattr(st, "datetime_input") else datetime.combine(
            st.date_input("Fecha de reconfiguración", value=ahora_def.date(), key="rc_f"),
            time(st.time_input("Hora de reconfiguración", value=time(ahora_def.hour, 0), key="rc_h", step=3600).hour, 0))
    ahora = pd.Timestamp(ahora).floor("h")
    celulas_ids = [int(c) for c in esc.celulas["celula"]]
    tipo = st.selectbox("Tipo de evento", [
        "baja_celula", "alta_celula", "recursos_reales", "correccion_demanda", "expedicion_real",
        "mantenimiento", "stock_real"],
        format_func=lambda t: {"baja_celula": "Baja de célula (avería)", "alta_celula": "Alta de célula (recuperada)",
                               "recursos_reales": "Recursos reales del turno", "correccion_demanda": "Corrección de demanda diaria",
                               "expedicion_real": "Expedición real (carga de camión)", "mantenimiento": "Mantenimiento",
                               "stock_real": "Stock real (inventario)"}[t])
    d: dict = {}
    with st.form("form_evento"):
        if tipo == "baja_celula":
            d["celula"] = st.selectbox("Célula", celulas_ids)
            c1, c2 = st.columns(2)
            d["desde"] = pd.Timestamp(datetime.combine(c1.date_input("Desde (fecha)", value=ahora.date()),
                                                       c1.time_input("Desde (hora)", value=time(ahora.hour, 0), step=3600)))
            d["hasta"] = pd.Timestamp(datetime.combine(c2.date_input("Hasta (fecha)", value=(ahora + pd.Timedelta(hours=8)).date()),
                                                       c2.time_input("Hasta (hora)", value=time((ahora + pd.Timedelta(hours=8)).hour, 0), step=3600)))
        elif tipo == "alta_celula":
            d["celula"] = st.selectbox("Célula", celulas_ids)
        elif tipo == "recursos_reales":
            c1, c2 = st.columns(2)
            d["fecha"] = pd.Timestamp(c1.date_input("Fecha del turno", value=ahora.date()))
            d["turno"] = c2.selectbox("Turno", ["M", "T", "N"], format_func=lambda t: NOMBRE_TURNO[t])
            cols = st.columns(5)
            vals = {}
            for col, r in zip(cols, config.RECURSOS):
                vals[r] = col.number_input(G.NOMBRE_REC.get(r, r), min_value=0, value=0, step=1,
                                           help="0 = usar el estándar con absentismo")
            d["valores"] = {r: v for r, v in vals.items() if v > 0}
        elif tipo == "correccion_demanda":
            c1, c2, c3 = st.columns(3)
            d["fecha"] = pd.Timestamp(c1.date_input("Día", value=ahora.date()))
            d["ve"] = c2.number_input("Chasis VE del día", min_value=0, value=520, step=10)
            d["comb"] = c3.number_input("Chasis COMB del día", min_value=0, value=300, step=10)
        elif tipo == "expedicion_real":
            c1, c2, c3, c4 = st.columns(4)
            f_ = c1.date_input("Fecha camión", value=ahora.date())
            h_ = c2.time_input("Hora camión", value=time(6, 0), step=1800)
            d["fecha_hora"] = pd.Timestamp(datetime.combine(f_, h_))
            d["ve"] = c3.number_input("Chasis VE", min_value=0.0, value=30.0)
            d["comb"] = c4.number_input("Chasis COMB", min_value=0.0, value=20.0)
        elif tipo == "mantenimiento":
            c1, c2, c3, c4 = st.columns(4)
            d["fecha"] = pd.Timestamp(c1.date_input("Fecha", value=ahora.date()))
            d["turno"] = c2.selectbox("Turno", ["M", "T", "N", "DIA"])
            d["celula"] = c3.selectbox("Célula", celulas_ids)
            d["tecnicos"] = c4.number_input("Técnicos", min_value=0, value=2, step=1)
        elif tipo == "stock_real":
            st.caption("Indica el stock real (piezas) de las células que quieras corregir; las demás se mantienen.")
            prod = [c for c in celulas_ids if c != config.CELULA_LOGISTICA]
            actual = {}
            try:
                idx = list(plan.stock.index)
                actual = {int(c): float(plan.stock[c].iloc[0]) for c in plan.stock.columns}
            except Exception:
                pass
            cols = st.columns(4)
            sv = {}
            for i, c in enumerate(prod):
                sv[c] = cols[i % 4].number_input(f"Célula {c}", min_value=0.0, value=float(round(actual.get(c, 0.0))), step=10.0,
                                                 key=f"sr_{c}")
            d = {c: v for c, v in sv.items() if abs(v - round(actual.get(c, 0.0))) > 0.5}
        enviado = st.form_submit_button("Aplicar evento y recalcular", type="primary")
    if enviado:
        try:
            if tipo == "stock_real" and not d:
                st.warning("No has modificado ningún stock.")
            else:
                antes = rec
                with st.spinner("Reconfigurando…"):
                    nuevo = rolling.reconfigurar(esc, rec, rolling.Evento(tipo, d), ahora)
                st.session_state.antes_despues = (antes, nuevo, tipo)
                st.session_state.historial.append({"etiqueta": f"{ahora:%d/%m %H:%M} · {tipo}", "rec": nuevo})
                st.session_state.rec = nuevo
                st.session_state.esc = rolling.aplicar_evento(esc, rolling.Evento(tipo, d))
                st.session_state.pdf = None
                st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo aplicar el evento: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())

    ad = st.session_state.get("antes_despues")
    if ad:
        a, n, t = ad
        pa, ca_ = plan_principal(a)
        pn, cn_ = plan_principal(n)
        st.markdown("### Antes / después de la última incidencia")
        m1, m2, m3 = st.columns(3)
        m1.metric("Puntuación", f"{pn.puntuacion:.1f}", f"{pn.puntuacion - pa.puntuacion:+.1f}")
        if pn.idoneidad is None or pa.idoneidad is None:
            m2.metric("Idoneidad", "—" if pn.idoneidad is None else f"{pn.idoneidad:.1f} %",
                      "No aplica: plan de contingencia", delta_color="off")
        else:
            m2.metric("Idoneidad", f"{pn.idoneidad:.1f} %", f"{pn.idoneidad - pa.idoneidad:+.1f}")
        m3.metric("Estado", str(pn.estado), f"antes: {pa.estado}", delta_color="off")
        ka = pa.kpis or {}
        kn = pn.kpis or {}
        filas = [{"KPI": str(x).replace("_", " ").capitalize(), "Antes": ka[x], "Después": kn[x],
                  "Δ": kn[x] - ka[x]} for x in kn
                 if x in ka and isinstance(kn[x], (int, float, np.floating, np.integer)) and isinstance(ka[x], (int, float, np.floating, np.integer))]
        if filas:
            st.dataframe(pd.DataFrame(filas).round(2), width="stretch", hide_index=True)
        st.write("**Células Top 1 antes:** " + (", ".join(map(str, pa.config_turno_actual)) or "—") +
                 "  →  **después:** " + (", ".join(map(str, pn.config_turno_actual)) or "—"))
        if cn_:
            st.error("El nuevo resultado es un plan de contingencia — incumple: " + "; ".join(map(str, pn.incumplimientos)))
    if st.session_state.historial:
        with st.expander("Historial de recomendaciones"):
            st.write([h["etiqueta"] for h in st.session_state.historial])

# ------------------------------------------------------------------ 6. Datos de entrada
HOJAS_EDIT = [("Demanda semanal", "DemandaSemanal"), ("Corrección diaria", "CorreccionDiaria"),
              ("Stock actual", "StockActual"), ("Disponibilidad (bajas)", "Disponibilidad"),
              ("Mantenimientos", "Mantenimientos"), ("Recursos reales", "RecursosReales"),
              ("Expediciones", "Expediciones"), ("Células", "Celulas"), ("Turnos", "Turnos"),
              ("Almacén", "Almacen")]
with tabs[5]:
    st.subheader("Datos de entrada")
    st.caption("Edita las tablas y pulsa **Aplicar cambios y recalcular**. Descarga el escenario resultante como Excel.")
    subt = st.tabs([n for n, _ in HOJAS_EDIT] + ["Parámetros"])
    editados = {}
    for tab_, (nombre, hoja) in zip(subt, HOJAS_EDIT):
        with tab_:
            try:
                attr = hoja_attr(esc, hoja)
                df = getattr(esc, attr)
                editados[attr] = st.data_editor(df, num_rows="dynamic", width="stretch", key=f"ed_{attr}")
            except Exception as e:  # noqa: BLE001
                st.warning(f"Hoja no disponible: {e}")
    with subt[-1]:
        pdf_ = pd.DataFrame({"parametro": list(esc.parametros.keys()), "valor": list(esc.parametros.values())})
        ped = st.data_editor(pdf_, width="stretch", key="ed_param", disabled=["parametro"])
    cb1, cb2 = st.columns(2)
    if cb1.button("Aplicar cambios y recalcular", type="primary"):
        try:
            nuevo = copy.deepcopy(esc)
            for attr, df in editados.items():
                setattr(nuevo, attr, df.reset_index(drop=True))
            nuevo.parametros = dict(zip(ped["parametro"], ped["valor"]))
            st.session_state.origen = "ejemplo" if st.session_state.origen == "ejemplo" else st.session_state.origen
            calcular(nuevo, pd.Timestamp(rec.inicio))
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Error al recalcular: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())
    try:
        tmpx = Path(tempfile.mkdtemp()) / "escenario.xlsx"
        nuevo = copy.deepcopy(esc)
        for attr, df in editados.items():
            setattr(nuevo, attr, df.reset_index(drop=True))
        datos.guardar_entrada(nuevo, str(tmpx))
        cb2.download_button("Descargar escenario (Excel)", tmpx.read_bytes(), file_name="escenario_kwd.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:  # noqa: BLE001
        cb2.warning(f"No se pudo preparar la descarga: {e}")

# ------------------------------------------------------------------ 7. Semana
with tabs[6]:
    st.subheader("Simulación semanal (rolling horizon, 15 turnos)")
    lunes_def = pd.Timestamp(rec.inicio).normalize()
    lunes_def = lunes_def - pd.Timedelta(days=lunes_def.weekday())
    lunes = st.date_input("Lunes de la semana", value=lunes_def.date())
    st.caption("Resuelve 24 h por turno y consolida las 8 h del turno actual. Puede tardar varios minutos.")
    if st.button("Simular semana", type="primary"):
        try:
            with st.spinner("Simulando la semana turno a turno…"):
                st.session_state.semana = rolling.simular_semana(esc, pd.Timestamp(lunes))
        except Exception as e:  # noqa: BLE001
            st.error(f"Error en la simulación: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())
    sem = st.session_state.semana
    if isinstance(sem, pd.DataFrame) and len(sem):
        st.dataframe(sem, width="stretch")
        num = sem.select_dtypes("number")
        import plotly.express as px
        for c in [c for c in num.columns if any(s in c.lower() for s in ("puntuacion", "puntuación", "m2", "m²", "kwh"))][:4]:
            x = sem["turno"].astype(str) if "turno" in sem else sem.index.astype(str)
            fig = px.bar(sem, x=x, y=c, title=c.replace("_", " ").capitalize(), color_discrete_sequence=[NAVY])
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10))
            pc(fig, width="stretch")
        st.download_button("Descargar tabla (CSV)", sem.to_csv(index=False).encode("utf-8-sig"),
                           file_name="simulacion_semana.csv", mime="text/csv")

# ------------------------------------------------------------------ 8. Informe
with tabs[7]:
    st.subheader("Informe PDF para dirección")
    st.write("Incluye resumen ejecutivo, células del turno, KPIs, alternativas, gráficos, alertas y supuestos.")
    if st.button("Generar informe PDF", type="primary"):
        try:
            with st.spinner("Generando informe…"):
                ruta = Path(tempfile.mkdtemp()) / "informe_kwd.pdf"
                informes.generar_informe_pdf(rec, esc, str(ruta))
                st.session_state.pdf = ruta.read_bytes()
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo generar el informe: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())
    if st.session_state.pdf:
        st.success("Informe generado.")
        st.download_button("Descargar informe PDF", st.session_state.pdf,
                           file_name=f"informe_kwd_{pd.Timestamp(rec.inicio):%Y%m%d_%H%M}.pdf", mime="application/pdf")

# ------------------------------------------------------------------ pie
with st.expander("Supuestos (FLAGS) a revisar"):
    for k_, v_ in (rec.flags or config.FLAGS).items():
        st.markdown(f"**{k_}** — {v_}")





