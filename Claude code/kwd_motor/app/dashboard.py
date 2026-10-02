"""Dashboard Streamlit del motor de decisión KWD v3 (español)."""
from __future__ import annotations

import re
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
ESTADO_JSON = RAIZ / "data" / "estado.json"
NOMBRE_TURNO = {"M": "Mañana (06–14)", "T": "Tarde (14–22)", "N": "Noche (22–06)"}
ROLES = list(config.RECURSOS)
COLOR_EST = {"PRODUCIENDO": "#2E9E5B", "EN ESPERA": "#6B7280", "PARADA PROGRAMADA": "#E0A100", "AVERÍA": "#C0392B"}

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
.celda {{border-radius:10px;padding:8px 10px;background:#FAFBFE;border:1px solid #DDE2F3;border-top:6px solid #999;
   font-size:.78rem;line-height:1.3;min-height:215px;margin-bottom:6px;}}
.celda .tit {{font-weight:700;font-size:.95rem;color:{NAVY};}}
.celda .pill {{display:inline-block;padding:1px 9px;border-radius:12px;color:white;font-weight:700;font-size:.72rem;}}
.resumen {{background:#EEF3FF;border-left:5px solid {NAVY};padding:10px 16px;border-radius:8px;}}
.kpi-principal {{background:#FFF7E0;border:1px solid #F0D58A;border-left:6px solid {G.AMBAR};border-radius:10px;
   padding:6px 14px;margin:6px 0 4px 0;color:#7A5A00;font-weight:700;}}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ utilidades
def _norm(s: str) -> str:
    return s.replace("_", "").lower()


def hoja_attr(esc, nombre: str):
    """Nombre del atributo del Escenario correspondiente a una hoja (None si no existe)."""
    nombres = list(getattr(esc, "__dataclass_fields__", {}).keys()) or list(vars(esc).keys())
    for n in nombres:
        if _norm(n) == _norm(nombre):
            return n
    for n in nombres:
        if _norm(nombre) in _norm(n) or _norm(n) in _norm(nombre):
            return n
    return None


def get_hoja(esc, nombre: str) -> pd.DataFrame:
    a = hoja_attr(esc, nombre)
    return getattr(esc, a) if a else pd.DataFrame()


def set_hoja(esc, nombre: str, df: pd.DataFrame):
    a = hoja_attr(esc, nombre)
    if a:
        setattr(esc, a, df.reset_index(drop=True))


def fnum(x, d=1, suf=""):
    try:
        return f"{float(x):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") + suf
    except Exception:
        return str(x)


def fts(x, fmt="%d/%m %H:%M"):
    try:
        if x is None or pd.isna(x):
            return "—"
        return pd.Timestamp(x).strftime(fmt)
    except Exception:
        return str(x)


def _num(v):
    return isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool)


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


def horas_rec(rec):
    return [pd.Timestamp(x) for x in rec.horizonte.slots.reset_index(drop=True)["inicio"]]


def idx_hora(rec, ts) -> int:
    hs = horas_rec(rec)
    ts = pd.Timestamp(ts).floor("h")
    if ts <= hs[0]:
        return 0
    if ts >= hs[-1]:
        return len(hs) - 1
    return hs.index(ts) if ts in hs else int((ts - hs[0]) / pd.Timedelta(hours=1))


def turno_fecha(rec, ts):
    """(código de turno, fecha de inicio del turno) de la hora `ts` según el horizonte."""
    s = rec.horizonte.slots.reset_index(drop=True)
    t = str(s["turno"].iloc[idx_hora(rec, ts)])
    ts = pd.Timestamp(ts)
    f = ts.normalize()
    if t == "N" and ts.hour < 6:
        f = f - pd.Timedelta(days=1)
    return t, f


# ------------------------------------------------------------------ estado de entrada (sin Excel)
def guardar_entrada_auto(esc) -> None:
    """Guarda el estado de entrada en data/estado.json (silencioso si falla)."""
    try:
        ESTADO_JSON.parent.mkdir(parents=True, exist_ok=True)
        datos.guardar_estado(esc, str(ESTADO_JSON))
    except Exception as e:  # noqa: BLE001
        st.session_state.msg = ("warning", f"No se pudo guardar data/estado.json: {e}", traceback.format_exc())


def cargar_entrada_inicial():
    try:
        if ESTADO_JSON.exists():
            return datos.cargar_estado(str(ESTADO_JSON))
    except Exception:  # noqa: BLE001
        pass
    esc0 = datos.estado_ejemplo()
    try:
        ESTADO_JSON.parent.mkdir(parents=True, exist_ok=True)
        datos.guardar_estado(esc0, str(ESTADO_JSON))
    except Exception:  # noqa: BLE001
        pass
    return esc0


def _tipar(df: pd.DataFrame, fechas=(), numeros=(), textos=()) -> pd.DataFrame:
    df = df.copy()
    for c in fechas:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    for c in numeros:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in textos:
        if c in df:
            df[c] = df[c].astype(object)
    return df.reset_index(drop=True)


def _limpia_bajas(df):
    df = df.dropna(subset=["fecha", "turno"]).copy()
    for r in ROLES:
        df[r] = pd.to_numeric(df[r], errors="coerce").fillna(0).astype(int)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.normalize()
    return df.reset_index(drop=True)


def _limpia_paradas(df):
    df = df.dropna(subset=["celula", "desde", "hasta"]).copy()
    df = df[df["celula"].astype(float) != config.CELULA_LOGISTICA]
    df["celula"] = df["celula"].astype(int)
    df["tipo"] = df["tipo"].fillna("AVERIA")
    df["tecnicos"] = pd.to_numeric(df["tecnicos"], errors="coerce").fillna(0)
    return df.reset_index(drop=True)


def _limpia_dem(df, col):
    df = df.dropna(subset=[col]).copy()
    for c in ("piezas_ve", "piezas_comb"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if col in ("fecha", "semana_inicio"):
        df[col] = pd.to_datetime(df[col]).dt.normalize()
    return df.reset_index(drop=True)


def _limpia_stock(df):
    df = df.dropna(subset=["celula"]).copy()
    df["celula"] = df["celula"].astype(int)
    df["piezas"] = pd.to_numeric(df["piezas"], errors="coerce").fillna(0)
    return df.reset_index(drop=True)


def _limpia_exp(df):
    df = df.dropna(subset=["fecha_hora"]).copy()
    for c in ("piezas_ve", "piezas_comb"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df.reset_index(drop=True)


def fuentes_editables(esc) -> dict:
    """DataFrames tipados que alimentan los editores de la pestaña Datos."""
    return {
        "bajas": _tipar(get_hoja(esc, "Bajas"), fechas=["fecha"], numeros=ROLES, textos=["turno"]),
        "paradas": _tipar(get_hoja(esc, "Paradas"), fechas=["desde", "hasta"], numeros=["celula", "tecnicos"], textos=["tipo"]),
        "dsem": _tipar(get_hoja(esc, "DemandaSemanal"), fechas=["semana_inicio"], numeros=["piezas_ve", "piezas_comb"]),
        "cor": _tipar(get_hoja(esc, "CorreccionDiaria"), fechas=["fecha"], numeros=["piezas_ve", "piezas_comb"]),
        "stock": _tipar(get_hoja(esc, "StockActual"), numeros=["celula", "piezas"]),
        "exp": _tipar(get_hoja(esc, "Expediciones"), fechas=["fecha_hora"], numeros=["piezas_ve", "piezas_comb"]),
    }


def _sig(*dfs) -> str:
    return "||".join(d.reset_index(drop=True).astype(str).to_csv(index=False) for d in dfs)


def sig_fuentes(f: dict) -> str:
    return _sig(_limpia_bajas(f["bajas"]), _limpia_paradas(f["paradas"]), _limpia_dem(f["dsem"], "semana_inicio"),
                _limpia_dem(f["cor"], "fecha"), _limpia_stock(f["stock"]), _limpia_exp(f["exp"]))


def init_state():
    defaults = dict(esc_in=None, esc=None, rec=None, historial=[], semana=None, pdf=None, error=None,
                    hora_actual=None, cont=None, esc_ver=0, msg=None, dirty=False, src=None, ed_sig=None)
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    if st.session_state.esc_in is None:
        st.session_state.esc_in = cargar_entrada_inicial()
    if st.session_state.src is None:
        st.session_state.src = fuentes_editables(st.session_state.esc_in)
        st.session_state.ed_sig = sig_fuentes(st.session_state.src)


init_state()


def restablecer_entrada(esc_nuevo, guardar=True):
    """Sustituye el estado de entrada (reinicia editores) y, opcionalmente, lo guarda en disco."""
    st.session_state.esc_in = esc_nuevo
    st.session_state.src = fuentes_editables(esc_nuevo)
    st.session_state.ed_sig = sig_fuentes(st.session_state.src)
    st.session_state.esc_ver += 1
    if guardar:
        guardar_entrada_auto(esc_nuevo)


def fijar(esc, rec, etiqueta, hora=None):
    """Guarda el estado vigente (escenario + recomendación) y anota el historial."""
    st.session_state.esc = esc
    st.session_state.rec = rec
    st.session_state.hora_actual = pd.Timestamp(hora if hora is not None else rec.inicio).floor("h")
    st.session_state.historial.append({"etiqueta": f"{pd.Timestamp(rec.inicio):%d/%m %H:%M} · {etiqueta}", "rec": rec})
    st.session_state.pdf = None


def calcular(esc, inicio, etiqueta="cálculo", conservar_hora=False):
    hora = st.session_state.hora_actual if conservar_hora else None
    with st.spinner("Resolviendo el modelo de optimización…"):
        rec = motor.recomendar(esc, pd.Timestamp(inicio))
    fijar(esc, rec, etiqueta, hora)
    st.session_state.dirty = False
    return rec


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.markdown(f"<h3 style='color:{NAVY}'>KWD · Motor de decisión</h3>", unsafe_allow_html=True)
    st.caption("Planificación de células de soldadura: qué activar, por qué y con qué impacto.")
    st.caption("Los datos de entrada se editan en la pestaña «Datos» y se guardan solos (data/estado.json).")
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
        st.session_state.historial = []
        st.session_state.semana = None
        st.session_state.cont = None
        calcular(st.session_state.esc_in.copiar(), inicio)
        st.session_state.error = None
    except Exception as e:  # noqa: BLE001
        st.session_state.error = f"{e}\n\n{traceback.format_exc()}"

st.markdown("""<div class="kwd-header"><h1>KWD Automotive · Motor de decisión de producción</h1>
<span>Soldadura · horizonte 24 h · una pieza exclusiva por célula · VE en verde, combustión en azul</span></div>""",
            unsafe_allow_html=True)

if st.session_state.error:
    st.error("No se ha podido calcular el plan.")
    with st.expander("Detalle técnico"):
        st.code(st.session_state.error)

if st.session_state.msg:
    nivel, texto, det = st.session_state.msg
    (st.error if nivel == "error" else st.warning)(texto)
    if det:
        with st.expander("Detalle técnico"):
            st.code(det)
    st.session_state.msg = None

rec = st.session_state.rec
esc = st.session_state.esc

NOM_TABS = ["Datos", "Planta en tiempo real", "Trabajadores", "Recomendación", "KPIs", "Overview 24 h", "Alternativas",
            "Contingencia", "Semana", "Informe"]
_tabs = st.tabs(NOM_TABS)
T = dict(zip(NOM_TABS, _tabs))


# ------------------------------------------------------------------ 0. Datos (entrada)
with T["Datos"]:
    esc_in = st.session_state.esc_in
    src = st.session_state.src
    ev = st.session_state.esc_ver
    st.subheader("Datos de entrada")
    st.caption("Todo lo que se edita aquí se guarda automáticamente en data/estado.json. Después pulsa "
               "«Calcular plan» en la barra lateral para obtener el plan con estos datos.")
    cel_ids = [int(c) for c in esc_in.celulas["celula"] if int(c) != config.CELULA_LOGISTICA]

    if st.button("Restaurar ejemplo", key="restaurar_ejemplo",
                 help="Descarta los datos actuales y vuelve al escenario de demostración."):
        restablecer_entrada(datos.estado_ejemplo())
        st.session_state.esc = st.session_state.rec = st.session_state.cont = st.session_state.semana = None
        st.session_state.historial = []
        st.session_state.dirty = False
        st.rerun()

    t_dem, t_baj, t_par, t_oper = st.tabs(["Demanda", "Bajas por turno", "Paradas programadas", "Stock y expediciones"])
    with t_dem:
        h1, h2, h3, h4 = st.columns([1, 1.2, 1.2, 1])
        coches = h1.number_input("Coches/día", min_value=0, value=1500, step=100, key="coches_dia")
        ratio = h2.number_input("Ratio COMB : VE", min_value=0.0, value=2.0, step=0.5, key="ratio_cv")
        modo_d = h3.radio("Rellenar", ["Semana", "Un día"], horizontal=True, key="modo_dem")
        fecha_d = h4.date_input("Fecha / lunes", value=pd.Timestamp(inicio).date(), key="fecha_dem")
        if st.button("Rellenar desde coches/día", key="rellenar_dem"):
            try:
                pve, pcomb = datos.demanda_desde_coches(float(coches), float(ratio))
                nuevo = st.session_state.esc_in.copiar()
                f = pd.Timestamp(fecha_d).normalize()
                if modo_d == "Semana":
                    lunes_ = f - pd.Timedelta(days=f.weekday())
                    dsem = _tipar(get_hoja(nuevo, "DemandaSemanal"), fechas=["semana_inicio"])
                    dsem = dsem[dsem["semana_inicio"] != lunes_]
                    dsem = pd.concat([dsem, pd.DataFrame([{"semana_inicio": lunes_, "piezas_ve": pve * 5,
                                                           "piezas_comb": pcomb * 5}])], ignore_index=True)
                    set_hoja(nuevo, "DemandaSemanal", dsem)
                else:
                    dd = _tipar(get_hoja(nuevo, "CorreccionDiaria"), fechas=["fecha"])
                    dd = dd[dd["fecha"] != f]
                    dd = pd.concat([dd, pd.DataFrame([{"fecha": f, "piezas_ve": pve, "piezas_comb": pcomb}])],
                                   ignore_index=True)
                    set_hoja(nuevo, "CorreccionDiaria", dd)
                restablecer_entrada(nuevo)
                st.session_state.dirty = True
                st.rerun()
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ in ("RerunException", "StopException"):
                    raise
                st.error(f"No se pudo rellenar la demanda: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
        st.caption("2 : 1 significa el doble de piezas de combustión que eléctricas. «Coches/día» se convierte en piezas "
                   "de cada referencia del tipo; la demanda semanal se reparte en 5 días laborables.")
        cs, cd = st.columns(2)
        with cs:
            st.markdown("**Demanda semanal (5 días laborables, piezas por tipo)**")
            dem_ed = st.data_editor(src["dsem"], num_rows="dynamic", width="stretch", key=f"ed_dsem_{ev}", column_config={
                "semana_inicio": st.column_config.DateColumn("Lunes de la semana", format="DD/MM/YYYY"),
                "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza, semana)", min_value=0),
                "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza, semana)", min_value=0)})
        with cd:
            st.markdown("**Demanda corregida confirmada del día (piezas por tipo)**")
            cor_ed = st.data_editor(src["cor"], num_rows="dynamic", width="stretch", key=f"ed_dcor_{ev}", column_config={
                "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza, día)", min_value=0),
                "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza, día)", min_value=0)})
        st.caption("Si un día tiene demanda corregida se usa esa; si no, la demanda semanal / 5.")
    with t_baj:
        st.caption("Personas de baja de cada rol en cada turno (sin fila: se aplica el absentismo estándar).")
        bajas_ed = st.data_editor(src["bajas"], num_rows="dynamic", width="stretch", key=f"ed_bajas_{ev}", column_config={
            "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "turno": st.column_config.SelectboxColumn("Turno", options=["M", "T", "N"]),
            **{r: st.column_config.NumberColumn(G.NOMBRE_REC.get(r, r), min_value=0, step=1) for r in ROLES}})
    with t_par:
        st.caption("Paradas programadas (con técnicos de mantenimiento ocupados). La célula 10 no puede pararse. "
                   "Las averías reales se registran desde la pestaña «Contingencia».")
        par_ed = st.data_editor(src["paradas"], num_rows="dynamic", width="stretch", key=f"ed_paradas_{ev}", column_config={
            "celula": st.column_config.SelectboxColumn("Célula", options=cel_ids),
            "desde": st.column_config.DatetimeColumn("Desde", format="DD/MM/YYYY HH:mm"),
            "hasta": st.column_config.DatetimeColumn("Hasta", format="DD/MM/YYYY HH:mm"),
            "tipo": st.column_config.SelectboxColumn("Tipo", options=["PROGRAMADA", "AVERIA"]),
            "tecnicos": st.column_config.NumberColumn("Técnicos", min_value=0, step=1)})
    with t_oper:
        st.caption("Stock de cada pieza al inicio del plan y cargas de camión realmente expedidas.")
        stock_ed = st.data_editor(src["stock"], num_rows="dynamic", width="stretch", key=f"ed_stock_{ev}", column_config={
            "celula": st.column_config.SelectboxColumn("Célula", options=cel_ids),
            "piezas": st.column_config.NumberColumn("Piezas en stock", min_value=0)})
        st.markdown("**Expediciones reales**")
        exp_ed = st.data_editor(src["exp"], num_rows="dynamic", width="stretch", key=f"ed_exp_{ev}", column_config={
            "fecha_hora": st.column_config.DatetimeColumn("Fecha y hora", format="DD/MM/YYYY HH:mm"),
            "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza)"),
            "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza)")})

    # ---- guardado automático
    try:
        n_baj, n_par = _limpia_bajas(bajas_ed), _limpia_paradas(par_ed)
        n_sem, n_cor = _limpia_dem(dem_ed, "semana_inicio"), _limpia_dem(cor_ed, "fecha")
        n_stk, n_exp = _limpia_stock(stock_ed), _limpia_exp(exp_ed)
        sig_actual = _sig(n_baj, n_par, n_sem, n_cor, n_stk, n_exp)
    except Exception as e:  # noqa: BLE001
        sig_actual = None
        st.warning(f"Completa las filas incompletas de las tablas ({e}).")
    if sig_actual is not None and sig_actual != st.session_state.ed_sig:
        nuevo = st.session_state.esc_in.copiar()
        set_hoja(nuevo, "Bajas", n_baj)
        set_hoja(nuevo, "Paradas", n_par)
        set_hoja(nuevo, "DemandaSemanal", n_sem)
        set_hoja(nuevo, "CorreccionDiaria", n_cor)
        set_hoja(nuevo, "StockActual", n_stk)
        set_hoja(nuevo, "Expediciones", n_exp)
        st.session_state.esc_in = nuevo
        st.session_state.ed_sig = sig_actual
        st.session_state.dirty = True
        guardar_entrada_auto(nuevo)
    if st.session_state.dirty:
        st.info("Datos guardados en data/estado.json. Pulsa «Calcular plan» (barra lateral) para actualizar el plan.")
    else:
        st.caption("Datos guardados en data/estado.json.")

    esc_in = st.session_state.esc_in
    st.markdown("**Demanda diaria efectiva (próximos 7 días)**")
    f0 = pd.Timestamp(inicio).normalize()
    cor_f = set(pd.to_datetime(get_hoja(esc_in, "CorreccionDiaria")["fecha"]).dt.normalize()) \
        if len(get_hoja(esc_in, "CorreccionDiaria")) else set()
    filas_d = []
    for i_ in range(7):
        f_ = f0 + pd.Timedelta(days=i_)
        try:
            dv, dc = datos.demanda_dia(esc_in, f_)
        except Exception:  # noqa: BLE001
            dv = dc = float("nan")
        filas_d.append({"Fecha": f_.strftime("%a %d/%m"), "Piezas VE (de cada pieza)": fnum(dv, 0),
                        "Piezas COMB (de cada pieza)": fnum(dc, 0),
                        "Origen": "corregida" if f_ in cor_f else ("fin de semana" if f_.weekday() > 4 else "semanal / 5")})
    st.dataframe(pd.DataFrame(filas_d), width="stretch", hide_index=True)

    with st.expander("Parámetros constantes (sólo lectura)"):
        st.markdown("**Células**")
        st.dataframe(esc_in.celulas, width="stretch", hide_index=True)
        c1_, c2_ = st.columns(2)
        c1_.markdown("**Turnos (recursos estándar)**")
        c1_.dataframe(esc_in.turnos, width="stretch", hide_index=True)
        c2_.markdown("**Almacén**")
        c2_.dataframe(esc_in.almacen, width="stretch", hide_index=True)
        st.markdown("**Parámetros**")
        st.dataframe(pd.DataFrame({"parámetro": list(esc_in.parametros.keys()), "valor": list(esc_in.parametros.values()),
                                   "descripción": [esc_in.descripciones.get(k, "") for k in esc_in.parametros]}),
                     width="stretch", hide_index=True)

