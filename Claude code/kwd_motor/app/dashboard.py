"""Dashboard Streamlit del motor de decisión KWD v2 (español)."""
from __future__ import annotations

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
DEF_PLAN_MANUAL = getattr(config, "DEFINICION_PLAN_MANUAL",
                          "Plan de referencia manual: simulación de cómo planificaría un encargado sin optimizador.")
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


def init_state():
    defaults = dict(esc=None, rec=None, historial=[], semana=None, pdf=None, error=None, origen="",
                    hora_actual=None, antes_despues=None, cont=None, auto=True, esc_ver=0, manual_sig=None,
                    manual_ver=-1, msg=None)
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


init_state()


def cargar_ejemplo():
    if not EJEMPLO.exists():
        EJEMPLO.parent.mkdir(parents=True, exist_ok=True)
        datos.crear_plantilla_ejemplo(str(EJEMPLO))
    return datos.cargar_entrada(str(EJEMPLO))


def fijar(esc, rec, etiqueta, hora=None):
    """Guarda el estado vigente (escenario + recomendación) y anota el historial."""
    st.session_state.esc = esc
    st.session_state.rec = rec
    st.session_state.esc_ver += 1
    st.session_state.hora_actual = pd.Timestamp(hora if hora is not None else rec.inicio).floor("h")
    st.session_state.historial.append({"etiqueta": f"{pd.Timestamp(rec.inicio):%d/%m %H:%M} · {etiqueta}", "rec": rec})
    st.session_state.pdf = None


def calcular(esc, inicio, etiqueta="cálculo", conservar_hora=False):
    hora = st.session_state.hora_actual if conservar_hora else None
    with st.spinner("Resolviendo el modelo de optimización…"):
        rec = motor.recomendar(esc, pd.Timestamp(inicio))
    fijar(esc, rec, etiqueta, hora)
    return rec


def aplicar_evento_real(ev, etiqueta, ahora=None, mostrar=True):
    """Aplica un evento con `rolling.reconfigurar` desde la hora actual y guarda antes/después."""
    esc = st.session_state.esc
    antes = st.session_state.rec
    ahora = pd.Timestamp(ahora if ahora is not None else st.session_state.hora_actual).floor("h")
    try:
        with st.spinner("Recalculando desde la hora actual…"):
            res = rolling.reconfigurar(esc, antes, ev, ahora)
        if isinstance(res, tuple):
            esc2, rec2 = res
        else:
            rec2 = res
            esc2 = rolling.aplicar_evento(esc, ev)
    except Exception as e:  # noqa: BLE001
        st.session_state.msg = ("error", f"No se pudo aplicar «{etiqueta}»: {e}", traceback.format_exc())
        return False
    st.session_state.antes_despues = {"antes": antes, "despues": rec2, "etiqueta": etiqueta, "hora": ahora}
    fijar(esc2, rec2, etiqueta, ahora)
    return True


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.markdown(f"<h3 style='color:{NAVY}'>KWD · Motor de decisión</h3>", unsafe_allow_html=True)
    st.caption("Planificación de células de soldadura: qué activar, por qué y con qué impacto.")
    fuente = st.radio("Datos de entrada",
                      ["Escenario de ejemplo", "Escenario de contingencia (avería célula 14)", "Cargar Excel"],
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
            esc_n = datos.cargar_entrada(str(tmp))
            st.session_state.origen = subido.name
        elif fuente.startswith("Escenario de contingencia"):
            esc_n = datos.cargar_entrada(str(CONTINGENCIA))
            st.session_state.origen = "contingencia"
        else:
            esc_n = cargar_ejemplo()
            st.session_state.origen = "ejemplo"
        st.session_state.historial = []
        st.session_state.semana = None
        st.session_state.cont = None
        st.session_state.antes_despues = None
        calcular(esc_n, inicio)
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

NOM_TABS = ["Planta en tiempo real", "Trabajadores", "Recomendación", "KPIs", "Overview 24 h", "Alternativas", "Contingencia",
            "Entrada manual", "Semana", "Informe"]
tabs = st.tabs(NOM_TABS)

if rec is None:
    with tabs[0]:
        st.info("Elige el origen de datos y la hora de inicio en la barra lateral y pulsa **Calcular plan**.")
    for i in range(1, len(tabs)):
        with tabs[i]:
            st.info("Calcula primero un plan para ver esta sección.")
    st.stop()

plan, es_contingencia = plan_principal(rec)
ss = datos.ss_por_celula(esc)
k_colchon = float(esc.parametros.get("colchon_ss", 0.10))
estado_txt = "CONTINGENCIA" if es_contingencia else str(plan.estado)
col_estado = G.COLOR_ESTADO.get(str(plan.estado), G.GRIS)
tp = G.tipos(esc)
HS = horas_rec(rec)
if st.session_state.hora_actual is None or pd.Timestamp(st.session_state.hora_actual) < HS[0] \
        or pd.Timestamp(st.session_state.hora_actual) > HS[-1]:
    st.session_state.hora_actual = HS[0]
hora_actual = pd.Timestamp(st.session_state.hora_actual)


def badge(estado, color):
    return f'<span class="estado" style="background:{color}">{estado}</span>'


# ---- comparación antes / después (reutilizable)
def _num(v):
    return isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool)


