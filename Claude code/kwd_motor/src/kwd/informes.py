"""Generador del informe PDF (ReportLab + matplotlib). Todo el texto en español."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,  # noqa: E402
                                SimpleDocTemplate, Spacer, Table, TableStyle)

NAVY = "#1F2A6B"
VERDE = "#2E9E5B"
AZUL = "#2F6FD0"
ROJO = "#C0392B"
AMBAR = "#E0A100"
GRIS = "#6B7280"
GRIS_LOG = "#7A869A"
COLOR_TIPO = {"VE": VERDE, "COMB": AZUL}
COLOR_ESTADO = {"OPTIMO": VERDE, "FACTIBLE": AMBAR, "INVIABLE": ROJO}
NOMBRE_REC = {"operarios": "Operarios", "picking": "Picking", "carretilleros": "Carretilleros",
              "mto": "Mantenimiento", "calidad": "Calidad"}
NOMBRE_CRIT = {"R": "Recursos", "S": "Espacio", "Q": "Calidad + Mto", "B": "Stock seguridad",
               "E": "Energía"}
PESOS = {"R": 50, "S": 20, "Q": 15, "B": 10, "E": 5}


# ----------------------------------------------------------------- utilidades
def _tipos(esc) -> dict:
    df = esc.celulas
    return {int(r["celula"]): str(r["tipo"]) for _, r in df.iterrows()}


def _slots(rec) -> pd.DataFrame:
    s = rec.horizonte.slots.reset_index(drop=True)
    return s


def _horas(rec) -> list:
    s = _slots(rec)
    return [pd.Timestamp(x) for x in s["inicio"]]


def _plan_principal(rec):
    if rec.top:
        return rec.top[0], False
    return rec.contingencia, True


def _f(x, d=1):
    try:
        return f"{float(x):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(x)


def _hhmm(ts) -> str:
    return pd.Timestamp(ts).strftime("%H:%M")


def _shade_solar(ax, horas, slots):
    ini = pd.Timestamp(horas[0])
    if "factor_energia" in slots:
        for i, (h, f) in enumerate(zip(horas, slots["factor_energia"])):
            if f < 1.0:
                ax.axvspan(h, h + pd.Timedelta(hours=1), color="#FFE9A8", alpha=0.45, lw=0, zorder=0)
    return ini


def _marcar_turnos(ax, horas, slots):
    prev = None
    for h, t in zip(horas, slots["turno"]):
        if prev is not None and t != prev:
            ax.axvline(h, color=GRIS, ls="--", lw=0.8, zorder=1)
        prev = t


def _fmt_x(ax):
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H"))
    ax.tick_params(labelsize=7)


# ------------------------------------------------------------------- gráficos
def _grafico_gantt(rec, esc, plan, ruta):
    horas = _horas(rec)
    slots = _slots(rec)
    tipos = _tipos(esc)
    cel = list(plan.activacion.columns)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    _shade_solar(ax, horas, slots)
    for yi, c in enumerate(cel):
        col = GRIS_LOG if int(c) == 10 else COLOR_TIPO.get(tipos.get(int(c), "COMB"), AZUL)
        for h_i, h in enumerate(horas):
            if plan.activacion.iloc[h_i][c] > 0.5:
                u = float(plan.uso.iloc[h_i][c]) if hasattr(plan, "uso") and plan.uso is not None else 1.0
                ax.barh(yi, 1 / 24, left=mdates.date2num(h), height=0.7, color=col,
                        alpha=0.35 + 0.65 * min(max(u, 0), 1), edgecolor="white", lw=0.4, zorder=3)
    _marcar_turnos(ax, horas, slots)
    ax.set_yticks(range(len(cel)))
    ax.set_yticklabels([(f"Célula {int(c)} (Logística)" if int(c) == 10 else f"Célula {int(c)} ({tipos.get(int(c), '?')})") for c in cel], fontsize=7)
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.set_xlim(mdates.date2num(horas[0]), mdates.date2num(horas[-1] + pd.Timedelta(hours=1)))
    _fmt_x(ax)
    ax.set_xlabel("Hora del día (zona amarilla = franja con factor energético reducido; línea discontinua = cambio de turno)", fontsize=7)
    ax.set_title("Activación de células en las próximas 24 h", fontsize=10, color=NAVY, loc="left")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=VERDE, label="VE"), Patch(color=AZUL, label="Combustión"), Patch(color=GRIS_LOG, label="Logística")],
              fontsize=7, loc="upper right", ncol=3)
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def _grafico_stock(rec, esc, plan, ruta):
    from kwd import datos
    horas = _horas(rec)
    slots = _slots(rec)
    ss = datos.ss_por_celula(esc)
    cols = list(plan.stock.columns)
    n = len(cols)
    nc = 3
    nf = int(np.ceil(n / nc))
    fig, axs = plt.subplots(nf, nc, figsize=(10, 1.9 * nf), sharex=True, squeeze=False)
    tipos = _tipos(esc)
    k = float(esc.parametros.get("colchon_ss", 0.10))
    for i, c in enumerate(cols):
        ax = axs[i // nc][i % nc]
        _shade_solar(ax, horas, slots)
        ax.plot(horas, plan.stock[c].values, color=COLOR_TIPO.get(tipos.get(int(c), "COMB"), AZUL), lw=1.6)
        ax.axhline(ss[int(c)], color=ROJO, ls="--", lw=1)
        ax.axhline(ss[int(c)] * (1 + k), color=AMBAR, ls=":", lw=1)
        ax.set_title(f"Célula {int(c)} ({tipos.get(int(c), '?')})", fontsize=7, loc="left")
        _fmt_x(ax)
        ax.tick_params(labelsize=6)
    for j in range(n, nf * nc):
        axs[j // nc][j % nc].axis("off")
    fig.suptitle("Stock por pieza (piezas) · rojo = stock de seguridad · ámbar = colchón objetivo",
                 fontsize=9, color=NAVY, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def _grafico_almacen(rec, esc, plan, ruta):
    horas = _horas(rec)
    slots = _slots(rec)
    A = 800.0
    fig, ax = plt.subplots(figsize=(10, 3))
    _shade_solar(ax, horas, slots)
    ax.fill_between(horas, plan.espacio.values, color=NAVY, alpha=0.25)
    ax.plot(horas, plan.espacio.values, color=NAVY, lw=1.8, label="m² ocupados")
    ax.axhline(A, color=ROJO, ls="--", lw=1.2, label="Capacidad 800 m²")
    ax.axhline(0.9 * A, color=AMBAR, ls=":", lw=1.2, label="Umbral de alerta 90 %")
    _marcar_turnos(ax, horas, slots)
    ax.set_ylim(0, max(A * 1.1, float(np.nanmax(plan.espacio.values)) * 1.05))
    _fmt_x(ax)
    ax.set_ylabel("m²", fontsize=8)
    ax.set_title("Ocupación del almacén de producto terminado", fontsize=10, color=NAVY, loc="left")
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def _grafico_recursos(rec, esc, plan, ruta):
    horas = _horas(rec)
    slots = _slots(rec)
    recs = ["operarios", "picking", "carretilleros", "mto", "calidad"]
    fig, axs = plt.subplots(1, 5, figsize=(10, 2.8), sharex=True)
    for ax, r in zip(axs, recs):
        _shade_solar(ax, horas, slots)
        u = plan.recursos[f"{r}_usado"].values
        d = plan.recursos[f"{r}_disp"].values
        ax.step(horas, d, where="post", color=ROJO, ls="--", lw=1.2, label="Disponible")
        ax.fill_between(horas, u, step="post", color=NAVY, alpha=0.6, label="Usado")
        ax.set_title(NOMBRE_REC[r], fontsize=8, color=NAVY)
        _fmt_x(ax)
        ax.tick_params(labelsize=6)
    axs[0].legend(fontsize=6, loc="lower left")
    fig.suptitle("Recursos usados vs disponibles por hora", fontsize=9, color=NAVY, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def _grafico_energia(rec, esc, plan, ruta):
    horas = _horas(rec)
    slots = _slots(rec)
    fig, ax = plt.subplots(figsize=(10, 3))
    _shade_solar(ax, horas, slots)
    cols = [COLOR_TIPO["VE"] if f < 1 else (ROJO if f > 1 else NAVY) for f in slots["factor_energia"]]
    ax.bar([h + pd.Timedelta(minutes=30) for h in horas], plan.energia_kwh.values,
           width=1 / 24 * 0.85, color=cols)
    _marcar_turnos(ax, horas, slots)
    _fmt_x(ax)
    ax.set_ylabel("kWh", fontsize=8)
    ax.set_title("Consumo energético horario (verde = franja solar, rojo = franja nocturna)",
                 fontsize=10, color=NAVY, loc="left")
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ KPIs
def _kpi(plan, *claves, default=None):
    k = plan.kpis or {}
    for c in claves:
        if c in k and k[c] is not None:
            return k[c]
    return default


def kpi_lista(plan) -> list:
    """Lista curada de KPIs (etiqueta, valor formateado, grupo)."""
    k = plan.kpis or {}
    g = lambda n, d=1, suf="": (_f(k[n], d) + suf) if k.get(n) is not None else "—"  # noqa: E731
    out = [
        ("Demanda cubierta", g("demanda_cubierta_pct", 1, " %"), "Demanda"),
        ("Chasis VE a expedir / cubiertos", f"{g('chasis_ve_a_expedir', 0)} / {g('chasis_ve_cubiertos', 0)}", "Demanda"),
        ("Chasis COMB a expedir / cubiertos", f"{g('chasis_comb_a_expedir', 0)} / {g('chasis_comb_cubiertos', 0)}", "Demanda"),
    ]
    for r, n in (("operarios", "Operarios"), ("picking", "Picking"), ("carretilleros", "Carretilleros"),
                 ("mto", "Mantenimiento"), ("calidad", "Calidad")):
        out.append((f"{n}: horas usadas", g(f"{r}_horas", 1, " h"), "Recursos"))
        out.append((f"{n}: ocupación media / pico",
                    f"{g(f'{r}_ocup_media_pct', 0, ' %')} / {g(f'{r}_ocup_pico_pct', 0, ' %')}", "Recursos"))
    out += [
        ("Almacén: m² medios", f"{g('m2_medio', 0)} m² ({g('m2_medio_pct', 0, ' %')})", "Almacén"),
        ("Almacén: m² pico", f"{g('m2_pico', 0)} m² ({g('m2_pico_pct', 0, ' %')})", "Almacén"),
        ("Energía total (red)", g("kwh_total", 0, " kWh"), "Energía"),
        ("Energía en franja solar", g("kwh_solar_pct", 0, " %"), "Energía"),
        ("Energía nocturna (bruta)", g("kwh_noche", 0, " kWh"), "Energía"),
        ("Piezas producidas", g("piezas_producidas", 0), "Producción"),
        ("Horas-célula activas", g("horas_celula", 0, " h"), "Producción"),
    ]
    sm = k.get("stock_min_vs_ss") or {}
    if sm:
        peor = min(sm, key=lambda c: sm[c])
        out.append(("Stock mínimo vs SS (peor pieza)", f"{_f(sm[peor], 2)}× SS (célula {peor})", "Stock"))
    cob = {c: v for c, v in (k.get("cobertura_horas") or {}).items() if v is not None}
    if cob:
        peor = min(cob, key=lambda c: cob[c])
        out.append(("Cobertura mínima de stock", f"{_f(cob[peor], 1)} h (célula {peor})", "Stock"))
    return out


def filas_kpi(plan) -> list:
    return [(a, b) for a, b, _ in kpi_lista(plan)]


def filas_impacto(rec) -> list:
    """Filas (indicador, plan, referencia, diferencia) a partir de explicacion['impacto']."""
    imp = (rec.explicacion or {}).get("impacto", {}) or {}
    p, b = imp.get("plan", {}), imp.get("baseline", {})
    etiquetas = [("puntuacion", "Puntuación", 1), ("horas_operario", "Horas-operario", 1),
                 ("horas_picking", "Horas-picking", 1), ("horas_carretillero", "Horas-carretillero", 1),
                 ("m2_medio", "m² medios almacén", 1), ("m2_pico", "m² pico almacén", 1),
                 ("kwh_total", "kWh (red)", 0), ("kwh_solar_pct", "% energía en franja solar", 1),
                 ("demanda_cubierta_pct", "Demanda cubierta (%)", 1)]
    filas = []
    for clave, nombre, d in etiquetas:
        if clave in p and clave in b:
            filas.append((nombre, _f(p[clave], d), _f(b[clave], d), _f(p[clave] - b[clave], d)))
    return filas

# ------------------------------------------------------------------ PDF
def _estilos():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1k", parent=ss["Heading1"], textColor=colors.HexColor(NAVY), fontSize=15, spaceAfter=6))
    ss.add(ParagraphStyle("H2k", parent=ss["Heading2"], textColor=colors.HexColor(NAVY), fontSize=11.5,
                          spaceBefore=8, spaceAfter=4))
    ss.add(ParagraphStyle("Cuerpo", parent=ss["BodyText"], fontSize=9, leading=12))
    ss.add(ParagraphStyle("Peq", parent=ss["BodyText"], fontSize=7.5, leading=9.5))
    ss.add(ParagraphStyle("Celda", parent=ss["BodyText"], fontSize=8, leading=9.5))
    ss.add(ParagraphStyle("CeldaH", parent=ss["BodyText"], fontSize=8, leading=9.5, textColor=colors.white,
                          fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("Portada", parent=ss["Title"], textColor=colors.white, fontSize=22, alignment=TA_CENTER))
    return ss


def _tabla(datos, anchos=None, ss=None, cabecera=True, zebra=True):
    ss = ss or _estilos()
    filas = []
    for i, f in enumerate(datos):
        st = ss["CeldaH"] if (i == 0 and cabecera) else ss["Celda"]
        filas.append([Paragraph(str(c), st) for c in f])
    t = Table(filas, colWidths=anchos, repeatRows=1 if cabecera else 0)
    est = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD2E0")),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    if cabecera:
        est.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)))
    if zebra:
        for i in range(1, len(filas)):
            if i % 2 == 0:
                est.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F2F4FA")))
    t.setStyle(TableStyle(est))
    return t


def _df_a_tabla(df: pd.DataFrame, ss, anchos=None, max_filas=60):
    df = df.head(max_filas)
    datos = [[str(c) for c in df.columns]]
    for _, r in df.iterrows():
        datos.append([_f(v, 1) if isinstance(v, (float, np.floating)) else str(v) for v in r.values])
    return _tabla(datos, anchos, ss)


def _pie_cabecera(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(colors.HexColor(NAVY))
    canvas.rect(0, h - 1.1 * cm, w, 1.1 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(1.5 * cm, h - 0.72 * cm, "KWD Automotive · Motor de decisión de producción")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 1.5 * cm, h - 0.72 * cm, getattr(doc, "_etiqueta", ""))
    canvas.setFillColor(colors.HexColor(GRIS))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(1.5 * cm, 0.8 * cm, f"Generado el {datetime.now():%d/%m/%Y %H:%M} · documento de apoyo a la decisión")
    canvas.drawRightString(w - 1.5 * cm, 0.8 * cm, f"Página {doc.page}")
    canvas.restoreState()


def _turno_nombre(t):
    return {"M": "Mañana (06–14)", "T": "Tarde (14–22)", "N": "Noche (22–06)"}.get(str(t), str(t))


def _ventanas(serie_bool, horas):
    """Devuelve texto con tramos horarios contiguos donde serie_bool es True."""
    tramos, ini = [], None
    for i, v in enumerate(list(serie_bool)):
        if v and ini is None:
            ini = i
        if (not v) and ini is not None:
            tramos.append((ini, i))
            ini = None
    if ini is not None:
        tramos.append((ini, len(horas)))
    return ", ".join(f"{_hhmm(horas[a])}–{_hhmm(horas[b - 1] + pd.Timedelta(hours=1))}" for a, b in tramos) or "—"


def generar_informe_pdf(rec, esc, ruta) -> str:
    ruta = str(ruta)
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    ss = _estilos()
    plan, es_contingencia = _plan_principal(rec)
    horas = _horas(rec)
    slots = _slots(rec)
    tipos = _tipos(esc)
    inicio = pd.Timestamp(rec.inicio)
    turno_act = str(slots["turno"].iloc[0])
    ancho = A4[0] - 3 * cm
    est = "CONTINGENCIA (INVIABLE)" if es_contingencia else str(plan.estado)
    col_est = COLOR_ESTADO.get(str(plan.estado), GRIS)

    tmp = tempfile.mkdtemp(prefix="kwd_inf_")
    graf = {}
    for nombre, fn in [("gantt", _grafico_gantt), ("stock", _grafico_stock), ("almacen", _grafico_almacen),
                       ("recursos", _grafico_recursos), ("energia", _grafico_energia)]:
        p = os.path.join(tmp, f"{nombre}.png")
        try:
            fn(rec, esc, plan, p)
            graf[nombre] = p
        except Exception as e:  # el informe no debe caer por un gráfico
            graf[nombre] = None
            graf[nombre + "_err"] = str(e)

    def img(nombre, alto_max=None):
        p = graf.get(nombre)
        if not p:
            return Paragraph(f"(Gráfico '{nombre}' no disponible: {graf.get(nombre + '_err', '')})", ss["Peq"])
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(p).getSize()
        w = ancho
        h = w * ih / iw
        if alto_max and h > alto_max:
            h = alto_max
            w = h * iw / ih
        return Image(p, width=w, height=h)

    story = []
    # ---- portada
    bloque = Table([[Paragraph("KWD Automotive", ss["Portada"])],
                    [Paragraph("Plan de producción · Recomendación de células a activar",
                               ParagraphStyle("sub", parent=ss["Cuerpo"], textColor=colors.white, alignment=TA_CENTER, fontSize=11))]],
                   colWidths=[ancho])
    bloque.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(NAVY)),
                                ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
    story += [bloque, Spacer(1, 8)]
    portada = [["Inicio del plan", f"{inicio:%d/%m/%Y %H:%M}", "Turno actual", _turno_nombre(turno_act)],
               ["Estado", f'<font color="{col_est}"><b>{est}</b></font>', "Puntuación global", f"<b>{_f(plan.puntuacion, 1)}</b> / 100"],
               ["Idoneidad (óptimo garantizado ±gap)",
                "<b>—</b> (No aplica: plan de contingencia)" if plan.idoneidad is None
                else f"<b>{_f(plan.idoneidad, 1)} %</b>", "Horizonte", f"{len(horas)} h"]]
    story.append(_tabla(portada, [3 * cm, 5.5 * cm, 3.5 * cm, ancho - 12 * cm], ss, cabecera=False, zebra=False))
    if es_contingencia:
        story += [Spacer(1, 6), Paragraph(
            '<font color="%s"><b>Plan de contingencia — incumple:</b> %s</font>' % (ROJO, "; ".join(map(str, plan.incumplimientos)) or "ver alertas"),
            ss["Cuerpo"])]

    # ---- resumen ejecutivo
    story.append(Paragraph("Resumen ejecutivo", ss["H1k"]))
    exp = rec.explicacion or {}
    que = exp.get("que")
    porque = exp.get("porque", []) or []
    impacto = exp.get("impacto", {}) or {}
    activas = list(plan.config_turno_actual or [])
    story.append(Paragraph("<b>Qué activar</b>", ss["H2k"]))
    txt_activas = ", ".join(f"célula {c} ({tipos.get(int(c), '?')})" for c in activas) or "ninguna"
    story.append(Paragraph(f"Durante el turno {_turno_nombre(turno_act)} se recomienda activar: {txt_activas}.", ss["Cuerpo"]))
    story.append(Paragraph("<b>Por qué</b>", ss["H2k"]))
    for p_ in porque[:16]:
        story.append(Paragraph("• " + str(p_), ss["Cuerpo"]))
    story.append(Paragraph("<b>Con qué impacto</b>", ss["H2k"]))
    fi = filas_impacto(rec)
    if fi:
        story.append(_tabla([["Indicador", "Plan recomendado", "Plan de referencia manual", "Diferencia"]] +
                            [list(x) for x in fi], [ancho * 0.34, ancho * 0.22, ancho * 0.26, ancho * 0.18], ss))
    else:
        story.append(Paragraph("Sin comparación con el plan de referencia disponible.", ss["Cuerpo"]))
    # ---- turno actual
    story.append(Paragraph(f"Células activas en el turno actual ({_turno_nombre(turno_act)})", ss["H1k"]))
    mask_t = (slots["turno"] == turno_act).values
    # sólo el tramo contiguo del turno actual desde el slot 0
    n_t = 0
    for v in mask_t:
        if v:
            n_t += 1
        else:
            break
    cel_df = esc.celulas.set_index("celula")
    filas = [["Célula", "Tipo", "Horas activas", "Ventana horaria", "Piezas", "Operarios"]]
    for c in activas:
        c = int(c)
        act = plan.activacion[c].iloc[:n_t] > 0.5
        h_act = float(plan.uso[c].iloc[:n_t].sum()) if hasattr(plan, "uso") else float(act.sum())
        piezas = float(plan.produccion[c].iloc[:n_t].sum()) if c in plan.produccion.columns else 0.0
        op = cel_df.loc[c, "operarios"] if c in cel_df.index else "—"
        filas.append([str(c), tipos.get(c, "?"), _f(h_act, 1), _ventanas(act, horas[:n_t]), _f(piezas, 0), str(op)])
    if len(filas) == 1:
        filas.append(["—", "—", "—", "Ninguna célula activa", "—", "—"])
    story.append(_tabla(filas, [1.6 * cm, 1.6 * cm, 2.4 * cm, ancho - 12.2 * cm, 2.4 * cm, 2.2 * cm], ss))

    # ---- KPIs y contribuciones
    story.append(Paragraph("Indicadores clave (KPIs)", ss["H1k"]))
    kp = filas_kpi(plan)
    if kp:
        mitad = (len(kp) + 1) // 2
        izq, der = kp[:mitad], kp[mitad:]
        filas = [["Indicador", "Valor", "Indicador", "Valor"]]
        for i in range(mitad):
            a = izq[i]
            b = der[i] if i < len(der) else ("", "")
            filas.append([a[0], a[1], b[0], b[1]])
        story.append(_tabla(filas, [ancho * 0.31, ancho * 0.19, ancho * 0.31, ancho * 0.19], ss))
    story.append(Paragraph("Contribución por criterio a la puntuación", ss["H2k"]))
    filas = [["Criterio", "Peso KWD", "Contribución (puntos)"]]
    for kk in ["R", "S", "Q", "B", "E"]:
        v = (plan.contribuciones or {}).get(kk)
        if v is None:
            v = (plan.contribuciones or {}).get(NOMBRE_CRIT[kk])
        filas.append([NOMBRE_CRIT[kk], f"{PESOS[kk]} %", _f(v, 2) if v is not None else "—"])
    filas.append(["<b>Puntuación global</b>", "100 %", f"<b>{_f(plan.puntuacion, 2)}</b>"])
    story.append(_tabla(filas, [ancho * 0.5, ancho * 0.2, ancho * 0.3], ss))

    # ---- alternativas
    story.append(Paragraph("Alternativas (Top 1/2/3) y plan de referencia", ss["H1k"]))
    filas = [["Plan", "Estado", "Puntuación", "Idoneidad %", "Células activas (turno actual)", "m² medios", "kWh"]]
    opciones = [(f"Top {i + 1}", p_) for i, p_ in enumerate(rec.top)]
    if es_contingencia:
        opciones.append(("Contingencia", rec.contingencia))
    if rec.baseline is not None:
        opciones.append(("Referencia manual", rec.baseline))
    for nombre, p_ in opciones:
        filas.append([nombre, str(p_.estado), _f(p_.puntuacion, 1), "—" if p_.idoneidad is None else _f(p_.idoneidad, 1),
                      ", ".join(str(int(c)) for c in (p_.config_turno_actual or [])) or "—",
                      _f(float(np.mean(p_.espacio.values)), 0), _f(float(np.sum(p_.energia_kwh.values)), 0)])
    story.append(_tabla(filas, [2.6 * cm, 2 * cm, 2 * cm, 2 * cm, ancho - 14.2 * cm, 2.0 * cm, 1.6 * cm], ss))

    # ---- gráficos
    story.append(PageBreak())
    story.append(Paragraph("Overview de 24 horas", ss["H1k"]))
    story += [img("gantt", 8.5 * cm), Spacer(1, 6), img("almacen", 6 * cm), Spacer(1, 6), img("energia", 6 * cm)]
    story.append(PageBreak())
    story.append(Paragraph("Stock y recursos", ss["H1k"]))
    story += [img("stock", 15 * cm), Spacer(1, 8), img("recursos", 6.5 * cm)]

    # ---- alertas
    story.append(Paragraph("Alertas", ss["H1k"]))
    alertas = list(rec.alertas or [])
    if alertas:
        for a in alertas:
            story.append(Paragraph("⚠ " + str(a) if False else "• " + str(a), ss["Cuerpo"]))
    else:
        story.append(Paragraph("Sin alertas.", ss["Cuerpo"]))

    # ---- próximos turnos
    story.append(Paragraph("Próximos turnos del horizonte", ss["H1k"]))
    rt = plan.resumen_turnos
    if isinstance(rt, pd.DataFrame) and len(rt):
        cols_ = [c for c in ["turno", "inicio", "horas", "operarios_horas", "operarios_ocup_pico_pct", "m2_medio", "kwh_total", "demanda_cubierta_pct", "celulas_activas"] if c in rt.columns]
        rt2 = rt[cols_].copy()
        for c in rt2.columns:
            if c == "inicio":
                rt2[c] = rt2[c].map(lambda x: pd.Timestamp(x).strftime("%d/%m %H:%M"))
            elif rt2[c].map(lambda x: isinstance(x, (list, tuple, set))).any():
                rt2[c] = rt2[c].map(lambda x: ", ".join(str(int(i)) for i in x))
        story.append(_df_a_tabla(rt2, ss))
    else:
        story.append(Paragraph("Sin resumen por turno.", ss["Cuerpo"]))

    # ---- anexo
    story.append(PageBreak())
    story.append(Paragraph("Anexo · Supuestos (flags) a revisar", ss["H1k"]))
    flags = rec.flags or {}
    filas = [["Id", "Supuesto"]] + [[str(k_), str(v_)] for k_, v_ in flags.items()]
    story.append(_tabla(filas, [1.5 * cm, ancho - 1.5 * cm], ss))

    doc = SimpleDocTemplate(ruta, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.5 * cm,
                            title="KWD · Plan de producción", author="KWD Motor de decisión")
    doc._etiqueta = f"Plan {inicio:%d/%m/%Y %H:%M} · turno {turno_act}"
    doc.build(story, onFirstPage=_pie_cabecera, onLaterPages=_pie_cabecera)
    return ruta