# ------------------------------------------------------------------ sin plan todavía
if rec is None:
    with T["Planta en tiempo real"]:
        st.info("Revisa los datos en la pestaña **Datos** y pulsa **Calcular plan** en la barra lateral.")
    for n_ in NOM_TABS[2:]:
        with T[n_]:
            st.info("Calcula primero un plan para ver esta sección.")
    st.stop()

plan, es_contingencia = plan_principal(rec)
ss = datos.ss_por_celula(esc)
estado_txt = "CONTINGENCIA" if es_contingencia else str(plan.estado)
col_estado = G.COLOR_ESTADO.get(str(plan.estado), G.GRIS)
tp = G.tipos(esc)
HS = horas_rec(rec)
if st.session_state.hora_actual is None or pd.Timestamp(st.session_state.hora_actual) < HS[0] \
        or pd.Timestamp(st.session_state.hora_actual) > HS[-1]:
    st.session_state.hora_actual = HS[0]
hora_actual = pd.Timestamp(st.session_state.hora_actual)
OPT0 = informes.stock_optimo(esc, HS[0].normalize())
HL = informes.horas_libres(plan)
AVISO = informes.aviso_direccion(rec)


def badge(estado, color):
    return f'<span class="estado" style="background:{color}">{estado}</span>'


def aviso_dir(texto):
    """Aviso destacado para la dirección (caja roja)."""
    st.error("**AVISO PARA LA DIRECCIÓN — riesgo de desabastecimiento**\n\n" + str(texto).replace("\n", "\n\n"), icon="🚨")