def mostrar_antes_despues(a, n, titulo="Antes / después"):
    pa, _ = plan_principal(a)
    pn, cn_ = plan_principal(n)
    st.markdown(f"#### {titulo}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Puntuación", f"{pn.puntuacion:.1f}", f"{pn.puntuacion - pa.puntuacion:+.1f}")
    if pn.idoneidad is None or pa.idoneidad is None:
        m2.metric("Idoneidad", "—" if pn.idoneidad is None else f"{pn.idoneidad:.1f} %",
                  "No aplica: plan de contingencia", delta_color="off")
    else:
        m2.metric("Idoneidad", f"{pn.idoneidad:.1f} %", f"{pn.idoneidad - pa.idoneidad:+.1f}")
    m3.metric("Estado", str(pn.estado), f"antes: {pa.estado}", delta_color="off")
    ka, kn = pa.kpis or {}, pn.kpis or {}
    m4.metric("Células activas turno", str(len(pn.config_turno_actual or [])),
              f"antes: {len(pa.config_turno_actual or [])}", delta_color="off")
    filas = [{"KPI": str(x).replace("_", " ").capitalize(), "Antes": ka[x], "Después": kn[x], "Δ": kn[x] - ka[x]}
             for x in kn if x in ka and _num(kn[x]) and _num(ka[x]) and abs(kn[x] - ka[x]) > 1e-9]
    if filas:
        with st.expander("KPIs que cambian"):
            st.dataframe(pd.DataFrame(filas).round(2), width="stretch", hide_index=True)
    st.write("**Células activas en el turno, antes:** " + (", ".join(map(str, pa.config_turno_actual)) or "—") +
             "  →  **después:** " + (", ".join(map(str, pn.config_turno_actual)) or "—"))
    if cn_:
        st.error("El nuevo resultado es un plan de contingencia — incumple: " + "; ".join(map(str, pn.incumplimientos)))


