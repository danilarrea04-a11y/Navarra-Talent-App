"""Genera salida/KWD_Solucion_y_Plan_de_Accion.pdf ejecutando el motor real.

Uso (desde la raíz del proyecto, PowerShell):
    $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe docs\\generar_documento.py

Todas las cifras, tablas y gráficos se calculan en cada ejecución con `kwd.motor.recomendar` y
`kwd.rolling.simular_semana` (tarda ~1 min por la simulación semanal).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch, Rectangle
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from kwd import datos, motor, rolling, validador
from kwd.config import FLAGS, NOMBRES_COMPONENTE, NOMBRES_TURNO, PARAMETROS_DEFECTO

NAVY = colors.HexColor("#1F2A6B")
NAVY_HEX = "#1F2A6B"
ZEBRA = colors.HexColor("#EEF0F8")
GRIS = colors.HexColor("#5A6070")
LINEA = colors.HexColor("#C9CDE0")
C_VE = "#2E9E6B"
C_COMB = "#2F6FB5"
C_SOLAR = "#FFE9A8"
C_ROJO = "#C0392B"
SALIDA = RAIZ / "salida" / "KWD_Solucion_y_Plan_de_Accion.pdf"
FECHA_DOC = "2 de octubre de 2026"

# ------------------------------------------------------------------------------------- fuentes
_ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
for nombre, fich in [("DV", "DejaVuSans.ttf"), ("DV-B", "DejaVuSans-Bold.ttf"),
                     ("DV-I", "DejaVuSans-Oblique.ttf"), ("DV-BI", "DejaVuSans-BoldOblique.ttf"),
                     ("DVM", "DejaVuSansMono.ttf"), ("DVM-B", "DejaVuSansMono-Bold.ttf")]:
    pdfmetrics.registerFont(TTFont(nombre, str(_ttf / fich)))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-BI")
pdfmetrics.registerFontFamily("DVM", normal="DVM", bold="DVM-B", italic="DVM", boldItalic="DVM-B")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titlesize": 9, "axes.titleweight": "bold",
                     "axes.titlecolor": NAVY_HEX})

# ------------------------------------------------------------------------------------- estilos
def _st(name, **kw):
    base = dict(fontName="DV", fontSize=9, leading=13, textColor=colors.HexColor("#222222"), spaceAfter=5)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "body": _st("body", alignment=TA_LEFT),
    "small": _st("small", fontSize=7.8, leading=10.5, textColor=GRIS),
    "h1": _st("h1", fontName="DV-B", fontSize=16, leading=20, textColor=NAVY, spaceBefore=4, spaceAfter=8),
    "h1x": _st("h1x", fontName="DV-B", fontSize=16, leading=20, textColor=NAVY, spaceBefore=4, spaceAfter=8),
    "h2": _st("h2", fontName="DV-B", fontSize=11.5, leading=15, textColor=NAVY, spaceBefore=9, spaceAfter=4,
              keepWithNext=1),
    "h3": _st("h3", fontName="DV-B", fontSize=9.5, leading=13, textColor=colors.HexColor("#333B7A"),
              spaceBefore=6, spaceAfter=3, keepWithNext=1),
    "bul": _st("bul", leftIndent=14, bulletIndent=3, spaceAfter=2.5),
    "cel": _st("cel", fontSize=7.8, leading=10, spaceAfter=0),
    "celh": _st("celh", fontName="DV-B", fontSize=7.8, leading=10, textColor=colors.white, spaceAfter=0),
    "formula": _st("formula", fontName="DVM", fontSize=7.9, leading=11.2, spaceAfter=0),
    "titulo": _st("titulo", fontName="DV-B", fontSize=27, leading=33, textColor=colors.white, alignment=TA_LEFT),
    "sub": _st("sub", fontSize=14, leading=19, textColor=colors.white, alignment=TA_LEFT),
    "toc1": _st("toc1", fontName="DV-B", fontSize=9, leading=11, spaceBefore=1, spaceAfter=0),
    "toc2": _st("toc2", fontSize=7.8, leading=9, leftIndent=14, spaceAfter=0),
    "cap": _st("cap", fontSize=7.8, leading=10, textColor=GRIS, alignment=TA_CENTER, spaceAfter=8),
}


def fm(x, d=1):
    """Número con coma decimal."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") if abs(x) >= 1000 else \
        f"{x:.{d}f}".replace(".", ",")


def sg(x, d=1):
    """Número con signo."""
    return ("+" if x > 0 else "") + fm(x, d) if abs(x) >= 0.05 * 10 ** (1 - d) or d == 0 else fm(0.0, d)


def P(txt, st="body"):
    return Paragraph(txt, S[st])


def bullets(items, st="bul"):
    return [Paragraph(t, S[st], bulletText="•") for t in items]


def tabla(datos_, anchos, align=None, zebra=True, destacar_filas=(), fs=None, repetir=1):
    """Tabla con cabecera navy y filas cebra. `datos_[0]` es la cabecera."""
    align = align or ["L"] * len(anchos)
    filas = []
    for i, f in enumerate(datos_):
        fila = []
        for j, v in enumerate(f):
            if isinstance(v, (Paragraph, Image)):
                fila.append(v)
                continue
            est = S["celh"] if i == 0 else S["cel"]
            est = ParagraphStyle("x", parent=est, alignment={"L": 0, "C": 1, "R": 2}[align[j]],
                                 **({"fontSize": fs, "leading": fs + 2.2} if fs else {}))
            fila.append(Paragraph(str(v), est))
        filas.append(fila)
    t = Table(filas, colWidths=anchos, repeatRows=repetir)
    est = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
           ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
           ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINEA), ("BOX", (0, 0), (-1, -1), 0.5, LINEA)]
    if zebra:
        for i in range(1, len(filas)):
            if i % 2 == 0:
                est.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    for i in destacar_filas:
        est.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#DDE3F7")))
    t.setStyle(TableStyle(est))
    return t


def caja(contenido, color=NAVY, fondo=colors.HexColor("#F4F6FC"), ancho=17 * cm, st="body"):
    """Recuadro con barra lateral de color; `contenido` es texto o lista de flowables."""
    if isinstance(contenido, str):
        contenido = [Paragraph(contenido, S[st])]
    t = Table([[contenido]], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def formulas(lineas):
    txt = "<br/>".join(l.replace(" ", "&nbsp;") if l.startswith(" ") is False else
                       "&nbsp;" * (len(l) - len(l.lstrip())) + l.lstrip().replace(" ", "&nbsp;") for l in lineas)
    # conserva espacios internos significativos sólo al inicio de línea; el resto se deja fluir
    txt = "<br/>".join("&nbsp;" * (len(l) - len(l.lstrip())) + l.lstrip() for l in lineas)
    t = Table([[Paragraph(txt, S["formula"])]], colWidths=[17 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F7FB")),
                           ("BOX", (0, 0), (-1, -1), 0.5, LINEA), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def figc(ruta, ancho_cm, caption):
    """Figura con su pie, sin separarlos entre páginas."""
    return KeepTogether([fig(ruta, ancho_cm), Paragraph(caption, S["cap"])])


def fig(ruta, ancho_cm=17.0):
    from PIL import Image as PI
    with PI.open(ruta) as im:
        w, h = im.size
    return Image(str(ruta), width=ancho_cm * cm, height=ancho_cm * cm * h / w)


# ------------------------------------------------------------------------------------- documento
class Doc(BaseDocTemplate):
    def __init__(self, ruta, **kw):
        super().__init__(str(ruta), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2.2 * cm,
                         bottomMargin=2 * cm, title="Motor de decisión de producción — KWD España",
                         author="Equipo Navarra Talent Challenge 2026", **kw)
        marco = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="n", leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="n", frames=[marco], onPage=self._pie)])

    def _pie(self, c, doc):
        if doc.page == 1:
            return
        c.saveState()
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.line(2 * cm, A4[1] - 1.6 * cm, A4[0] - 2 * cm, A4[1] - 1.6 * cm)
        c.setFont("DV-B", 8)
        c.setFillColor(NAVY)
        c.drawString(2 * cm, A4[1] - 1.4 * cm, "Motor de decisión de producción · KWD España")
        c.setFont("DV", 8)
        c.setFillColor(GRIS)
        c.drawRightString(A4[0] - 2 * cm, A4[1] - 1.4 * cm, "Navarra Talent Challenge 2026")
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.5)
        c.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
        c.drawString(2 * cm, 1.0 * cm, f"Solución paso a paso y plan de acción · {FECHA_DOC}")
        c.drawRightString(A4[0] - 2 * cm, 1.0 * cm, f"Página {doc.page}")
        c.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name in ("h1", "h2"):
            nivel = 0 if fl.style.name == "h1" else 1
            txt = fl.getPlainText()
            clave = f"h{self.seq.nextf('toc')}"
            self.canv.bookmarkPage(clave)
            self.canv.addOutlineEntry(txt, clave, level=nivel, closed=nivel == 0)
            self.notify("TOCEntry", (nivel, txt, self.page, clave))


def H1(t):
    return Paragraph(t, S["h1"])


def H2(t):
    return Paragraph(t, S["h2"])


# ------------------------------------------------------------------------------------- gráficos
def _eje_horas(ax, hz):
    s = hz.slots
    n = len(s)
    ax.set_xlim(0, n)
    ticks = list(range(0, n, 2))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{s['inicio'].iloc[i]:%H}" for i in ticks])
    ax.set_xlabel(f"Hora (inicio {s['inicio'].iloc[0]:%d/%m %H:%M}, horizonte {n} h)")


def _decor_horizonte(ax, hz, esc, etiquetas=True):
    s = hz.slots
    n = len(s)
    sol0, sol1 = float(esc.parametros["solar_ini"]), float(esc.parametros["solar_fin"])
    for i in range(n):
        if sol0 <= s["hora"].iloc[i] < sol1:
            ax.axvspan(i, i + 1, color=C_SOLAR, alpha=0.55, lw=0, zorder=0)
    for i in range(1, n):
        if s["turno"].iloc[i] != s["turno"].iloc[i - 1]:
            ax.axvline(i, color="#5A6070",lw=0.8, ls="--", zorder=1)
    if etiquetas:
        ini = 0
        for i in range(1, n + 1):
            if i == n or s["turno"].iloc[i] != s["turno"].iloc[ini]:
                ax.text((ini + i) / 2, 1.01, f"Turno {NOMBRES_TURNO[s['turno'].iloc[ini]]}",
                        transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=7.5, color=NAVY_HEX)
                ini = i