def kpi_principal(pl, titulo="KPI principal: horas libres del personal"):
    """Tarjetas destacadas de horas libres (total y por rol) y ocupación por rol."""
    hl = informes.horas_libres(pl)
    st.markdown(f'<div class="kpi-principal">{titulo} (horas-persona presentes sin tarea; menos es mejor)</div>',
                unsafe_allow_html=True)
    cols = st.columns(1 + len(hl["roles"]))
    cols[0].metric("Horas libres — total", fnum(hl["total"], 0, " h"))
    for col, (r, v) in zip(cols[1:], hl["roles"].items()):
        col.metric(G.NOMBRE_REC.get(r, r), fnum(v["libres"], 0, " h"),
                   None if v["ocupacion_pct"] is None else f"ocupación {fnum(v['ocupacion_pct'], 0)} %", delta_color="off")


def stock_opt_tabla(pl, rc, es_):
    """DataFrame de stock vs óptimo por cierre de turno (vacío si falla)."""
    try:
        return informes.stock_vs_optimo(rc, es_, pl)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def mostrar_antes_despues(a, n, titulo="Antes / después"):
    pa, _ = plan_principal(a)
    pn, cn_ = plan_principal(n)
    ha, hn = informes.horas_libres(pa), informes.horas_libres(pn)
    st.markdown(f"#### {titulo}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Horas libres (total)", fnum(hn["total"], 0, " h"), f"{hn['total'] - ha['total']:+.0f} h", delta_color="inverse")
    m2.metric("Puntuación", f"{pn.puntuacion:.1f}", f"{pn.puntuacion - pa.puntuacion:+.1f}")
    m3.metric("Estado", str(pn.estado), f"antes: {pa.estado}", delta_color="off")
    m4.metric("Células activas turno", str(len(pn.config_turno_actual or [])),
              f"antes: {len(pa.config_turno_actual or [])}", delta_color="off")
    sa = informes.resumen_stock_optimo(stock_opt_tabla(pa, a, st.session_state.esc))
    sn = informes.resumen_stock_optimo(stock_opt_tabla(pn, n, st.session_state.esc))
    if sa and sn:
        s1, s2 = st.columns(2)
        s1.metric("Desviación media del stock vs óptimo", fnum(sn["media_pct"], 1, " %"),
                  f"{sn['media_pct'] - sa['media_pct']:+.1f} puntos", delta_color="inverse")
        s2.metric("Desviación máxima del stock vs óptimo", fnum(sn["max_pct"], 1, " %"),
                  f"{sn['max_pct'] - sa['max_pct']:+.1f} puntos", delta_color="inverse")
    ka, kn = pa.kpis or {}, pn.kpis or {}
    filas = [{"KPI": str(x).replace("_", " ").capitalize(), "Antes": ka[x], "Después": kn[x], "Δ": kn[x] - ka[x]}
             for x in kn if x in ka and _num(kn[x]) and _num(ka[x]) and abs(kn[x] - ka[x]) > 1e-9]
    if filas:
        with st.expander("KPIs que cambian"):
            st.dataframe(pd.DataFrame(filas).round(2), width="stretch", hide_index=True)
    st.write("**Células activas en el turno, antes:** " + (", ".join(map(str, pa.config_turno_actual)) or "—") +
             "  →  **después:** " + (", ".join(map(str, pn.config_turno_actual)) or "—"))
    if cn_:
        st.error("El nuevo resultado es un plan de contingencia — incumple: " + "; ".join(map(str, pn.incumplimientos)))