# ------------------------------------------------------------------ 0. Planta en tiempo real
with tabs[0]:
    st.subheader("Planta en tiempo real")
    c1, c2, c3, c4 = st.columns([1.3, 0.7, 1.6, 2.4])
    if c2.button("+1 h", key="mas1h", width="stretch", help="Avanza la hora actual una hora"):
        nueva = min(hora_actual + pd.Timedelta(hours=1), HS[-1])
        st.session_state.hora_actual = nueva
        hora_actual = nueva
    opciones_h = HS
    sel = c1.selectbox("Hora actual", opciones_h, index=opciones_h.index(hora_actual) if hora_actual in opciones_h else 0,
                       format_func=lambda t: f"{t:%d/%m %H:%M}")
    st.session_state.hora_actual = pd.Timestamp(sel)
    hora_actual = pd.Timestamp(sel)
    ih = idx_hora(rec, hora_actual)
    turno_h, fecha_turno = turno_fecha(rec, hora_actual)
    c3.markdown(f"**Turno:** {NOMBRE_TURNO.get(turno_h, turno_h)}<br>**Plan calculado desde:** {rec.inicio:%d/%m %H:%M}",
                unsafe_allow_html=True)
    c4.caption("Cada acción recalcula automáticamente el plan desde la hora actual (sin pulsar «Calcular plan») "
               "y muestra el resumen antes / después.")

    with st.expander("Opciones de las paradas (se aplican a los botones Avería / Parada programada)", expanded=False):
        o1, o2, o3 = st.columns(3)
        modo_fin = o1.radio("Fin de la parada", ["Hasta fin del horizonte", "Hasta una hora concreta"], key="modo_fin")
        fin_opts = [h for h in HS if h > hora_actual] or [HS[-1] + pd.Timedelta(hours=1)]
        hasta_sel = o2.selectbox("Hasta", fin_opts, format_func=lambda t: f"{t:%d/%m %H:%M}", key="hasta_parada",
                                 disabled=(modo_fin == "Hasta fin del horizonte"))
        tec_prog = o3.number_input("Técnicos ocupados (parada programada)", min_value=0, value=2, step=1, key="tec_prog")
    hasta_parada = (HS[-1] + pd.Timedelta(hours=1)) if modo_fin == "Hasta fin del horizonte" else pd.Timestamp(hasta_sel)

    try:
        est = rolling.estado_en(esc, rec, hora_actual)
    except Exception as e:  # noqa: BLE001
        est = None
        st.error(f"No se pudo calcular el estado de la planta: {e}")
        with st.expander("Detalle técnico"):
            st.code(traceback.format_exc())

    if est is not None:
        est = est.reset_index(drop=True)
        n_ver = st.session_state.esc_ver
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
                    stock_txt = f"{fnum(st_pieza, 0)} / SS {fnum(ss_c, 0)}"
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
                    if ci != config.CELULA_LOGISTICA:
                        b1, b2 = st.columns(2)
                        parada = estado_c in ("PARADA PROGRAMADA", "AVERÍA")
                        if b1.button("Avería", key=f"averia_{ci}_{n_ver}", width="stretch"):
                            if aplicar_evento_real(rolling.Evento("parada_celula", {
                                    "celula": ci, "desde": hora_actual, "hasta": hasta_parada,
                                    "tipo": "AVERIA", "tecnicos": 0}), f"avería C{ci}"):
                                st.rerun()
                        if b2.button("Parada prog.", key=f"parada_{ci}_{n_ver}", width="stretch"):
                            if aplicar_evento_real(rolling.Evento("parada_celula", {
                                    "celula": ci, "desde": hora_actual, "hasta": hasta_parada,
                                    "tipo": "PROGRAMADA", "tecnicos": int(tec_prog)}), f"parada programada C{ci}"):
                                st.rerun()
                        if st.button("Reactivar", key=f"reactivar_{ci}_{n_ver}", width="stretch", disabled=not parada):
                            if aplicar_evento_real(rolling.Evento("fin_parada", {"celula": ci, "ahora": hora_actual}),
                                                   f"reactivar C{ci}"):
                                st.rerun()
                    else:
                        st.caption("Célula 10: no puede averiarse ni pararse")

    # ---- personal
    st.markdown("##### Personal en la hora actual")
    pers_df = getattr(plan, "personal", None)
    per_n = getattr(plan, "personas", None)
    n_ver = st.session_state.esc_ver

    pers_h = informes.personal_en_slot(rec, plan, ih)

    def _baja(trab, rol, turno_w, etiqueta):
        d_ = {"fecha": fecha_turno, "turno": turno_w or turno_h, "rol": rol, "cantidad": 1}
        if trab:
            d_["trabajador"] = trab
        if aplicar_evento_real(rolling.Evento("baja_personal", d_), etiqueta):
            st.rerun()

    cols_r = st.columns(len(ROLES))
    for col, rol in zip(cols_r, ROLES):
        with col:
            disp = float(plan.recursos[f"{rol}_disp"].iloc[ih])
            d = pers_h[pers_h["rol"].astype(str) == rol] if len(pers_h) else pers_h
            st.markdown(f"**{G.NOMBRE_REC.get(rol, rol)}**")
            if len(d):
                est_ = d["estado"].astype(str).str.upper()
                n_as, n_li, n_ex = int((est_ == "ASIGNADO").sum()), int((est_ == "LIBRE").sum()), int((est_ == "EXCEDENTE").sum())
                st.caption(f"Disponibles: {fnum(disp, 0)} · plantilla: {n_as + n_li} · ocupados: {n_as} · "
                           f"libres: {n_li} · excedente: {n_ex}")
                with st.expander(f"Trabajadores ({len(d)})"):
                    for j, pr in enumerate(d.itertuples()):
                        est_p = str(pr.estado).upper()
                        cel_txt = str(pr.celulas) if str(pr.celulas) else ("libre" if est_p == "LIBRE" else "excedente")
                        carga = getattr(pr, "carga", None)
                        txt = f"<b>{pr.trabajador}</b>: {cel_txt}"
                        if _num(carga) and 0 < carga < 0.999:
                            txt += f" ({fnum(carga, 2)})"
                        if est_p != "ASIGNADO":
                            txt += f" <i>[{est_p.lower()}]</i>"
                        a, b = st.columns([3, 2])
                        a.markdown(f"<small>{txt}</small>", unsafe_allow_html=True)
                        if b.button("Dar de baja", key=f"baja_{pr.trabajador}_{n_ver}", width="stretch"):
                            _baja(str(pr.trabajador), rol, str(getattr(pr, "turno", "") or turno_h),
                                  f"baja de {pr.trabajador}")
            else:
                st.caption(f"Disponibles: {fnum(disp, 0)} · asignados: {fnum(plan.recursos[f'{rol}_usado'].iloc[ih], 1)}")
                if disp > 0 and st.button("Dar de baja 1", key=f"baja1_{rol}_{n_ver}", width="stretch"):
                    _baja(None, rol, turno_h, f"baja de 1 {rol}")
    st.caption(f"Las bajas se registran en el turno {NOMBRE_TURNO.get(turno_h, turno_h)} del {fecha_turno:%d/%m/%Y}. "
               "El detalle por trabajador (recorrido del turno y cuadrante horario) está en la pestaña «Trabajadores».")
    ad = st.session_state.antes_despues
    if ad:
        st.markdown("---")
        mostrar_antes_despues(ad["antes"], ad["despues"], f"Último cambio: {ad['etiqueta']} ({ad['hora']:%d/%m %H:%M})")
    if st.session_state.historial:
        with st.expander("Historial de recomendaciones"):
            st.write([h["etiqueta"] for h in st.session_state.historial])

# ------------------------------------------------------------------ 1. Trabajadores
PALETA_CEL = ["#9FD8B4", "#A8C7F0", "#F4C98B", "#E3B5E8", "#F2A9A0", "#B8E0E6", "#D9D99B", "#C5B8F0",
              "#F0B8CF", "#B5D49A", "#EBCB9E", "#9ECDE0", "#E8A9D6", "#CFE0A8", "#F5D78E", "#A9B8E8"]


def _color_celda(v):
    s = str(v)
    if s in ("libre", "LIBRE"):
        return "background-color:#EEF0F4;color:#6B7280"
    if s in ("excedente", "EXCEDENTE"):
        return "background-color:#FBEFD5;color:#8A6D1D;font-style:italic"
    if s in ("—", ""):
        return "color:#B0B6C3"
    import re
    m = re.search(r"C(\d+)", s)
    if m:
        return f"background-color:{PALETA_CEL[(int(m.group(1)) - 1) % len(PALETA_CEL)]};color:#1b1f3b"
    return ""


