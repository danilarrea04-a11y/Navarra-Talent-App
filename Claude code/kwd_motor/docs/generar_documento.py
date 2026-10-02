"""Genera salida/KWD_Solucion_y_Plan_de_Accion.pdf ejecutando el motor real.

Uso (desde la raíz del proyecto, PowerShell):
    $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe docs\\generar_documento.py

Todas las cifras, tablas y gráficos se calculan en cada ejecución con `kwd.motor.recomendar` y
`kwd.rolling.contingencia` (tarda ~2 min: 8 s por plan).
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
from kwd.config import NOMBRES_COMPONENTE, NOMBRES_TURNO, PARAMETROS_DEFECTO

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
    opt = datos.stock_optimo(esc, hz.slots["inicio"].iloc[0])
    fig_, axs = plt.subplots(1, 2, figsize=(8.4, 3.3), sharey=True)
    fin = list(range(1, len(hz.slots) + 1))
    for ax, ve, nom, col in [(axs[0], True, "Piezas VE", C_VE), (axs[1], False, "Piezas combustión", C_COMB)]:
        _decor_horizonte(ax, hz, esc, etiquetas=False)
        cs = [c for c in plan.stock.columns if bool(t.loc[c, "es_ve"]) == ve]
        for c in cs:
            ax.plot(fin, plan.stock[c] / ss[c], color=col, lw=0.9, alpha=0.75)
        ax.axhline(1.0, color=C_ROJO, lw=1.2)
        ax.axhline(opt[cs[0]] / ss[cs[0]], color="#E08A00", lw=1, ls="--")
        for k in hz.cierres:
            ax.axvline(k + 1, color="#5A6070", lw=0.6, ls=":")
        ax.set_title(nom, loc="left")
        _eje_horas(ax, hz)
        ax.set_xlabel("Hora")
    axs[0].set_ylabel("Stock / stock de seguridad")
    axs[1].legend(handles=[Patch(fc="none", ec=C_ROJO, label="SS"),
                           Patch(fc="none", ec="#E08A00", label="Stock óptimo (SS + 1 turno)")], frameon=False,
                  fontsize=7.5, loc="upper right")
    fig_.suptitle(titulo, x=0.01, ha="left", fontsize=9, fontweight="bold", color=NAVY_HEX)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_almacen(plan, hz, esc, ruta):
    A = esc.area_producto_terminado()
    fig_, ax = plt.subplots(figsize=(8.4, 2.9))
    _decor_horizonte(ax, hz, esc, etiquetas=False)
    x = list(range(1, len(hz.slots) + 1))
    ax.plot(x, plan.espacio.values, color=NAVY_HEX, lw=2, label="Top 1")
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
    ax.set_xlabel("Contribución al Índice KWD (máx. 100 = 50+20+15+10+5)")
    ax.legend(ncol=5, frameon=False, fontsize=7.3, loc="upper center", bbox_to_anchor=(0.5, -0.32))
    ax.set_title("Contribución de cada criterio KWD al Índice KWD", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=170)
    plt.close(fig_)


def g_libres(plan, ruta):
    from kwd.config import ETIQUETA_ROL, RECURSOS
    k = plan.kpis
    fig_, ax = plt.subplots(figsize=(8.4, 2.4))
    tot = [k[f"horas_libres_{r}"] for r in RECURSOS]
    ocu = [k[f"ocupacion_{r}_pct"] for r in RECURSOS]
    x = range(len(RECURSOS))
    ax.bar(x, tot, color=NAVY_HEX, width=0.55)
    for i, (v, o) in enumerate(zip(tot, ocu)):
        ax.text(i, v + max(tot) * 0.02, f"{fm(v, 1)} h\n(ocup. {fm(o, 0)} %)", ha="center", fontsize=7.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels([ETIQUETA_ROL[r] for r in RECURSOS])
    ax.set_ylim(0, max(tot) * 1.3)
    ax.set_ylabel("Horas libres (persona-h)")
    ax.set_title("Tiempo muerto del personal presente por rol (Top 1, 06:00, 24 h)", loc="left")
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

    caja_(1, 40, 17, 11, "Estado", "data/estado.json", "#8A90A6")
    caja_(23, 40, 17, 11, "datos.py", "carga + validación", NAVY_HEX)
    caja_(45, 40, 17, 11, "horizonte.py", "24 slots horarios", NAVY_HEX)
    caja_(67, 40, 17, 11, "modelo.py", "MILP · PuLP + HiGHS", "#3E64B8")
    caja_(66, 22, 19, 11, "motor.py", "Top 1/2/3 · explicación", "#3E64B8")
    caja_(44, 22, 17, 11, "personal.py", "trabajadores M-OP01…", NAVY_HEX)
    caja_(22, 22, 17, 11, "validador.py", "certifica reglas", NAVY_HEX)
    caja_(89, 22, 10.5, 11, "rolling.py", "contingencia", "#3E64B8")
    caja_(1, 4, 24, 11, "app/dashboard.py", "Streamlit · 9 pestañas", C_VE)
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
def filas_top(rec):
    f = [["Plan", "Estado", "Índice KWD", "Idoneidad", "Horas libres", "Células activas en el turno actual"]]
    for p in (rec.top or [rec.contingencia]):
        f.append([p.nombre, p.estado, fm(p.puntuacion, 2), p.idoneidad_txt().replace(".", ","),
                  fm(p.kpis["horas_libres_total"], 1) + " h", ", ".join(map(str, p.config_turno_actual))])
    return f


def kp_plan(p):
    k = p.kpis
    return {"punt": p.puntuacion, "ido": p.idoneidad, "libres": k["horas_libres_total"],
            "ocup": k["ocupacion_total_pct"], "m2": k["m2_medio"], "pico_m2": k["m2_pico"], "kwh": k["kwh_total"],
            "solar": k["kwh_solar_pct"], "maxpt": k.get("puntuacion_max_teorica"), "dev": k["stock_opt_dev_media_pct"],
            "devmax": k["stock_opt_dev_max_pct"], "cam": k["camiones_dia"], "cub": k["demanda_cubierta_pct"]}


def tabla_antes_despues(ra, rd):
    pa, pd_ = ra.mejor, rd.mejor
    ka, kd = pa.kpis, pd_.kpis
    f = [["Indicador (24 h)", "Antes de la incidencia", "Después", "Diferencia"]]
    for nom, key, d, u in [("Índice KWD", "puntuacion", 2, ""), ("Horas libres (todo el personal)", "horas_libres_total", 1, " h"),
                           ("Ocupación del personal", "ocupacion_total_pct", 1, " %"),
                           ("Desviación media vs stock óptimo", "stock_opt_dev_media_pct", 1, " %"),
                           ("Almacén medio", "m2_medio", 1, " m²"), ("Energía de red", "kwh_total", 0, " kWh")]:
        a, b = ka[key], kd[key]
        f.append([nom, fm(a, d) + u, fm(b, d) + u, sg(b - a, d) + u])
    return f


def main():
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="kwd_doc_"))
    SALIDA.parent.mkdir(exist_ok=True)

    print("Ejecutando el motor…")
    esc = datos.estado_ejemplo()
    cache = os.environ.get("KWD_DOC_CACHE")  # sólo para depurar el maquetado; vacío = ejecuta el motor
    import pickle
    if cache and os.path.exists(cache):
        rec06, rec14, c14, cgrave = pickle.load(open(cache, "rb"))
    else:
        ini = pd.Timestamp("2026-10-02 06:00")
        rec06 = motor.recomendar(esc, ini)
        rec14 = motor.recomendar(esc, pd.Timestamp("2026-10-02 14:00"))
        ahora_c = pd.Timestamp("2026-10-02 10:00")
        print("Contingencia C14…")
        c14 = rolling.contingencia(esc, rec06, ahora_c, bajas_celulas=[{"celula": 14, "desde": ahora_c, "hasta": None}])
        print("Contingencia grave C3+C14+C15…")
        cgrave = rolling.contingencia(esc, rec06, ahora_c, bajas_celulas=[
            {"celula": c, "desde": ahora_c, "hasta": None} for c in (3, 14, 15)])
        if cache:
            pickle.dump((rec06, rec14, c14, cgrave), open(cache, "wb"))
    print(f"Motor listo en {time.time() - t0:.0f} s")

    t1, t14 = rec06.top[0], rec14.top[0]
    hz06 = rec06.horizonte
    cert = {n: validador.validar(esc, r.horizonte, r.mejor) for n, r in [("06:00", rec06), ("14:00", rec14)]}
    ss = datos.ss_por_celula(esc)
    tc = datos.tabla_celulas(esc)
    area = esc.area_producto_terminado()
    suma_ss = datos.validar_ss_almacen(esc)
    gap_par = float(esc.parametros.get("gap_relativo", 0.001))
    pesos = {c: float(esc.parametros[p]) for c, p in
             [("R", "peso_recursos"), ("S", "peso_espacio"), ("Q", "peso_calidad_mto"), ("B", "peso_stock"), ("E", "peso_energia")]}
    ve_d, comb_d = datos.demanda_dia(esc, "2026-10-02")
    r1, r14 = kp_plan(t1), kp_plan(t14)
    rec_c, rec_g = c14["rec_despues"], cgrave["rec_despues"]
    pc, pg = rec_c.mejor, rec_g.mejor
    k1 = t1.kpis
    _act = t1.activacion
    h_c3 = float(_act[3].sum()) if 3 in _act.columns else float(_act['3'].sum())
    from kwd.config import ETIQUETA_ROL, RECURSOS

    # ---- gráficos
    g_gantt(t1, hz06, esc, tmp / "gantt06.png", "Top 1 a las 06:00 · activación de células en 24 h")
    g_stock(t1, hz06, esc, tmp / "stock06.png", "Stock por pieza frente al SS y al stock óptimo (Top 1, 06:00)")
    g_almacen(t1, hz06, esc, tmp / "almacen06.png")
    g_energia(t1, hz06, esc, tmp / "energia06.png")
    g_contrib([t1, t14], ["Top 1 · 06:00", "Top 1 · 14:00"], tmp / "contrib06.png")
    g_libres(t1, tmp / "libres06.png")
    g_gantt(t14, rec14.horizonte, esc, tmp / "gantt14.png", "Top 1 a las 14:00 · activación de células en 24 h")
    g_gantt(pc, rec_c.horizonte, c14["escenario"], tmp / "ganttc.png", "Contingencia: avería de la célula 14 desde las 10:00")
    g_gantt(pg, rec_g.horizonte, cgrave["escenario"], tmp / "ganttg.png", "Contingencia grave: células 3, 14 y 15 averiadas desde las 10:00")
    g_arquitectura(tmp / "arq.png")

    E = []
    # =============================== PORTADA
    portada = Table([[[Spacer(1, 4.2 * cm), P("Motor de decisión de producción — KWD España", "titulo"),
                       Spacer(1, 0.5 * cm), P("Navarra Talent Challenge 2026", "sub"), Spacer(1, 1.2 * cm),
                       P("Solución paso a paso y plan de acción del equipo", "sub"), Spacer(1, 4 * cm),
                       P("2 de octubre de 2026<br/>Presentación del reto: sábado 3 de octubre de 2026, 08:30", "sub"),
                       Spacer(1, 3.5 * cm)]]], colWidths=[17 * cm])
    portada.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * cm),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * cm), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    E += [portada, Spacer(1, 0.6 * cm),
          caja("<b>Qué es este documento.</b> Explica el problema de KWD Automotive, el modelo matemático que lo resuelve, "
               "los resultados reales de la demo (calculados al generar este PDF ejecutando el motor, versión v3), cómo usar la "
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
            "activar (16 células, personal limitado, stock de seguridad, almacén de 800 m², camiones cada 1,5 h) de forma "
            "que se cumplan las reglas obligatorias y se optimicen cinco criterios ponderados: <b>personal sin tiempo muerto 50 %</b>, "
            "espacio 20 %, calidad y mantenimiento 15 %, stock óptimo 10 % y energía 5 %."),
          P("<b>Nuestra respuesta: qué activar, por qué y con qué impacto.</b> Un motor de optimización (MILP con HiGHS) "
            "que, dado el estado de la planta, devuelve las <b>3 mejores configuraciones del turno</b>, la "
            "<b>explicación</b> de cada decisión, a <b>cada trabajador</b> (M-OP01…) su puesto hora a hora, una "
            "<b>idoneidad</b> certificada (gap del solver + validador independiente) y la <b>Índice KWD máximo alcanzable</b>. "
            "Ante una avería o una baja, la pestaña <b>Contingencia</b> calcula el plan de reubicación, las máquinas a "
            "activar y, si hay riesgo de desabastecimiento, un <b>aviso para dirección</b>.")]
    E += [Spacer(1, 3), H2("Resultados clave de la demo (2 oct 2026, 06:00)")]
    maxpt = fm(r1["maxpt"], 2) if (r1["maxpt"] is not None and r1["maxpt"] >= r1["punt"] - 1e-9) else "no disponible"
    kp = [["Indicador", "Valor", "Lectura"],
          ["Índice KWD Top 1 (06:00)", f"{fm(r1['punt'], 2)} (máx. alcanzable {maxpt})", f"idoneidad {t1.idoneidad_txt().replace('.', ',')} = Índice del plan / máximo alcanzable"],
          ["Horas libres del personal (24 h)", f"{fm(r1['libres'], 1)} h", f"ocupación del personal presente {fm(r1['ocup'], 1)} % (criterio R, el de más peso)"],
          ["Stock vs óptimo al cierre de turno", f"desv. media {fm(r1['dev'], 1)} %, máx. {fm(r1['devmax'], 1)} %", "óptimo = SS + demanda de un turno"],
          ["Almacén medio / pico", f"{fm(r1['m2'], 1)} / {fm(r1['pico_m2'], 0)} m²", f"capacidad {fm(area, 0)} m²"],
          ["Energía de red", f"{fm(r1['kwh'], 0)} kWh", f"{fm(r1['solar'], 0)} % en franja solar (11–14 h)"],
          ["Demanda cubierta", f"{fm(r1['cub'], 0)} %", f"{fm(r1['cam'], 0)} camiones/día, 16 ciclos de expedición"],
          ["Validador independiente", f"{len(cert['06:00'])} incumplimientos (06:00), {len(cert['14:00'])} (14:00)", "reglas obligatorias recalculadas sin el modelo"],
          ["Contingencia: avería de la C14 (10:00)", f"{pc.estado}", f"{len(c14['reubicacion'])} cambios de puesto; aviso a dirección: {'sí' if c14['aviso_direccion'] else 'no'}"],
          ["Contingencia grave: C3 + C14 + C15", f"{pg.estado}", f"aviso a dirección: {'sí' if cgrave['aviso_direccion'] else 'no'}"]]
    E += [tabla(kp, [5.3 * cm, 5.3 * cm, 6.4 * cm], ["L", "L", "L"]), Spacer(1, 6)]
    E += [caja(f"<b>Mensaje.</b> El motor ocupa al personal presente sin sobrepasar los recursos: en las 24 h de la demo quedan "
               f"{fm(r1['libres'], 0)} horas-persona libres, mantiene el stock cerca del óptimo de cierre de turno y recibe "
               f"el estado de la planta desde la aplicación (<font name='DVM'>data/estado.json</font>, sin Excel). "
               f"Ante una avería grave consume stock de seguridad para aguantar, avisa a dirección y repone después.")]
    E += [H2("Qué contiene la aplicación")]
    E += bullets([
        "<b>Motor</b> (paquete <font name='DVM'>kwd</font>): estado persistente en JSON, horizonte de 24 h, modelo MILP, Top 1/2/3, asignación nominal de trabajadores, explicación, validador, contingencia y rolling horizon.",
        "<b>Dashboard Streamlit</b> con 9 pestañas en este orden: Datos, Recomendación (con «Generar informe PDF» arriba), Overview 24 h, Contingencia, Planta en tiempo real (sólo visualización), KPIs, Trabajadores, Alternativas y Semana.",
        "<b>Informe PDF</b> para dirección (desde el dashboard o por consola) y <b>CLI</b> (<font name='DVM'>python -m kwd.cli</font>).",
        "<b>Batería de pruebas</b> (pytest) y escenarios de demo: estado de ejemplo, contingencia de una célula y contingencia grave.",
    ])
    E += [PageBreak()]

    # =============================== 2 PROBLEMA
    E += [H1("2. El problema")]
    E += [P("En cada momento hay que decidir <b>qué células de soldadura están activas cada hora</b> durante las próximas "
            "24 h, de modo que se atienda la demanda de <b>piezas</b> (VE y combustión) que sale en camiones, sin romper las "
            "reglas obligatorias y con la mejor Índice KWD según los pesos de KWD. KWD no ensambla: <b>cada célula fabrica "
            "una pieza exclusiva</b> y la demanda llega por tipo (VE/COMB) para cada pieza de ese tipo.")]
    E += [H2("Entradas (pestaña Datos → data/estado.json)")]
    E += bullets([
        "<b>Demanda semanal</b> (piezas VE y COMB por referencia, 5 días) y <b>demanda corregida</b> de un día. Demanda del día = corregida si está confirmada; si no, semanal / 5. "
        f"Con 1.500 coches/día y proporción 2 COMB : 1 VE son <b>{fm(ve_d, 0)} piezas VE y {fm(comb_d, 0)} COMB de cada referencia al día</b>.",
        "<b>Bajas por turno</b> de todos los roles (o por trabajador), <b>paradas programadas</b> de células (con técnicos ocupados), <b>stock actual</b> y <b>expediciones reales</b>.",
        "<b>Constantes</b> fijas en el código (sólo lectura en la app): células, turnos estándar, almacén y parámetros. No se usa Excel.",
    ])
    E += [H2("Reglas obligatorias")]
    E += bullets([
        "La <b>célula 10</b> (servicio logístico) está activa en todas las horas laborables y <b>no puede averiarse ni pararse</b>; no hay producción fuera de lunes–viernes.",
        "Las células <b>11 y 12</b> funcionan siempre en pareja (misma activación y mismo uso).",
        "Una célula de <b>baja</b> o en <b>parada programada</b> no puede activarse en ese intervalo.",
        "La carga de las células activas cabe en el <b>personal presente</b> de cada rol en cada hora (personas enteras, trabajadores enumerados M-OP01…).",
        "El producto terminado cabe en los <b>800 m²</b>. El stock de seguridad (SS) <b>puede consumirse</b> para servir un camión (se repone con máxima prioridad); un pedido sin servir (stock &lt; 0) no descarta el plan: lo marca <b>CRÍTICO</b> y genera un aviso para dirección.",
    ])
    E += [H2("Criterios KWD, personal, almacén y expediciones")]
    pt = [["Criterio KWD", "Peso", "Qué mide (menor valor = mejor)"],
          ["R · Personal sin tiempo muerto", f"{pesos['R']:.0%}", "Tiempo muerto real de TODO el personal presente: (presentes − trabajo productivo) / presentes, con trabajo = carga de la célula × fracción de la hora en que produce"],
          ["S · Espacio de almacén", f"{pesos['S']:.0%}", "m² ocupados de producto terminado / 800 m²"],
          ["Q · Calidad y mantenimiento", f"{pesos['Q']:.0%}", "Uso de técnicos de mantenimiento y de calidad (más margen libre = mejor)"],
          ["B · Stock óptimo", f"{pesos['B']:.0%}", "Desviación |stock − óptimo| / óptimo en cada cierre de turno (06:00, 14:00, 22:00)"],
          ["E · Energía", f"{pesos['E']:.0%}", "kWh de red ponderados por factor horario (premia la franja solar 11–14 h)"]]
    E += [tabla(pt, [4.6 * cm, 1.4 * cm, 11 * cm], ["L", "C", "L"])]
    trn = esc.turnos.set_index("recurso")
    E += [Spacer(1, 6)]
    rt = [["Recurso (estándar)", "Mañana 06–14", "Tarde 14–22", "Noche 22–06"]] + \
         [[r.capitalize(), fm(trn.loc[r, "M"], 0), fm(trn.loc[r, "T"], 0), fm(trn.loc[r, "N"], 0)] for r in trn.index]
    alm = esc.almacen.set_index("zona")["m2"]
    E += [tabla(rt, [5 * cm, 4 * cm, 4 * cm, 4 * cm], ["L", "C", "C", "C"])]
    E += [P(f"Sin fila de bajas se descuenta un absentismo del {float(esc.parametros['absentismo']):.0%} (redondeo comercial). "
            f"<b>Almacén:</b> materia prima {fm(alm['materia_prima'], 0)} m², cargas {fm(alm['cargas'], 0)} m², "
            f"producto terminado {fm(alm['producto_terminado'], 0)} m² (la que limita el plan). <b>Expediciones:</b> "
            f"{fm(float(esc.parametros['ciclos_dia']), 0)} ciclos por día laborable, uno cada "
            f"{fm(float(esc.parametros['intervalo_camion_h']), 1)} h desde las 06:00; en cada ciclo salen <b>tantos camiones de "
            f"{fm(float(esc.parametros['m2_max_camion']), 0)} m² como hagan falta</b>. <b>Horizonte:</b> {int(float(esc.parametros['horas_horizonte']))} h, "
            f"slots de una hora, recalculado en cada turno o incidencia. <b>Tiempo límite: "
            f"{fm(float(esc.parametros['tiempo_limite_s']), 0)} s por plan.</b>", "small")]
    ct = [["Cél.", "Tipo", "Ciclo (s)", "Cap. (pz/h)", "Pz/m²", "Oper.", "Pick.", "Carr.", "Mto", "Cal.", "kW", "SS (pz)"]]
    for c, r in tc.iterrows():
        ct.append([c, r["tipo"], fm(r["ciclo_s"], 0), fm(r["cap_h"], 0), fm(r["piezas_m2"], 0), fm(r["operarios"], 1),
                   fm(r["picking"], 1), fm(r["carretilleros"], 1), fm(r["mto"], 2), fm(r["calidad"], 2), fm(r["kw"], 1),
                   "—" if c == 10 else fm(ss[c], 0)])
    E += [KeepTogether([H2("Las 16 células"), tabla(ct, [1.1 * cm, 1.3 * cm, 1.5 * cm, 1.7 * cm, 1.4 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm,
                     1.5 * cm, 1.6 * cm], ["C"] * 12, fs=7.5),
                        P("Cap. = capacidad con OEE 100 % (3600 / ciclo). Recursos = personas equivalentes que exige la célula mientras "
                          "está activa. La célula 10 no produce piezas ni ocupa almacén; 11 y 12 van en pareja.", "small")]),
          PageBreak()]

    # =============================== 3 DECISIONES DE MODELADO
    E += [H1("3. Decisiones de modelado e hipótesis")]
    E += [P("El enunciado deja huecos. Las decisiones del equipo (v2 y v3) quedan documentadas en <font name='DVM'>docs/</font> "
            "(no en la aplicación) y los valores numéricos son constantes o parámetros del código, así que cambiar un "
            "supuesto no obliga a rehacer el modelo.")]
    sup = [["Tema", "Decisión adoptada"],
           ["Pieza exclusiva", "Cada célula fabrica una pieza que ninguna otra puede fabricar; un ciclo = una pieza. La demanda por tipo (VE/COMB) aplica a cada pieza de ese tipo; no hay conjuntos ni chasis."],
           ["Demanda", f"1.500 coches/día con 2 COMB : 1 VE → {fm(ve_d, 0)} VE y {fm(comb_d, 0)} COMB de cada referencia al día, repartidas por igual entre los 16 ciclos del día."],
           ["Expediciones", "Ciclos cada 1,5 h desde las 06:00 (16 al día); cada ciclo envía los camiones de 15 m² que hagan falta, sin recortar la carga."],
           ["Franja solar", "11–14 h: factor energético ×0,85; noche (22–06) ×1,20; resto ×1,00."],
           ["Célula 10", "Servicio logístico siempre activo en horas laborables; no puede averiarse ni pararse; no ocupa almacén."],
           ["Personal", "Personas enteras, enumeradas (M-OP01…), asignadas puesto a puesto; cada una está ASIGNADA, LIBRE o PARADA (técnico en una parada). Sin plantilla ni excedente: se ocupa a todo el presente."],
           ["Criterio R", "Tiempo muerto real: (presentes − trabajo productivo)/presentes. Activar una célula sin producir no reduce el tiempo muerto: sólo cuenta la fracción de la hora en que produce."],
           ["Stock de seguridad", "Tamaño fijo (400 pz por célula VE, 200 por COMB). Un camión puede consumirlo; la reposición tiene prioridad máxima."],
           ["Stock óptimo", "SS + demanda de un turno de la pieza (SS + demanda diaria / 3). Se penaliza |stock − óptimo| en cada cierre de turno; sustituye al colchón del 10 % y a la condición de stock final."],
           ["Jerarquía de penalizaciones", "Pedido no servido (stock &lt; 0) ≫ stock bajo SS ≫ criterios R, S, Q, B, E. Con pedidos sin servir el plan es CRÍTICO y se avisa a dirección; INVIABLE sólo por almacén &gt; 800 m² o reglas de células."],
           ["Capacidad", "OEE 100 % (3600 / ciclo); sin cambios de útil ni rechazos."],
           ["Solver", "HiGHS, 8 s por plan en dos fases (activación continua, luego personas enteras con arranque en caliente); gap relativo aceptado 0,1 %."]]
    E += [tabla(sup, [3.6 * cm, 13.4 * cm], ["L", "L"], fs=7.7)]
    E += [Spacer(1, 6),
          caja([P(f"<b>Validación de «cada célula = una pieza».</b> Con el stock de seguridad (400 piezas por célula VE, 200 por "
                  f"combustión), la suma ΣSS<sub>c</sub> / densidad<sub>c</sub> sobre las 15 células productivas vale "
                  f"<b>{fm(suma_ss, 1)} m²</b>, frente a los <b>≈150 m²</b> que indica KWD (desviación "
                  f"{fm(100 * abs(suma_ss - 150) / 150, 1)} %). Sólo encaja si cada célula produce una pieza distinta y un "
                  f"ciclo equivale a una pieza.")], color=C_VE, fondo=colors.HexColor("#F0F8F4")),
          PageBreak()]

    # =============================== 4 SOLUCIÓN PASO A PASO
    E += [H1("4. La solución paso a paso")]
    E += [P("Ocho pasos, del estado de la planta a la contingencia. Cada uno corresponde a un módulo del paquete.")]
    E += [H2("Paso 1 · Estado de entrada")]
    E += [P("<font name='DVM'>datos.cargar_estado</font> lee <font name='DVM'>data/estado.json</font> (demanda semanal y corregida, "
            "bajas, paradas, stock actual, expediciones reales) sobre las constantes del código; <font name='DVM'>datos.guardar_estado</font> "
            "lo escribe tras cada cambio en la pestaña Datos; <font name='DVM'>datos.estado_ejemplo()</font> genera el estado de la demo (la app ya no tiene botón «Restaurar ejemplo»). "
            "La demanda de un día es la corregida si se ha confirmado; si no, la semanal / 5.")]
    E += [H2("Paso 2 · Construcción del horizonte horario")]
    E += [P("<font name='DVM'>horizonte.construir_horizonte</font> genera 24 slots de una hora desde el instante de inicio y calcula, "
            "para cada uno:")]
    E += bullets([
        "<b>Turno</b> (M 06–14, T 14–22, N 22–06) y si es <b>laborable</b> (lunes–viernes; fin de semana sin producción ni camiones).",
        "<b>Personal presente</b> por rol: estándar menos bajas (o menos el absentismo si no hay fila); los técnicos se restan durante las paradas programadas.",
        "<b>Células bloqueadas</b> por bajas o paradas (la célula 10 nunca).",
        "<b>Ciclos de expedición</b> (cada 1,5 h, 16 al día) con su carga y los camiones de 15 m² necesarios.",
        "<b>Stock óptimo</b> de cada pieza en los cierres de turno (06:00, 14:00, 22:00) y <b>factor energético</b> horario (×0,85 de 11 a 14 h, ×1,20 de noche).",
    ])
    E += [H2("Paso 3 · Modelo MILP")]
    E += [P("<font name='DVM'>modelo.resolver</font> plantea un problema de programación lineal entera mixta con HiGHS vía PuLP. "
            "Conjuntos: C = células, P = C \\ {10}, H = 24 horas, W ⊆ H horas laborables, K = cierres de turno.")]
    E += [P("<b>Variables</b>", "h3"), formulas([
        "a[c,h] ∈ {0,1}     célula c activa (personal comprometido) en la hora h",
        "u[c,h] ∈ [0,1]     fracción de la hora que produce",
        "N[k,h] ∈ ℕ         personas ocupadas del rol k en la hora h",
        "I[c,h] libre       stock de la pieza al final de h (negativo = pedido sin servir)",
        "s[c,h], sn[c,h] ≥ 0   SS consumido y pedido no servido;   sa[h] ≥ 0 exceso de almacén",
        "d⁺[c,κ], d⁻[c,κ] ≥ 0  desviación del stock respecto al óptimo en el cierre κ"]),
          P("<b>Restricciones</b>", "h3"), formulas([
              "(1)  u[c,h] ≤ a[c,h]",
              "(2)  a[10,h] = 1  ∀h ∈ W ;   a[c,h] = 0  ∀h ∉ W o si c está bloqueada",
              "(3)  a[11,h] = a[12,h] ;   u[11,h] = u[12,h]",
              "(4)  Σ_c req[c,k]·a[c,h] ≤ N[k,h] ≤ Disp[k,h]   ∀k, ∀h ∈ W    (personas enteras)",
              "(5)  I[c,h] = I[c,h-1] + cap[c]·u[c,h] − env[c,h]",
              "(6)  I[c,h] ≥ −sn[c,h] ;   I[c,h] ≥ SS[c] − s[c,h]             (pedido servido / SS)",
              "(7)  I[c,κ] − opt[c,κ] = d⁺[c,κ] − d⁻[c,κ]                     (stock óptimo en cierres)",
              "(8)  Σ_{c∈P} I[c,h] / dens[c] ≤ 800 + sa[h]                    (almacén)"]),
          P("<b>Función objetivo e Índice KWD</b> (componentes normalizados en [0,1]; menor es mejor)", "h3"), formulas([
              "R = Σ_{h∈W} (Disp − trabajo) / Σ_{h∈W} Disp ,  trabajo = Σ_c req[c,k]·u[c,h]    peso 50 %",
              "S = media_h ( m² ocupados / 800 )                                              peso 20 %",
              "Q = media_{h∈W} ½( mto N/Disp + calidad N/Disp )                               peso 15 %",
              "B = media_{c,κ} pen_tramos(d⁺, d⁻) / opt[c,κ]   (por tramos, ver abajo)         peso 10 %",
              "E = Σ f[h]·kW[c]·u[c,h] / Σ máx(f)·kW[c]                                       peso  5 %",
              "min  0,5R + 0,2S + 0,15Q + 0,1B + 0,05E + 1000·Σ sn/SS + 50·Σ s/SS + pen(sa) + 0,0001·Σ arranques",
              "Índice KWD = 100 · (1 − (0,5R + 0,2S + 0,15Q + 0,1B + 0,05E))"])]
    E += [Spacer(1, 4), P("La jerarquía 1000 (pedido no servido) ≫ 50 (SS consumido) ≫ criterios hace que, ante una avería grave, "
                          "el plan consuma SS para aguantar y, resuelta la incidencia, reponga primero el SS y luego vuelva al "
                          "stock óptimo. Sólo un exceso de almacén o la ruptura de reglas de células hace «INVIABLE» un plan.")]
    E += [P("<b>Desviación del stock óptimo por tramos.</b> El criterio B no penaliza linealmente |stock − óptimo|: mide la desviación al cierre de "
            "cada turno en unidades D = demanda de un turno de la pieza (D = demanda diaria / 3 = óptimo − SS) y cobra más cuanto más se aleja:"),
          tabla([["Desviación", "Tramo", "Factor"],
                 ["Exceso (sobre el óptimo)", "0 – 0,25 D", "×0"], ["", "0,25 – 0,5 D", "×1"], ["", "0,5 – 1 D", "×5"], ["", "&gt; 1 D", "×30"],
                 ["Defecto (bajo el óptimo)", "0 – 0,25 D", "×0"], ["", "resto", "×2"]], [6 * cm, 5 * cm, 3 * cm], ["L", "L", "L"], fs=7.8),
          Spacer(1, 3),
          P("Por debajo del SS se aplica además la penalización existente de 50; el pedido no servido sigue en 1000. "
            f"<b>Por qué:</b> antes de los tramos, la célula 3 funcionaba las 24 h sólo para ocupar operarios y su stock llegaba a "
            f"<b>+207 %</b> sobre el óptimo. Con los tramos, la desviación máxima en la demo es de <b>{fm(r1['devmax'], 0)} %</b> "
            f"y la célula 3 trabaja unas <b>{fm(h_c3, 0)} h</b>. La penalización es convexa (cada tramo cuesta más que el anterior), "
            "de modo que el solver llena primero los tramos baratos y no hacen falta variables binarias.")]
    E += [H2("Paso 4 · Top 1, Top 2 y Top 3 por cortes")]
    E += [P("Una variable binaria y<sub>c</sub> indica si la célula c se activa en algún momento del turno actual. Tras obtener la "
            "configuración S<sub>j</sub> se añade un <b>corte</b> que la prohíbe, y se vuelve a resolver:")]
    E += [formulas(["y[c] ≥ a[c,h] ∀h del turno actual ;  y[c] ≤ Σ_{h∈turno} a[c,h]",
                    "Σ_{c∈Sj} (1 − y[c]) + Σ_{c∉Sj} y[c] ≥ 1       (corte: configuración distinta a Sj)"]),
          P("Así se obtienen K = 3 configuraciones <b>realmente distintas</b> del turno actual. El Top 1 es el plan con menos piezas "
            "sin servir y, a igualdad, mejor Índice KWD; las alternativas cuyo gap del solver supera el 50 % (cota demasiado lejana tras el tiempo límite) "
            "no se presentan."), Spacer(1, 2)]
    E += [H2("Paso 5 · Asignación nominal de trabajadores")]
    E += [P("<font name='DVM'>personal.asignacion_personal</font> reparte, hora a hora, a los trabajadores enumerados (M-OP01…, T-OP01…, N-…) "
            "entre las células activas según la carga de cada rol, manteniendo en lo posible a cada persona en su puesto. "
            "Cada persona presente queda <b>ASIGNADA</b> (con sus células), <b>LIBRE</b> (tiempo muerto) o <b>PARADA</b> "
            "(técnico de mantenimiento en una parada programada). De ahí salen las horas libres por rol y el roster por trabajador.")]
    E += [H2("Paso 6 · Idoneidad, Índice KWD máximo alcanzable y validador")]
    E += bullets([
        f"<b>Idoneidad (%) = Índice KWD del plan / índice máximo alcanzable demostrado por el solver × 100.</b> El máximo alcanzable es el Índice KWD del plan más el margen que permite la cota del solver (100 · objetivo · gap). En la demo el Top 1 de las 06:00 tiene idoneidad {t1.idoneidad_txt().replace('.', ',')}: consigue casi todo lo que estos recursos y esta demanda permiten.",
        "<b>Índice KWD</b> (antes «puntuación»): escala 0–100 que sirve para <b>comparar planes entre sí</b>. El 100 no es alcanzable con estos recursos y esta demanda (siempre hay personal sin trabajo productivo, almacén ocupado y energía consumida), por lo que la idoneidad se mide contra el máximo demostrable y no contra 100.",
        f"Si el solver para por tiempo (8 s) el estado es FACTIBLE y se informa del gap real; las alternativas Top 2/3 con gap &gt; 50 % se descartan. Gap relativo aceptado: {fm(100 * gap_par, 1)} %.",
        "<b>Validador independiente</b> (<font name='DVM'>validador.py</font>): recalcula con bucles explícitos reglas, personal e Índice KWD a partir de la activación del plan, sin reutilizar el modelo. Lista vacía = certificado.",
        f"En esta demo el validador encuentra {len(cert['06:00'])} incumplimientos en el Top 1 de las 06:00 y {len(cert['14:00'])} en el de las 14:00.",
    ])
    E += [H2("Paso 7 · Explicación automática")]
    E += [P("Para el turno actual del Top 1, <font name='DVM'>motor</font> genera: <b>qué</b> (por célula: horas activas, franja, piezas, "
            "recursos), <b>por qué</b> (la hora en que su pieza caería por debajo del SS sin producir, si se coloca en franja solar, "
            "o «PARADA/AVERÍA» / «stock suficiente hasta HH:MM» en las inactivas) y <b>con qué impacto</b> (KPIs y diferencia frente a "
            "Top 2/3), más alertas (stock lejos del óptimo, almacén &gt; 90 %, recursos al 100 %, plan CRÍTICO o INVIABLE).")]
    E += [H2("Paso 8 · Contingencia a petición y rolling horizon")]
    E += bullets([
        "<b>Rolling horizon:</b> el horizonte de 24 h se desplaza con el tiempo; sólo se ejecuta el turno actual y se recalcula al siguiente.",
        "<b>Contingencia</b> (<font name='DVM'>rolling.contingencia</font>): se seleccionan células averiadas (desde/hasta) y/o personas concretas o nº por rol y se pulsa <i>Calcular plan de contingencia</i>. Parte del stock del plan vigente y devuelve plan antes/después, <b>reubicación</b> de trabajadores, <b>máquinas</b> a activar, consumo de SS y agotamiento por pieza, desabastecimiento por ciclo, <b>aviso para dirección</b> y un resumen en lenguaje de planta.",
        "<b>Aplicar como incidencia real</b> fija la incidencia en el estado. La pestaña «Planta en tiempo real» es sólo visualización.",
    ])
    E += [PageBreak()]

    # =============================== 5 RESULTADOS
    E += [H1("5. Resultados de la demo")]
    E += [P("Estado de ejemplo: semana del lunes 28 de septiembre de 2026, 1.500 coches/día (2 COMB : 1 VE), es decir 2.500 VE / 5.000 COMB "
            "piezas por referencia y semana; parada programada de la célula 13 el 2 oct en el turno de tarde (2 técnicos), un operario de baja "
            "esa tarde y stock inicial de 2 × SS. <b>Todas las cifras se han calculado al generar este documento.</b>")]
    E += [H2("5.1 Top 3 a las 06:00 (turno de mañana)")]
    E += [tabla(filas_top(rec06), [2.4 * cm, 2.2 * cm, 2.3 * cm, 2.0 * cm, 2.3 * cm, 5.8 * cm], ["L", "L", "C", "C", "C", "L"], destacar_filas=(1,))]
    E += [P(f"Tiempo de cálculo del Top 3: {fm(rec06.tiempo_total_s, 0)} s (8 s por plan). Las configuraciones mostradas son válidas y "
            f"se comparan en Índice KWD y horas libres."
            + (f" Sólo se muestra {len(rec06.top)} configuración(es): las alternativas cuyo gap del solver supera el 50 % tras el límite de 8 s se descartan." if len(rec06.top) < 3 else ""), "small")]
    E += [H2("5.2 Top 3 a las 14:00 (turno de tarde)")]
    E += [tabla(filas_top(rec14), [2.4 * cm, 2.2 * cm, 2.3 * cm, 2.0 * cm, 2.3 * cm, 5.8 * cm], ["L", "L", "C", "C", "C", "L"], destacar_filas=(1,))]
    E += [P(f"A las 14:00 hay una parada programada de la célula 13 (2 técnicos) y un operario de baja. Top 1: Índice KWD "
            f"{fm(r14['punt'], 2)}, {fm(r14['libres'], 1)} h libres en 24 h, idoneidad {t14.idoneidad_txt().replace('.', ',')}.", "small")]
    E += [H2("5.3 Horas libres: el criterio principal")]
    E += [P(f"El tiempo muerto se mide sobre el <b>trabajo productivo</b> (carga de la célula × fracción de la hora en que produce). En la demo del Top 1 de las 06:00 son <b>{fm(r1['libres'], 0)} h</b> de tiempo muerto en 24 h, frente a unas 114 h antes de los tramos de stock óptimo: aquella cifra menor se lograba sobreproduciendo para ocupar personal, y los tramos lo impiden.", "small")]
    hl = [["Rol", "Horas libres (24 h)", "Ocupación"]]
    for r in RECURSOS:
        hl.append([ETIQUETA_ROL[r], fm(k1[f"horas_libres_{r}"], 1) + " h", fm(k1[f"ocupacion_{r}_pct"], 1) + " %"])
    hl.append(["Total personal presente", fm(k1["horas_libres_total"], 1) + " h", fm(k1["ocupacion_total_pct"], 1) + " %"])
    E += [tabla(hl, [6 * cm, 5 * cm, 4 * cm], ["L", "C", "C"], destacar_filas=(len(hl) - 1,)), Spacer(1, 4),
          fig(tmp / "libres06.png", 16), P("Figura 1. Tiempo muerto (horas-persona sin trabajo productivo) por rol en las 24 h del Top 1 de las 06:00.", "cap")]
    E += [fig(tmp / "contrib06.png", 16.5)]
    E += [P(f"Figura 2. Contribución de cada criterio al Índice KWD (100 = perfección en los cinco). Índice KWD máximo alcanzable del Top 1: {maxpt}.", "cap")]
    E += [H2("5.4 Qué activar, por qué y con qué impacto (Top 1, 06:00)")]
    que = rec06.explicacion["que"]
    qt = [["Célula", "Tipo", "Horas", "Franja", "Piezas", "Horas en franja solar"]]
    for _, r in que.iterrows():
        qt.append([int(r["celula"]), r["tipo"], int(r["horas_activas"]), r["franja"], fm(r["piezas"], 0), int(r["horas_franja_solar"])])
    E += [tabla(qt, [1.8 * cm, 1.8 * cm, 1.8 * cm, 4 * cm, 2.4 * cm, 4.2 * cm], ["C"] * 6)]
    porq = [x for x in rec06.explicacion["porque"] if "activa" in x and "inactiva" not in x][:3] + \
           [x for x in rec06.explicacion["porque"] if "inactiva" in x][:1]
    E += [Spacer(1, 4)] + bullets([x.replace("<", "&lt;") for x in porq])
    E += [P(f"Impacto: {fm(r1['libres'], 1)} h libres en 24 h (ocupación {fm(r1['ocup'], 1)} %), almacén medio {fm(r1['m2'], 0)} m² y "
            f"{fm(r1['kwh'], 0)} kWh de red, de los que el {fm(r1['solar'], 0)} % en franja solar.", "body")]
    for al in rec06.alertas[:4]:
        E += [P("• " + al.replace("<", "&lt;"), "small")]
    E += [Spacer(1, 4), fig(tmp / "gantt06.png", 17)]
    E += [P("Figura 3. Gantt de 24 h del Top 1 a las 06:00: células activas por hora, franja solar y cambios de turno.", "cap")]
    E += [H2("5.5 Stock frente al óptimo")]
    sv = t1.stock_vs_optimo
    cierres = sv.groupby("cierre", sort=True).agg(dm=("desviacion_pct", "mean"), dx=("desviacion_pct", "max")).reset_index()
    ct2 = [["Cierre de turno", "Desviación media vs óptimo", "Desviación máxima"]]
    for _, r in cierres.iterrows():
        ct2.append([f"{pd.Timestamp(r['cierre']):%d/%m %H:%M}", fm(r["dm"], 1) + " %", fm(r["dx"], 1) + " %"])
    E += [tabla(ct2, [5 * cm, 6 * cm, 5 * cm], ["L", "C", "C"])]
    cv = next(c for c in sv["celula"].unique() if tc.loc[c, "es_ve"])
    cb = next(c for c in sv["celula"].unique() if not tc.loc[c, "es_ve"])
    e1 = sv[sv["cierre"] == cierres["cierre"].iloc[0]]
    ej = [["Pieza (primer cierre)", "Stock", "SS", "Óptimo (SS + 1 turno)", "Desviación"]]
    for c in (cv, cb):
        r = e1[e1["celula"] == c].iloc[0]
        ej.append([f"Célula {c} ({tc.loc[c, 'tipo']})", fm(r["stock"], 0), fm(ss[c], 0), fm(r["optimo"], 0), sg(r["desviacion"], 0)])
    E += [Spacer(1, 4), tabla(ej, [4.5 * cm, 2.6 * cm, 2.6 * cm, 4.3 * cm, 3 * cm], ["L", "C", "C", "C", "C"]), Spacer(1, 4)]
    E += [fig(tmp / "stock06.png", 17)]
    E += [P("Figura 4. Stock de cada pieza dividido por su SS. Línea roja: SS; línea naranja: stock óptimo (SS + demanda de un turno).", "cap")]
    E += [fig(tmp / "almacen06.png", 17)]
    E += [P(f"Figura 5. Ocupación de producto terminado: pico de {fm(r1['pico_m2'], 0)} m² de {fm(area, 0)} m² disponibles ({fm(100 * r1['pico_m2'] / area, 0)} %).", "cap")]
    E += [fig(tmp / "energia06.png", 17)]
    E += [P(f"Figura 6. Energía de red por hora; {fm(r1['solar'], 0)} % del consumo bruto se concentra en la franja solar (11–14 h).", "cap")]
    E += [H2("5.6 Trabajadores asignados (extracto, turno de mañana)")]
    tr = t1.trabajadores
    tr = tr[tr["turno"] == "M"]
    ext = pd.concat([tr[tr["rol"] == r].head(2) for r in RECURSOS]) if len(tr) else tr
    rost = [["Trabajador", "Rol", "H. asignado", "H. libre", "Recorrido por células"]]
    for _, r in ext.iterrows():
        rost.append([r["trabajador"], ETIQUETA_ROL.get(r["rol"], r["rol"]), int(r["horas_asignado"]), int(r["horas_libre"]), r["celulas"] or "—"])
    E += [tabla(rost, [2.4 * cm, 2.6 * cm, 2.2 * cm, 2.0 * cm, 7.8 * cm], ["L", "L", "C", "C", "L"], fs=7.4)]
    E += [P(f"El roster completo ({len(t1.trabajadores)} filas para el horizonte) está en la pestaña Trabajadores del dashboard.", "small")]
    E += [H2("5.7 Top 1 a las 14:00")]
    E += [fig(tmp / "gantt14.png", 17)]
    E += [P("Figura 7. Activación de células del Top 1 a las 14:00 (la célula 13, rayada, en parada programada).", "cap")]

    # ---- contingencia
    E += [H2("5.8 Contingencia: avería de la célula 14 (10:00)")]
    E += [P("Se pulsa «Calcular plan de contingencia» a las 10:00 con la célula 14 (VE) averiada hasta nuevo aviso. El sistema recalcula "
            "desde el stock del plan vigente y responde en lenguaje de planta:")]
    E += bullets([x.replace("<", "&lt;") for x in c14["resumen"][:7]])
    E += [Spacer(1, 4), tabla(tabla_antes_despues(rec06, rec_c), [5.6 * cm, 4 * cm, 3.6 * cm, 3.8 * cm], ["L", "R", "R", "R"])]
    mq = c14["maquinas"]
    if len(mq):
        mt = [["Célula", "Horas antes", "Horas después", "Δ", "Franjas nuevas"]]
        for _, r in mq.iterrows():
            mt.append([int(r["celula"]), int(r["horas_antes"]), int(r["horas_despues"]), sg(r["delta"], 0), r["franjas_nuevas"] or "—"])
        E += [Spacer(1, 4), P("Máquinas a activar / cambios de horario", "h3"), tabla(mt, [2 * cm, 2.5 * cm, 3 * cm, 1.5 * cm, 8 * cm], ["C", "C", "C", "C", "L"], fs=7.4)]
    ru = c14["reubicacion"]
    if len(ru):
        cols = [c for c in ["persona", "rol", "de_celula", "a_celulas", "desde", "hasta"] if c in ru.columns]
        rt_ = [[c.replace("_", " ").capitalize() for c in cols]]
        for _, r in ru.head(8).iterrows():
            rt_.append([(f"{pd.Timestamp(r[c]):%H:%M}" if c in ("desde", "hasta") else (r[c] or "LIBRE")) for c in cols])
        E += [Spacer(1, 4), P(f"Reubicación de trabajadores (8 de {len(ru)} cambios)", "h3"), tabla(rt_, [17 / len(cols) * cm] * len(cols), ["L"] * len(cols), fs=7.2)]
    E += [Spacer(1, 4), fig(tmp / "ganttc.png", 17)]
    E += [P(f"Figura 8. Plan tras la avería de la célula 14 (rayado rojo); estado {pc.estado}.", "cap")]

    E += [H2("5.9 Contingencia grave: células 3, 14 y 15 averiadas")]
    ag = cgrave["agotamiento"]
    des = cgrave["desabastecimiento"]
    E += [P(f"Con tres células averiadas desde las 10:00 el plan queda en estado <b>{pg.estado}</b>. "
            f"{'Consume stock de seguridad en ' + str(len(ag)) + ' pieza(s)' if len(ag) else 'No consume stock de seguridad'}"
            f"{' y deja ' + fm(float(des['piezas_no_servidas'].sum()), 0) + ' piezas sin servir en ' + str(des['ciclo'].nunique()) + ' ciclo(s)' if len(des) else ' y sirve todos los pedidos'}.")]
    if len(ag):
        at = [["Pieza", "Baja del SS", "Se queda sin stock", "Repone el SS", "Stock mínimo"]]
        f_ = lambda x: "—" if pd.isna(x) else f"{pd.Timestamp(x):%d/%m %H:%M}"  # noqa: E731
        for _, r in ag.iterrows():
            at.append([int(r["pieza"]), f_(r["hora_bajo_ss"]), f_(r["hora_sin_stock"]), f_(r["hora_repone_ss"]), fm(r["stock_min"], 0)])
        E += [tabla(at, [2 * cm, 3.6 * cm, 4 * cm, 3.6 * cm, 3.8 * cm], ["C"] * 5, fs=7.6)]
    if cgrave["aviso_direccion"]:
        E += [Spacer(1, 5), caja(f"<b>Ejemplo de aviso para dirección (generado por el motor).</b><br/>{cgrave['aviso_direccion'].replace('<', '&lt;')}",
                                 color=C_ROJO, fondo=colors.HexColor("#FDF0EE"), st="small")]
    else:
        E += [P("En este caso el motor consigue servir todos los pedidos y no genera aviso para dirección.", "small")]
    E += [Spacer(1, 4), fig(tmp / "ganttg.png", 17)]
    E += [P("Figura 9. Plan de contingencia grave: células 3, 14 y 15 bloqueadas y resto trabajando al máximo posible.", "cap")]
    E += [PageBreak()]

    # =============================== 6 APLICACIÓN
    E += [H1("6. La aplicación")]
    E += [H2("6.1 Arquitectura")]
    E += [fig(tmp / "arq.png", 16.5)]
    E += [P("Figura 10. Módulos del proyecto <font name='DVM'>kwd_motor</font>.", "cap")]
    E += [H2("6.2 Instalación y arranque en VS Code")]
    E += bullets([
        "<b>Abrir la carpeta</b> <font name='DVM'>kwd_motor</font> en VS Code (Archivo → Abrir carpeta). Acepta instalar las extensiones recomendadas.",
        "<b>Intérprete:</b> está configurado el entorno <font name='DVM'>.venv\\Scripts\\python.exe</font> (si no aparece: Ctrl+Mayús+P → «Python: Select Interpreter»). Las dependencias están en <font name='DVM'>requirements.txt</font>.",
        "<b>Ejecutar y depurar</b> (F5, panel «Run and Debug»): elige una de las configuraciones.",
    ])
    rr = [["Configuración", "Qué hace"],
          ["Dashboard (Streamlit)", "Arranca la aplicación web en http://localhost:8501"],
          ["Plan por consola", "Imprime el Top 3 y los KPIs de data/estado.json"],
          ["Tests", "Ejecuta pytest (reglas, validador, contingencia)"]]
    E += [tabla(rr, [5 * cm, 12 * cm], ["L", "L"]), Spacer(1, 3)]
    E += bullets(["<b>Sin VS Code:</b> doble clic en <font name='DVM'>iniciar_dashboard.bat</font> (fija PYTHONPATH y lanza Streamlit).",
                  "<b>Por terminal (PowerShell):</b> <font name='DVM'>$env:PYTHONPATH=\"src\"; .venv\\Scripts\\python.exe -m streamlit run app\\dashboard.py</font>"])
    E += [H2("6.3 Recorrido por las pestañas del dashboard")]
    tabs_ = [["Pestaña", "Para qué sirve"],
             ["Datos", "Demanda: sólo 4 entradas enteras (piezas VE y COMB por semana y piezas VE y COMB corregidas del día); además bajas por turno, paradas programadas, stock actual y expediciones reales; se guarda en data/estado.json. Constantes en un expander de sólo lectura. No hay botón «Restaurar ejemplo»"],
             ["Recomendación", "Botón «Generar informe PDF» arriba. Top 1 (Índice KWD, máximo alcanzable, idoneidad, estado), «Qué activar / Por qué / Impacto» y alertas; aviso a dirección si el plan es CRÍTICO"],
             ["Overview 24 h", "Gantt de células (VE/COMB), resumen por turno, stock vs SS/óptimo y ocupación de almacén"],
             ["Contingencia", "Única pestaña de incidencias: células averiadas (desde/hasta) y personas o nº por rol; «Calcular plan de contingencia» sólo calcula al pulsar. Muestra resumen en lenguaje de planta, reubicación, máquinas, consumo de SS, aviso a dirección y KPIs antes/después; «Aplicar como incidencia real»"],
             ["Planta en tiempo real", "Sólo visualización: selector de hora, rejilla de 16 células con su estado, trabajadores asignados y stock frente a SS y óptimo"],
             ["KPIs", "Horas libres por rol y total (KPI principal), ocupación, stock vs óptimo al cierre de turno, almacén y energía"],
             ["Trabajadores", "Roster por trabajador (M-OP01…): puesto hora a hora, horas asignadas y libres"],
             ["Alternativas", "Top 1/2/3 lado a lado, con diferencias de KPIs"],
             ["Semana", "Simulación de 15 turnos con rolling horizon y los mismos 8 s por plan que la app (≈2,5 min en total): producción por turno y evolución del stock por pieza; descarga CSV"]]
    E += [tabla(tabs_, [3.8 * cm, 13.2 * cm], ["L", "L"], fs=7.6)]
    E += [Spacer(1, 4), P("En la barra lateral se elige la fecha/hora de inicio y se pulsa <b>Calcular plan</b>.", "small")]
    E += [H2("6.4 Informe PDF y datos")]
    E += bullets([
        "<b>Informe PDF:</b> pestaña «Recomendación» → «Generar informe PDF», o por consola <font name='DVM'>python -m kwd.cli --estado data/estado.json --inicio \"2026-10-02 14:00\" --pdf salida/informe.pdf</font>.",
        "<b>Editar datos:</b> en la pestaña Datos; cada cambio se guarda en <font name='DVM'>data/estado.json</font> (demanda semanal y diaria, bajas, paradas, stock actual, expediciones reales). No hay Excel.",
        "<b>Este documento</b> se regenera con <font name='DVM'>$env:PYTHONPATH=\"src\"; .venv\\Scripts\\python.exe docs\\generar_documento.py</font> (ejecuta el motor real, varios minutos).",
    ])
    E += [PageBreak()]

    # =============================== 7 LIMITACIONES
    E += [H1("7. Limitaciones y puntos abiertos")]
    E += [P("Presentamos las limitaciones con honestidad: forman parte de la solidez de la propuesta.")]
    lim = [["Punto", "Qué ocurre", "Cómo lo tratamos"],
           ["Límite de 8 s por plan", f"El solver puede parar por tiempo (FACTIBLE); idoneidad del Top 1 de las 06:00: {t1.idoneidad_txt().replace('.', ',')}; las alternativas con gap &gt; 50 % se descartan.",
            "La idoneidad y la Índice KWD máximo alcanzable declaran el margen real de mejora; subir tiempo_limite_s lo reduce."],
           ["Gap relativo 0,1 %", "Demostrar el óptimo exacto supera el tiempo disponible (8 s por plan); se acepta un gap relativo pequeño.",
            "Diferencias de Índice KWD menores que el gap no son significativas."],
           ["Viernes noche sin cobertura del lunes", "El horizonte termina el sábado a las 06:00 y el fin de semana no hay demanda; el primer camión del lunes 06:00 no queda protegido por el stock óptimo.",
            "Pendiente: ampliar el horizonte o fijar un óptimo de viernes."],
           ["Supuestos por confirmar con KWD", "Pieza exclusiva por célula, SS de 400/200, 16 ciclos de 1,5 h, franja solar 11–14 h, factor nocturno ×1,20 y OEE 100 %.",
            "Preguntas concretas en la sección 8; son parámetros o constantes del código."],
           ["Capacidad ideal (OEE 100 %)", "Sin paradas, cambios de útil ni rechazos.", "Bastaría un OEE por célula en las constantes."],
           ["Paradas como dato", "No se decide cuándo conviene hacer el mantenimiento.", "Entra como parada programada; el motor adelanta producción para cubrirla."],
           ["Contingencia a petición", "El plan de contingencia se calcula al pulsar, no de forma automática.", "Evita recálculos innecesarios; «Aplicar como incidencia real» lo consolida."]]
    E += [tabla(lim, [3.6 * cm, 6.7 * cm, 6.7 * cm], ["L", "L", "L"], fs=7.6)]
    E += [PageBreak()]

    # =============================== 8 PLAN DE ACCIÓN
    E += [H1("8. Plan de acción del equipo")]
    E += [P("Punto de partida: v3 funcional en local (motor MILP + dashboard + informes PDF). Hito final: <b>sábado 3 de octubre, 08:30, presentación "
            "del reto</b> (selección de finalistas 09:00, presentaciones 10:00).")]
    E += [H2("Roles")]
    roles = [["#", "Rol", "Responsabilidad principal", "Entregable"],
             ["P1", "<b>Modelado y optimización</b> (líder técnico)", "Validar la formulación MILP, pesos y normalizaciones; revisar supuestos con KWD; ajustar la jerarquía de penalizaciones (SS, pedido no servido) y el rendimiento del solver en 8 s.", "Modelo validado + justificación matemática (1 diapositiva)"],
             ["P2", "<b>Datos y validación con KWD</b>", "Contrastar supuestos (pieza exclusiva, turnos, franja solar, camiones de 15 m²); preparar estados realistas en la pestaña Datos.", "Estados de demo (estado.json) + lista de supuestos confirmados/cambiados"],
             ["P3", "<b>Dashboard / UX</b>", "Pulir el dashboard para uso en minutos; flujo de demo (cargar datos → planta en tiempo real → recomendación → contingencia → aviso a dirección).", "Dashboard final + guion de la demo en vivo"],
             ["P4", "<b>Informes y presentación</b>", "Diapositivas (problema → solución → demo → horas libres e impacto → idoneidad → próximos pasos); revisar el informe PDF.", "Presentación (≤10 min) + informe PDF de ejemplo"],
             ["P5", "<b>Pruebas y calidad</b>", "Escenarios límite (varias células averiadas, noche con poco personal, almacén lleno, demanda alta); comprobar que ninguna recomendación viola reglas; medir tiempos.", "Informe de pruebas + 3 escenarios de demo"]]
    E += [tabla(roles, [0.9 * cm, 3.7 * cm, 8.1 * cm, 4.3 * cm], ["C", "L", "L", "L"], fs=7.6)]
    E += [H2("Cronograma")]
    crono = [["Franja", "Actividad", "Quién"],
             ["Vie 14:00–15:00", "Comida. Cada uno instala el proyecto (VS Code + iniciar_dashboard.bat) y lo arranca.", "Todos"],
             ["15:00–16:00", "Lectura de CAMBIOS_V3 y de los supuestos; reparto de tareas; lista de preguntas para KWD.", "Todos (P1 coordina)"],
             ["16:00–17:00", "Espacio RRHH / contacto con la empresa: resolver supuestos con KWD.", "P2 (+ P1)"],
             ["16:00–19:00", "Ajustes del modelo según respuestas; escenarios de prueba; mejoras de UX; estructura del pitch.", "P1, P5, P3, P4"],
             ["19:00–21:00", "Integración: congelar versión. Ensayo de demo completo con el estado oficial.", "Todos"],
             ["21:00–22:00", "Cena.", "—"],
             ["22:00–00:30", "Pulido: textos, gráficos, informe PDF final, diapositivas de idoneidad, horas libres y contingencia. Segundo ensayo cronometrado.", "P3, P4 (P1/P5 soporte)"],
             ["Sáb 07:30–08:30", "Desayuno y comprobación final del portátil: app arrancada, estado cargado, PDF generado, plan B en vídeo/capturas.", "Todos"],
             ["Sáb 08:30", "Presentación.", "P4 + P1 (demo: P3)"]]
    E += [tabla(crono, [3.0 * cm, 10.2 * cm, 3.8 * cm], ["L", "L", "L"], fs=7.7)]
    E += [H2("Reglas de trabajo")]
    E += bullets(["<b>Una única fuente de verdad:</b> <font name='DVM'>docs/ESPECIFICACION.md</font> con los cambios <font name='DVM'>CAMBIOS_V2.md</font> y <font name='DVM'>CAMBIOS_V3.md</font>, que prevalecen. Cualquier cambio de supuesto implica actualizar la documentación y el test.",
                  "<b>Antes de congelar:</b> pytest en verde y validador sin incumplimientos en los escenarios de demo.",
                  "<b>Plan B:</b> capturas y PDF generados de antemano por si falla el portátil."])
    E += [H2("Mensajes clave del pitch")]
    E += bullets([
        "<b>1. Qué activar, por qué y con qué impacto:</b> una decisión clara y explicable, y a cada trabajador su puesto hora a hora.",
        f"<b>2. Idoneidad demostrada:</b> el solver certifica el Top 1 con idoneidad {t1.idoneidad_txt().replace('.', ',')} (Índice KWD máximo alcanzable {maxpt}) y un validador independiente comprueba todas las reglas.",
        f"<b>3. Personal sin tiempo muerto:</b> el criterio de más peso mide las horas libres reales; en la demo quedan {fm(r1['libres'], 0)} horas-persona libres en 24 h.",
        "<b>4. Stock óptimo y reposición:</b> el stock tiende a SS + un turno de demanda; ante una avería grave se consume SS para aguantar y se repone después, con aviso a dirección si hay pedidos sin servir.",
        "<b>5. Contingencia a petición:</b> en segundos, el plan de reubicación de personas y máquinas para una avería o una baja.",
    ])
    E += [H2("Preguntas para KWD (espacio de las 16:00)")]
    E += [P("Ordenadas por impacto en el modelo. P2 las lleva impresas y anota la respuesta junto a cada una.")]
    q = [["#", "Tema", "Pregunta", "Si la respuesta cambia…"],
         ["1", "Pieza", "¿Cada célula fabrica una pieza exclusiva y un ciclo es una pieza?", "SS y m² cambian"],
         ["2", "SS", "¿Los stocks de seguridad son 400 piezas por célula VE y 200 por combustión, o dependen de la pieza?", "ss_ve / ss_comb (o SS por célula)"],
         ["3", "Camiones", "¿Salen realmente cada 1,5 h desde las 06:00 (16 ciclos), con tantos camiones de 15 m² como hagan falta?", "Parámetros de expedición"],
         ["4", "Demanda", "¿1.500 coches/día con doble de combustión que de VE es el régimen normal? ¿Cuándo se confirma la demanda corregida?", "Demanda por defecto"],
         ["5", "Turnos", "¿Los turnos son 06–14, 14–22 y 22–06? ¿Se trabaja fines de semana o con horas extra?", "Turnos y días laborables"],
         ["6", "Energía", "¿La franja solar (11–14 h, −15 %) y el sobrecoste nocturno (+20 %) son correctos? ¿Cuánto pesa la energía?", "factor_solar, factor_noche; peso_energia"],
         ["7", "Personal", "¿Cómo se registran las bajas por trabajador y se puede mover a un operario entre células durante un turno?", "Reglas de asignación"],
         ["8", "Célula 10", "¿La célula 10 (logística) debe estar siempre activa y no puede averiarse?", "Regla de la célula 10"],
         ["9", "Stock óptimo", "¿El stock óptimo (SS + un turno de demanda) es el objetivo correcto? ¿Es aceptable consumir SS ante una avería?", "Penalizaciones B y de SS"],
         ["10", "Fin de semana", "¿Cómo se planifica el fin de semana y el lunes por la mañana? ¿Cobertura mínima el viernes noche?", "Horizonte / stock objetivo"],
         ["11", "OEE y paradas", "¿Qué OEE reales tienen las células y quién decide las paradas programadas y con qué antelación?", "OEE por célula; paradas como variable"],
         ["12", "Pesos", "¿Los pesos 50/20/15/10/5 son fijos? ¿Se acepta medir R como tiempo muerto del personal presente?", "Parámetros peso_*"]]
    E += [tabla(q, [0.7 * cm, 2.2 * cm, 8.8 * cm, 5.3 * cm], ["C", "C", "L", "L"], fs=7.5), PageBreak()]

    # =============================== 9 ANEXO
    E += [H1("Anexo. Glosario")]
    gl = [["Término", "Significado"],
          ["MILP", "Programación lineal entera mixta: optimizar una función lineal con variables enteras (0/1) y continuas bajo restricciones lineales."],
          ["HiGHS", "Solver de código abierto que resuelve el MILP (invocado vía PuLP)."],
          ["Rolling horizon", "Planificar 24 h, ejecutar sólo el primer turno y recalcular al avanzar el tiempo o ante una incidencia."],
          ["Gap relativo", "Distancia entre la mejor solución y la mejor cota demostrada, en proporción."],
          ["Idoneidad", "Índice KWD del plan / índice máximo alcanzable demostrado por el solver × 100. Mide qué parte de lo posible consigue el plan; el 100 absoluto no es alcanzable."],
          ["Índice KWD máximo alcanzable", "Índice KWD del plan más el margen de mejora que permite la cota del solver."],
          ["Validador", "Módulo que recalcula las reglas obligatorias y el Índice KWD sin usar el modelo; lista vacía = plan factible."],
          ["Horas libres / tiempo muerto", "Horas-persona del personal presente sin trabajo productivo: presentes − carga × fracción de la hora produciendo."],
          ["SS (stock de seguridad)", "Piezas mínimas de cada tipo (400 VE / 200 COMB). Un camión puede consumirlas; se reponen con máxima prioridad."],
          ["Stock óptimo", "SS + demanda de un turno de la pieza (SS + demanda diaria / 3), objetivo al cierre de cada turno."],
          ["OEE", "Overall Equipment Effectiveness: disponibilidad × rendimiento × calidad de una máquina. Aquí se supone 100 %."],
          ["Célula", "Puesto de soldadura que fabrica una pieza exclusiva (VE o combustión)."],
          ["VE / COMB", "Vehículo eléctrico / vehículo de combustión."],
          ["Slot", "Hora del horizonte (24 por plan)."],
          ["Corte", "Restricción añadida para excluir una configuración ya encontrada y obtener la siguiente mejor (Top 2, Top 3)."],
          ["Franja solar", "11:00–14:00: la energía fotovoltaica cubre el 15 % de la potencia (factor 0,85)."],
          ["Contingencia", "Plan recalculado a petición ante averías de células o bajas de personas, con reubicación y aviso a dirección."],
          ["OPTIMO / FACTIBLE / CRITICO / INVIABLE", "Óptimo demostrado; parado por tiempo con gap; con pedidos sin servir (aviso a dirección); incumple almacén o reglas de células."]]
    E += [tabla(gl, [4.6 * cm, 12.4 * cm], ["L", "L"])]

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