# ------------------------------------------------------------------ 1. Planta en tiempo real (sólo visualización)
with T["Planta en tiempo real"]:
    st.subheader("Planta en tiempo real")
    c1, c3, c4 = st.columns([1.3, 1.6, 3])
    sel = c1.selectbox("Hora", HS, index=HS.index(hora_actual) if hora_actual in HS else 0,
                       format_func=lambda t: f"{t:%d/%m %H:%M}", key="hora_planta")
    st.session_state.hora_actual = pd.Timestamp(sel)
    hora_actual = pd.Timestamp(sel)
    ih = idx_hora(rec, hora_actual)
    turno_h, fecha_turno = turno_fecha(rec, hora_actual)
    c3.markdown(f"**Turno:** {NOMBRE_TURNO.get(turno_h, turno_h)}<br>**Plan calculado desde:** {rec.inicio:%d/%m %H:%M}",
                unsafe_allow_html=True)
    c4.caption("Vista de sólo lectura del plan vigente. Las averías y bajas se registran en la pestaña «Contingencia».")
    opt_h = informes.stock_optimo(esc, hora_actual.normalize())

    try:
        est = rolling.estado_en(esc, rec, hora_actual)
    except Exception as e:  # noqa: BLE001
        est = None
        st.error(f"No se pudo calcular el estado de la planta: {e}")
        with st.expander("Detalle técnico"):
            st.code(traceback.format_exc())

    if est is not None:
        est = est.reset_index(drop=True)
        st.markdown("##### Células")
        celulas_orden = list(est["celula"].astype(int))
        for fila0 in range(0, len(celulas_orden), 4):
            cols = st.columns(4)
            for col, ci in zip(cols, celulas_orden[fila0:fila0 + 4]):
                r = est[est["celula"].astype(int) == ci].iloc[0]
                tipo_c = str(r.get("tipo", tp.get(ci, "")))
                color = "#7A869A" if ci == config.CELULA_LOGISTICA else G.COLOR_TIPO.get(tipo_c, G.AZUL)
                estado_c = str(r.get("estado", ""))
                st_pieza = r.get("stock", np.nan)
                ss_c = r.get("ss", ss.get(ci, np.nan))
                cob = r.get("cobertura_h", np.nan)
                pers = str(r.get("personas", "")) or "—"
                if ci == config.CELULA_LOGISTICA:
                    lineas = f"Personas: {pers}<br>Servicio logístico (no produce pieza)"
                else:
                    bajo = (_num(st_pieza) and _num(ss_c) and st_pieza < ss_c)
                    stock_txt = f"{fnum(st_pieza, 0)} / SS {fnum(ss_c, 0)} / ópt. {fnum(opt_h.get(ci, np.nan), 0)}"
                    if bajo:
                        stock_txt = f"<span style='color:{G.ROJO};font-weight:700'>{stock_txt} (bajo SS)</span>"
                    cob_txt = "—" if (not _num(cob) or not np.isfinite(cob)) else fnum(cob, 1, " h")
                    lineas = (f"Personas: {pers}<br>Stock: {stock_txt}<br>Cobertura: {cob_txt}<br>"
                              f"Próx. activación: {fts(r.get('proxima_activacion'))}")
                with col:
                    st.markdown(
                        f"<div class='celda' style='border-top-color:{color}'>"
                        f"<span class='tit'>C{ci}</span> <small>{tipo_c if ci != config.CELULA_LOGISTICA else 'LOG'}</small> "
                        f"<span class='pill' style='background:{COLOR_EST.get(estado_c, G.GRIS)};float:right'>{estado_c}</span>"
                        f"<br>{lineas}</div>", unsafe_allow_html=True)

    # ---- personal (sólo lectura)
    st.markdown("##### Personal en la hora seleccionada")
    pers_h = informes.personal_en_slot(rec, plan, ih)
    cols_r = st.columns(len(ROLES))
    for col, rol in zip(cols_r, ROLES):
        with col:
            disp = float(plan.recursos[f"{rol}_disp"].iloc[ih])
            d = pers_h[pers_h["rol"].astype(str) == rol] if len(pers_h) else pers_h
            st.markdown(f"**{G.NOMBRE_REC.get(rol, rol)}**")
            if len(d):
                est_ = d["estado"].astype(str).str.upper()
                n_as, n_li, n_pa = int((est_ == "ASIGNADO").sum()), int((est_ == "LIBRE").sum()), int((est_ == "PARADA").sum())
                st.caption(f"Presentes: {fnum(disp, 0)} · asignados: {n_as} · libres: {n_li}"
                           + (f" · en parada: {n_pa}" if n_pa else ""))
                with st.expander(f"Trabajadores ({len(d)})"):
                    for pr in d.itertuples():
                        est_p = str(pr.estado).upper()
                        cel_txt = str(pr.celulas) if str(pr.celulas) else ("libre" if est_p == "LIBRE" else est_p.lower())
                        carga = getattr(pr, "carga", None)
                        txt = f"<b>{pr.trabajador}</b>: {cel_txt}"
                        if _num(carga) and 0 < carga < 0.999:
                            txt += f" ({fnum(carga, 2)})"
                        if est_p not in ("ASIGNADO",):
                            txt += f" <i>[{est_p.lower()}]</i>"
                        st.markdown(f"<small>{txt}</small>", unsafe_allow_html=True)
            else:
                st.caption(f"Presentes: {fnum(disp, 0)} · asignados: {fnum(plan.recursos[f'{rol}_usado'].iloc[ih], 1)}")
    st.caption("El detalle por trabajador (recorrido del turno y cuadrante horario) está en la pestaña «Trabajadores».")
    if st.session_state.historial:
        with st.expander("Historial de recomendaciones"):
            st.write([h["etiqueta"] for h in st.session_state.historial])