with tabs[1]:
    st.subheader("Trabajadores del turno")
    st.caption("Cada trabajador está numerado (turno-rol-nº: M-OP01 = operario 1 de mañana; PK picking, CA carretillero, "
               "MT mantenimiento, CL calidad) y se sigue puesto a puesto; en una misma hora puede cubrir varias células.")
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
        # --- KPIs de plantilla por rol
        filas_k = []
        for r in ROLES:
            dr = pers_t[pers_t["rol"].astype(str) == r]
            if not len(dr):
                continue
            exced = set(dr.groupby("trabajador")["estado"].apply(lambda s: (s == "EXCEDENTE").all()).loc[lambda x: x].index)
            plantilla = dr[~dr["trabajador"].isin(exced)]["trabajador"].nunique()
            n_h = max(dr["_i"].nunique(), 1)
            asig = float((dr["estado"] == "ASIGNADO").sum())
            libres = float((dr["estado"] == "LIBRE").sum())
            filas_k.append({"Rol": G.NOMBRE_REC.get(r, r), "Plantilla necesaria": plantilla,
                            "Disponibles": plantilla + len(exced), "Excedente (reubicable)": len(exced),
                            "Horas-persona asignadas": round(asig, 1), "Horas libres de plantilla": round(libres, 1),
                            "Ocupación de plantilla %": round(100 * asig / max(asig + libres, 1e-9), 0)})
        if filas_k:
            st.markdown("**Plantilla necesaria vs disponible**")
            st.dataframe(pd.DataFrame(filas_k), width="stretch", hide_index=True)
        # --- tabla de trabajadores
        tt = trab[trab["turno"].astype(str) == turno_sel]
        tt = tt[tt["rol"].astype(str).isin(roles_sel)]
        if buscar:
            tt = tt[tt["trabajador"].astype(str).str.contains(buscar.strip(), case=False, regex=False)]
        ahora_ = informes.personal_en_slot(rec, plan, ih).set_index("trabajador") if len(pers_all) else pd.DataFrame()
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
        # --- cuadrante horario trabajador x hora
        d_g = pers_t[pers_t["rol"].astype(str).isin(roles_sel)].copy()
        if buscar:
            d_g = d_g[d_g["trabajador"].astype(str).str.contains(buscar.strip(), case=False, regex=False)]
        if len(d_g):
            ce = d_g["celulas"].fillna("").astype(str)
            ce = ce.where(~(d_g["estado"] == "EXCEDENTE"), "excedente")
            ce = ce.where(~((d_g["estado"] == "LIBRE") & (ce == "")), "libre")
            d_g["_v"] = ce.replace("", "libre")
            d_g["_h"] = d_g["_i"].map(lambda i: HS[min(max(int(i), 0), len(HS) - 1)].strftime("%H:%M"))
            cuad = d_g.pivot_table(index="trabajador", columns="_h", values="_v", aggfunc="first", sort=False)
            cuad = cuad[[c for c in dict.fromkeys(d_g.sort_values("_i")["_h"])]].fillna("—")
            st.markdown("**Cuadrante horario (color = célula)**")
            estilo = cuad.style.map(_color_celda) if hasattr(cuad.style, "map") else cuad.style.applymap(_color_celda)
            st.dataframe(estilo, width="stretch", height=min(700, 38 + 35 * len(cuad)))
        # --- baja de un trabajador concreto
        st.markdown("**Dar de baja a un trabajador**")
        cand = [str(t) for t in tt["trabajador"]] or [str(t) for t in trab[trab["turno"].astype(str) == turno_sel]["trabajador"]]
        b1, b2 = st.columns([2, 1])
        trab_baja = b1.selectbox("Trabajador", cand, key=f"tr_baja_{st.session_state.esc_ver}")
        if b2.button("Dar de baja", key=f"tr_baja_btn_{st.session_state.esc_ver}", width="stretch"):
            rol_b = str(trab[trab["trabajador"].astype(str) == trab_baja]["rol"].iloc[0])
            _t = turno_sel
            _i0 = int(pers_t["_i"].min()) if len(pers_t) else ih
            _ts0 = HS[min(max(_i0, 0), len(HS) - 1)]
            _f_ = _ts0.normalize() - (pd.Timedelta(days=1) if (_t == "N" and _ts0.hour < 6) else pd.Timedelta(0))
            if aplicar_evento_real(rolling.Evento("baja_personal", {
                    "fecha": _f_, "turno": _t, "rol": rol_b, "cantidad": 1, "trabajador": trab_baja}),
                    f"baja de {trab_baja}"):
                st.rerun()