def g_gantt(plan, hz, esc, ruta, titulo):
    t = datos.tabla_celulas(esc)
    cel = sorted(t.index)
    fig_, ax = plt.subplots(figsize=(8.4, 4.3))
    _decor_horizonte(ax, hz, esc)
    for k, c in enumerate(cel):
        for h in hz.bloqueos.get(c, set()):
            ax.add_patch(Rectangle((h, k - 0.4), 1, 0.8, fc="#F5C6C0", ec=C_ROJO, hatch="////", lw=0.3, zorder=2))
        for h in range(len(hz.slots)):
            if plan.activacion.at[h, c] > 0.5:
                ax.add_patch(Rectangle((h + 0.04, k - 0.36), 0.92, 0.72, fc=C_VE if t.loc[c, "es_ve"] else C_COMB,
                                       ec="white", lw=0.4, zorder=3))
    ax.set_yticks(range(len(cel)))
    ax.set_yticklabels([f"Célula {c}" for c in cel])
    ax.set_ylim(len(cel) - 0.4, -0.7)
    _eje_horas(ax, hz)
    ax.grid(axis="x", color="#DDDDDD", lw=0.4, zorder=0)
    leyenda = [Patch(fc=C_VE, label="Activa (VE)"), Patch(fc=C_COMB, label="Activa (combustión)"),
               Patch(fc=C_SOLAR, label="Franja solar"), Patch(fc="#F5C6C0", ec=C_ROJO, hatch="////", label="Baja / mantenimiento")]
    ax.legend(handles=leyenda, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4, frameon=False, fontsize=7.5)
    ax.set_title(titulo, loc="left", pad=16)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_stock(plan, hz, esc, ruta, titulo):
    t = datos.tabla_celulas(esc)
    ss = datos.ss_por_celula(esc)
    k = float(esc.parametros["colchon_ss"])
    fig_, axs = plt.subplots(1, 2, figsize=(8.4, 3.3), sharey=True)
    fin = list(range(1, len(hz.slots) + 1))
    for ax, ve, nom, col in [(axs[0], True, "Piezas VE", C_VE), (axs[1], False, "Piezas combustión", C_COMB)]:
        _decor_horizonte(ax, hz, esc, etiquetas=False)
        for c in plan.stock.columns:
            if bool(t.loc[c, "es_ve"]) == ve:
                ax.plot(fin, plan.stock[c] / ss[c], color=col, lw=0.9, alpha=0.75)
        ax.axhline(1.0, color=C_ROJO, lw=1.2)
        ax.axhline(1 + k, color="#E08A00", lw=1, ls="--")
        ax.set_title(nom, loc="left")
        _eje_horas(ax, hz)
        ax.set_xlabel("Hora")
    axs[0].set_ylabel("Stock / stock de seguridad")
    axs[1].legend(handles=[Patch(fc="none", ec=C_ROJO, label="SS (obligatorio)"),
                           Patch(fc="none", ec="#E08A00", label=f"Colchón +{k:.0%}")], frameon=False,
                  fontsize=7.5, loc="upper right")
    fig_.suptitle(titulo, x=0.01, ha="left", fontsize=9, fontweight="bold", color=NAVY_HEX)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_almacen(plan, base, hz, esc, ruta):
    A = esc.area_producto_terminado()
    fig_, ax = plt.subplots(figsize=(8.4, 2.9))
    _decor_horizonte(ax, hz, esc, etiquetas=False)
    x = list(range(1, len(hz.slots) + 1))
    ax.plot(x, plan.espacio.values, color=NAVY_HEX, lw=2, label="Top 1")
    ax.plot(x, base.espacio.values, color="#999999", lw=1.4, ls="--", label="Referencia manual")
    ax.axhline(A, color=C_ROJO, lw=1.2)
    ax.axhline(0.9 * A, color="#E08A00", lw=0.9, ls=":")
    ax.text(0.3, A * 0.97, f"Capacidad {A:.0f} m²", color=C_ROJO, va="top", fontsize=7.5)
    ax.text(0.3, 0.9 * A * 0.985, "Aviso 90 %", color="#E08A00", va="top", fontsize=7.5)
    ax.set_ylim(0, A * 1.05)
    ax.set_ylabel("m² ocupados")
    _eje_horas(ax, hz)
    ax.legend(frameon=False, fontsize=7.5, loc="center right")
    ax.set_title("Ocupación del almacén de producto terminado", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_energia(plan, hz, esc, ruta):
    f = hz.slots["factor_energia"].values
    cols = [C_SOLAR if v < 0.99 else ("#8E9AC8" if v > 1.01 else "#B9BDC9") for v in f]
    sol = [i for i, v in enumerate(f) if v < 0.99]
    fig_, ax = plt.subplots(figsize=(8.4, 2.8))
    ax.bar([i + 0.5 for i in range(len(f))], plan.energia_kwh.values, width=0.85,
           color=["#E6A700" if v < 0.99 else ("#4A5AA8" if v > 1.01 else "#8A90A6") for v in f], zorder=3)
    if sol:
        ax.axvspan(min(sol), max(sol) + 1, color=C_SOLAR, alpha=0.5, lw=0, zorder=0)
    ax.set_ylim(0, max(plan.energia_kwh.max(), 1) * 1.28)
    ax.set_ylabel("kWh de red por hora")
    _eje_horas(ax, hz)
    ax.legend(handles=[Patch(fc="#E6A700", label="Franja solar (×0,85)"), Patch(fc="#8A90A6", label="Normal (×1,00)"),
                       Patch(fc="#4A5AA8", label="Noche (×1,20)")], frameon=False, fontsize=7.5, ncol=3,
              loc="upper center", bbox_to_anchor=(0.5, 1.0))
    ax.set_title("Energía horaria del plan Top 1", loc="left", pad=14)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_contrib(planes, nombres, ruta):
    comp = ["R", "S", "Q", "B", "E"]
    pal = ["#1F2A6B", "#3E64B8", "#6E9BD8", "#2E9E6B", "#E6A700"]
    fig_, ax = plt.subplots(figsize=(8.4, 2.3))
    for i, (p, n) in enumerate(zip(planes, nombres)):
        izq = 0
        for c, col in zip(comp, pal):
            v = float(p.contribuciones[c])
            ax.barh(i, v, left=izq, color=col, label=NOMBRES_COMPONENTE[c] if i == 0 else None, height=0.55)
            if v > 4:
                ax.text(izq + v / 2, i, fm(v, 1), ha="center", va="center", color="white", fontsize=7.5)
            izq += v
        ax.text(izq + 0.8, i, f"{fm(p.puntuacion, 2)} pts", va="center", fontsize=8, fontweight="bold", color=NAVY_HEX)
    ax.set_yticks(range(len(nombres)))
    ax.set_yticklabels(nombres)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Contribución a la puntuación (máx. 100 = 50+20+15+10+5)")
    ax.legend(ncol=5, frameon=False, fontsize=7.3, loc="upper center", bbox_to_anchor=(0.5, -0.32))
    ax.set_title("Contribución de cada criterio KWD a la puntuación", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_semana(sem, ruta):
    x = range(len(sem))
    fig_, (ax, ax2) = plt.subplots(2, 1, figsize=(8.4, 4.4), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax.bar([i - 0.2 for i in x], sem["puntuacion_plan_24h"], width=0.4, color=NAVY_HEX, label="Motor (plan 24 h)")
    ax.bar([i + 0.2 for i in x], sem["puntuacion_baseline"], width=0.4, color="#B9BDC9", label="Referencia manual")
    for i, e in enumerate(sem["estado"]):
        if e != "OPTIMO":
            ax.plot(i, sem["puntuacion_plan_24h"].iloc[i] + 3, marker="v", color="#E08A00", ms=6)
    ax.plot([], [], "v", color="#E08A00", label="FACTIBLE (límite 10 s)")
    ax.set_ylabel("Puntuación")
    ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper left")
    ax.set_title("Simulación semanal: puntuación por turno consolidado", loc="left")
    ax.set_ylim(0, max(sem["puntuacion_plan_24h"].max(), sem["puntuacion_baseline"].max()) * 1.18)
    ids = sem["idoneidad"].astype(float)
    ax2.bar(x, ids, color=[("#2E9E6B" if v >= 99 else "#E08A00") for v in ids], width=0.6)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Idoneidad %")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([f"{pd.Timestamp(f):%a %d}\n{t}".replace("Mon", "Lun").replace("Tue", "Mar")
                         .replace("Wed", "Mié").replace("Thu", "Jue").replace("Fri", "Vie")
                         for f, t in zip(sem["fecha_turno"], sem["turno"])], fontsize=6.5)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_arquitectura(ruta):
    fig_, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.set_xlim(0, 100)
    ax.set_ylim(-2, 56)
    ax.axis("off")

    def caja_(x, y, w, h, titulo, sub, fc, tc="white"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2", fc=fc, ec="none"))
        ax.text(x + w / 2, y + h * 0.63, titulo, ha="center", va="center", color=tc, fontsize=8.3, fontweight="bold")
        ax.text(x + w / 2, y + h * 0.27, sub, ha="center", va="center", color=tc, fontsize=6.8)

    def flecha(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, color="#5A6070", lw=1))

    caja_(1, 40, 17, 11, "Excel", "data/*.xlsx · 11 hojas", "#8A90A6")
    caja_(23, 40, 17, 11, "datos.py", "carga + validación", NAVY_HEX)
    caja_(45, 40, 17, 11, "horizonte.py", "24 slots horarios", NAVY_HEX)
    caja_(67, 40, 17, 11, "modelo.py", "MILP · PuLP + HiGHS", "#3E64B8")
    caja_(66, 22, 19, 11, "motor.py", "Top 1/2/3 · explicación", "#3E64B8")
    caja_(44, 22, 17, 11, "baseline.py", "plan manual", NAVY_HEX)
    caja_(22, 22, 17, 11, "validador.py", "certifica reglas", NAVY_HEX)
    caja_(89, 22, 10.5, 11, "rolling.py", "incidencias", "#3E64B8")
    caja_(1, 4, 24, 11, "app/dashboard.py", "Streamlit · 8 pestañas", C_VE)
    caja_(30, 4, 22, 11, "informes.py", "PDF para dirección", C_VE)
    caja_(57, 4, 20, 11, "cli.py", "plan por consola", C_VE)
    caja_(82, 4, 17, 11, "tests/", "pytest", "#8A90A6")
    for a, b in [((18.3, 45.5), (22.7, 45.5)), ((40.3, 45.5), (44.7, 45.5)), ((62.3, 45.5), (66.7, 45.5))]:
        flecha(*a, *b)
    flecha(75.5, 39.7, 75.5, 33.3)
    flecha(65.7, 27.5, 61.3, 27.5)
    flecha(43.7, 27.5, 39.3, 27.5)
    flecha(88.7, 27.5, 85.3, 27.5)
    for xd in (13, 41, 67, 90):
        flecha(min(max(xd, 24), 92) if xd != 13 else 25, 21.7, xd, 15.5)
    ax.text(50, 54.5, "Datos → modelo → decisión → interfaces", ha="center", fontsize=8.5, color=NAVY_HEX,
            fontweight="bold")
    ax.text(50, 0.3, "Las interfaces (verde) sólo llaman a motor.py / rolling.py: la lógica vive en el paquete kwd.",
            ha="center", fontsize=7.3, color="#5A6070", style="italic")
    fig_.savefig(ruta, dpi=170, bbox_inches="tight")
    plt.close(fig_)


# ------------------------------------------------------------------------------------- contenido
def resumen_plan(p):
    k = p.kpis
    return {"punt": p.puntuacion, "ido": p.idoneidad, "opH": k["operarios_horas"], "m2": k["m2_medio"],
            "kwh": k["kwh_total"], "solar": k["kwh_solar_pct"], "pico_m2": k["m2_pico"]}


def filas_top(rec, etiqueta_base=True):
    f = [["Plan", "Estado", "Puntuación", "Idoneidad", "Gap", "Células activas en el turno actual"]]
    for p in (rec.top or [rec.contingencia]):
        f.append([p.nombre, p.estado, fm(p.puntuacion, 2), p.idoneidad_txt().replace(".", ",") if p.idoneidad is not None else "—",
                  "—" if p.gap is None else fm(100 * p.gap, 2) + " %", ", ".join(map(str, p.config_turno_actual))])
    if etiqueta_base:
        b = rec.baseline
        f.append(["Referencia manual", "REFERENCIA", fm(b.puntuacion, 2), "—", "—", ", ".join(map(str, b.config_turno_actual))])
    return f


def tabla_comparacion(rec):
    t1, b = rec.top[0], rec.baseline
    k1, kb = t1.kpis, b.kpis
    filas = [["Indicador (24 h)", "Top 1", "Referencia manual", "Diferencia"]]
    for nom, key, d, unid in [("Puntuación global", "puntuacion", 2, ""), ("Horas-operario", "operarios_horas", 1, " h"),
                              ("Horas-picking", "picking_horas", 1, " h"), ("Horas-carretillero", "carretilleros_horas", 1, " h"),
                              ("Almacén medio", "m2_medio", 1, " m²"), ("Almacén pico", "m2_pico", 1, " m²"),
                              ("Energía de red", "kwh_total", 0, " kWh"), ("Energía en franja solar", "kwh_solar_pct", 1, " %")]:
        a, c = k1[key], kb[key]
        filas.append([nom, fm(a, d) + unid, fm(c, d) + unid, sg(a - c, d) + unid])
    return filas


def main():
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="kwd_doc_"))
    SALIDA.parent.mkdir(exist_ok=True)

    print("Ejecutando el motor…")
    esc = datos.cargar_entrada(RAIZ / "data" / "entrada_ejemplo.xlsx")
    esc_c = datos.cargar_entrada(RAIZ / "data" / "escenario_contingencia.xlsx")
    cache = os.environ.get("KWD_DOC_CACHE")  # sólo para depurar el maquetado; vacío = ejecuta el motor
    import pickle
    if cache and os.path.exists(cache):
        rec06, rec14, rec_c, sem = pickle.load(open(cache, "rb"))
    else:
        rec06 = motor.recomendar(esc, pd.Timestamp("2026-10-02 06:00"))
        rec14 = motor.recomendar(esc, pd.Timestamp("2026-10-02 14:00"))
        rec_c = motor.recomendar(esc_c, pd.Timestamp("2026-10-02 06:00"))
        print("Simulación semanal…")
        sem = rolling.simular_semana(esc, pd.Timestamp("2026-09-28"))
        if cache:
            pickle.dump((rec06, rec14, rec_c, sem), open(cache, "wb"))
    print(f"Motor listo en {time.time() - t0:.0f} s")

    t1, b06 = rec06.top[0], rec06.baseline
    hz06 = rec06.horizonte
    cert = {n: validador.validar(e, r.horizonte, r.mejor) for n, e, r in
            [("06:00", esc, rec06), ("14:00", esc, rec14), ("contingencia", esc_c, rec_c)]}
    cert_base = validador.validar(esc, hz06, b06)
    ss = datos.ss_por_celula(esc)
    tc = datos.tabla_celulas(esc)
    area = esc.area_producto_terminado()
    suma_ss = datos.validar_ss_almacen(esc)
    gap_par = float(esc.parametros.get("gap_relativo", 0.001))
    pesos = {c: float(esc.parametros[p]) for c, p in
             [("R", "peso_recursos"), ("S", "peso_espacio"), ("Q", "peso_calidad_mto"), ("B", "peso_stock"), ("E", "peso_energia")]}

    # ---- gráficos
    g_gantt(t1, hz06, esc, tmp / "gantt06.png", "Top 1 a las 06:00 · activación de células en 24 h")
    g_stock(t1, hz06, esc, tmp / "stock06.png", "Stock por pieza frente al stock de seguridad (Top 1, 06:00)")
    g_almacen(t1, b06, hz06, esc, tmp / "almacen06.png")
    g_energia(t1, hz06, esc, tmp / "energia06.png")
    g_contrib([t1, b06], ["Top 1", "Referencia manual"], tmp / "contrib06.png")
    t14 = rec14.top[0]
    g_gantt(t14, rec14.horizonte, esc, tmp / "gantt14.png", "Top 1 a las 14:00 · activación de células en 24 h")
    pc = rec_c.mejor
    g_gantt(pc, rec_c.horizonte, esc_c, tmp / "ganttc.png", "Contingencia (célula 14 de baja) · plan de contingencia, no recomendable")
    g_semana(sem, tmp / "semana.png")
    g_arquitectura(tmp / "arq.png")

    # ---- derivados para el texto
    r1, rb = resumen_plan(t1), resumen_plan(b06)
    d_op, d_m2, d_kwh, d_pt = r1["opH"] - rb["opH"], r1["m2"] - rb["m2"], r1["kwh"] - rb["kwh"], r1["punt"] - rb["punt"]
    pct = lambda a, b: 100 * (a - b) / b if b else 0.0  # noqa: E731
    r14, rb14 = resumen_plan(t14), resumen_plan(rec14.baseline)
    rs = t1.resumen_turnos
    noche = rs[rs["turno"] == "N"]
    noche_solo10 = bool(len(noche)) and list(noche.iloc[0]["celulas_activas"]) == [10]
    n_fact = int((sem["estado"] != "OPTIMO").sum())
    sem_baja = sem[sem["idoneidad"].astype(float) < 90]
    sem_dpt = (sem["puntuacion_plan_24h"] - sem["puntuacion_baseline"])
    top2_mayor = [p.nombre for p in rec06.top[1:] if p.puntuacion > t1.puntuacion + 1e-9]
    inc_c = pc.incumplimientos
    cel_inc = sorted({int(x.split("célula ")[1].split(",")[0]) for x in inc_c if "célula " in x}) if inc_c else []
    ts = tc.loc[[c for c in tc.index if c != 10]]

    E = []  # flowables
    # =============================== PORTADA
    portada = Table([[[Spacer(1, 4.2 * cm), P("Motor de decisión de producción — KWD España", "titulo"),
                       Spacer(1, 0.5 * cm), P("Navarra Talent Challenge 2026", "sub"), Spacer(1, 1.2 * cm),
                       P("Solución paso a paso y plan de acción del equipo", "sub"), Spacer(1, 4 * cm),
                       P(f"2 de octubre de 2026<br/>Presentación del reto: sábado 3 de octubre de 2026, 08:30", "sub"),
                       Spacer(1, 3.5 * cm)]]], colWidths=[17 * cm])
    portada.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * cm),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * cm), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    E += [portada, Spacer(1, 0.6 * cm),
          caja("<b>Qué es este documento.</b> Explica el problema de KWD Automotive, el modelo matemático que lo resuelve, "
               "los resultados reales de la demo (calculados al generar este PDF ejecutando el motor), cómo usar la "
               "aplicación y el plan de trabajo del equipo de cinco personas hasta la presentación."),
          PageBreak()]

    # =============================== ÍNDICE
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    E += [Paragraph("Índice", S["h1x"]), toc, PageBreak()]

    # =============================== 1 RESUMEN EJECUTIVO
    E += [H1("1. Resumen ejecutivo")]
    E += [P("<b>El reto.</b> En cada turno, el responsable de la planta de soldadura de KWD debe decidir qué células "
            "activar (16 células, recursos limitados, stock de seguridad, almacén de 800 m², 15 camiones al día) de forma "
            "que se cumplan las reglas obligatorias y se optimicen cinco criterios ponderados: recursos 50 %, espacio 20 %, "
            "calidad y mantenimiento 15 %, stock de seguridad 10 % y energía 5 %."),
          P("<b>Nuestra respuesta: qué activar, por qué y con qué impacto.</b> Un motor de optimización (MILP con HiGHS) "
            "que, dado el estado de la planta, devuelve las <b>3 mejores configuraciones del turno</b>, la "
            "<b>explicación</b> de cada decisión, el <b>impacto cuantificado</b> frente a un plan manual y una "
            "<b>idoneidad</b> certificada (gap del solver + validador independiente de reglas). Se recalcula en segundos "
            "ante cualquier incidencia (baja, avería, cambio de demanda).")]
    E += [Spacer(1, 3), H2("Resultados clave de la demo (2 oct 2026, 06:00)")]
    kp = [["Indicador", "Valor", "Lectura"],
          ["Puntuación Top 1 / referencia manual", f"{fm(r1['punt'], 2)} / {fm(rb['punt'], 2)}", f"{sg(d_pt, 2)} puntos sobre un plan manual razonable"],
          ["Horas-operario (24 h)", f"{fm(r1['opH'], 0)} h vs {fm(rb['opH'], 0)} h", f"{sg(d_op, 1)} h ({sg(pct(r1['opH'], rb['opH']), 1)} %)"],
          ["Almacén medio ocupado", f"{fm(r1['m2'], 1)} m² vs {fm(rb['m2'], 1)} m²", f"{sg(d_m2, 1)} m² ({sg(pct(r1['m2'], rb['m2']), 1)} %)"],
          ["Energía de red", f"{fm(r1['kwh'], 0)} kWh vs {fm(rb['kwh'], 0)} kWh", f"{sg(d_kwh, 0)} kWh ({sg(pct(r1['kwh'], rb['kwh']), 1)} %)"],
          ["Idoneidad del Top 1", t1.idoneidad_txt().replace(".", ","), f"óptimo garantizado ±{fm(100 * gap_par, 1)} %; {'0 incumplimientos' if not cert['06:00'] else str(len(cert['06:00'])) + ' incumplimientos'} en el validador"],
          ["Demanda cubierta", f"{fm(t1.kpis['demanda_cubierta_pct'], 0)} %", f"{fm(t1.kpis['chasis_ve_a_expedir'], 0)} chasis VE y {fm(t1.kpis['chasis_comb_a_expedir'], 0)} COMB expedidos"],
          ["Escenario con célula 14 de baja", "INVIABLE", "se detecta y se muestra como plan de contingencia, nunca como recomendación"],
          ["Simulación semanal (15 turnos)", f"{len(sem) - n_fact} OPTIMO / {n_fact} FACTIBLE", f"puntuación media {fm(sem['puntuacion_plan_24h'].mean(), 1)} vs {fm(sem['puntuacion_baseline'].mean(), 1)} manual"]]
    E += [tabla(kp, [5.3 * cm, 4.6 * cm, 7.1 * cm], ["L", "L", "L"]), Spacer(1, 6)]
    E += [caja(f"<b>Mensaje.</b> En la demo del 2 oct a las 06:00 el motor mejora al plan manual en puntuación con menos "
               f"horas-operario, menos almacén y menos energía; además <b>demuestra</b> que la decisión es adecuada "
               f"(idoneidad y validador). La mejora media sobre el plan manual en la semana simulada es de "
               f"{fm(sem_dpt.mean(), 1)} puntos (mín. {fm(sem_dpt.min(), 1)}, máx. {fm(sem_dpt.max(), 1)}).")]
    E += [H2("Qué contiene la aplicación")]
    E += bullets([
        "<b>Motor</b> (paquete <font name='DVM'>kwd</font>): carga de Excel, horizonte de 24 h, modelo MILP, Top 1/2/3, plan manual de referencia, explicación, validador, rolling horizon y simulación semanal.",
        "<b>Dashboard Streamlit</b> con 8 pestañas: Recomendación, KPIs, Overview 24 h, Alternativas, Incidencias/Reconfigurar, Datos de entrada, Semana e Informe.",
        "<b>Informe PDF</b> para dirección (generado desde el dashboard o por consola) y <b>CLI</b> (<font name='DVM'>python -m kwd.cli</font>).",
        "<b>Batería de pruebas</b> (pytest) y tres escenarios de demo: plantilla de ejemplo, contingencia y simulación semanal.",
    ])
    E += [PageBreak()]

    # =============================== 2 PROBLEMA
    E += [H1("2. El problema")]
    E += [P("En cada momento hay que decidir <b>qué células de soldadura están activas cada hora</b> durante las próximas "
            "24 h, de modo que se atienda la demanda de chasis (VE y combustión) que sale en camiones, sin romper las "
            "reglas obligatorias y con la mejor puntuación según los pesos de KWD.")]
    E += [H2("Entradas (hojas del Excel)")]
    E += bullets([
        "<b>Células</b>: tipo (VE/COMB), ciclo, piezas/m², recursos por célula (operarios, picking, carretilleros, mantenimiento, calidad) y potencia kW.",
        "<b>Turnos</b>: recursos disponibles por turno. <b>Almacén</b>: m² por zona. <b>Parámetros</b>: pesos, absentismo, camiones, franja solar, solver.",
        "<b>Demanda</b> semanal y corrección diaria; <b>stock actual</b>; <b>bajas</b> de célula; <b>mantenimientos</b>; <b>recursos reales</b> del turno; <b>expediciones reales</b>.",
    ])
    E += [H2("Reglas obligatorias (descartan la solución)")]
    E += bullets([
        "La <b>célula 10</b> (servicio logístico) está activa en todas las horas laborables; no hay producción fuera de lunes–viernes.",
        "Las células <b>11 y 12</b> funcionan siempre en pareja (misma activación y mismo uso).",
        "Una célula de <b>baja</b> o en <b>mantenimiento</b> no puede activarse en ese intervalo.",
        "Los recursos usados por las células activas no superan los disponibles en ninguna hora (incluido mantenimiento y calidad).",
        "El <b>stock de seguridad</b> de cada pieza se respeta en todas las horas, y el producto terminado cabe en los <b>800 m²</b>.",
    ])
    E += [H2("Pesos KWD, recursos, almacén y expediciones")]
    pt = [["Criterio KWD", "Peso", "Qué mide (menor valor = mejor)"],
          ["Recursos", f"{pesos['R']:.0%}", "Uso medio de operarios, picking y carretilleros respecto a los disponibles"],
          ["Espacio de almacén", f"{pesos['S']:.0%}", "m² ocupados de producto terminado / 800 m²"],
          ["Calidad y mantenimiento", f"{pesos['Q']:.0%}", "Uso de técnicos de mantenimiento y de calidad (más margen libre = mejor)"],
          ["Stock de seguridad", f"{pesos['B']:.0%}", "Déficit frente al colchón objetivo (SS +10 %)"],
          ["Energía", f"{pesos['E']:.0%}", "kWh de red ponderados por factor horario (premia la franja solar)"]]
    E += [tabla(pt, [4.2 * cm, 1.8 * cm, 11 * cm], ["L", "C", "L"])]
    trn = esc.turnos.set_index("recurso")
    E += [Spacer(1, 6)]
    rt = [["Recurso (estándar)", "Mañana 06–14", "Tarde 14–22", "Noche 22–06"]] + \
         [[r.capitalize(), fm(trn.loc[r, "M"], 0), fm(trn.loc[r, "T"], 0), fm(trn.loc[r, "N"], 0)] for r in trn.index]
    alm = esc.almacen.set_index("zona")["m2"]
    E += [tabla(rt, [5 * cm, 4 * cm, 4 * cm, 4 * cm], ["L", "C", "C", "C"])]
    E += [P(f"Con un absentismo del {float(esc.parametros['absentismo']):.0%} (F7) se descuenta el redondeo comercial de cada "
            f"estándar. <b>Almacén:</b> materia prima {fm(alm['materia_prima'], 0)} m², cargas {fm(alm['cargas'], 0)} m², "
            f"producto terminado {fm(alm['producto_terminado'], 0)} m² (la que limita el plan). <b>Expediciones:</b> "
            f"{fm(float(esc.parametros['camiones_dia']), 0)} camiones por día laborable, uno cada "
            f"{fm(float(esc.parametros['intervalo_camion_h']), 1)} h desde las 06:00, con un máximo de "
            f"{fm(float(esc.parametros['m2_max_camion']), 0)} m² por camión. <b>Horizonte:</b> {int(float(esc.parametros['horas_horizonte']))} h, "
            f"en slots de una hora, y se vuelve a calcular en cada turno o incidencia.", "small")]
    ct =[["Cél.", "Tipo", "Ciclo (s)", "Cap. (pz/h)", "Pz/m²", "Oper.", "Pick.", "Carr.", "Mto", "Cal.", "kW", "SS (pz)"]]
    for c, r in tc.iterrows():
        ct.append([c, r["tipo"], fm(r["ciclo_s"], 0), fm(r["cap_h"], 0), fm(r["piezas_m2"], 0), fm(r["operarios"], 1),
                   fm(r["picking"], 1), fm(r["carretilleros"], 1), fm(r["mto"], 2), fm(r["calidad"], 2), fm(r["kw"], 1),
                   "—" if c == 10 else fm(ss[c], 0)])
    E += [KeepTogether([H2("Las 16 células"), tabla(ct, [1.1 * cm, 1.3 * cm, 1.5 * cm, 1.7 * cm, 1.4 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm,
                     1.5 * cm, 1.6 * cm], ["C"] * 12, fs=7.5),
                        P("Cap. = capacidad con OEE 100 % (3600 / ciclo). Recursos = personas equivalentes que exige la célula mientras "
                          "está activa. La célula 10 no produce piezas ni ocupa almacén (F11); 11 y 12 van en pareja.", "small")]),
          PageBreak()]

    # =============================== 3 DECISIONES DE MODELADO
    E += [H1("3. Decisiones de modelado e hipótesis")]
    E += [P("El enunciado deja huecos. Cada supuesto está aislado como un <b>flag</b> (<font name='DVM'>kwd/config.py::FLAGS</font>), "
            "marcado en el código con <font name='DVM'>FLAG Fx</font> y visible en el dashboard y en el informe PDF. "
            "Cambiar un supuesto no obliga a rehacer el modelo.")]
    cambiar = {
        "F1": "<font name='DVM'>Celulas.piezas_por_conjunto</font> (Excel)", "F2": "<font name='DVM'>Celulas.piezas_por_conjunto</font>",
        "F3": "Hoja <font name='DVM'>Mantenimientos</font>", "F4": "<font name='DVM'>config.py</font>: TURNOS, DIAS_LABORABLES",
        "F5": "<font name='DVM'>Parametros</font>: solar_ini/fin, factor_solar, factor_noche",
        "F6": "<font name='DVM'>Parametros</font>: camiones_dia, intervalo_camion_h, m2_max_camion; hoja <font name='DVM'>Expediciones</font>",
        "F7": "<font name='DVM'>Parametros.absentismo</font>; hoja <font name='DVM'>RecursosReales</font>",
        "F8": "Dividir la capacidad por el OEE en <font name='DVM'>datos.tabla_celulas</font> (hoy 100 %)",
        "F9": "<font name='DVM'>Parametros.colchon_ss</font>", "F10": "<font name='DVM'>Parametros</font>: ss_ve, ss_comb",
        "F11": "<font name='DVM'>config.CELULA_LOGISTICA</font>", "F12": "<font name='DVM'>datos.demanda_dia</font> / hoja <font name='DVM'>DemandaSemanal</font>",
        "F13": "<font name='DVM'>horizonte.py</font> (cálculo de bloqueos)", "F14": "<font name='DVM'>modelo.py</font> (regla 2)",
        "F15": "<font name='DVM'>horizonte.py</font> (tolerancia ±45 min)", "F16": "<font name='DVM'>modelo.py</font> (cálculo de energía)",
        "F17": "<font name='DVM'>horizonte.py</font> (redondeo)", "F18": "<font name='DVM'>Parametros.cobertura_final_h</font>",
        "F19": "Hoja <font name='DVM'>StockActual</font> (factor de la plantilla de ejemplo)"}
    ft = [["Id", "Supuesto actual", "Cómo cambiarlo"]]
    for k, v in FLAGS.items():
        ft.append([k, v, cambiar.get(k, "—")])
    E += [tabla(ft, [1.0 * cm, 10.2 * cm, 5.8 * cm], ["C", "L", "L"], fs=7.3)]
    E += [Spacer(1, 6),
          caja([P(f"<b>Validación del supuesto F1/F2.</b> Con el stock de seguridad de F10 (400 piezas por célula VE, 200 por "
                  f"combustión), la suma ΣSS<sub>c</sub> / densidad<sub>c</sub> sobre las 15 células productivas vale "
                  f"<b>{fm(suma_ss, 1)} m²</b>, frente a los <b>≈150 m²</b> que indica KWD. La coincidencia (desviación "
                  f"{fm(100 * abs(suma_ss - 150) / 150, 1)} %) sólo se explica si cada célula produce una pieza distinta y un "
                  f"ciclo equivale a una pieza; confirma <b>«cada célula = una pieza»</b>.")], color=C_VE,
               fondo=colors.HexColor("#F0F8F4")),
          PageBreak()]

    # =============================== 4 SOLUCIÓN PASO A PASO
    E += [H1("4. La solución paso a paso")]
    E += [P("Nueve pasos, de los datos de entrada a la simulación de una semana completa. Cada uno corresponde a un módulo del paquete.")]
    E += [H2("Paso 1 · Carga y validación de datos")]
    E += [P("<font name='DVM'>datos.cargar_entrada</font> lee las 11 hojas del Excel, completa los parámetros que falten con sus "
            "valores por defecto, normaliza fechas y tipos, y comprueba coherencias (por ejemplo, la suma ΣSS/densidad frente "
            "a ≈150 m²). Una hoja vacía es válida: significa «sin incidencias» (sin bajas, sin mantenimientos, sin recursos reales).")]
    E += [H2("Paso 2 · Construcción del horizonte horario")]
    E += [P("<font name='DVM'>horizonte.construir_horizonte</font> genera 24 slots de una hora desde el instante de inicio y calcula, "
            "para cada uno:")]
    E += bullets([
        "<b>Turno</b> (M 06–14, T 14–22, N 22–06) y si es <b>laborable</b> (lunes–viernes; fin de semana sin producción ni camiones).",
        "<b>Recursos disponibles</b>: estándar menos el 5 % de <b>absentismo</b>, o los recursos reales si existen; los técnicos de mantenimiento se restan durante los <b>mantenimientos</b> programados.",
        "<b>Células bloqueadas</b> por <b>bajas</b> o mantenimientos, con la regla de solape de slot (F13).",
        "<b>Camiones</b>: 15 al día cada 1,5 h desde las 06:00; cada uno lleva demanda diaria/15 (o la carga real) con tope de 15 m²; se convierte en piezas que salen por célula.",
        "<b>Factor energético</b> horario: ×0,85 en franja solar (11–17 h) y ×1,20 de noche.",
    ])
    E += [H2("Paso 3 · Modelo MILP")]
    E += [P("<font name='DVM'>modelo.resolver</font> plantea un problema de programación lineal entera mixta, resuelto con HiGHS "
            "a través de PuLP. Conjuntos: C = células, P = C \\ {10} células productivas, H = 24 horas, W ⊆ H horas laborables.")]
    E += [P("<b>Variables</b>", "h3"), formulas([
        "a[c,h] ∈ {0,1}     célula c activa (recursos comprometidos) en la hora h",
        "u[c,h] ∈ [0,1]     fracción de la hora que produce (permite activar parte de la hora)",
        "I[c,h] libre       stock de la pieza al final de h (negativo = backlog; sólo con holgura)",
        "s[c,h], sa[h] ≥ 0  holguras de stock de seguridad y de espacio (penalización enorme)",
        "short[c,h] ≥ 0     déficit frente al colchón (1+k)·SS[c];   st[c,h] ≥ 0  arranques"]),
          P("<b>Restricciones obligatorias</b>", "h3"), formulas([
              "(1)  u[c,h] ≤ a[c,h]",
              "(2)  a[10,h] = 1  ∀h ∈ W ;   a[c,h] = 0  ∀h ∉ W",
              "(3)  a[11,h] = a[12,h] ;   u[11,h] = u[12,h]",
              "(4)  a[c,h] = 0  si c está bloqueada (baja o mantenimiento) en h",
              "(5)  Σ_c req[c,k]·a[c,h] ≤ Disp[k,h]   ∀k ∈ {operarios, picking, carretilleros, mto, calidad}, ∀h ∈ W",
              "(6)  I[c,h] = I[c,h-1] + cap[c]·u[c,h] − env[c,h]      (I[c,-1] = stock inicial)",
              "(7)  I[c,h] ≥ SS[c] − s[c,h]                          (stock de seguridad)",
              "(8)  Σ_{c∈P} I[c,h] / dens[c] ≤ 800 + sa[h]            (almacén de producto terminado)",
              "(9)  short[c,h] ≥ (1+k)·SS[c] − I[c,h] ;  (10) st[c,h] ≥ a[c,h] − a[c,h-1]"]),
          P("<b>Función objetivo y puntuación</b> (componentes normalizados en [0,1]; menor es mejor)", "h3"), formulas([
              "R = media_{h∈W, k∈{op,pick,carr}} (usado / disponible)          peso 50 %",
              "S = media_h ( m² ocupados / 800 )                               peso 20 %",
              "Q = media_{h∈W} ½( mto usado/disp + calidad usada/disp )        peso 15 %",
              "B = media_{c,h} short[c,h] / (k·SS[c])                          peso 10 %",
              "E = Σ f[h]·kW[c]·u[c,h] / Σ máx(f)·kW[c]                        peso  5 %",
              "min  0,5R + 0,2S + 0,15Q + 0,1B + 0,05E + 1000·(Σ s/SS + Σ sa/800)",
              "     + 10·(holgura del stock final) + 0,0001·Σ st",
              "Puntuación = 100 · (1 − (0,5R + 0,2S + 0,15Q + 0,1B + 0,05E))"])]
    E += [Spacer(1, 4), P("La penalización de 1000 convierte cualquier incumplimiento obligatorio en «INVIABLE» (nunca se recomienda "
                          "un plan así). El término de arranques (0,0001) estabiliza el plan y el del stock final (peso 10) "
                          "implementa la condición terminal del paso 8.")]
    E += [H2("Paso 4 · Top 1, Top 2 y Top 3 por cortes")]
    E += [P("Una variable binaria y<sub>c</sub> indica si la célula c se activa en algún momento del turno actual. Tras obtener la "
            "configuración S<sub>j</sub> se añade un <b>corte</b> que la prohíbe, y se vuelve a resolver:")]
    E += [formulas(["y[c] ≥ a[c,h] ∀h del turno actual ;  y[c] ≤ Σ_{h∈turno} a[c,h]",
                    "Σ_{c∈Sj} (1 − y[c]) + Σ_{c∉Sj} y[c] ≥ 1       (corte: configuración distinta a Sj)"]),
          P("Así se obtienen K = 3 configuraciones <b>realmente distintas</b> del turno actual. Sólo los planes viables entran "
            "en el Top; si ninguno lo es, se devuelve el mejor como «plan de contingencia» con sus incumplimientos."), Spacer(1, 2)]
    E += [H2("Paso 5 · Plan de referencia manual e impacto")]
    E += [P("<font name='DVM'>baseline.plan_referencia</font> imita lo que haría un planificador sin optimizador: en cada hora, "
            "para cada célula disponible, si su stock previsto a 8 h queda por debajo del colchón la activa a tope, "
            "por orden de célula y mientras haya recursos, respetando las reglas 2–4. Se puntúa con la misma función. "
            "El <b>impacto</b> es la diferencia Top 1 − referencia en puntuación, horas-operario, m² medios y kWh. "
            "Además, su activación se usa como <b>arranque en caliente</b> de HiGHS.")]
    E += [H2("Paso 6 · Idoneidad: gap de HiGHS y validador independiente")]
    E += bullets([
        f"<b>Idoneidad (%) = 100 · (1 − gap relativo)</b>. Con gap 0 el solver demuestra el óptimo; con el gap aceptado "
        f"({fm(100 * gap_par, 1)} %) el plan es óptimo garantizado ±{fm(100 * gap_par, 1)} %. Si el solver para por tiempo, el estado es FACTIBLE y se informa del gap real.",
        "<b>Validador independiente</b> (<font name='DVM'>validador.py</font>): recalcula con bucles explícitos todas las reglas obligatorias y la puntuación a partir de la activación del plan, sin reutilizar el modelo. Lista vacía = certificado de factibilidad.",
        f"En esta demo el validador encuentra {len(cert['06:00'])} incumplimientos en el Top 1 de las 06:00 y {len(cert['14:00'])} en el de las 14:00, y detecta {len(cert['contingencia'])} en el plan de contingencia (escenario con la célula 14 de baja).",
    ])
    E += [H2("Paso 7 · Explicación automática")]
    E += [P("Para el turno actual del Top 1, <font name='DVM'>motor</font> genera: <b>qué</b> (por célula: horas activas, franja, piezas, "
            "recursos), <b>por qué</b> (la hora en que su pieza caería por debajo del SS sin producir, si se coloca en franja solar, "
            "o «BAJA/MANTENIMIENTO» / «stock suficiente hasta HH:MM» en las inactivas) y <b>con qué impacto</b> (KPIs, comparación con "
            "Top 2/3 y baseline), más alertas (stock bajo colchón, almacén &gt; 90 %, recursos al 100 %, expedición insuficiente, plan inviable).")]
    E += [H2("Paso 8 · Rolling horizon, condición de stock final y reconfiguración")]
    E += bullets([
        "<b>Rolling horizon:</b> el horizonte de 24 h se desplaza con el tiempo; sólo se ejecuta el turno actual y se recalcula al siguiente.",
        "<b>Condición de stock final (F18):</b> sin ella el modelo vaciaría el almacén al final de las 24 h («efecto borde»). Se exige I[c,H−1] ≥ SS + envíos de las 8 h siguientes, de forma blanda (su incumplimiento avisa pero no hace inviable el plan).",
        "<b>Reconfiguración ante incidencias</b> (<font name='DVM'>rolling.reconfigurar</font>): 7 tipos de evento (baja/alta de célula, recursos reales, corrección de demanda, expedición real, mantenimiento, stock real). El stock de partida se toma del plan vigente y se recalcula 24 h desde «ahora».",
    ])
    E += [H2("Paso 9 · Simulación semanal")]
    E += [P("<font name='DVM'>rolling.simular_semana</font> recorre la semana desde el lunes 06:00 con <b>15 iteraciones</b> (3 turnos × 5 días): resuelve 24 h, "
            "consolida las 8 h del turno actual, actualiza el stock y avanza. Para acotar el tiempo usa un límite de 10 s y gap 0,5 %. "
            "Devuelve una tabla por turno con KPIs, estado, idoneidad y configuración."), PageBreak()]

    # =============================== 5 RESULTADOS
    E += [H1("5. Resultados de la demo")]
    E += [P("Escenario de ejemplo: semana del lunes 28 de septiembre de 2026, demanda 2400 VE / 1600 COMB chasis por semana; corrección "
            "del 2 oct a 520 VE / 300 COMB; mantenimiento de la célula 13 en el turno de tarde (2 técnicos); recursos reales de "
            "la tarde del 2 oct con 15 operarios; stock inicial 1,8 × SS (F19). <b>Todas las cifras se han calculado al generar este documento.</b>")]
    E += [H2("5.1 Top 3 a las 06:00 (turno de mañana)")]
    E += [tabla(filas_top(rec06), [2.8 * cm, 2.2 * cm, 2.4 * cm, 2.0 * cm, 1.6 * cm, 6.0 * cm], ["L", "L", "C", "C", "C", "L"], destacar_filas=(1,))]
    nota = ""
    if top2_mayor:
        nota = (f" Nótese que {', '.join(top2_mayor)} muestra{'n' if len(top2_mayor) > 1 else ''} una puntuación ligeramente superior "
                f"a la del Top 1: el orden sigue el <b>objetivo del MILP</b>, que además de la puntuación incluye pequeñas "
                f"penalizaciones (arranques y cobertura final), y las diferencias son del orden del gap aceptado.")
    E += [P(f"Las tres configuraciones son válidas y muy próximas en puntuación (diferencias de décimas): la decisión robusta es "
            f"elegir entre alternativas casi equivalentes según criterios que el modelo no ve.{nota}", "small")]
    E += [H2("5.2 Top 3 a las 14:00 (turno de tarde)")]
    E += [tabla(filas_top(rec14), [2.8 * cm, 2.2 * cm, 2.4 * cm, 2.0 * cm, 1.6 * cm, 6.0 * cm], ["L", "L", "C", "C", "C", "L"], destacar_filas=(1,))]
    E += [P(f"A las 14:00 se activan otras células: reponen lo que se consumió por la mañana y las que la mañana no produjo. "
            f"El Top 1 a las 14:00 mejora al plan manual en {sg(r14['punt'] - rb14['punt'], 2)} puntos "
            f"({sg(r14['opH'] - rb14['opH'], 1)} h-operario, {sg(r14['m2'] - rb14['m2'], 1)} m², {sg(r14['kwh'] - rb14['kwh'], 0)} kWh).", "small")]
    E += [H2("5.3 Comparación con el plan manual (06:00)")]
    E += [tabla(tabla_comparacion(rec06), [5.4 * cm, 3.8 * cm, 4.2 * cm, 3.6 * cm], ["L", "R", "R", "R"]), Spacer(1, 4), fig(tmp / "contrib06.png", 16.5)]
    E += [P("Figura 1. Contribución de cada criterio a la puntuación. Una contribución mayor es mejor (100 = perfección en los cinco criterios).", "cap")]
    E += [PageBreak()]
    E += [H2("5.4 Qué activar, por qué y con qué impacto (Top 1, 06:00)")]
    que = rec06.explicacion["que"]
    qt = [["Célula", "Tipo", "Horas", "Franja", "Piezas", "Horas en franja solar"]]
    for _, r in que.iterrows():
        qt.append([int(r["celula"]), r["tipo"], int(r["horas_activas"]), r["franja"], fm(r["piezas"], 0), int(r["horas_franja_solar"])])
    E += [tabla(qt, [1.8 * cm, 1.8 * cm, 1.8 * cm, 4 * cm, 2.4 * cm, 4.2 * cm], ["C"] * 6)]
    porq = [x for x in rec06.explicacion["porque"] if "activa" in x and "inactiva" not in x][:3] + \
           [x for x in rec06.explicacion["porque"] if "inactiva" in x][:1]
    E += [Spacer(1, 4)] + bullets([x.replace("<", "&lt;") for x in porq]) + [
        P(f"Impacto frente al plan manual: {sg(d_op, 1)} h-operario, {sg(d_m2, 1)} m² de almacén medio y {sg(d_kwh, 0)} kWh. "
          f"El {fm(r1['solar'], 0)} % de la energía del plan se consume en franja solar (referencia manual: {fm(rb['solar'], 0)} %).", "body")]
    if noche_solo10:
        E += [caja("<b>Noches con sólo la célula 10.</b> El motor deja el turno de noche con la célula 10 (obligatoria): la energía nocturna "
                   "pesa ×1,20 y el stock acumulado de día cubre la demanda de la noche, así que producir de noche empeoraría la puntuación. "
                   "Es una consecuencia directa de F5 (factor energético) y conviene confirmarlo con KWD.", color="#E08A00",
                   fondo=colors.HexColor("#FFF8EA"))]
    E += [Spacer(1, 4), fig(tmp / "gantt06.png", 17)]
    E += [P("Figura 2. Gantt de 24 h del Top 1 a las 06:00: células activas por hora, franja solar y cambios de turno.", "cap")]
    E += [fig(tmp / "stock06.png", 17)]
    E += [P("Figura 3. Stock de cada pieza dividido por su stock de seguridad. Todas las líneas quedan por encima de 1 (regla obligatoria).", "cap")]
    E += [fig(tmp / "almacen06.png", 17)]
    E += [P(f"Figura 4. Ocupación de producto terminado: pico de {fm(r1['pico_m2'], 0)} m² de {fm(area, 0)} m² disponibles ({fm(100 * r1['pico_m2'] / area, 0)} %).", "cap")]
    E += [fig(tmp / "energia06.png", 17)]
    E += [P(f"Figura 5. Energía de red por hora; {fm(r1['solar'], 0)} % del consumo se concentra en la franja solar.", "cap")]
    E += [PageBreak()]
    E += [H2("5.5 Top 1 a las 14:00")]
    E += [fig(tmp / "gantt14.png", 17)]
    E += [P("Figura 6. Activación de células del Top 1 a las 14:00.", "cap")]
    E += [H2("5.6 Caso de contingencia: célula 14 de baja")]
    E += [P("Escenario <font name='DVM'>escenario_contingencia.xlsx</font>: la célula 14 (VE, 77,9 kW) está de baja desde el 02/10 a las 06:00 "
            "hasta el 03/10 a las 06:00.")]
    estado_c = pc.estado
    E += bullets([
        f"<b>Resultado:</b> estado <b>{estado_c}</b>; no hay ningún plan viable (Top vacío: {len(rec_c.top)} planes) y se devuelve un plan de contingencia con puntuación {fm(pc.puntuacion, 1)} que <b>no se recomienda</b> (idoneidad «—»: no aplica).",
        f"<b>Por qué:</b> sin la célula 14 no se puede mantener el stock de seguridad de su pieza durante las 24 h; el validador lista {len(inc_c)} incumplimientos de la regla 7 (stock de seguridad), p. ej. «{inc_c[0] if inc_c else '—'}».",
        "<b>Qué hace el sistema:</b> lo comunica de forma explícita (alerta «PLAN INVIABLE»), muestra qué regla se rompe y cuándo, y permite reconfigurar (pestaña Incidencias) añadiendo, por ejemplo, un alta anticipada, una expedición reducida o un stock real distinto.",
        "<b>Valor:</b> el planificador sabe <b>con antelación</b> a qué hora se romperá el stock de seguridad y puede actuar (otra célula equivalente, hacer horas extra, negociar camiones).",
    ])
    E += [fig(tmp / "ganttc.png", 17)]
    E += [P("Figura 7. Plan de contingencia: la célula 14 aparece bloqueada (rayado rojo) y el resto trabaja al máximo posible.", "cap")]
    E += [H2("5.7 Resumen de la simulación semanal")]
    E += [fig(tmp / "semana.png", 17)]
    E += [P("Figura 8. Puntuación del plan de 24 h en cada iteración (motor frente a referencia manual) e idoneidad de cada resolución.", "cap")]
    sw = [["It.", "Turno", "Estado", "Idoneidad", "Punt. motor", "Punt. manual", "Células activas", "Tiempo (s)"]]
    for _, r in sem.iterrows():
        sw.append([int(r["iteracion"]), f"{r['turno']} {pd.Timestamp(r['fecha_turno']):%d/%m}", r["estado"], fm(float(r["idoneidad"]), 1) + " %",
                   fm(r["puntuacion_plan_24h"], 1), fm(r["puntuacion_baseline"], 1), len(r["configuracion"]), fm(r["tiempo_s"], 1)])
    E += [tabla(sw, [1 * cm, 1.9 * cm, 2.2 * cm, 2.3 * cm, 2.4 * cm, 2.4 * cm, 2.6 * cm, 2.2 * cm], ["C"] * 8, fs=7.4)]
    E += [P(f"Resumen: {len(sem) - n_fact} de {len(sem)} iteraciones OPTIMO y {n_fact} FACTIBLE (límite de 10 s). Demanda cubierta "
            f"{fm(sem['demanda_cubierta_pct'].min(), 0)} % o más en todos los turnos. Mejora media sobre el plan manual "
            f"{sg(sem_dpt.mean(), 2)} puntos; el motor supera al plan manual en {int((sem_dpt > 0).sum())} de {len(sem)} turnos. "
            f"Tiempo medio por iteración {fm(sem['tiempo_s'].mean(), 1)} s.", "small"), PageBreak()]

    # =============================== 6 APLICACIÓN
    E += [H1("6. La aplicación")]
    E += [H2("6.1 Arquitectura")]
    E += [fig(tmp / "arq.png", 16.5)]
    E += [P("Figura 9. Módulos del proyecto <font name='DVM'>kwd_motor</font>.", "cap")]
    E += [H2("6.2 Instalación y arranque en VS Code")]
    E += bullets([
        "<b>Abrir la carpeta</b> <font name='DVM'>kwd_motor</font> en VS Code (Archivo → Abrir carpeta). Acepta instalar las extensiones recomendadas.",
        "<b>Intérprete:</b> está configurado el entorno <font name='DVM'>.venv\\Scripts\\python.exe</font> (si no aparece: Ctrl+Mayús+P → «Python: Select Interpreter»). Las dependencias (HiGHS, PuLP, pandas, Streamlit, Plotly, matplotlib, ReportLab, pytest) están en <font name='DVM'>requirements.txt</font>.",
        "<b>Ejecutar y depurar</b> (F5, panel «Run and Debug»): elige una de las tres configuraciones.",
    ])
    rr = [["Configuración", "Qué hace"],
          ["Dashboard (Streamlit)", "Arranca la aplicación web en http://localhost:8501"],
          ["Plan por consola", "Imprime el Top 3 y los KPIs de data/entrada_ejemplo.xlsx"],
          ["Tests", "Ejecuta pytest (reglas, validador, rolling horizon)"]]
    E += [tabla(rr, [5 * cm, 12 * cm], ["L", "L"]), Spacer(1, 3)]
    E += bullets(["<b>Sin VS Code:</b> doble clic en <font name='DVM'>iniciar_dashboard.bat</font> (fija PYTHONPATH y lanza Streamlit).",
                  "<b>Por terminal (PowerShell):</b> <font name='DVM'>$env:PYTHONPATH=\"src\"; .venv\\Scripts\\python.exe -m streamlit run app\\dashboard.py</font>"])
    E += [H2("6.3 Recorrido por las pestañas del dashboard")]
    tabs_ = [["Pestaña", "Para qué sirve"],
             ["Recomendación", "Tarjeta del Top 1 (puntuación, idoneidad, estado), «Qué activar / Por qué / Impacto», tabla del turno y alertas"],
             ["KPIs", "Demanda cubierta, ocupación de recursos, almacén, stock mínimo frente a SS, kWh y % solar, con gráficos horarios"],
             ["Overview 24 h", "Gantt de células (VE/COMB), resumen por turno, stock vs SS y ocupación de almacén"],
             ["Alternativas", "Top 1/2/3 y plan manual lado a lado, con diferencias de KPIs"],
             ["Incidencias / Reconfigurar", "Formularios para bajas, mantenimientos, recursos reales, demanda, expediciones y stock real; recalcula y muestra antes/después"],
             ["Datos de entrada", "Edición de las hojas del Excel (st.data_editor), aplicar y recalcular, descargar el escenario"],
             ["Semana", "Simulación de 15 turnos con rolling horizon; descarga CSV"],
             ["Informe", "Genera y descarga el informe PDF de dirección"]]
    E += [KeepTogether([tabla(tabs_, [4.2 * cm, 12.8 * cm], ["L", "L"])])]
    E.insert(len(E) - 2, CondPageBreak(7 * cm))
    E += [Spacer(1, 4), P("En la barra lateral se elige el origen de datos (ejemplo o Excel propio), la fecha/hora de inicio y se pulsa "
                          "<b>Calcular plan</b>. Los supuestos (flags) están en un desplegable al pie.", "small")]
    E += [H2("6.4 Informe PDF y edición de datos")]
    E += bullets([
        "<b>Informe PDF:</b> pestaña «Informe» → «Generar», o por consola <font name='DVM'>python -m kwd.cli --entrada data/entrada_ejemplo.xlsx --inicio \"2026-10-02 14:00\" --pdf salida/informe.pdf</font>.",
        "<b>Editar datos:</b> abrir el Excel (copia de <font name='DVM'>data/entrada_ejemplo.xlsx</font>) y modificar las hojas <font name='DVM'>DemandaSemanal</font>, <font name='DVM'>CorreccionDiaria</font>, <font name='DVM'>StockActual</font>, <font name='DVM'>Disponibilidad</font> (bajas), <font name='DVM'>Mantenimientos</font>, <font name='DVM'>RecursosReales</font>, <font name='DVM'>Expediciones</font>; las hojas <font name='DVM'>Celulas</font>, <font name='DVM'>Turnos</font>, <font name='DVM'>Almacen</font> y <font name='DVM'>Parametros</font> describen la planta. La cabecera debe estar en la fila 1 con los nombres de columna exactos.",
        "También se puede editar dentro del dashboard («Datos de entrada»), aplicar y descargar el nuevo Excel.",
        "<b>Este documento</b> se regenera con <font name='DVM'>$env:PYTHONPATH=\"src\"; .venv\\Scripts\\python.exe docs\\generar_documento.py</font> (ejecuta el motor real, ~1 min).",
    ])
    E += [PageBreak()]

    # =============================== 7 LIMITACIONES
    E += [H1("7. Limitaciones y puntos abiertos")]
    E += [P("Presentamos las limitaciones con honestidad: forman parte de la solidez de la propuesta.")]
    lim = [["Punto", "Qué ocurre", "Cómo lo tratamos"],
           ["Gap relativo 0,1 %", f"La especificación pedía gap 0; demostrar el óptimo exacto supera los 30 s por simetrías y degeneración. Se acepta {fm(100 * gap_par, 1)} %.",
            "La idoneidad lo declara («óptimo garantizado ±0,1 %»). Diferencias de puntuación menores que el gap no son significativas; los Top se ordenan por puntuación global, como define KWD."],
           ["Simulación semanal con límite de 10 s", f"{n_fact} de {len(sem)} iteraciones han parado por tiempo (FACTIBLE)" +
            (f"; la de menor idoneidad llega sólo al {fm(float(sem['idoneidad'].astype(float).min()), 0)} %." if n_fact else "."),
            "Se muestra la idoneidad real de cada iteración. En operación real (30 s por plan) el gap baja; aumentar tiempo_limite_s lo mejora."],
           ["Viernes noche sin cobertura del lunes", "El horizonte termina el sábado a las 06:00 y el fin de semana no hay demanda; la condición terminal (F18) cubre 8 h de envíos posteriores, que son 0. El primer camión del lunes 06:00 no está protegido.",
            "Pendiente: ampliar la cobertura al lunes o fijar un stock objetivo de viernes."],
           ["Flags pendientes de confirmar con KWD", "F1/F2 (pieza por conjunto), F4 (turnos), F5 (factor energético), F6 (camiones), F7 (absentismo), F8 (OEE), F9–F11, F19.",
            "Preguntas concretas en la sección 8; cada flag se cambia sin tocar el modelo."],
           ["Noches con sólo la célula 10", "El factor energético nocturno ×1,20 (F5) hace que el motor concentre la producción de día.",
            "Es coherente con F5, pero depende de él: si KWD no penaliza la energía nocturna el plan de noche cambiaría."],
           ["Capacidad ideal (OEE 100 %)", "Sin paradas, cambios de útil ni rechazos.", "F8: bastaría un OEE por célula en la hoja Celulas."],
           ["Mantenimiento como dato", "No se calcula cuándo conviene hacer el mantenimiento (F3).", "Entra como input; el motor adelanta producción para cubrirlo."]]
    E += [tabla(lim, [3.6 * cm, 6.7 * cm, 6.7 * cm], ["L", "L", "L"], fs=7.6)]
    E += [PageBreak()]

    # =============================== 8 PLAN DE ACCIÓN
    E += [H1("8. Plan de acción del equipo")]
    E += [P("Punto de partida: v1.1 funcional en local (motor MILP + dashboard + informes PDF). Hito final: <b>sábado 3 de octubre, 08:30, presentación "
            "del reto</b> (selección de finalistas 09:00, presentaciones 10:00).")]
    E += [H2("Roles")]
    roles = [["#", "Rol", "Responsabilidad principal", "Entregable"],
             ["P1", "<b>Modelado y optimización</b> (líder técnico)", "Validar la formulación MILP, pesos y normalizaciones; revisar flags con KWD; ajustar restricciones (stock, espacio, energía) y rendimiento del solver.", "Modelo validado + justificación matemática (1 diapositiva)"],
             ["P2", "<b>Datos y validación con KWD</b>", "Contrastar supuestos (piezas por conjunto, turnos, franja solar, cargas de camión); preparar escenarios realistas.", "Excel de escenarios + lista de flags confirmados/cambiados"],
             ["P3", "<b>Dashboard / UX</b>", "Pulir el dashboard para uso en minutos; flujo de demo (cargar → validar → excluir → decidir → incidencia → reconfigurar).", "Dashboard final + guion de la demo en vivo"],
             ["P4", "<b>Informes y presentación</b>", "Diapositivas (problema → solución → demo → impacto → idoneidad → próximos pasos); revisar el informe PDF.", "Presentación (≤10 min) + informe PDF de ejemplo"],
             ["P5", "<b>Pruebas y calidad</b>", "Escenarios límite (célula crítica de baja, noche con poco personal, almacén lleno, demanda imposible); comprobar que ninguna recomendación viola reglas; medir tiempos.", "Informe de pruebas + 3 escenarios de demo"]]
    E += [tabla(roles, [0.9 * cm, 3.7 * cm, 8.1 * cm, 4.3 * cm], ["C", "L", "L", "L"], fs=7.6)]
    E += [H2("Cronograma")]
    crono = [["Franja", "Actividad", "Quién"],
             ["Vie 14:00–15:00", "Comida. Cada uno instala el proyecto (VS Code + iniciar_dashboard.bat) y lo arranca.", "Todos"],
             ["15:00–16:00", "Lectura de la especificación y de los flags; reparto de tareas; lista de preguntas para KWD.", "Todos (P1 coordina)"],
             ["16:00–17:00", "Espacio RRHH / contacto con la empresa: resolver flags con KWD.", "P2 (+ P1)"],
             ["16:00–19:00", "Ajustes del modelo según respuestas; escenarios de prueba; mejoras de UX; estructura del pitch.", "P1, P5, P3, P4"],
             ["19:00–21:00", "Integración: congelar versión. Ensayo de demo completo con el escenario oficial.", "Todos"],
             ["21:00–22:00", "Cena.", "—"],
             ["22:00–00:30", "Pulido: textos, gráficos, informe PDF final, diapositivas de idoneidad e impacto (vs. plan manual). Segundo ensayo cronometrado.", "P3, P4 (P1/P5 soporte)"],
             ["Sáb 07:30–08:30", "Desayuno y comprobación final del portátil: app arrancada, escenario cargado, PDF generado, plan B en vídeo/capturas.", "Todos"],
             ["Sáb 08:30", "Presentación.", "P4 + P1 (demo: P3)"]]
    E += [tabla(crono, [3.0 * cm, 10.2 * cm, 3.8 * cm], ["L", "L", "L"], fs=7.7)]
    E += [H2("Reglas de trabajo")]
    E += bullets(["<b>Una única fuente de verdad:</b> <font name='DVM'>docs/ESPECIFICACION.md</font>. Cualquier cambio de supuesto implica actualizar el flag y el test.",
                  "<b>Antes de congelar:</b> pytest en verde y validador sin incumplimientos en los 3 escenarios de demo.",
                  "<b>Plan B:</b> capturas y PDF generados de antemano por si falla el portátil."])
    E += [H2("Mensajes clave del pitch")]
    E += bullets([
        "<b>1. Qué activar, por qué y con qué impacto:</b> una decisión clara y explicable, no sólo una configuración posible.",
        f"<b>2. Idoneidad demostrada:</b> el solver certifica el Top 1 como óptimo garantizado a ±{fm(100 * gap_par, 1)} % y un validador independiente comprueba todas las reglas.",
        "<b>3. Planta dinámica:</b> rolling horizon de 24 h; reconfiguración en segundos ante averías, bajas o cambios de demanda.",
        f"<b>4. Impacto cuantificado frente al plan manual:</b> {fm(abs(d_op), 1)} horas-operario, {fm(abs(d_m2), 0)} m² medios y {fm(abs(d_kwh), 0)} kWh menos en 24 h; {sg(d_pt, 2)} puntos.",
        "<b>5. Preparado para evolucionar:</b> supuestos aislados como flags configurables (piezas por conjunto, mantenimiento externo, turnos).",
    ])
    E += [H2("Preguntas para KWD (espacio de las 16:00)")]
    E += [P("Derivadas de los flags; ordenadas por impacto en el modelo. P2 las lleva impresas y anota la respuesta junto a cada una.")]
    q = [["#", "Flag", "Pregunta", "Si la respuesta cambia…"],
         ["1", "F1/F2", "¿Cada célula fabrica una pieza distinta, y un ciclo es una pieza? ¿Cuántas piezas de cada célula lleva un conjunto (chasis)?", "piezas_por_conjunto por célula; SS y m² cambian"],
         ["2", "F10", "¿Los stocks de seguridad son 400 piezas por célula VE y 200 por combustión, o dependen de la pieza?", "ss_ve / ss_comb (o SS por célula)"],
         ["3", "F6", "¿Los 15 camiones salen realmente cada 1,5 h desde las 06:00? ¿Qué carga media llevan y cuál es el máximo de 15 m²?", "Parámetros de camiones; hoja Expediciones"],
         ["4", "F4", "¿Los turnos son 06–14, 14–22 y 22–06? ¿Se trabaja sábados, domingos o con horas extra?", "TURNOS y DIAS_LABORABLES"],
         ["5", "F5", "¿La franja solar (11–17 h, −15 % de energía) y el sobrecoste nocturno (+20 %) son correctos? ¿Cuánto pesa la energía de verdad?", "factor_solar, factor_noche; peso_energia"],
         ["6", "F7/F8", "¿Qué absentismo y qué OEE reales tienen las células? ¿Alguna célula tiene paradas conocidas o cambios de útil?", "absentismo; OEE por célula"],
         ["7", "F11", "¿La célula 10 (logística) debe estar siempre activa? ¿Puede pararse en caso de falta de personal?", "Regla 2 / F14"],
         ["8", "F3", "¿Quién decide los mantenimientos y con cuánta antelación se conocen? ¿Se pueden mover?", "Mantenimientos como input o variable"],
         ["9", "F9", "¿El colchón de seguridad del 10 % es un objetivo real? ¿Qué pasa si el stock baja del SS un par de horas?", "colchon_ss; si el SS es duro o blando"],
         ["10", "F12/F18", "¿Cómo se planifica el fin de semana y el lunes por la mañana? ¿Cobertura mínima el viernes noche?", "cobertura_final_h; ampliar horizonte"],
         ["11", "F19", "¿Con qué stock inicial se arranca un día normal? (Con 1,3 × SS el escenario es inviable.)", "Hoja StockActual; realismo del escenario"],
         ["12", "Pesos", "¿Los pesos 50/20/15/10/5 son fijos? ¿Se mide la «ocupación de recursos» como media o como pico?", "Parámetros peso_*; definición de R"]]
    E += [tabla(q, [0.7 * cm, 1.5 * cm, 9.2 * cm, 5.6 * cm], ["C", "C", "L", "L"], fs=7.5), PageBreak()]

    # =============================== 9 ANEXO
    E += [H1("Anexo. Glosario")]
    gl = [["Término", "Significado"],
          ["MILP", "Programación lineal entera mixta: optimizar una función lineal con variables enteras (0/1) y continuas bajo restricciones lineales."],
          ["HiGHS", "Solver de código abierto que resuelve el MILP (invocado vía PuLP)."],
          ["Rolling horizon", "Planificar 24 h, ejecutar sólo el primer turno y recalcular al avanzar el tiempo o ante una incidencia."],
          ["Gap relativo", "Distancia entre la mejor solución y la mejor cota demostrada, en proporción. Gap 0 = óptimo demostrado."],
          ["Idoneidad", "100 · (1 − gap). Mide cuán cerca está el plan del óptimo; se complementa con el validador independiente."],
          ["Validador", "Módulo que recalcula las reglas obligatorias y la puntuación sin usar el modelo; lista vacía = plan factible."],
          ["SS (stock de seguridad)", "Piezas mínimas de cada tipo que deben estar siempre en almacén (400 VE / 200 COMB en el supuesto F10)."],
          ["Colchón", "Margen adicional sobre el SS (10 %) que sólo puntúa; no es obligatorio."],
          ["OEE", "Overall Equipment Effectiveness: disponibilidad × rendimiento × calidad de una máquina. Aquí se supone 100 % (F8)."],
          ["Célula", "Puesto de soldadura que fabrica un tipo de pieza (VE o combustión)."],
          ["Chasis / conjunto", "Producto final que se ensambla con una pieza de cada célula de su tipo y se expide en camión."],
          ["VE / COMB", "Vehículo eléctrico / vehículo de combustión."],
          ["Slot", "Hora del horizonte (24 por plan)."],
          ["Corte", "Restricción añadida para excluir una configuración ya encontrada y obtener la siguiente mejor (Top 2, Top 3)."],
          ["Baseline / referencia manual", "Plan de una heurística sin optimizar, usado para cuantificar el impacto."],
          ["Franja solar", "11:00–17:00: la energía fotovoltaica cubre el 15 % de la potencia (factor 0,85)."],
          ["Flag", "Supuesto del modelo identificado (F1…F19), documentado, visible y modificable."],
          ["FACTIBLE / OPTIMO / INVIABLE", "Estados del plan: parado por tiempo con gap; óptimo demostrado; incumple alguna regla obligatoria (contingencia)."]]
    E += [tabla(gl, [4.2 * cm, 12.8 * cm], ["L", "L"])]

    # figura + pie siempre juntos
    E2, i = [], 0
    while i < len(E):
        if isinstance(E[i], Image) and i + 1 < len(E) and isinstance(E[i + 1], Paragraph) and E[i + 1].style.name == "cap":
            E2.append(KeepTogether([E[i], E[i + 1]]))
            i += 2
        else:
            E2.append(E[i])
            i += 1
    # saltos de página: sólo tras portada, índice y resumen ejecutivo; el resto fluye con salto condicional
    E3, n_pb = [], 0
    for fl in E2:
        if isinstance(fl, PageBreak):
            n_pb += 1
            if n_pb <= 3:
                E3.append(fl)
            continue
        if isinstance(fl, Paragraph) and fl.style.name == "h1" and n_pb >= 3:
            E3.append(PageBreak() if fl.getPlainText().startswith("Anexo") else CondPageBreak(9 * cm))
        E3.append(fl)
    E = E3

    doc = Doc(SALIDA)
    doc.multiBuild(E)
    kb = SALIDA.stat().st_size / 1024
    print(f"PDF generado: {SALIDA} · {doc.page} páginas · {kb:.0f} KB · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