# ------------------------------------------------------------------ 2. Trabajadores
PALETA_CEL = ["#9FD8B4", "#A8C7F0", "#F4C98B", "#E3B5E8", "#F2A9A0", "#B8E0E6", "#D9D99B", "#C5B8F0",
              "#F0B8CF", "#B5D49A", "#EBCB9E", "#9ECDE0", "#E8A9D6", "#CFE0A8", "#F5D78E", "#A9B8E8"]


def _color_celda(v):
    s = str(v)
    if s in ("libre", "LIBRE"):
        return "background-color:#EEF0F4;color:#6B7280"
    if s in ("parada", "PARADA"):
        return "background-color:#FBEFD5;color:#8A6D1D;font-style:italic"
    if s in ("—", ""):
        return "color:#B0B6C3"
    m = re.search(r"C(\d+)", s)
    if m:
        return f"background-color:{PALETA_CEL[(int(m.group(1)) - 1) % len(PALETA_CEL)]};color:#1b1f3b"
    return ""


with T["Trabajadores"]:
    st.subheader("Trabajadores del turno")
    st.caption("Cada trabajador está numerado (turno-rol-nº: M-OP01 = operario 1 de mañana; PK picking, CA carretillero, "
               "MT mantenimiento, CL calidad) y se sigue puesto a puesto; en una misma hora puede cubrir varias células. "
               "Cada persona presente está asignada a una célula o libre.")
    trab = getattr(plan, "trabajadores", None)
    pers_all = getattr(plan, "personal", None)
    if not isinstance(trab, pd.DataFrame) or not len(trab) or not isinstance(pers_all, pd.DataFrame) or not len(pers_all):
        st.info("El plan no incluye la enumeración de trabajadores.")
    else:
        trab = trab.copy()
        turnos_t = [t for t in ["M", "T", "N"] if t in set(trab["turno"].astype(str))] or sorted(set(trab["turno"].astype(str)))
        f1, f2, f3 = st.columns([1, 2, 1.5])
        turno_sel = f1.selectbox("Turno", turnos_t, index=turnos_t.index(turno_h) if turno_h in turnos_t else 0,
                                 format_func=lambda t: NOMBRE_TURNO.get(t, t), key="tr_turno")
        roles_sel = f2.multiselect("Rol", ROLES, default=ROLES, format_func=lambda r: G.NOMBRE_REC.get(r, r), key="tr_roles")
        buscar = f3.text_input("Buscar trabajador", placeholder="p. ej. M-OP07", key="tr_buscar")
        pers_t = pers_all.copy()
        if "trabajador" not in pers_t.columns and "persona" in pers_t.columns:
            pers_t["trabajador"] = pers_t["persona"]
        pers_t["_i"] = informes._slot_idx(rec, pers_t["slot"])
        pers_t["estado"] = pers_t["estado"].astype(str).str.upper() if "estado" in pers_t else "ASIGNADO"
        if "turno" not in pers_t:
            pers_t["turno"] = turno_h
        pers_t = pers_t[pers_t["turno"].astype(str) == turno_sel]
        filas_k = []
        for r in ROLES:
            dr = pers_t[pers_t["rol"].astype(str) == r]
            if not len(dr):
                continue
            asig = float((dr["estado"] == "ASIGNADO").sum())
            libres = float((dr["estado"] == "LIBRE").sum())
            parada = float((dr["estado"] == "PARADA").sum())
            filas_k.append({"Rol": G.NOMBRE_REC.get(r, r), "Presentes": dr["trabajador"].nunique(),
                            "Horas-persona asignadas": round(asig, 1), "Horas libres": round(libres, 1),
                            "Horas en parada": round(parada, 1),
                            "Ocupación %": round(100 * asig / max(asig + libres, 1e-9), 0)})
        if filas_k:
            st.markdown("**Personal presente, ocupación y horas libres del turno**")
            st.dataframe(pd.DataFrame(filas_k), width="stretch", hide_index=True)
        tt = trab[trab["turno"].astype(str) == turno_sel]
        tt = tt[tt["rol"].astype(str).isin(roles_sel)]
        if buscar:
            tt = tt[tt["trabajador"].astype(str).str.contains(buscar.strip(), case=False, regex=False)]
        ih_t = idx_hora(rec, st.session_state.hora_actual)
        ahora_ = informes.personal_en_slot(rec, plan, ih_t).set_index("trabajador") if len(pers_all) else pd.DataFrame()
        tabla = pd.DataFrame({
            "Trabajador": tt["trabajador"].astype(str).values,
            "Rol": [G.NOMBRE_REC.get(str(r), str(r)) for r in tt["rol"]],
            "Estado ahora": [ahora_["estado"].get(t, "—") if len(ahora_) else "—" for t in tt["trabajador"]],
            "Célula(s) ahora": [(ahora_["celulas"].get(t, "") or "—") if len(ahora_) else "—" for t in tt["trabajador"]],
            "Horas asignado": tt["horas_asignado"].values if "horas_asignado" in tt else "",
            "Horas libre": tt["horas_libre"].values if "horas_libre" in tt else "",
            "Recorrido del turno": tt["celulas"].astype(str).values if "celulas" in tt else "",
        })
        st.markdown(f"**Trabajadores ({len(tabla)})**")
        st.dataframe(tabla, width="stretch", hide_index=True, height=min(420, 38 + 35 * len(tabla)))
        d_g = pers_t[pers_t["rol"].astype(str).isin(roles_sel)].copy()
        if buscar:
            d_g = d_g[d_g["trabajador"].astype(str).str.contains(buscar.strip(), case=False, regex=False)]
        if len(d_g):
            ce = d_g["celulas"].fillna("").astype(str)
            ce = ce.where(~(d_g["estado"] == "PARADA"), "parada")
            ce = ce.where(~((d_g["estado"] == "LIBRE") & (ce == "")), "libre")
            d_g["_v"] = ce.replace("", "libre")
            d_g["_h"] = d_g["_i"].map(lambda i: HS[min(max(int(i), 0), len(HS) - 1)].strftime("%H:%M"))
            cuad = d_g.pivot_table(index="trabajador", columns="_h", values="_v", aggfunc="first", sort=False)
            cuad = cuad[[c for c in dict.fromkeys(d_g.sort_values("_i")["_h"])]].fillna("—")
            st.markdown("**Cuadrante horario (color = célula)**")
            estilo = cuad.style.map(_color_celda) if hasattr(cuad.style, "map") else cuad.style.applymap(_color_celda)
            st.dataframe(estilo, width="stretch", height=min(700, 38 + 35 * len(cuad)))