# ------------------------------------------------------------------ 2. Recomendación
with tabs[2]:
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
    # KPIs nuevos: desperdicio de personal y camiones
    k1, k2, k3, k4 = st.columns(4)
    desp = informes.kpis_desperdicio(plan)
    k1.metric("Desperdicio de personal (h-persona)", fnum(sum(v for _, v in desp), 1) if desp else "—",
              help="Σ horas (personas asignadas − personas que las células necesitan). " +
                   "; ".join(f"{n}: {fnum(v, 1)}" for n, v in desp))
    cam = informes.resumen_camiones(rec)
    k2.metric("Camiones en el horizonte (24 h)", fnum(cam["total"], 0) if cam else "—",
              help="Nº de camiones = ceil(m² de la carga / 15 m²) en cada ciclo de expedición (cada 1,5 h).")
    k3.metric("Camiones por ciclo (media / máx.)", f"{fnum(cam['media'], 1)} / {fnum(cam['max'], 0)}" if cam else "—")
    k4.metric("Camiones por día (previsto)", fnum(cam["por_dia"], 0) if cam else "—")
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
            st.dataframe(pd.DataFrame(fi, columns=["Indicador", "Plan", "Plan manual de referencia", "Diferencia"]),
                         width="stretch", hide_index=True,
                         column_config={"Plan manual de referencia": st.column_config.TextColumn(
                             "Plan manual de referencia", help=DEF_PLAN_MANUAL)})
            st.caption("Diferencia negativa en horas / m² / kWh = ahorro frente al plan manual.")
            for dd in (exp.get("impacto", {}) or {}).get("delta_vs_alternativas", []) or []:
                st.markdown(f"- Frente a **{dd['nombre']}**: {fnum(dd['puntuacion'], 2)} puntos de diferencia")
        else:
            st.write("Sin datos de comparación.")
        with st.expander("¿Qué es el plan manual de referencia?"):
            st.write(DEF_PLAN_MANUAL)

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