# ------------------------------------------------------------------ 3. Recomendación
with T["Recomendación"]:
    inicio_rec = pd.Timestamp(rec.inicio)
    st.subheader(f"Turno {NOMBRE_TURNO.get(turno_de(rec), turno_de(rec))} · desde {inicio_rec:%d/%m/%Y %H:%M}")
    if AVISO:
        aviso_dir(AVISO)
    if es_contingencia:
        st.markdown(f'<div class="contingencia">Plan de contingencia — incumple: '
                    f'{"; ".join(map(str, plan.incumplimientos)) or "ver alertas"}<br>'
                    f'<small>No existe ningún plan viable: este es el menos malo y NO debe tomarse como recomendación '
                    f'viable sin decisión de la dirección.</small></div>', unsafe_allow_html=True)
        st.write("")
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 2])
    c1.markdown("**Estado**<br>" + badge(estado_txt if es_contingencia else str(plan.estado), col_estado),
                unsafe_allow_html=True)
    techo = (plan.kpis or {}).get("puntuacion_max_teorica")
    c2.metric("Puntuación", f"{plan.puntuacion:.1f} / 100",
              None if techo is None else f"máximo alcanzable ≈ {techo:.1f}", delta_color="off",
              help=("Puntuación máxima alcanzable: ningún plan posible puede superarla según la cota demostrada "
                    "por el solver (aproximada). El plan recomendado está, como mucho, a "
                    f"{techo - plan.puntuacion:.1f} puntos del óptimo." if techo is not None else None))
    c3.metric("Idoneidad (óptimo garantizado ±gap)", "—" if plan.idoneidad is None else f"{plan.idoneidad:.1f} %",
              help=("No aplica: plan de contingencia" if plan.idoneidad is None else
                    f"Gap relativo del solver: {100 * (plan.gap or 0):.2f} %"))
    if plan.idoneidad is None:
        c3.caption("No aplica: plan de contingencia")
    c4.markdown("**Células a activar en este turno**<br>" +
                " ".join(f'<span class="estado" style="background:{G.COLOR_TIPO.get(tp.get(int(c)), G.AZUL)}">C{int(c)}</span>'
                         for c in (plan.config_turno_actual or [])) or "ninguna", unsafe_allow_html=True)
    st.write("")
    kpi_principal(plan)
    k1, k2, k3 = st.columns(3)
    cam = informes.resumen_camiones(rec)
    k1.metric("Camiones en el horizonte (24 h)", fnum(cam["total"], 0) if cam else "—",
              help="Nº de camiones = ceil(m² de la carga / 15 m²) en cada ciclo de expedición (cada 1,5 h).")
    k2.metric("Camiones por ciclo (media / máx.)", f"{fnum(cam['media'], 1)} / {fnum(cam['max'], 0)}" if cam else "—")
    k3.metric("Camiones por día (previsto)", fnum(cam["por_dia"], 0) if cam else "—")
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
        st.markdown(f"- Horas libres del personal: **{fnum(HL['total'], 0)} h** en el horizonte.")
        dv = (exp.get("impacto", {}) or {}).get("delta_vs_alternativas", []) or []
        for dd in dv:
            st.markdown(f"- Frente a **{dd['nombre']}**: {fnum(dd['puntuacion'], 2)} puntos de diferencia")
        if not dv:
            st.markdown("- Sin alternativas viables con las que comparar.")
        svo_r = stock_opt_tabla(plan, rec, esc)
        rs = informes.resumen_stock_optimo(svo_r)
        if rs:
            st.markdown(f"- Stock frente al óptimo en cierres de turno: desviación media {fnum(rs['media_pct'], 1)} %, "
                        f"máxima {fnum(rs['max_pct'], 1)} %.")

    st.markdown("#### Asignación nominal de personal del turno actual")
    ro = informes.roster_turno(rec, plan)
    if ro is not None and len(ro):
        st.dataframe(ro, width="stretch", hide_index=True)
        st.caption("Cada persona se asigna a una o varias células cuya carga suma ≤ 1 (p. ej. «C8+C9»). "
                   "La asignación se mantiene estable entre horas.")
    else:
        st.info("El plan no incluye reparto nominal de personal.")
    pc(G.contribuciones(plan), width="stretch")
    st.markdown("#### Alertas")
    if rec.alertas:
        for a in rec.alertas:
            (st.error if es_contingencia and "INVIABLE" in str(a).upper() else st.warning)(str(a))
    else:
        st.success("Sin alertas.")
    if plan.incumplimientos and not es_contingencia:
        st.warning("Incumplimientos: " + "; ".join(map(str, plan.incumplimientos)))

# ------------------------------------------------------------------ 4. KPIs
with T["KPIs"]:
    st.subheader("Indicadores clave del plan recomendado")
    kpi_principal(plan)
    st.markdown("**Puntuación**")
    q1, q2, q3 = st.columns(3)
    q1.metric("Puntuación", f"{plan.puntuacion:.1f} / 100")
    q2.metric("Máximo alcanzable", "—" if techo is None else f"≈ {techo:.1f}")
    q3.metric("Idoneidad", "—" if plan.idoneidad is None else f"{plan.idoneidad:.1f} %")
    svo = stock_opt_tabla(plan, rec, esc)
    rs = informes.resumen_stock_optimo(svo)
    st.markdown("**Stock frente al óptimo en los cierres de turno (06:00, 14:00, 22:00)**")
    if rs:
        w1, w2, w3 = st.columns(3)
        w1.metric("Desviación media vs óptimo", fnum(rs["media_pct"], 1, " %"))
        w2.metric("Desviación máxima vs óptimo", fnum(rs["max_pct"], 1, " %"))
        w3.metric("Cierres-pieza bajo SS", str(rs["bajo_ss"]))
        t_ = svo.copy()
        t_["cierre"] = t_["cierre"].map(lambda x: pd.Timestamp(x).strftime("%d/%m %H:%M"))
        st.dataframe(t_.rename(columns={"cierre": "Cierre de turno", "celula": "Célula", "tipo": "Tipo", "stock": "Stock",
                                        "ss": "SS", "optimo": "Óptimo", "desviacion": "Desviación",
                                        "desviacion_pct": "Desviación %"}).round(1),
                     width="stretch", hide_index=True)
        pc(G.stock_vs_optimo(svo), width="stretch")
    else:
        st.caption("El horizonte no contiene cierres de turno.")
    lista = [x for x in informes.kpi_lista(plan) if x[2] != "Personal"]
    for grupo in dict.fromkeys(g for _, _, g in lista):
        st.markdown(f"**{grupo}**")
        items = [(a, b) for a, b, g in lista if g == grupo]
        for n0 in range(0, len(items), 4):
            cols = st.columns(4)
            for col, (n, v) in zip(cols, items[n0:n0 + 4]):
                col.metric(n, v)
    if HL["roles"]:
        pc(G.horas_libres_barras(HL), width="stretch")
    pc(G.recursos(rec, plan), width="stretch")
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.energia(rec, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### KPIs por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")

# ------------------------------------------------------------------ 5. Overview 24 h
with T["Overview 24 h"]:
    st.subheader("Overview de 24 horas")
    pc(G.gantt(rec, esc, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### Resumen por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")
    pc(G.stock(rec, esc, plan, ss, OPT0), width="stretch")
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.recursos(rec, plan), width="stretch")

# ------------------------------------------------------------------ 6. Alternativas
with T["Alternativas"]:
    st.subheader("Top 1/2/3")
    opciones = [(f"Top {i + 1}", p) for i, p in enumerate(rec.top)]
    if rec.contingencia is not None and not rec.top:
        opciones.append(("Contingencia", rec.contingencia))
    filas = []
    for n, p in opciones:
        filas.append({"Plan": n, "Estado": str(p.estado), "Puntuación": round(p.puntuacion, 2),
                      "Horas libres": round(informes.horas_libres(p)["total"], 0),
                      "Idoneidad %": "—" if p.idoneidad is None else fnum(p.idoneidad, 1),
                      "Máx. alcanzable": ("—" if (p.kpis or {}).get("puntuacion_max_teorica") is None
                                          else fnum(p.kpis["puntuacion_max_teorica"], 1)),
                      "Células turno actual": ", ".join(str(int(c)) for c in (p.config_turno_actual or [])) or "—",
                      "m² medios": round(float(np.mean(p.espacio.values)), 1),
                      "kWh": round(float(np.sum(p.energia_kwh.values)), 1)})
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
    colores = [G.VERDE if n.startswith("Top") else G.ROJO for n, _ in opciones]
    pc(G.comparar_barras([n for n, _ in opciones], [p.puntuacion for _, p in opciones], "Puntuación", colores),
       width="stretch")
    pc(G.comparar_barras([n for n, _ in opciones], [informes.horas_libres(p)["total"] for _, p in opciones],
                         "Horas libres (menos es mejor)", colores), width="stretch")
    pc(G.comparar_barras([n for n, _ in opciones], [float(np.sum(p.energia_kwh.values)) for _, p in opciones],
                         "kWh totales", colores), width="stretch")
    nombres = [n for n, _ in opciones]
    todas = []
    for _, p in opciones:
        for a in (p.kpis or {}):
            if a not in todas and _num(p.kpis[a]):
                todas.append(a)
    comp = pd.DataFrame({n: [p.kpis.get(a) for a in todas] for n, p in opciones},
                        index=[str(a).replace("_", " ").capitalize() for a in todas])
    st.markdown("#### KPIs lado a lado")
    st.dataframe(comp.round(2), width="stretch")
    sel_g = st.selectbox("Ver Gantt de", nombres)
    pc(G.gantt(rec, esc, dict(opciones)[sel_g]), width="stretch")

# ------------------------------------------------------------------ 7. Contingencia
def _fmt_df_horas(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    for c_ in d.columns:
        if "hora" in str(c_) or c_ in ("desde", "hasta"):
            d[c_] = d[c_].map(lambda x: fts(x))
    return d


def aplicar_contingencia(ct):
    """Registra la incidencia como real: escenario de la contingencia + recomendación posterior."""
    res = ct["res"]
    ahora = ct["ahora"]
    esc2 = res.get("escenario")
    if esc2 is None:
        esc2 = st.session_state.esc
        for b in ct["bajas_celulas"]:
            fin = b["hasta"] if b.get("hasta") is not None else HS[-1] + pd.Timedelta(hours=1)
            esc2 = rolling.aplicar_evento(esc2, rolling.Evento("parada_celula", {
                "celula": b["celula"], "desde": b["desde"], "hasta": fin, "tipo": "AVERIA", "tecnicos": 0}), ahora)
        t_, f_ = turno_fecha(rec, ahora)
        for w in ct["bajas_personas"]:
            rol_w = next((r for r in ROLES if f"-{config.CODIGO_ROL[r]}" in str(w)), None)
            if rol_w:
                esc2 = rolling.aplicar_evento(esc2, rolling.Evento("baja_personal", {
                    "fecha": f_, "turno": str(w).split("-")[0], "rol": rol_w, "cantidad": 1, "trabajador": w}), ahora)
        for rol_w, n_ in ct["bajas_rol"].items():
            esc2 = rolling.aplicar_evento(esc2, rolling.Evento("baja_personal", {
                "fecha": f_, "turno": t_, "rol": rol_w, "cantidad": int(n_)}), ahora)
    fijar(esc2, res["rec_despues"], "incidencia real (contingencia)", ahora)
    restablecer_entrada(esc2.copiar())


with T["Contingencia"]:
    st.subheader("Contingencia")
    st.caption("Simula la caída de células y/o la baja de personal desde una hora, con el stock del plan vigente. "
               "Sólo se calcula al pulsar «Calcular plan de contingencia».")
    prod_ids = [int(c) for c in esc.celulas["celula"] if int(c) != config.CELULA_LOGISTICA]
    cc1, cc2 = st.columns([1, 2])
    ahora_c = cc1.selectbox("Desde (hora de la incidencia)", HS, index=HS.index(hora_actual) if hora_actual in HS else 0,
                            format_func=lambda t: f"{t:%d/%m %H:%M}", key="cont_ahora")
    cel_sel = cc2.multiselect("Células que se dan de baja", prod_ids, default=[],
                              format_func=lambda c: f"Célula {c} ({tp.get(c, '?')})", key="cont_celulas")
    FIN_HZ = "Hasta fin del horizonte"
    hasta_opts = [FIN_HZ] + [h for h in HS if h > pd.Timestamp(ahora_c)]
    hastas = {}
    if cel_sel:
        hc = st.columns(min(len(cel_sel), 4))
        for j, c_ in enumerate(cel_sel):
            hastas[c_] = hc[j % len(hc)].selectbox(f"C{c_}: hasta", hasta_opts, key=f"cont_hasta_{c_}",
                                                   format_func=lambda t: t if isinstance(t, str) else f"{t:%d/%m %H:%M}")
    st.markdown("**Personal de baja**")
    trab_c = getattr(plan, "trabajadores", None)
    pw1, pw2, pw3 = st.columns([1, 1.5, 2.5])
    turno_ini_c, _ = turno_fecha(rec, ahora_c)
    f_turno = pw1.selectbox("Turno", ["M", "T", "N"], index=["M", "T", "N"].index(turno_ini_c) if turno_ini_c in "MTN" else 0,
                            format_func=lambda t: NOMBRE_TURNO.get(t, t), key="cont_turno")
    f_roles = pw2.multiselect("Filtrar por rol", ROLES, default=ROLES, format_func=lambda r: G.NOMBRE_REC.get(r, r),
                              key="cont_roles")
    if isinstance(trab_c, pd.DataFrame) and len(trab_c):
        cand = trab_c[(trab_c["turno"].astype(str) == f_turno) & (trab_c["rol"].astype(str).isin(f_roles))]
        opciones_w = sorted(cand["trabajador"].astype(str))
    else:
        opciones_w = []
    pers_sel = pw3.multiselect("Trabajadores concretos (ID)", opciones_w, default=[], key="cont_personas")
    with st.expander("O indicar un número de bajas por rol"):
        rc_ = st.columns(len(ROLES))
        bajas_rol = {}
        for col, r in zip(rc_, ROLES):
            n_r = col.number_input(G.NOMBRE_REC.get(r, r), min_value=0, value=0, step=1, key=f"cont_rol_{r}")
            if n_r:
                bajas_rol[r] = int(n_r)

    if st.button("Calcular plan de contingencia", type="primary", key="cont_calc"):
        if not cel_sel and not pers_sel and not bajas_rol:
            st.warning("Selecciona al menos una célula, un trabajador o un número de bajas por rol.")
        else:
            bc = [{"celula": int(c_), "desde": pd.Timestamp(ahora_c),
                   "hasta": None if isinstance(hastas[c_], str) else pd.Timestamp(hastas[c_])} for c_ in cel_sel]
            try:
                with st.spinner("Calculando el plan de contingencia…"):
                    res = rolling.contingencia(esc, rec, pd.Timestamp(ahora_c), bc, list(pers_sel), dict(bajas_rol) or None)
                st.session_state.cont = {"res": res, "ahora": pd.Timestamp(ahora_c), "bajas_celulas": bc,
                                         "bajas_personas": list(pers_sel), "bajas_rol": dict(bajas_rol), "aplicada": False}
            except Exception as e:  # noqa: BLE001
                st.session_state.cont = None
                st.error(f"No se pudo calcular la contingencia: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
    cont = st.session_state.cont
    if cont:
        res = cont["res"]
        partes = []
        if cont["bajas_celulas"]:
            partes.append("células " + ", ".join(f"C{b['celula']}" for b in cont["bajas_celulas"]))
        if cont["bajas_personas"]:
            partes.append("trabajadores " + ", ".join(cont["bajas_personas"]))
        if cont["bajas_rol"]:
            partes.append("bajas por rol: " + ", ".join(f"{G.NOMBRE_REC.get(r, r)} {n}" for r, n in cont["bajas_rol"].items()))
        st.markdown(f"##### Incidencia desde {cont['ahora']:%d/%m %H:%M}: " + "; ".join(partes))
        aviso_c = res.get("aviso_direccion")
        if aviso_c:
            aviso_dir(aviso_c)
        resumen = res.get("resumen") or []
        if resumen:
            st.markdown("<div class='resumen'><b>Qué hacer</b><br>" + "<br>".join(f"• {t}" for t in resumen) + "</div>",
                        unsafe_allow_html=True)
        ta, tb = st.columns(2)
        with ta:
            st.markdown("**Reubicación de trabajadores**")
            ru = res.get("reubicacion")
            if isinstance(ru, pd.DataFrame) and len(ru):
                st.dataframe(_fmt_df_horas(ru).rename(columns={"persona": "Trabajador", "trabajador": "Trabajador",
                                                                "rol": "Rol", "de_celula": "De célula",
                                                                "a_celulas": "A células", "desde": "Desde",
                                                                "hasta": "Hasta"}),
                             width="stretch", hide_index=True)
            else:
                st.write("Sin cambios de personal.")
        with tb:
            st.markdown("**Máquinas a activar o ampliar**")
            mq = res.get("maquinas")
            if isinstance(mq, pd.DataFrame) and len(mq):
                st.dataframe(mq.rename(columns={"celula": "Célula", "horas_antes": "Horas antes",
                                                "horas_despues": "Horas después", "delta": "Δ horas",
                                                "franjas_nuevas": "Franjas nuevas"}).round(1),
                             width="stretch", hide_index=True)
            else:
                st.write("Sin cambios en las máquinas.")
        st.markdown("**Consumo de stock de seguridad y agotamiento por pieza**")
        ag = informes.tabla_agotamiento(res.get("agotamiento"))
        if len(ag):
            ag = _fmt_df_horas(ag).rename(columns={"celula": "Pieza", "pieza": "Pieza", "hora_bajo_ss": "Baja del SS",
                                                   "hora_sin_stock": "Se agota el stock"})
            st.dataframe(ag, width="stretch", hide_index=True)
        else:
            st.write("Ninguna pieza baja de su stock de seguridad.")
        ds = res.get("desabastecimiento")
        if isinstance(ds, pd.DataFrame) and len(ds):
            st.markdown("**Piezas no servidas por camión / ciclo de expedición**")
            col_n = next((c for c in ds.columns if "no_servidas" in str(c)), None)
            if col_n:
                st.metric("Total de piezas no servidas", fnum(pd.to_numeric(ds[col_n], errors="coerce").sum(), 0))
            st.dataframe(_fmt_df_horas(ds), width="stretch", hide_index=True)
        mostrar_antes_despues(res["rec_antes"], res["rec_despues"], "KPIs antes / después de la incidencia")
        if st.button("Aplicar como incidencia real", key="cont_aplicar",
                     help="Registra la incidencia en los datos y sustituye el plan vigente por el de contingencia."):
            try:
                aplicar_contingencia(cont)
                cont["aplicada"] = True
                st.session_state.cont = cont
                st.rerun()
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ in ("RerunException", "StopException"):
                    raise
                st.error(f"No se pudo aplicar la incidencia: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
        if cont.get("aplicada"):
            st.success("Incidencia aplicada como real: el plan vigente ya la incluye.")

# ------------------------------------------------------------------ 8. Semana
with T["Semana"]:
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
        import plotly.express as px
        num = sem.select_dtypes("number")
        for c in [c for c in num.columns if any(s in c.lower() for s in ("puntuacion", "puntuación", "m2", "m²", "kwh", "libres"))][:4]:
            x = sem["turno"].astype(str) if "turno" in sem else sem.index.astype(str)
            fig = px.bar(sem, x=x, y=c, title=c.replace("_", " ").capitalize(), color_discrete_sequence=[NAVY])
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10))
            pc(fig, width="stretch")
        st.download_button("Descargar tabla (CSV)", sem.to_csv(index=False).encode("utf-8-sig"),
                           file_name="simulacion_semana.csv", mime="text/csv")

# ------------------------------------------------------------------ 9. Informe
with T["Informe"]:
    st.subheader("Informe PDF para dirección")
    st.write("Incluye resumen ejecutivo, aviso para la dirección (si hay desabastecimiento), células y personal del turno, "
             "camiones por ciclo, horas libres, stock frente al óptimo, KPIs, alternativas, contingencia (si procede), "
             "gráficos y alertas.")
    if st.button("Generar informe PDF", type="primary"):
        try:
            with st.spinner("Generando informe…"):
                ruta = Path(tempfile.mkdtemp()) / "informe_kwd.pdf"
                ct = st.session_state.cont
                informes.generar_informe_pdf(rec, esc, str(ruta),
                                             contingencia=(ct["res"] if ct and ct.get("aplicada") else None))
                st.session_state.pdf = ruta.read_bytes()
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo generar el informe: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())
    if st.session_state.pdf:
        st.success("Informe generado.")
        st.download_button("Descargar informe PDF", st.session_state.pdf,
                           file_name=f"informe_kwd_{pd.Timestamp(rec.inicio):%Y%m%d_%H%M}.pdf", mime="application/pdf")