# ------------------------------------------------------------------ 2. KPIs
with tabs[3]:
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
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.energia(rec, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### KPIs por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")

# ------------------------------------------------------------------ 3. Overview 24 h
with tabs[4]:
    st.subheader("Overview de 24 horas")
    pc(G.gantt(rec, esc, plan), width="stretch")
    if isinstance(plan.resumen_turnos, pd.DataFrame) and len(plan.resumen_turnos):
        st.markdown("#### Resumen por turno")
        st.dataframe(plan.resumen_turnos, width="stretch")
    pc(G.stock(rec, esc, plan, ss, k_colchon), width="stretch")
    pc(G.almacen(rec, plan), width="stretch")
    pc(G.recursos(rec, plan), width="stretch")

# ------------------------------------------------------------------ 4. Alternativas
with tabs[5]:
    st.subheader("Top 1/2/3 y plan manual de referencia")
    with st.expander("¿Qué es el plan manual de referencia?"):
        st.write(DEF_PLAN_MANUAL)
    opciones = [(f"Top {i + 1}", p) for i, p in enumerate(rec.top)]
    if rec.contingencia is not None and not rec.top:
        opciones.append(("Contingencia", rec.contingencia))
    if rec.baseline is not None:
        opciones.append(("Plan manual", rec.baseline))
    filas = []
    for n, p in opciones:
        filas.append({"Plan": n, "Estado": str(p.estado), "Puntuación": round(p.puntuacion, 2),
                      "Idoneidad %": "—" if p.idoneidad is None else fnum(p.idoneidad, 1),
                      "Células turno actual": ", ".join(str(int(c)) for c in (p.config_turno_actual or [])) or "—",
                      "m² medios": round(float(np.mean(p.espacio.values)), 1),
                      "kWh": round(float(np.sum(p.energia_kwh.values)), 1)})
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
    colores = [G.VERDE if n.startswith("Top") else (G.ROJO if n == "Contingencia" else G.GRIS) for n, _ in opciones]
    pc(G.comparar_barras([n for n, _ in opciones], [p.puntuacion for _, p in opciones], "Puntuación", colores),
       width="stretch")
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
    if rec.baseline is not None and "Plan manual" in comp.columns:
        for n in nombres:
            if n != "Plan manual":
                comp[f"Δ {n} − plan manual"] = comp[n] - comp["Plan manual"]
    st.markdown("#### KPIs lado a lado")
    st.dataframe(comp.round(2), width="stretch")
    sel_g = st.selectbox("Ver Gantt de", nombres)
    pc(G.gantt(rec, esc, dict(opciones)[sel_g]), width="stretch")

# ------------------------------------------------------------------ 5. Contingencia
with tabs[6]:
    st.subheader("Contingencia: avería de una célula")
    st.caption("Simula la avería de una célula desde una hora (hasta una hora opcional), con el stock del plan vigente, "
               "y muestra a dónde van las personas, qué máquinas se activan y cuándo se agota la pieza.")
    prod_ids = [int(c) for c in esc.celulas["celula"] if int(c) != config.CELULA_LOGISTICA]
    cc1, cc2, cc3 = st.columns([1, 1.4, 1.8])
    cel_c = cc1.selectbox("Célula averiada", prod_ids, index=prod_ids.index(14) if 14 in prod_ids else 0,
                          format_func=lambda c: f"Célula {c} ({tp.get(c, '?')})", key="cont_cel")
    desde_opts = [h for h in HS]
    desde_c = cc2.selectbox("Desde", desde_opts, index=desde_opts.index(hora_actual) if hora_actual in desde_opts else 0,
                            format_func=lambda t: f"{t:%d/%m %H:%M}", key=f"cont_desde_{hora_actual:%d%H}")
    usar_hasta = cc3.checkbox("Indicar hasta cuándo dura la avería", value=False, key="cont_usar_hasta")
    hasta_opts = [h for h in HS if h > pd.Timestamp(desde_c)] or [HS[-1] + pd.Timedelta(hours=1)]
    hasta_c = cc3.selectbox("Hasta", hasta_opts, format_func=lambda t: f"{t:%d/%m %H:%M}", disabled=not usar_hasta,
                            key="cont_hasta")
    hasta_val = pd.Timestamp(hasta_c) if usar_hasta else None
    if st.button("Calcular contingencia", type="primary", key="cont_calc"):
        try:
            with st.spinner("Calculando el plan de contingencia…"):
                res = rolling.contingencia_celula(esc, rec, int(cel_c), pd.Timestamp(desde_c), hasta_val)
            st.session_state.cont = {"res": res, "celula": int(cel_c), "desde": pd.Timestamp(desde_c),
                                     "hasta": hasta_val, "aplicada": False}
        except Exception as e:  # noqa: BLE001
            st.session_state.cont = None
            st.error(f"No se pudo calcular la contingencia: {e}")
            with st.expander("Detalle técnico"):
                st.code(traceback.format_exc())
    cont = st.session_state.cont
    if cont:
        res = cont["res"]
        st.markdown(f"##### Avería de la célula {cont['celula']} desde {cont['desde']:%d/%m %H:%M}"
                    + (f" hasta {cont['hasta']:%d/%m %H:%M}" if cont["hasta"] is not None else " (hasta fin de horizonte)"))
        resumen = res.get("resumen") or []
        if resumen:
            st.markdown("<div class='resumen'><b>Qué hacer</b><br>" + "<br>".join(f"• {t}" for t in resumen) + "</div>",
                        unsafe_allow_html=True)
        ag = res.get("agotamiento")
        a1, a2, a3 = st.columns(3)
        if ag:
            a1.metric("Pieza bajo stock de seguridad", fts(ag.get("hora_bajo_ss")))
            a2.metric("Pieza sin stock", fts(ag.get("hora_sin_stock")))
        else:
            a1.metric("Pieza bajo stock de seguridad", "No ocurre")
            a2.metric("Pieza sin stock", "No ocurre")
        a3.caption("Agotamiento de la pieza de la célula averiada si no se repara.")
        ta, tb = st.columns(2)
        with ta:
            st.markdown("**Reubicación de personas**")
            ru = res.get("reubicacion")
            if isinstance(ru, pd.DataFrame) and len(ru):
                d = ru.copy()
                for c_ in ("desde", "hasta"):
                    if c_ in d:
                        d[c_] = d[c_].map(lambda x: fts(x, "%d/%m %H:%M"))
                st.dataframe(d.rename(columns={"persona": "Persona", "rol": "Rol", "de_celula": "De célula",
                                               "a_celulas": "A células", "desde": "Desde", "hasta": "Hasta"}),
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
        mostrar_antes_despues(res["rec_antes"], res["rec_despues"], "KPIs antes / después de la avería")
        if st.button("Aplicar como incidencia real", key="cont_aplicar",
                     help="Registra la avería en el escenario y recalcula el plan vigente desde esa hora."):
            fin = hasta_val if hasta_val is not None else HS[-1] + pd.Timedelta(hours=1)
            if aplicar_evento_real(rolling.Evento("parada_celula", {
                    "celula": cont["celula"], "desde": cont["desde"], "hasta": fin, "tipo": "AVERIA", "tecnicos": 0}),
                    f"avería C{cont['celula']} (contingencia)", ahora=cont["desde"]):
                cont["aplicada"] = True
                st.session_state.cont = cont
                st.rerun()
        if cont.get("aplicada"):
            st.success("Avería aplicada como incidencia real: el plan vigente ya la incluye.")

# ------------------------------------------------------------------ 6. Entrada manual
def _tipar(df: pd.DataFrame, fechas=(), enteros=(), numeros=(), textos=()) -> pd.DataFrame:
    df = df.copy()
    for c in fechas:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    for c in numeros:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in enteros:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in textos:
        if c in df:
            df[c] = df[c].astype(object)
    return df.reset_index(drop=True)


def _sig(*dfs) -> str:
    return "||".join(d.reset_index(drop=True).astype(str).to_csv(index=False) for d in dfs)


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
    return df.reset_index(drop=True)


with tabs[7]:
    st.subheader("Entrada manual")
    st.caption("Datos que cambian a diario: bajas por turno, paradas de máquinas y demanda semanal / diaria por tipo "
               "(cantidad que se pide de **cada** pieza del tipo).")
    st.checkbox("Recalcular automáticamente al cambiar los datos", key="auto")
    ev = st.session_state.esc_ver
    cel_ids = [int(c) for c in esc.celulas["celula"] if int(c) != config.CELULA_LOGISTICA]

    t_baj, t_par, t_dem = st.tabs(["Bajas por turno", "Paradas de máquinas", "Demanda"])
    with t_baj:
        st.caption("Personas de baja de cada rol en cada turno (disponible = estándar − bajas; sin fila: estándar − 5 % de absentismo).")
        d0 = _tipar(get_hoja(esc, "Bajas"), fechas=["fecha"], numeros=ROLES, textos=["turno"])
        bajas_ed = st.data_editor(d0, num_rows="dynamic", width="stretch", key=f"ed_bajas_{ev}", column_config={
            "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "turno": st.column_config.SelectboxColumn("Turno", options=["M", "T", "N"]),
            **{r: st.column_config.NumberColumn(G.NOMBRE_REC.get(r, r), min_value=0, step=1) for r in ROLES}})
    with t_par:
        st.caption("Tipo PROGRAMADA (con técnicos de mantenimiento ocupados) o AVERIA. La célula 10 no puede pararse.")
        d0 = _tipar(get_hoja(esc, "Paradas"), fechas=["desde", "hasta"], numeros=["celula", "tecnicos"], textos=["tipo"])
        par_ed = st.data_editor(d0, num_rows="dynamic", width="stretch", key=f"ed_paradas_{ev}", column_config={
            "celula": st.column_config.SelectboxColumn("Célula", options=cel_ids),
            "desde": st.column_config.DatetimeColumn("Desde", format="DD/MM/YYYY HH:mm"),
            "hasta": st.column_config.DatetimeColumn("Hasta", format="DD/MM/YYYY HH:mm"),
            "tipo": st.column_config.SelectboxColumn("Tipo", options=["PROGRAMADA", "AVERIA"]),
            "tecnicos": st.column_config.NumberColumn("Técnicos", min_value=0, step=1)})
    with t_dem:
        h1, h2, h3, h4 = st.columns([1, 1.2, 1.2, 1])
        coches = h1.number_input("Coches/día", min_value=0, value=1500, step=100, key="coches_dia")
        ratio = h2.number_input("Ratio COMB : VE", min_value=0.0, value=2.0, step=0.5, key="ratio_cv")
        modo_d = h3.radio("Rellenar", ["Semana", "Un día"], horizontal=True, key="modo_dem")
        fecha_d = h4.date_input("Fecha / lunes", value=pd.Timestamp(rec.inicio).date(), key="fecha_dem")
        if st.button("Rellenar desde coches/día", key="rellenar_dem"):
            try:
                pve, pcomb = datos.demanda_desde_coches(float(coches), float(ratio))
                nuevo = esc.copiar()
                f = pd.Timestamp(fecha_d).normalize()
                if modo_d == "Semana":
                    lunes = f - pd.Timedelta(days=f.weekday())
                    dsem = _tipar(get_hoja(nuevo, "DemandaSemanal"), fechas=["semana_inicio"])
                    dsem = dsem[dsem["semana_inicio"] != lunes]
                    dsem = pd.concat([dsem, pd.DataFrame([{"semana_inicio": lunes, "piezas_ve": pve * 5,
                                                           "piezas_comb": pcomb * 5}])], ignore_index=True)
                    set_hoja(nuevo, "DemandaSemanal", dsem)
                else:
                    dd = _tipar(get_hoja(nuevo, "CorreccionDiaria"), fechas=["fecha"])
                    dd = dd[dd["fecha"] != f]
                    dd = pd.concat([dd, pd.DataFrame([{"fecha": f, "piezas_ve": pve, "piezas_comb": pcomb}])],
                                   ignore_index=True)
                    set_hoja(nuevo, "CorreccionDiaria", dd)
                st.session_state.msg = None
                if st.session_state.auto:
                    calcular(nuevo, rec.inicio, "demanda desde coches/día", conservar_hora=True)
                else:
                    st.session_state.esc = nuevo
                    st.session_state.esc_ver += 1
                    st.info("Datos actualizados. Pulsa «Aplicar y recalcular» para recalcular el plan.")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo rellenar la demanda: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
        st.caption("2 : 1 significa el doble de piezas de combustión que eléctricas. La demanda semanal se reparte en 5 días laborables.")
        cs, cd = st.columns(2)
        with cs:
            st.markdown("**Demanda semanal (piezas por tipo)**")
            d0 = _tipar(get_hoja(esc, "DemandaSemanal"), fechas=["semana_inicio"], numeros=["piezas_ve", "piezas_comb"])
            dem_ed = st.data_editor(d0, num_rows="dynamic", width="stretch", key=f"ed_dsem_{ev}", column_config={
                "semana_inicio": st.column_config.DateColumn("Lunes de la semana", format="DD/MM/YYYY"),
                "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza)", min_value=0),
                "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza)", min_value=0)})
        with cd:
            st.markdown("**Corrección diaria (piezas por tipo)**")
            d0 = _tipar(get_hoja(esc, "CorreccionDiaria"), fechas=["fecha"], numeros=["piezas_ve", "piezas_comb"])
            cor_ed = st.data_editor(d0, num_rows="dynamic", width="stretch", key=f"ed_dcor_{ev}", column_config={
                "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza)", min_value=0),
                "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza)", min_value=0)})

    # ---- detección de cambios y recálculo automático
    try:
        n_baj = _limpia_bajas(bajas_ed)
        n_par = _limpia_paradas(par_ed)
        n_sem = _limpia_dem(dem_ed, "semana_inicio")
        n_cor = _limpia_dem(cor_ed, "fecha")
        sig_actual = _sig(n_baj, n_par, n_sem, n_cor)
    except Exception as e:  # noqa: BLE001
        n_baj = n_par = n_sem = n_cor = None
        sig_actual = None
        st.warning(f"Completa las filas incompletas de las tablas ({e}).")

    def aplicar_manual():
        nuevo = esc.copiar()
        set_hoja(nuevo, "Bajas", n_baj)
        set_hoja(nuevo, "Paradas", n_par)
        set_hoja(nuevo, "DemandaSemanal", n_sem)
        set_hoja(nuevo, "CorreccionDiaria", n_cor)
        calcular(nuevo, rec.inicio, "datos manuales", conservar_hora=True)

    if sig_actual is not None:
        if st.session_state.manual_ver != ev or st.session_state.manual_sig is None:
            st.session_state.manual_sig = sig_actual  # línea base: aún sin cambios del usuario
            st.session_state.manual_ver = ev
        cambiado = sig_actual != st.session_state.manual_sig
        if cambiado and st.session_state.auto:
            try:
                st.session_state.manual_sig = sig_actual
                aplicar_manual()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ in ("RerunException", "StopException"):
                    raise
                st.error(f"Error al recalcular: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
        elif cambiado:
            st.warning("Hay cambios sin aplicar.")
            if st.button("Aplicar y recalcular", type="primary", key="aplicar_manual"):
                try:
                    st.session_state.manual_sig = sig_actual
                    aplicar_manual()
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    if type(e).__name__ in ("RerunException", "StopException"):
                        raise
                    st.error(f"Error al recalcular: {e}")
                    with st.expander("Detalle técnico"):
                        st.code(traceback.format_exc())

    with st.expander("Parámetros constantes (sólo lectura)"):
        st.markdown("**Células**")
        st.dataframe(esc.celulas, width="stretch", hide_index=True)
        c1_, c2_ = st.columns(2)
        c1_.markdown("**Turnos (recursos estándar)**")
        c1_.dataframe(esc.turnos, width="stretch", hide_index=True)
        c2_.markdown("**Almacén**")
        c2_.dataframe(esc.almacen, width="stretch", hide_index=True)
        st.markdown("**Parámetros**")
        st.dataframe(pd.DataFrame({"parámetro": list(esc.parametros.keys()), "valor": list(esc.parametros.values()),
                                   "descripción": [esc.descripciones.get(k, "") for k in esc.parametros]}),
                     width="stretch", hide_index=True)
    with st.expander("Datos operativos (stock actual y expediciones reales)"):
        st.caption("Stock de cada pieza al inicio del plan y cargas de camión realmente expedidas.")
        so = _tipar(get_hoja(esc, "StockActual"), numeros=["celula", "piezas"])
        stock_ed = st.data_editor(so, num_rows="dynamic", width="stretch", key=f"ed_stock_{ev}")
        ex = _tipar(get_hoja(esc, "Expediciones"), fechas=["fecha_hora"], numeros=["piezas_ve", "piezas_comb"])
        exp_ed = st.data_editor(ex, num_rows="dynamic", width="stretch", key=f"ed_exp_{ev}", column_config={
            "fecha_hora": st.column_config.DatetimeColumn("Fecha y hora", format="DD/MM/YYYY HH:mm"),
            "piezas_ve": st.column_config.NumberColumn("Piezas VE (de cada pieza)"),
            "piezas_comb": st.column_config.NumberColumn("Piezas COMB (de cada pieza)")})
        if st.button("Aplicar datos operativos y recalcular", key="aplicar_oper"):
            try:
                nuevo = esc.copiar()
                set_hoja(nuevo, "StockActual", stock_ed.dropna(subset=["celula"]))
                set_hoja(nuevo, "Expediciones", exp_ed.dropna(subset=["fecha_hora"]))
                calcular(nuevo, rec.inicio, "datos operativos", conservar_hora=True)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ in ("RerunException", "StopException"):
                    raise
                st.error(f"Error al recalcular: {e}")
                with st.expander("Detalle técnico"):
                    st.code(traceback.format_exc())
    try:
        tmpx = Path(tempfile.mkdtemp()) / "escenario.xlsx"
        datos.guardar_entrada(esc, str(tmpx))
        st.download_button("Descargar escenario (Excel)", tmpx.read_bytes(), file_name="escenario_kwd.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:  # noqa: BLE001
        st.warning(f"No se pudo preparar la descarga: {e}")

# ------------------------------------------------------------------ 7. Semana
with tabs[8]:
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
        for c in [c for c in num.columns if any(s in c.lower() for s in ("puntuacion", "puntuación", "m2", "m²", "kwh"))][:4]:
            x = sem["turno"].astype(str) if "turno" in sem else sem.index.astype(str)
            fig = px.bar(sem, x=x, y=c, title=c.replace("_", " ").capitalize(), color_discrete_sequence=[NAVY])
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10))
            pc(fig, width="stretch")
        st.download_button("Descargar tabla (CSV)", sem.to_csv(index=False).encode("utf-8-sig"),
                           file_name="simulacion_semana.csv", mime="text/csv")

# ------------------------------------------------------------------ 8. Informe
with tabs[9]:
    st.subheader("Informe PDF para dirección")
    st.write("Incluye resumen ejecutivo, células y personal del turno, camiones por ciclo, KPIs, alternativas, "
             "contingencia (si procede), definición del plan manual, gráficos, alertas y supuestos.")
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

# ------------------------------------------------------------------ pie
with st.expander("Supuestos (FLAGS) a revisar"):
    for k_, v_ in (rec.flags or config.FLAGS).items():
        st.markdown(f"**{k_}** — {v_}")
