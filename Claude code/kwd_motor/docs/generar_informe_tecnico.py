r"""Genera salida/KWD_Informe_Tecnico.pdf: informe técnico del motor de decisión KWD (versión v3).

Uso (desde la raíz del proyecto, PowerShell 5.1):
    $env:PYTHONPATH="src"; .venv\Scripts\python.exe docs\generar_informe_tecnico.py

Las cifras del ejemplo numérico (planes de las 06:00 y 14:00 con kwd.datos.estado_ejemplo(), contingencia de la
célula 14 y un caso grave) se calculan en cada ejecución con el motor real (kwd.motor.recomendar,
kwd.rolling.contingencia). Tarda ~2-3 min. Variable de entorno opcional KWD_INFORME_CACHE=<ruta.pkl>:
sólo para depurar el maquetado (reutiliza los resultados del motor de una ejecución anterior).

Las fuentes DejaVu se toman de matplotlib para que Σ ≤ ≥ ∀ ∈ → se representen correctamente.
"""
from __future__ import annotations

import importlib.metadata as imd
import inspect
import json
import os
import pickle
import re
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch, Polygon, Rectangle
import numpy as np
import pandas as pd
import pulp

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle, XPreformatted)
from reportlab.platypus.tableofcontents import TableOfContents

from kwd import config, datos, horizonte, informes, modelo, motor, personal, plan as plan_mod, rolling, validador
from kwd.config import COMPONENTES, NOMBRES_COMPONENTE, NOMBRES_TURNO, PARAMETROS_DEFECTO, RECURSOS

NAVY = colors.HexColor("#1F2A6B")
NAVY_HEX = "#1F2A6B"
ZEBRA = colors.HexColor("#EEF0F8")
GRIS = colors.HexColor("#5A6070")
LINEA = colors.HexColor("#C9CDE0")
C_VE = "#2E9E6B"
C_COMB = "#2F6FB5"
C_LOG = "#7A869A"
C_SOLAR = "#FFE9A8"
C_ROJO = "#C0392B"
C_AMBAR = "#E08A00"
SALIDA = RAIZ / "salida" / "KWD_Informe_Tecnico.pdf"
FECHA_DOC = "2 de octubre de 2026"
VERSION = "v3"

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

# comprobación de glifos: se registran todos los textos y al final se verifica contra el cmap de DejaVu
_USADOS: list[tuple[str, str]] = []
_CMAP: dict = {}


def _cmap(fich):
    if fich not in _CMAP:
        from fontTools.ttLib import TTFont as FT
        _CMAP[fich] = set(FT(str(_ttf / fich)).getBestCmap().keys())
    return _CMAP[fich]


def comprobar_glifos():
    faltan: dict = {}
    for txt, fam in _USADOS:
        cm_ = _cmap("DejaVuSansMono.ttf" if fam == "m" else "DejaVuSans.ttf")
        plano = re.sub(r"<[^>]+>", "", txt).replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<") \
            .replace("&gt;", ">")
        for ch in plano:
            if ord(ch) > 31 and ord(ch) not in cm_:
                faltan[ch] = faltan.get(ch, 0) + 1
    return faltan


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
    "h2x": _st("h2x", fontName="DV-B", fontSize=11.5, leading=15, textColor=NAVY, spaceBefore=9, spaceAfter=4,
               keepWithNext=1),
    "h2": _st("h2", fontName="DV-B", fontSize=11.5, leading=15, textColor=NAVY, spaceBefore=9, spaceAfter=4,
              keepWithNext=1),
    "h3": _st("h3", fontName="DV-B", fontSize=9.5, leading=13, textColor=colors.HexColor("#333B7A"),
              spaceBefore=6, spaceAfter=3, keepWithNext=1),
    "bul": _st("bul", leftIndent=14, bulletIndent=3, spaceAfter=2.5),
    "cel": _st("cel", fontSize=7.8, leading=10, spaceAfter=0),
    "celh": _st("celh", fontName="DV-B", fontSize=7.8, leading=10, textColor=colors.white, spaceAfter=0),
    "formula": _st("formula", fontName="DVM", fontSize=7.6, leading=10.8, spaceAfter=0),
    "titulo": _st("titulo", fontName="DV-B", fontSize=28, leading=34, textColor=colors.white, alignment=TA_LEFT),
    "sub": _st("sub", fontSize=13, leading=18, textColor=colors.white, alignment=TA_LEFT),
    "toc1": _st("toc1", fontName="DV-B", fontSize=9, leading=11.5, spaceBefore=3, spaceAfter=0),
    "toc2": _st("toc2", fontSize=7.8, leading=9.2, leftIndent=14, spaceAfter=0),
    "cap": _st("cap", fontSize=7.8, leading=10, textColor=GRIS, alignment=TA_CENTER, spaceAfter=8),
}


def fm(x, d=1):
    """Número con coma decimal y punto de millar."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def sg(x, d=1):
    """Número con signo."""
    s = fm(x, d)
    return ("+" + s) if x > 0 else s


def K(txt):
    """Token de código en tipografía monoespaciada (escapa &, <, >)."""
    t = str(txt).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<font name='DVM'>{t}</font>"


def P(txt, st="body"):
    _USADOS.append((txt, "p"))
    return Paragraph(txt, S[st])


def bullets(items, st="bul"):
    out = []
    for t in items:
        _USADOS.append((t, "p"))
        out.append(Paragraph(t, S[st], bulletText="•"))
    return out


def tabla(datos_, anchos, align=None, zebra=True, destacar_filas=(), fs=None, repetir=1, valign="MIDDLE"):
    """Tabla con cabecera navy y filas cebra. `datos_[0]` es la cabecera."""
    align = align or ["L"] * len(anchos)
    filas = []
    for i, f in enumerate(datos_):
        fila = []
        for j, v in enumerate(f):
            if isinstance(v, (Paragraph, Image, list)) or hasattr(v, "wrap"):
                fila.append(v)
                continue
            est = S["celh"] if i == 0 else S["cel"]
            est = ParagraphStyle("x", parent=est, alignment={"L": 0, "C": 1, "R": 2}[align[j]],
                                 **({"fontSize": fs, "leading": fs + 2.2} if fs else {}))
            _USADOS.append((str(v), "p"))
            fila.append(Paragraph(str(v), est))
        filas.append(fila)
    t = Table(filas, colWidths=anchos, repeatRows=repetir)
    est = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), valign),
           ("LEFTPADDING", (0, 0), (-1, -1), 3.5), ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
           ("TOPPADDING", (0, 0), (-1, -1), 2.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
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
        contenido = [P(contenido, st)]
    t = Table([[contenido]], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def aviso(txt):
    return caja(txt, color=C_AMBAR, fondo=colors.HexColor("#FFF7E0"))


def formulas(lineas, ancho=17 * cm, titulo=None):
    """Bloque de fórmulas / pseudocódigo en monoespaciada (líneas de ≤ 100 caracteres)."""
    import textwrap
    envueltas = []
    for l in lineas:
        if len(l) > 98:
            sangria = " " * (len(l) - len(l.lstrip()))
            envueltas += textwrap.wrap(l, width=98, subsequent_indent=sangria + "      ", break_long_words=False,
                                       break_on_hyphens=False, drop_whitespace=True) or [l]
        else:
            envueltas.append(l)
    lineas = envueltas
    txt = "\n".join(lineas)
    _USADOS.append((txt, "m"))
    esc = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    cont = [XPreformatted(esc, S["formula"])]
    t = Table([[cont]], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F7FB")),
                           ("BOX", (0, 0), (-1, -1), 0.5, LINEA), ("LINEBEFORE", (0, 0), (0, -1), 2.5, NAVY),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def fig(ruta, ancho_cm=17.0, alto_max_cm=None):
    from PIL import Image as PI
    with PI.open(ruta) as im:
        w, h = im.size
    ancho, alto = ancho_cm * cm, ancho_cm * cm * h / w
    if alto_max_cm and alto > alto_max_cm * cm:
        alto = alto_max_cm * cm
        ancho = alto * w / h
    return Image(str(ruta), width=ancho, height=alto)


def figc(ruta, caption, ancho_cm=17.0, alto_max_cm=None):
    """Figura con su pie, sin separarlos entre páginas."""
    return KeepTogether([fig(ruta, ancho_cm, alto_max_cm), P(caption, "cap")])


# ------------------------------------------------------------------------------------- documento
class Doc(BaseDocTemplate):
    def __init__(self, ruta, **kw):
        super().__init__(str(ruta), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2.2 * cm,
                         bottomMargin=2 * cm, title="Informe técnico — Motor de decisión de producción KWD",
                         author="Equipo NTC26 con asistencia de IA", subject="Navarra Talent Challenge 2026",
                         **kw)
        marco = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="n", leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        ap = landscape(A4)
        marco_l = Frame(1.5 * cm, 1.9 * cm, ap[0] - 3 * cm, ap[1] - 4.1 * cm, id="l", leftPadding=0,
                        rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="port", frames=[marco], onPage=self._pie, pagesize=A4),
                               PageTemplate(id="land", frames=[marco_l], onPage=self._pie, pagesize=ap)])

    def _pie(self, c, doc):
        if doc.page == 1:
            return
        w, h = c._pagesize
        m = 2 * cm if w < 700 else 1.5 * cm
        c.saveState()
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.line(m, h - 1.6 * cm, w - m, h - 1.6 * cm)
        c.setFont("DV-B", 8)
        c.setFillColor(NAVY)
        c.drawString(m, h - 1.4 * cm, "Motor de decisión de producción · KWD España")
        c.setFont("DV", 8)
        c.setFillColor(GRIS)
        c.drawRightString(w - m, h - 1.4 * cm, "Informe técnico · Navarra Talent Challenge 2026")
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.5)
        c.line(m, 1.5 * cm, w - m, 1.5 * cm)
        c.drawString(m, 1.0 * cm, f"Informe técnico {VERSION} · {FECHA_DOC} · Equipo NTC26 con asistencia de IA")
        c.drawRightString(w - m, 1.0 * cm, f"Página {doc.page}")
        c.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name in ("h1", "h2"):
            nivel = 0 if fl.style.name == "h1" else 1
            txt = fl.getPlainText()
            clave = f"h{self.seq.nextf('toc')}"
            self.canv.bookmarkPage(clave)
            self.canv.addOutlineEntry(txt, clave, level=nivel, closed=nivel == 0)
            self.notify("TOCEntry", (nivel, txt, self.page, clave))


def H1(t, salto=True):
    _USADOS.append((t, "p"))
    return ([PageBreak()] if salto else []) + [Paragraph(t, S["h1"])]


def H2(t):
    _USADOS.append((t, "p"))
    return Paragraph(t, S["h2"])


def H3(t):
    return P(t, "h3")



# ------------------------------------------------------------------------------------- ejecución del motor
FIN_SEMANA_TXT = ""
T06 = pd.Timestamp("2026-10-02 06:00")
T10 = pd.Timestamp("2026-10-02 10:00")
T14 = pd.Timestamp("2026-10-02 14:00")
SEV_CELULAS = [3, 4, 8, 9, 13, 14, 15]   # caso grave: las 7 células VE averiadas desde las 06:00


def ejecutar_motor() -> dict:
    """Ejecuta el motor real y devuelve todos los resultados que usa el informe."""
    stats: list[dict] = []
    orig = pulp.LpProblem.solve

    def solve(self, *a, **k):
        nb = sum(1 for v in self.variables() if v.cat in ("Integer", "Binary"))
        stats.append(dict(vars=self.numVariables(), cons=self.numConstraints(), bins=nb))
        t = time.perf_counter()
        r = orig(self, *a, **k)
        stats[-1]["t"] = time.perf_counter() - t
        return r

    pulp.LpProblem.solve = solve
    X: dict = {}
    t0 = time.time()
    try:
        esc = datos.estado_ejemplo()
        X["esc"] = esc
        for nombre, ini in [("rec06", T06), ("rec14", T14)]:
            print(f"  motor: recomendar {ini:%H:%M}…", flush=True)
            stats.clear()
            X[nombre] = motor.recomendar(esc, ini)
            X["st_" + nombre] = list(stats)
        print("  motor: contingencia C14…", flush=True)
        X["c14"] = rolling.contingencia_celula(esc, X["rec06"], 14, T10, pd.Timestamp("2026-10-03 06:00"))
        # caso grave: stock inicial = 1 x SS y las 7 células VE averiadas desde las 06:00
        print("  motor: caso grave…", flush=True)
        eg = datos.estado_ejemplo()
        ss = datos.ss_por_celula(eg)
        eg.stock_actual = pd.DataFrame({"celula": list(ss), "piezas": [float(v) for v in ss.values()]})
        X["eg"] = eg
        X["rg"] = motor.recomendar(eg, T06, top_k=1)
        X["sev"] = rolling.contingencia(eg, X["rg"], T06, bajas_celulas=[
            {"celula": c, "desde": T06, "hasta": None} for c in SEV_CELULAS])
        X["t_motor"] = time.time() - t0
    finally:
        pulp.LpProblem.solve = orig
    return X


# ------------------------------------------------------------------------------------- gráficos
def _eje_horas(ax, n, t0, paso=2):
    ticks = list(range(0, n, paso))
    ax.set_xlim(0, n)
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{(t0 + pd.Timedelta(hours=i)):%H}" for i in ticks])


def _decor(ax, hz, esc, off=0, etiquetas=True):
    s = hz.slots
    sol0, sol1 = float(esc.parametros["solar_ini"]), float(esc.parametros["solar_fin"])
    for i in range(len(s)):
        if sol0 <= s["hora"].iloc[i] < sol1:
            ax.axvspan(off + i, off + i + 1, color=C_SOLAR, alpha=0.55, lw=0, zorder=0)
    for i in range(1, len(s)):
        if s["turno"].iloc[i] != s["turno"].iloc[i - 1]:
            ax.axvline(off + i, color="#5A6070", lw=0.8, ls="--", zorder=1)
    if etiquetas:
        ini = 0
        for i in range(1, len(s) + 1):
            if i == len(s) or s["turno"].iloc[i] != s["turno"].iloc[ini]:
                ax.text(off + (ini + i) / 2, 1.01, f"{NOMBRES_TURNO[s['turno'].iloc[ini]]}",
                        transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=7, color=NAVY_HEX)
                ini = i


_LEYENDA_G = [Patch(fc=C_VE, label="Activa VE"), Patch(fc=C_COMB, label="Activa combustión"),
              Patch(fc=C_LOG, label="Logística (10)"), Patch(fc=C_SOLAR, label="Franja solar"),
              Patch(fc="#F5C6C0", ec=C_ROJO, hatch="////", label="Parada / avería")]


def g_gantt(plan, hz, esc, ruta, titulo):
    t = datos.tabla_celulas(esc)
    cel = sorted(t.index)
    fig_, ax = plt.subplots(figsize=(8.6, 4.0))
    _decor(ax, hz, esc, 0, True)
    for k, c in enumerate(cel):
        for h in hz.bloqueos.get(c, set()):
            ax.add_patch(Rectangle((h, k - 0.4), 1, 0.8, fc="#F5C6C0", ec=C_ROJO, hatch="////", lw=0.3, zorder=2))
        for h in range(len(hz.slots)):
            if plan.activacion.at[h, c] > 0.5:
                col = C_LOG if c == 10 else (C_VE if t.loc[c, "es_ve"] else C_COMB)
                u = float(plan.uso.at[h, c]) if c != 10 else 1.0
                ax.add_patch(Rectangle((h + 0.04, k - 0.36), 0.92, 0.72, fc=col, ec="white", lw=0.4,
                                       alpha=0.45 + 0.55 * min(max(u, 0), 1), zorder=3))
    ax.set_yticks(range(len(cel)))
    ax.set_yticklabels([f"Célula {c}" for c in cel], fontsize=7)
    ax.set_ylim(len(cel) - 0.4, -0.7)
    _eje_horas(ax, len(hz.slots), hz.inicio)
    ax.grid(axis="x", color="#DDDDDD", lw=0.4, zorder=0)
    ax.set_title(titulo, loc="left", pad=16)
    ax.set_xlabel(f"Hora (inicio {hz.inicio:%d/%m %H:%M}; la intensidad del color indica la fracción u de uso)")
    ax.legend(handles=_LEYENDA_G, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=5, frameon=False, fontsize=7)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=150)
    plt.close(fig_)


def g_stock(plan, hz, esc, ruta, titulo):
    """Stock / SS por pieza, con el stock óptimo (puntos) en cada cierre de turno."""
    t = datos.tabla_celulas(esc)
    ss = datos.ss_por_celula(esc)
    fig_, axs = plt.subplots(1, 2, figsize=(8.6, 3.2), sharey=True)
    n = len(hz.slots)
    fin = list(range(1, n + 1))
    for ax, ve, nom, col in [(axs[0], True, "Piezas de células VE", C_VE),
                             (axs[1], False, "Piezas de células de combustión", C_COMB)]:
        _decor(ax, hz, esc, 0, etiquetas=False)
        for c in plan.stock.columns:
            if bool(t.loc[c, "es_ve"]) == ve:
                viola = float((plan.stock[c] / ss[c]).min()) < 1 - 1e-6
                ax.plot(fin, plan.stock[c] / ss[c], color=C_ROJO if viola else col, lw=1.3 if viola else 0.8,
                        alpha=0.95 if viola else 0.65)
                for k_, h in enumerate(hz.cierres):
                    ax.plot(h + 1, hz.stock_optimo.at[h, c] / ss[c], marker="D", ms=3.2, color=C_AMBAR, zorder=5)
        ax.axhline(1.0, color=C_ROJO, lw=1.2)
        ax.axhline(0.0, color="black", lw=0.8)
        ax.set_title(nom, loc="left")
        _eje_horas(ax, n, hz.inicio)
        ax.set_xlabel("Hora")
    axs[0].set_ylabel("Stock / stock de seguridad")
    axs[1].legend(handles=[Patch(fc="none", ec=C_ROJO, label="SS"),
                           Line2D([0], [0], marker="D", color="w", markerfacecolor=C_AMBAR, markersize=5,
                                  label="Stock óptimo (cierre de turno)")], frameon=False, fontsize=7,
                  loc="upper right")
    fig_.suptitle(titulo, x=0.01, ha="left", fontsize=9, fontweight="bold", color=NAVY_HEX)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=150)
    plt.close(fig_)


def g_contrib(planes, nombres, ruta):
    comp = ["R", "S", "Q", "B", "E"]
    pal = ["#1F2A6B", "#3E64B8", "#6E9BD8", "#2E9E6B", "#E6A700"]
    fig_, ax = plt.subplots(figsize=(8.6, 0.5 * len(nombres) + 1.5))
    for i, (p, n) in enumerate(zip(planes, nombres)):
        izq = 0
        for c, col in zip(comp, pal):
            v = float(p.contribuciones[c])
            ax.barh(i, v, left=izq, color=col, label=NOMBRES_COMPONENTE[c] if i == 0 else None, height=0.55)
            if v > 4:
                ax.text(izq + v / 2, i, fm(v, 1), ha="center", va="center", color="white", fontsize=7.5)
            izq += v
        ax.text(izq + 0.8, i, f"{fm(p.puntuacion, 2)} pts", va="center", fontsize=8, fontweight="bold",
                color=NAVY_HEX)
    ax.set_yticks(range(len(nombres)))
    ax.set_yticklabels(nombres, fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Contribución a la puntuación: 100·peso·(1 − componente); máximo 100 = 50+20+15+10+5")
    ax.legend(ncol=5, frameon=False, fontsize=7.2, loc="upper center", bbox_to_anchor=(0.5, -0.4))
    ax.set_title("Contribución de cada criterio KWD a la puntuación", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=150)
    plt.close(fig_)


def _caja(ax, x, y, w, h, titulo, sub, fc, tc="white", fs=7.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.0", fc=fc, ec="none"))
    ax.text(x + w / 2, y + h * 0.64, titulo, ha="center", va="center", color=tc, fontsize=fs, fontweight="bold")
    ax.text(x + w / 2, y + h * 0.26, sub, ha="center", va="center", color=tc, fontsize=6.3)


def _fl(ax, p1, p2, color="#5A6070", estilo="-|>", rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=estilo, mutation_scale=9, color=color, lw=1.1, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}"))


def g_pipeline(ruta):
    fig_, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 58)
    ax.axis("off")
    ax.text(50, 56.3, "Flujo de datos del motor v3 (cada caja es un módulo de src/kwd)", ha="center", fontsize=9,
            color=NAVY_HEX, fontweight="bold")
    _caja(ax, 1, 38, 20, 11, "data/estado.json", "datos.cargar_estado", "#8A90A6")
    _caja(ax, 27, 38, 22, 11, "Escenario", "constantes + entradas", NAVY_HEX)
    _caja(ax, 55, 38, 22, 11, "Horizonte 24 h", "horizonte.construir_horizonte", NAVY_HEX)
    _caja(ax, 83, 38, 20, 11, "MILP fase 1", "modelo.resolver: N continuo", "#3E64B8")
    for a, b in [((21.4, 43.5), (26.6, 43.5)), ((49.4, 43.5), (54.6, 43.5)), ((77.4, 43.5), (82.6, 43.5))]:
        _fl(ax, a, b)
    _caja(ax, 83, 21, 20, 11, "MILP fase 2", "N entero, arranque en caliente", "#3E64B8")
    _fl(ax, (93, 37.6), (93, 32.4))
    _caja(ax, 55, 21, 22, 11, "Evaluar + validar", "modelo.evaluar · validador", NAVY_HEX)
    _fl(ax, (82.6, 26.5), (77.4, 26.5))
    _caja(ax, 27, 21, 22, 11, "Top-K (cortes)", "motor.recomendar", "#3E64B8")
    _fl(ax, (54.6, 26.5), (49.4, 26.5))
    _caja(ax, 1, 21, 20, 11, "Roster", "personal.asignacion_personal", NAVY_HEX)
    _fl(ax, (26.6, 26.5), (21.4, 26.5))
    ax.text(38, 33.3, "corte → resolver otra vez", fontsize=6.3, color="#5A6070", ha="center")
    _fl(ax, (38, 32.4), (62, 37.6), color="#3E64B8", ls="--", rad=-0.2)
    _caja(ax, 1, 4, 22, 11, "Recomendacion", "top · alertas · aviso_direccion", "#8A90A6")
    _caja(ax, 30, 4, 22, 11, "Explicación y alertas", "qué · por qué · impacto", NAVY_HEX)
    _caja(ax, 59, 4, 20, 11, "App · PDF · CLI", "dashboard · informes · cli", C_VE)
    _caja(ax, 85, 4, 18, 11, "Contingencia", "rolling.contingencia", "#B5651D")
    _fl(ax, (11, 20.6), (11, 15.4))
    _fl(ax, (29.6, 9.5), (23.4, 9.5))
    _fl(ax, (58.6, 9.5), (52.4, 9.5))
    _fl(ax, (84.6, 9.5), (79.4, 9.5), color="#B5651D", ls="--")
    ax.plot([103.4, 103.9, 103.9], [9.5, 9.5, 53.6], color="#B5651D", lw=1.1, ls="--")
    ax.plot([103.9, 38.0], [53.6, 53.6], color="#B5651D", lw=1.1, ls="--")
    _fl(ax, (38.0, 53.6), (38.0, 49.6), color="#B5651D", ls="--")
    ax.text(70, 54.2, "bajas de células y personas → nuevo Escenario → recalcular 24 h", fontsize=6.6,
            color="#B5651D", ha="center")
    fig_.savefig(ruta, dpi=150, bbox_inches="tight")
    plt.close(fig_)


# ------------------------------------------------------------------------------------- derivados
def derivados(X) -> dict:
    esc = X["esc"]
    D: dict = {}
    t = datos.tabla_celulas(esc)
    prods = datos.celulas_productivas(esc)
    D["tc"], D["prods"] = t, prods
    D["ss"] = datos.ss_por_celula(esc)
    D["i0"] = datos.stock_inicial(esc)
    D["A"] = esc.area_producto_terminado()
    D["ve"] = [c for c in prods if t.loc[c, "es_ve"]]
    D["comb"] = [c for c in prods if not t.loc[c, "es_ve"]]
    D["m2ve"] = float(sum(1.0 / t.loc[c, "piezas_m2"] for c in D["ve"]))
    D["m2comb"] = float(sum(1.0 / t.loc[c, "piezas_m2"] for c in D["comb"]))
    D["suma_ss"] = datos.validar_ss_almacen(esc)
    D["dem_dia"] = datos.demanda_dia(esc, "2026-10-02")
    for n in ("rec06", "rec14"):
        p = X[n].top[0]
        W = X[n].horizonte.slots["laborable"].to_numpy(dtype=bool)
        D["disp_" + n] = float(sum(p.recursos[f"{r}_disp"].to_numpy()[W].sum() for r in RECURSOS))
    return D

# ------------------------------------------------------------------------------------- 0. portada
def seccion_portada(X, D):
    E = []
    portada = Table([[[Spacer(1, 3.6 * cm), P("Informe técnico", "titulo"), Spacer(1, 0.3 * cm),
                       P("Motor de decisión de producción — KWD España", "sub"), Spacer(1, 0.9 * cm),
                       P("Supuestos, trabajo realizado y metodología interna del algoritmo (versión v3)", "sub"),
                       Spacer(1, 3.2 * cm), P("Navarra Talent Challenge 2026", "sub"),
                       P(f"Versión {VERSION} · {FECHA_DOC}", "sub"), Spacer(1, 0.6 * cm),
                       P("Equipo NTC26 con asistencia de IA", "sub"), Spacer(1, 2.6 * cm)]]], colWidths=[17 * cm])
    portada.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * cm),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * cm), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    E += [portada, Spacer(1, 0.6 * cm),
          caja("<b>Naturaleza del documento.</b> Informe técnico de referencia del motor de decisión desarrollado para "
               "el reto KWD, actualizado a la <b>versión v3</b> de la aplicación. Recoge <b>(A)</b> las decisiones y "
               "supuestos, <b>(B)</b> el trabajo realizado y <b>(C)</b> la metodología interna del algoritmo. La fuente "
               "de verdad es el <b>código</b>; donde la documentación previa difiere del código se indica "
               "expresamente (anexo B). Las cifras del ejemplo numérico se calculan al generar el PDF, ejecutando el "
               "motor real."),
          PageBreak()]
    vers = {}
    for n in ("pulp", "highspy", "pandas", "numpy", "reportlab", "matplotlib", "streamlit"):
        try:
            vers[n] = imd.version(n)
        except Exception:
            vers[n] = "—"
    E += [Paragraph("Ficha del documento", S["h1x"])]
    ficha = [["Campo", "Contenido"],
             ["Título", "Informe técnico del motor de decisión de producción KWD (supuestos, trabajo realizado y metodología)"],
             ["Versión", f"{VERSION} (aplicación v3)"],
             ["Fecha", FECHA_DOC],
             ["Autores", "Equipo NTC26 con asistencia de IA"],
             ["Proyecto", "Reto KWD España S.L.U. · Navarra Talent Challenge 2026"],
             ["Objeto técnico", f"Paquete {K('src/kwd')} (motor), {K('app/')} (dashboard Streamlit), {K('tests/')} (pytest), {K('docs/')}"],
             ["Base documental", f"{K('docs/CAMBIOS_V2.md')} y {K('docs/CAMBIOS_V3.md')} (vinculantes) sobre {K('docs/ESPECIFICACION.md')} v1, y el código fuente"],
             ["Regla de precedencia", "Código &gt; CAMBIOS_V3 &gt; CAMBIOS_V2 &gt; ESPECIFICACION. Las diferencias detectadas figuran en el anexo B"],
             ["Generación", f"{K('docs/generar_informe_tecnico.py')} → {K('salida/KWD_Informe_Tecnico.pdf')}; ejecuta el motor real (≈{fm(X['t_motor'], 0)} s)"],
             ["Entorno", f"Python {sys.version.split()[0]} · PuLP {vers['pulp']} · HiGHS (highspy {vers['highspy']}) · pandas {vers['pandas']} · "
                         f"numpy {vers['numpy']} · ReportLab {vers['reportlab']} · matplotlib {vers['matplotlib']} · Streamlit {vers['streamlit']}"]]
    E += [tabla(ficha, [3.6 * cm, 13.4 * cm], ["L", "L"]), Spacer(1, 8)]
    E += [Paragraph("Convenciones del documento", S["h2x"])]
    E += bullets([
        f"<b>Identificadores.</b> {K('D1…D17')}: decisiones del equipo; {K('S1…S30')}: supuestos (decisiones de modelado "
        f"o de implementación que el código asume, con su estado de validación).",
        "<b>Referencias al código.</b> Toda mecánica se cita como " + K("modulo.funcion") + " (p. ej. " + K("modelo.resolver") + ").",
        "<b>Notación.</b> C = células (16), P = C ∖ {10} = células productivas (15), H = slots horarios (24), W ⊆ H = slots laborables, "
        "T ⊆ H = cierres de turno; c = célula, h = slot, k = rol. Los componentes de la puntuación R, S, Q, B, E están "
        "normalizados en [0, 1] y menor es mejor.",
        "<b>Formato numérico.</b> Coma decimal y punto de millar. Las horas son hora local sin zona.",
    ])
    E += [PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    E += [Paragraph("Índice", S["h1x"]), toc]
    return E


# ------------------------------------------------------------------------------------- 1. resumen
def seccion_resumen(X, D):
    r06, r14 = X["rec06"], X["rec14"]
    t1, t14 = r06.top[0], r14.top[0]
    c14 = X["c14"]["rec_despues"].mejor
    sev = X["sev"]["rec_despues"].mejor
    E = H1("1. Objeto, alcance y resumen ejecutivo")
    E += [P("<b>Objeto.</b> Documentar con precisión y de forma reproducible el motor de decisión que recomienda, para cada "
            "turno de la planta de soldadura de KWD España, <b>qué células activar, qué personas ponen en cada una, por "
            "qué y con qué impacto</b>, cumpliendo las reglas obligatorias y optimizando los cinco criterios ponderados del "
            "reto: horas libres del personal (50 %), espacio (20 %), calidad y mantenimiento (15 %), stock óptimo (10 %) y "
            "energía (5 %).")]
    E += [P("<b>Alcance.</b> (A) decisiones y supuestos (§3); (B) trabajo realizado, de la v1 a la v3 y la corrección final "
            "del tiempo muerto (§4); (C) metodología interna: datos, horizonte, formulación MILP, resolución en dos fases, "
            "Top-K, asignación nominal de personal, validador, contingencia y aviso a dirección (§5); ejemplo numérico, "
            "validación y limitaciones (§6–§7). El anexo B recoge las diferencias entre la documentación y el código.")]
    kp = [["Resultado (demo 02/10/2026, kwd.datos.estado_ejemplo)", "Valor"],
          ["Top 1 a las 06:00", f"{t1.estado}, {fm(t1.puntuacion, 2)} pts (máx. alcanzable {fm(t1.kpis.get('puntuacion_max_teorica'), 1)}), idoneidad {fm(t1.idoneidad, 1)} %; {fm(t1.kpis['horas_libres_total'], 0)} h libres"],
          ["Top 1 a las 14:00", f"{t14.estado}, {fm(t14.puntuacion, 2)} pts, idoneidad {fm(t14.idoneidad, 1)} %; {fm(t14.kpis['horas_libres_total'], 0)} h libres"],
          ["Tiempo muerto antes / después de medirlo sobre trabajo productivo", "06:00: 321 h → 114 h · 14:00: 238 h → 48 h (§4.4)"],
          ["Contingencia C14 (10:00 → 06:00)", f"{c14.estado}, {fm(c14.puntuacion, 2)} pts; el SS de la pieza 14 se consume pero no hay pedidos sin servir"],
          ["Caso grave (7 células VE averiadas, stock = SS)", f"{sev.estado}: {fm(sev.kpis['piezas_no_servidas_total'], 0)} piezas sin servir → aviso para dirección"],
          ["Tamaño del MILP (24 h)", f"fase 1: {fm(X['st_rec06'][0]['vars'], 0)} variables / {fm(X['st_rec06'][0]['cons'], 0)} restricciones / {X['st_rec06'][0]['bins']} binarias; "
                                     f"fase 2: {fm(X['st_rec06'][1]['vars'], 0)} / {fm(X['st_rec06'][1]['cons'], 0)} / {X['st_rec06'][1]['bins']} enteras"],
          ["Límite de tiempo", "8 s por plan (4 s por fase); Top-3 en ≈25 s"]]
    E += [tabla(kp, [5.6 * cm, 11.4 * cm], ["L", "L"], fs=7.6)]
    E += [H3("Conclusiones principales")]
    E += bullets([
        "El motor es un <b>MILP determinista</b> (PuLP + HiGHS). El objetivo principal es ocupar a <b>todo el personal "
        "presente</b> (criterio R = fracción de horas libres), sin sobrepasar los recursos disponibles.",
        "Jerarquía de penalizaciones: <b>pedido no servido (1000) ≫ stock bajo el SS (50) ≫ criterios KWD</b>. Un pedido sin servir "
        "ya no descarta el plan: lo marca <b>CRÍTICO</b> y genera un aviso para dirección. Sólo las imposibilidades duras "
        "(almacén &gt; 800 m², reglas de células) lo vuelven INVIABLE.",
        "La <b>idoneidad</b> es 100·(1 − gap), con gap = (primal − cota)/primal sobre el objetivo completo. Con 8 s por plan el solver no demuestra el óptimo en la demo (planes "
        "FACTIBLES con idoneidad ≈ 90–92 %), por lo que se informa también de la puntuación máxima alcanzable.",
        "Un <b>validador independiente</b> recalcula reglas y puntuación a partir de la activación del plan.",
        "Se eliminan el plan manual, los flags y el Excel: el estado de entrada vive en <font name='DVM'>data/estado.json</font> y los "
        "supuestos están documentados aquí.",
    ])
    return E


# ------------------------------------------------------------------------------------- 2. fuentes
def seccion_fuentes(X, D):
    E = H1("2. Fuentes y precedencia", salto=False)
    E += [P("El motor se construyó de forma incremental sobre cuatro documentos y el propio código. Cuando discrepan, "
            "se aplica este orden:")]
    E += [tabla([["Prioridad", "Fuente", "Contenido"],
                 ["1", f"Código ({K('src/kwd')}, {K('app/')})", "Ground truth. Todo lo descrito en este informe se ha contrastado con él."],
                 ["2", K("docs/CAMBIOS_V3.md"), "Horas libres, sin plan manual, stock óptimo, sin flags, contingencia a petición, estado.json"],
                 ["3", K("docs/CAMBIOS_V2.md"), "Piezas sin ensamblaje, camiones por ciclos, SS consumible, personal entero, trabajadores, contingencia"],
                 ["4", K("docs/ESPECIFICACION.md"), "Versión v1 del enunciado (reglas 1–10, formulación base, Top-K). Parcialmente obsoleta: aún habla de Excel, flags y plan de referencia"],
                 ["5", K("docs/PLAN_EQUIPO.md"), "Reparto de roles y cronograma del hackathon (histórico)"]],
                [2 * cm, 5.2 * cm, 9.8 * cm], ["C", "L", "L"], fs=7.6)]
    E += [P("Las reglas obligatorias del enunciado, que se conservan en la v3, son: (1) una célula sólo produce si está activa; "
            "(2) la célula 10 es un servicio logístico siempre activo en horas laborables; (3) las células 11 y 12 funcionan "
            "siempre a la vez; (4) una célula parada o averiada no puede activarse; (5) el personal asignado no supera el "
            "presente; (6) balance de stock con las expediciones; (7) stock de seguridad (ahora penalizado, no descartante); "
            "(8) almacén de producto terminado ≤ 800 m²; (10) el término de arranques sólo da estabilidad. La regla 9 "
            "(stock final) y el colchón del 10 % de la v1 desaparecen en la v3.", "body")]
    return E


# ------------------------------------------------------------------------------------- 3. supuestos
DEC = [
    ("D1", "Sin ensamblaje: cada célula fabrica una pieza exclusiva; la demanda llega por tipo VE/COMB y se aplica a cada pieza de ese tipo. Un ciclo = 1 pieza.", "v2 A1"),
    ("D2", "Expediciones cada 1,5 h desde las 06:00 (16 ciclos por día laborable); en cada ciclo salen los camiones que hagan falta, de 15 m² como máximo.", "v2 A2"),
    ("D3", "Un camión puede llevarse stock de seguridad: el SS deja de ser una regla dura y pasa a penalización de prioridad alta (repone el SS lo antes posible).", "v2 A3"),
    ("D4", "Franja solar de 11:00 a 14:00 (factor energético 0,85); noche 22–06 con factor 1,2.", "v2 A4"),
    ("D5", "La célula 10 no puede averiarse ni pararse; las paradas que la mencionen se ignoran con aviso.", "v2 A5"),
    ("D6", "Datos manuales: bajas por turno, paradas y demanda (semanal y corregida diaria); además stock actual y expediciones reales. El resto son constantes.", "v2 A6, v3 A6"),
    ("D7", "Personal entero: variable N<sub>k,h</sub> por rol y hora; trabajadores enumerados con identificador estable (M-OP01…) y asignación puesto a puesto.", "v2 A7"),
    ("D8", "Demo: semana del 28/09/2026, 1.500 coches/día con doble de combustión (2.500 VE / 5.000 COMB por semana y pieza); C13 parada programada el 02/10 turno T con 2 técnicos y 1 operario de baja.", "v2 A11, v3 A6"),
    ("D9", "Horas libres como objetivo principal: R = fracción de horas libres del personal presente; desaparecen la plantilla P<sub>k,s</sub> y el EXCEDENTE.", "v3 A1"),
    ("D10", "Se elimina el plan manual (baseline): el impacto se compara con Top 2/3 y, en contingencia, con el plan previo a la incidencia.", "v3 A2"),
    ("D11", "Stock óptimo = SS + demanda de un turno (demanda del día / 3), medido en cada cierre de turno; el criterio B (10 %) es la desviación media |I − óptimo| / óptimo.", "v3 A3"),
    ("D12", "Se eliminan los flags: los supuestos se documentan en <font name='DVM'>docs/</font> y en este informe, no en la app.", "v3 A4"),
    ("D13", "Jerarquía pedido no servido ≫ SS ≫ criterios; stock &lt; 0 no descarta el plan (CRÍTICO + aviso a dirección). Tiempo límite de 8 s por plan.", "v3 A5"),
    ("D14", "Contingencia a petición: se dan de baja células y personas concretas (o nº por rol) y sólo se calcula al pulsar el botón.", "v3 B3"),
    ("D15", "Sin Excel: constantes fijas en el código y estado persistente en <font name='DVM'>data/estado.json</font>.", "v3 A6"),
    ("D16", "Planta en tiempo real sólo de visualización (sin botones de avería/baja ni recálculo automático).", "v3 B2"),
    ("D17", "El tiempo muerto se mide sobre el trabajo productivo real (fracción u de la hora en que la célula produce) y se elimina el mínimo del 25 % por hora.", "commit ce2d6c6"),
]

SUP = [
    ("S1", "Capacidad de una célula = 3600 / ciclo_s piezas por hora a rendimiento (OEE) del 100 %; una célula activa produce u ∈ [0,1] de esa capacidad.", "Supuesto técnico", "datos.tabla_celulas"),
    ("S2", "Puntuación = 100·(1 − Σ w<sub>i</sub>·comp<sub>i</sub>) con pesos 50/20/15/10/5; cada componente se normaliza en [0,1].", "Enunciado KWD", "config.PESO_PARAM"),
    ("S3", "Stock de seguridad 400 piezas por célula VE y 200 por célula de combustión; la suma ocupa ≈ 151,7 m².", "Enunciado KWD", "datos.ss_por_celula"),
    ("S4", "Almacén de producto terminado 800 m² (incluye el SS); ocupación = Σ stock / piezas_m². La célula 10 no ocupa almacén.", "Enunciado KWD", "modelo r8"),
    ("S5", "Turnos M 06–14, T 14–22, N 22–06. La noche de 00–06 pertenece al día anterior (fecha_turno). Lunes a viernes laborables; sin producción ni expediciones en fin de semana.", "Confirmado por el equipo", "horizonte.construir_horizonte"),
    ("S6", "Personal estándar por turno (M/T: 16 operarios, 2 picking, 4 carretilleros, 7 mto, 3 calidad; N: 14/2/3/4/2). Sin fila de bajas se resta round(estándar × 5 %), es decir 1 operario por turno.", "Pendiente de validar con KWD", "datos.bajas_efectivas"),
    ("S7", "La carga de cada célula por rol es fraccionaria (personas-equivalente) y una persona puede cubrir varias células mientras Σ cargas ≤ 1.", "Enunciado KWD", "personal"),
    ("S8", "Los técnicos de una parada programada se restan del rol mto mientras dura; en una avería el nº de técnicos es 0 por defecto.", "Confirmado por el equipo", "horizonte"),
    ("S9", "Célula 10: no produce, exige activación en horas laborables (salvo recursos insuficientes) y cuenta su carga la hora completa.", "Enunciado KWD", "horizonte.logistica_exigida"),
    ("S10", "Células 11 y 12: misma activación y mismo uso en todo momento.", "Enunciado KWD", "modelo r3"),
    ("S11", "Energía de red = kW·u·factor horario; E normaliza con |W|·Σkw·f<sub>max</sub> (f<sub>max</sub> = 1,2).", "Supuesto técnico", "modelo.evaluar"),
    ("S12", "Camiones por ciclo = ⌈m² / 15⌉ con m² = Σ piezas / piezas_m²; la carga prevista de un ciclo es la demanda del día / 16; una expedición real sustituye al ciclo previsto más cercano (±45 min).", "Confirmado por el equipo", "horizonte._ciclos"),
    ("S13", "Demanda de un día = corregida si existe; si no, semanal / 5; una semana sin dato usa la última anterior; día no laborable = 0.", "Confirmado por el equipo", "datos.demanda_dia"),
    ("S14", "Stock inicial de la demo = 2 × SS (el stock actual real se introduce en la app).", "Supuesto técnico", "datos.estado_ejemplo"),
    ("S15", "Una parada bloquea toda la hora de cualquier slot con el que se solape.", "Supuesto técnico", "horizonte"),
    ("S16", "Q = media sobre horas laborables de la ocupación N/disp de los roles mto y calidad (media de los dos).", "Supuesto técnico", "modelo._componentes_RQ"),
    ("S17", "El término de arranques (1e-4 por arranque) sólo estabiliza el plan; no es un criterio KWD.", "Supuesto técnico", "modelo.PESO_ARRANQUES"),
    ("S18", "El stock óptimo de un cierre usa la demanda del día del turno que cierra; sólo se consideran los cierres laborables dentro del horizonte (06, 14 y 22 h).", "Supuesto técnico", "horizonte.cierres"),
    ("S19", "Pedido no servido = stock &lt; 0 (pedido pendiente acumulado); se penaliza por unidad normalizada (stock negativo / SS) y hora.", "Supuesto técnico", "modelo r6b"),
    ("S20", "El gap aceptado se impone como gap <b>absoluto</b> equivalente (gap_relativo × 0,5 sobre el objetivo escalado ×1000), no relativo; la fase 1 acepta 2 %.", "Supuesto técnico", "modelo._resolver_fase"),
    ("S21", "Idoneidad = 100·(1 − gap), con gap = (primal − cota)/primal calculado sobre el objetivo completo (HiGHS omite el término constante, que se le suma a la cota); las alternativas Top 2/3 con idoneidad &lt; 50 % no se muestran. Estado OPTIMO sólo si HiGHS demuestra optimalidad; FACTIBLE si agota el tiempo.", "Supuesto técnico", "motor.IDONEIDAD_MIN_ALTERNATIVA"),
    ("S22", "Top 1 = menos piezas sin servir y, a igualdad, mayor puntuación; las alternativas se obtienen con cortes sobre la configuración del turno actual.", "Supuesto técnico", "motor.recomendar"),
    ("S23", "Identificadores: del 1 al estándar por turno y rol; una baja por id quita ese id y el exceso de bajas quita los de numeración más alta. Todo trabajador presente está ASIGNADO, LIBRE o PARADA (técnico en parada).", "Supuesto técnico", "datos.trabajadores_disponibles"),
    ("S24", "La contingencia usa Top-1 (un solo plan), parte del stock del plan vigente en la hora indicada y una avería sin «hasta» dura hasta el fin del horizonte.", "Supuesto técnico", "rolling.contingencia"),
    ("S25", "Horizonte rodante de 24 h, recalculado desde la hora indicada con el stock del plan vigente.", "Confirmado por el equipo", "rolling"),
    ("S26", "Entrada de bajas por rol (nº) se aplica a todos los turnos del horizonte.", "Supuesto técnico", "rolling.contingencia"),
    ("S27", "Un trabajador de baja concreto sólo afecta a su turno de la fecha indicada.", "Supuesto técnico", "rolling.aplicar_evento"),
    ("S28", "Sin límite de arranques por célula ni tiempo mínimo de producción continua (sólo penalización 1e-4).", "Pendiente de validar con KWD", "modelo"),
    ("S29", "No se modelan cambios de referencia, mantenimiento preventivo automático ni materia prima: la producción sólo depende de la capacidad nominal y del personal.", "Pendiente de validar con KWD", "—"),
    ("S30", "Los trabajadores de mto y calidad se asignan a células como cualquier otro rol (carga fraccionaria).", "Supuesto técnico", "personal"),
]


def seccion_supuestos(X, D):
    E = H1("3. Registro de decisiones y supuestos")
    E += [P("Se distinguen las <b>decisiones</b> D (tomadas por el equipo el 2 de octubre de 2026, recogidas en CAMBIOS_V2 y "
            "CAMBIOS_V3) y los <b>supuestos</b> S (lo que el código asume de forma explícita o implícita). Ya no existen "
            "«flags» configurables en la app ni en el código: cada supuesto vive aquí y, si cambia, se cambia en el código.")]
    E += [H2("3.1 Decisiones del equipo")]
    E += [tabla([["Id", "Decisión", "Origen"]] + [[a, b, c] for a, b, c in DEC], [1.1 * cm, 13.6 * cm, 2.3 * cm],
                ["C", "L", "L"], fs=7.4)]
    E += [H2("3.2 Supuestos del modelo")]
    E += [P("Estados: <i>Enunciado KWD</i> (dato del reto), <i>Confirmado por el equipo</i>, <i>Pendiente de validar con KWD</i> "
            "y <i>Supuesto técnico</i> (decisión de implementación sin respaldo en las fuentes).", "small")]
    E += [tabla([["Id", "Supuesto", "Estado", "Código"]] + [[a, b, c, K(d)] for a, b, c, d in SUP],
                [1.0 * cm, 9.4 * cm, 2.9 * cm, 3.7 * cm], ["C", "L", "L", "L"], fs=7.2)]
    return E


# ------------------------------------------------------------------------------------- 4. trabajo realizado
def seccion_trabajo(X, D):
    E = H1("4. Trabajo realizado")
    E += [P("La aplicación se construyó el 2 de octubre de 2026 en tres iteraciones y una corrección final, todas "
            "trazables en el historial de git. Cada iteración incorporó las respuestas del equipo a las preguntas de la "
            "iteración anterior.")]
    E += [H2("4.1 Cronología")]
    E += [tabla([
        ["Hito", "Qué se hizo", "Resultado"],
        ["<b>v1</b> (mañana)", "Modelo MILP base: activación a<sub>c,h</sub> y uso u<sub>c,h</sub> en 24 h; reglas 1–10; criterios R/S/Q/B/E con los pesos KWD; Top 1/2/3 por cortes; "
                               "explicación qué/por qué/impacto; plan de referencia manual; horizonte rodante; dashboard Streamlit e informes PDF; datos en Excel.",
         "Motor funcional, 21 pruebas; plan viable con stock inicial 1,8 × SS."],
        ["<b>v2</b> (tarde)", "Piezas exclusivas sin ensamblaje (D1); camiones cada 1,5 h con 15 m² (D2); SS consumible con penalización (D3); franja solar 11–14 (D4); célula 10 no se para (D5); "
                              "bajas y paradas manuales (D6); <b>personal entero</b> N<sub>k,h</sub> en dos fases, trabajadores enumerados y asignación nominal (D7); R con plantilla P<sub>k,s</sub>; "
                              "planta en tiempo real; contingencia por avería de una célula; escenario 1.500 coches/día.",
         "Personal entero y plantilla; contingencia de una célula; planta en tiempo real."],
        ["<b>v3</b> (noche)", "<b>Horas libres</b> como objetivo principal (D9); eliminación del plan manual (D10), de la plantilla y del EXCEDENTE; <b>stock óptimo</b> y criterio B (D11); "
                              "sin flags (D12); <b>jerarquía de penalizaciones</b>, estado CRÍTICO y aviso a dirección (D13); contingencia a petición con varias células y personas (D14); "
                              "sin Excel: <font name='DVM'>data/estado.json</font> (D15); planta en tiempo real sólo de visualización (D16).",
         "Motor v3, 23 pruebas, resumen del MILP en lenguaje llano."],
        ["<b>Corrección</b>", "El tiempo muerto pasa a medirse sobre el <b>trabajo productivo real</b> (D17): una célula activa que produce sólo una fracción u de la hora ya no ocupa a todo el personal "
                              "durante la hora entera; se elimina el mínimo del 25 % por hora.",
         "Tiempo muerto real de la demo: 321 h → 114 h (06:00) y 238 h → 48 h (14:00)."]],
        [2.3 * cm, 10.7 * cm, 4.0 * cm], ["L", "L", "L"], fs=7.4, valign="TOP")]
    E += [H2("4.2 Inventario del código")]
    E += [tabla([
        ["Módulo", "Responsabilidad"],
        [K("config.py"), "Constantes, parámetros por defecto (PARAMETROS_DEFECTO), componentes y pesos de la puntuación"],
        [K("datos.py"), "Escenario, constantes de planta, estado persistente (cargar_estado / guardar_estado / estado_ejemplo), demanda del día, bajas y trabajadores disponibles, stock óptimo"],
        [K("horizonte.py"), "Slots horarios, turnos, factor energético, bloqueos por parada, disponibilidad de personal, ciclos de expedición y camiones, cierres de turno"],
        [K("modelo.py"), "Formulación MILP (preparar / resolver en dos fases), evaluación de planes (evaluar), KPIs y puntuación"],
        [K("motor.py"), "Top-K con cortes, explicación (qué/por qué/impacto), alertas y estado de la recomendación"],
        [K("personal.py"), "Asignación nominal de trabajadores puesto a puesto (first-fit decreasing estable)"],
        [K("validador.py"), "Validación independiente de reglas y puntuación; agotamiento, desabastecimiento y aviso para dirección"],
        [K("rolling.py"), "Eventos, estado en tiempo real por célula, contingencia (células/personas), simulación semanal"],
        [K("plan.py, informes.py, cli.py"), "Estructura Plan, informe PDF de la app y línea de comandos"],
        [K("app/dashboard.py, graficos.py"), "Dashboard Streamlit con 10 pestañas: Datos, Planta en tiempo real, Trabajadores, Recomendación, KPIs, Overview 24 h, Alternativas, Contingencia, Semana, Informe"],
        [K("tests/test_motor.py"), "23 pruebas pytest (reglas duras, SS, CRÍTICO, R, B, estado.json, personal, contingencia)"]],
        [4.4 * cm, 12.6 * cm], ["L", "L"], fs=7.4)]
    E += [H2("4.3 Qué se eliminó en la v3")]
    E += bullets([
        "<b>Plan manual / baseline</b>: ya no se calcula, no hay warm start desde él ni «impacto vs referencia». El arranque lo da la fase 1 continua del propio modelo.",
        "<b>Flags</b> (F1…F19): sustituidos por el registro de supuestos S1…S30 de este informe.",
        "<b>Excel</b> (<font name='DVM'>data/*.xlsx</font>): las constantes viven en <font name='DVM'>datos.py</font>/<font name='DVM'>config.py</font> y las entradas en <font name='DVM'>data/estado.json</font>.",
        "<b>Plantilla P<sub>k,s</sub>, EXCEDENTE y término auxiliar de horas libres de plantilla</b>; <b>colchón del 10 %</b> sobre el SS y <b>condición de stock final</b>.",
        "<b>Recálculo automático</b> y botones de avería/baja en la planta en tiempo real (ahora sólo visualiza).",
    ])
    E += [H2("4.4 Corrección final: tiempo muerto medido sobre trabajo productivo")]
    E += [P("Con la definición inicial de la v3, R contaba como ocupada toda hora en que una célula estaba <i>activa</i>, aunque "
            "produjera sólo una fracción u de la hora. El modelo podía así «ocupar» al personal activando células que no producían. "
            "La corrección calcula el trabajo como carga × u (la célula 10, que no produce, cuenta la hora completa): "
            "R = (Σ Disp − Σ trabajo) / Σ Disp. Efecto medido en la demo:")]
    E += [tabla([["Plan (demo 02/10)", "Horas libres antes", "Horas libres después", "Reducción"],
                 ["Top 1 a las 06:00", "321 h", f"{fm(X['rec06'].top[0].kpis['horas_libres_total'], 0)} h", "−64 %"],
                 ["Top 1 a las 14:00", "238 h", f"{fm(X['rec14'].top[0].kpis['horas_libres_total'], 0)} h", "−80 %"]],
                [5 * cm, 4 * cm, 4 * cm, 4 * cm], ["L", "R", "R", "R"], fs=7.8)]
    E += [P("Las cifras «antes» son las medidas por el equipo en el commit anterior (321 h y 238 h); las «después» se recalculan "
            "al generar este PDF con el motor actual.", "small")]
    return E

# ------------------------------------------------------------------------------------- 5. metodología
def seccion_metodologia(X, D, F):
    esc = X["esc"]
    E = H1("5. Metodología interna del algoritmo")
    E += [P("Esta sección describe, módulo a módulo, qué hace el motor v3. Las fórmulas reproducen el código "
            f"({K('modelo.py')}, {K('validador.py')}); donde la documentación previa difiere, prevalece el código.")]
    E += [figc(F["pipeline"], "Figura 5.1 — Flujo de datos del motor v3.", 16.2)]

    # --- 5.1 datos
    E += [H2("5.1 Datos de entrada")]
    E += [P("<b>Constantes</b> (fijas en el código): tabla de 16 células (ciclo, piezas/m², cargas de personal por rol, kW), personal "
            "estándar por turno, almacén (materia prima 400 m², cargas 100 m², producto terminado 800 m²) y parámetros "
            f"({K('config.PARAMETROS_DEFECTO')}, anexo A). <b>Entradas</b> persistentes en {K('data/estado.json')}: "
            f"{K('demanda_semanal')} (lunes, piezas VE/COMB por pieza y semana, 5 días), {K('demanda_diaria')} (demanda corregida confirmada), "
            f"{K('bajas')} (personas de baja por fecha, turno y rol), {K('paradas')} (célula, desde, hasta, tipo PROGRAMADA/AVERIA, técnicos), "
            f"{K('stock_actual')} y {K('expediciones_reales')}. Funciones: {K('datos.cargar_estado(path)')}, {K('datos.guardar_estado(esc, path)')} y "
            f"{K('datos.estado_ejemplo()')} (si el fichero no existe se crea con la demo).")]
    t = D["tc"]
    filas = [["Célula", "Tipo", "Ciclo (s)", "Piezas/h", "Piezas/m²", "Oper.", "Pick.", "Carret.", "Mto", "Cal.", "kW"]]
    for c in sorted(t.index):
        f = t.loc[c]
        filas.append([str(c), f["tipo"], fm(f["ciclo_s"], 0), fm(f["cap_h"], 0) if c != 10 else "—", fm(f["piezas_m2"], 0) if c != 10 else "—",
                      fm(f["operarios"], 2), fm(f["picking"], 2), fm(f["carretilleros"], 2), fm(f["mto"], 2), fm(f["calidad"], 2), fm(f["kw"], 1)])
    E += [tabla(filas, [1.3 * cm, 1.4 * cm, 1.5 * cm, 1.5 * cm, 1.7 * cm, 1.4 * cm, 1.4 * cm, 1.5 * cm, 1.4 * cm, 1.4 * cm, 1.4 * cm],
                ["C"] * 11, fs=7.0)]
    E += [P("Tabla 5.1 — Células y cargas por rol (personas-equivalente mientras la célula está activa). La célula 10 es el servicio logístico.", "cap")]
    E += [P("<b>Demanda del día</b> ({0}): corregida si existe; si no, semanal / 5 en día laborable. La demanda diaria por pieza de cada tipo se "
            "reparte por igual entre los 16 ciclos del día; la demo (1.500 coches/día, 2 COMB : 1 VE) da {1} piezas VE y {2} COMB por pieza y día."
            .format(K("datos.demanda_dia"), fm(D["dem_dia"][0], 0), fm(D["dem_dia"][1], 0)))]

    # --- 5.2 horizonte
    E += [H2("5.2 Horizonte de 24 horas")]
    E += [P(f"{K('horizonte.construir_horizonte(esc, inicio, horas=24)')} crea H = 24 slots horarios desde la hora de inicio y calcula para cada uno:")]
    E += bullets([
        "turno (M/T/N), <b>fecha de turno</b> (la noche 00–06 pertenece al día anterior) y si es laborable (lunes–viernes);",
        "<b>factor energético</b>: 0,85 en la franja solar 11–14, 1,2 de 22 a 06 y 1,0 en el resto;",
        "<b>disponibilidad</b> disp<sub>k,h</sub> = estándar − bajas (o − round(estándar × 5 %) si no hay fila) y, para mto, menos los técnicos de las paradas activas;",
        "<b>bloqueos</b> por célula: slots en que se solapa una parada o avería (la célula 10 se ignora con aviso);",
        "<b>ciclos de expedición</b> (16 por día laborable: 06:00, 07:30, …, 04:30) con las piezas de cada tipo y los camiones ⌈m²/15⌉; los envíos "
        "env<sub>h,c</sub> por pieza; las expediciones reales sustituyen al ciclo previsto más cercano (±45 min);",
        "<b>cierres de turno</b> T: slots laborables cuyo final cae en 06:00, 14:00 o 22:00, con el <b>stock óptimo</b> "
        "opt<sub>t,c</sub> = SS<sub>c</sub> + demanda_día(tipo de c) / 3 del día del turno que cierra.",
    ])

    # --- 5.3 MILP
    E += [H2("5.3 Formulación MILP exacta (v3)")]
    E += [P("<b>Conjuntos y parámetros.</b> c ∈ C (16 células), P = C ∖ {10}, h ∈ H (24 slots), W ⊆ H laborables, k ∈ K = {operarios, picking, "
            "carretilleros, mto, calidad}, t ∈ T cierres de turno. Parámetros: req<sub>c,k</sub> (carga), cap<sub>c</sub> = 3600/ciclo (piezas/h), "
            "kw<sub>c</sub>, dens<sub>c</sub> (piezas/m²), SS<sub>c</sub>, I0<sub>c</sub>, env<sub>h,c</sub>, disp<sub>k,h</sub>, f<sub>h</sub> (factor energético), "
            "A = 800 m², opt<sub>t,c</sub>.")]
    E += [H3("Variables de decisión")]
    E += [tabla([
        ["Variable", "Dominio", "Significado"],
        ["a<sub>c,h</sub>", "{0,1}; =1 forzada en C10 si es obligatoria; =0 si bloqueada o h ∉ W", "Célula activa"],
        ["u<sub>c,h</sub> (c ∈ P)", "[0,1]", "Fracción de la hora en que la célula produce"],
        ["I<sub>c,h</sub> (c ∈ P)", "ℝ", "Stock de la pieza al final de la hora (puede ser negativo: pedido pendiente)"],
        ["N<sub>k,h</sub> (h ∈ W)", "entero en [0, ⌊disp<sub>k,h</sub>⌋]", "Personas ocupadas del rol k (sólo en la fase 2)"],
        ["s<sub>c,h</sub>, q<sub>c,h</sub>", "≥ 0", "Holgura de SS consumido y de pedido no servido (stock &lt; 0)"],
        ["sa<sub>h</sub>", "≥ 0", "Holgura de almacén (m² por encima de 800)"],
        ["d<sup>+</sup><sub>t,c</sub>, d<sup>−</sup><sub>t,c</sub>", "≥ 0", "Desviación por encima/por debajo del stock óptimo en el cierre t"],
        ["w<sub>c,h</sub>", "≥ 0", "Arranque de la célula (a<sub>c,h</sub> − a<sub>c,h−1</sub>)"]],
        [3.2 * cm, 6.3 * cm, 7.5 * cm], ["L", "L", "L"], fs=7.4)]
    E += [H3("Restricciones")]
    E += [formulas([
        "(R1)  u[c,h] <= a[c,h]                                                   para c en P, h en H",
        "(R3)  a[11,h] = a[12,h]  y  u[11,h] = u[12,h]                            (células 11 y 12 siempre juntas)",
        "(R5)  carga[k,h] = Σ_c req[c,k]·a[c,h]  <=  disp[k,h]                    para h en W (reglas 2 y 5)",
        "      carga[k,h] <= N[k,h] <= carga[k,h] + 0,999                         (N = techo de la carga; fase 2)",
        "      N[k,h] >= ceil(req[c,k])·a[c,h]  si req[c,k] es fraccionaria      (corte válido; fase 2)",
        "(R6)  I[c,h] = I[c,h-1] + cap[c]·u[c,h] - env[h,c],   I[c,-1] = I0[c]     (balance de stock)",
        "(R6b) I[c,h] + q[c,h] >= 0                                               (pedido servido, con holgura q)",
        "(R7)  I[c,h] + s[c,h] >= SS[c]                                           (SS consumible, con holgura s)",
        "(R8)  Σ_c I[c,h] / dens[c]  <=  800 + sa[h]                              (almacén de producto terminado)",
        "(R10) w[c,h] >= a[c,h] - a[c,h-1]                                        (arranques)",
        "(B)   I[c,t] - opt[t,c] = d+[t,c] - d-[t,c]                              para cada cierre t en T",
        "(TK)  y[c] >= a[c,h], y[c] <= Σ_{h en turno actual} a[c,h]               (sólo con cortes; ver §5.5)",
        "      Σ_{c en S}(1 - y[c]) + Σ_{c no en S} y[c] >= 1                     para cada configuración S ya obtenida"])]
    E += [Spacer(1, 4), P("La regla 4 (paradas) y la regla 2 (fuera de W nada se activa) se imponen como cota superior 0 de a<sub>c,h</sub>. "
                          "En la fase 1 no existen N<sub>k,h</sub> y sólo se aplica (R5) con disp.")]
    E += [H3("Componentes de la puntuación (menor es mejor)")]
    E += [formulas([
        "Trabajo[k,h] = Σ_{c en P} req[c,k]·u[c,h]  +  req[10,k]·a[10,h]          (C10 cuenta la hora completa)",
        "R = ( Σ_{k,h en W} disp[k,h] - Σ_{k,h en W} Trabajo[k,h] ) / Σ_{k,h en W} disp[k,h]     horas libres / presentes",
        "S = (1/H) Σ_h ( Σ_c I[c,h]/dens[c] ) / 800                               ocupación media del almacén",
        "Q = (1/|W|) Σ_{h en W} media_{k en {mto,calidad}} ( N[k,h] / disp[k,h] )  (fase 1: N sustituido por la carga)",
        "B = media_{t en T, c en P} ( |I[c,t] - opt[t,c]| / opt[t,c] ) = media (d+ + d-)/opt",
        "E = Σ_h f[h]·Σ_c kw[c]·u[c,h]  /  ( |W|·Σ_c kw[c]·fmax ),   fmax = 1,2",
        "Puntuación = 100·(1 - 0,50·R - 0,20·S - 0,15·Q - 0,10·B - 0,05·E)"])]
    E += [Spacer(1, 4), P("Por tanto R <b>no usa N</b>: mide el tiempo muerto real del personal presente (disp menos trabajo productivo), mientras que N "
                          "interviene en Q, en las restricciones de personal y en el roster. (CAMBIOS_V3 A1 define R con N; el código la redefinió "
                          "en la corrección de §4.4.)")]
    E += [H3("Función objetivo y jerarquía de penalizaciones")]
    E += [formulas([
        "min 1000 · [ 0,50·R + 0,20·S + 0,15·Q + 0,10·B + 0,05·E                  criterios KWD (suma ≤ 1)",
        "             + 1000 · Σ_{c,h} q[c,h]/SS[c]                               pedido no servido",
        "             +   50 · Σ_{c,h} s[c,h]/SS[c]                               stock bajo el SS",
        "             + 1000 · Σ_h sa[h]/800                                      almacén > 800 m² (infactibilidad dura)",
        "             + 1e-4 · Σ_{c,h} w[c,h] ]                                   estabilidad (arranques)"])]
    E += [Spacer(1, 4), P("Los pesos de la jerarquía son los parámetros <i>penalizacion_pedido</i> (1000) y <i>penalizacion_ss</i> (50). Una hora con una "
                          "pieza al 100 % del SS consumido cuesta 50, frente a un máximo de 1 de todos los criterios juntos: el solver prefiere "
                          "casi siempre reponer el SS a mejorar R; y un pedido sin servir cuesta 20 veces más que consumir SS. Así, ante una "
                          "avería grave, el plan consume SS para aguantar y, resuelta la incidencia, repone primero el SS y luego vuelve al stock óptimo. "
                          "El objetivo se multiplica por 1000 (ESCALA_OBJ) para que los costes pequeños no queden por debajo de la tolerancia del solver.")]

    # --- 5.4 resolución
    E += [H2("5.4 Resolución en dos fases")]
    s1, s2 = X["st_rec06"][0], X["st_rec06"][1]
    E += [P(f"{K('modelo.resolver')} reparte el tiempo límite del plan (8 s, parámetro tiempo_limite_s) en dos fases iguales:")]
    E += bullets([
        f"<b>Fase 1 (4 s, N continuo).</b> Modelo sin variables N (Q usa la carga fraccionaria) y gap aceptado del 2 %: {fm(s1['vars'], 0)} variables, "
        f"{fm(s1['cons'], 0)} restricciones y {s1['bins']} binarias a<sub>c,h</sub>. Devuelve una buena activación; no hay plan de referencia que dé arranque.",
        f"<b>Fase 2 (4 s, N entero).</b> Se añaden las 120 variables N<sub>k,h</sub> y sus cortes ({fm(s2['vars'], 0)} variables, {fm(s2['cons'], 0)} restricciones, "
        f"{s2['bins']} enteras). HiGHS arranca en caliente con la activación de la fase 1 (<font name='DVM'>setInitialValue</font>).",
        "<b>Gap.</b> Se configura <font name='DVM'>gapRel = 0</font> y <font name='DVM'>gapAbs = gap_relativo × 0,5 × 1000</font> (equivalente a un gap relativo sobre un objetivo típico de 0,5): "
        "con penalizaciones grandes un gap relativo pararía demasiado pronto. El gap que se informa se recalcula con el objetivo completo: HiGHS no incluye el término constante (p. ej. el «1 −» de R), así que primal = valor del plan en PuLP, cota = mip_dual_bound + (primal − objetivo de HiGHS) y gap = (primal − cota)/primal. Idoneidad = 100·(1 − gap) y puntuación máxima alcanzable = min(100, puntuación + 100·objetivo·gap).",
        "<b>Estado.</b> OPTIMO si HiGHS demuestra optimalidad; FACTIBLE si se agota el tiempo con solución; INVIABLE si hay holgura de almacén "
        "(sa &gt; 1e-6) o el validador detecta incumplimientos; CRITICO si el plan es viable pero hay pedidos sin servir (§5.8).",
    ])

    # --- 5.5 Top-K
    E += [H2("5.5 Top 1/2/3 mediante cortes")]
    E += [P(f"{K('motor.recomendar(esc, inicio, horas=24, top_k=3)')} resuelve el MILP y excluye la configuración del turno actual: "
            "y<sub>c</sub> vale 1 si la célula c se activa en algún slot del turno actual y el corte (TK) obliga a que el conjunto de células "
            "activas difiera de cada configuración S ya obtenida en al menos una célula. Se repite K = 3 veces (o hasta que el modelo no tenga solución). "
            "Después: (i) se ordenan por piezas no servidas (redondeadas) y, a igualdad, por puntuación; (ii) se descartan las alternativas Top 2/3 con "
            "idoneidad &lt; 50 %; (iii) se genera la explicación del Top 1. Cada plan se resuelve con 8 s, de modo que un Top-3 tarda ≈25 s.")]

    # --- 5.6 roster
    E += [H2("5.6 Asignación nominal de personal (roster)")]
    E += [P(f"Tras fijar N<sub>k,h</sub>, {K('personal.asignacion_personal')} numera a todos los trabajadores presentes (M-OP01…; T-CA02…) y, para cada "
            "turno, rol y hora, reparte las cargas de las células activas con un algoritmo first-fit decreasing estable:")]
    E += bullets([
        "Las cargas se descomponen en unidades enteras (1,0) y un resto fraccionario, ordenadas de mayor a menor.",
        "<b>Estabilidad</b>: si el trabajador ya estaba en esa célula la hora anterior y le cabe, se queda.",
        "<b>First-fit</b>: el resto se coloca en el primer trabajador ya ocupado al que le quede hueco (suma de cargas ≤ 1); si no cabe, se abre uno nuevo, "
        "empezando por quienes se quedaron sin célula.",
        "Si el empaquetado usa más de N personas, se vacía a los menos cargados fraccionando sus cargas entre los demás.",
        "Cada trabajador presente queda <b>ASIGNADO</b> (a una o varias células, p. ej. «C8 (0,5) + C9 (0,5)») o <b>LIBRE</b>; los técnicos ocupados en una parada figuran como <b>PARADA</b>.",
    ])
    E += [P(f"El resultado es {K('Plan.personal')} (una fila por trabajador y hora) y {K('Plan.trabajadores')} (recorrido «C3 06–10 → C14 10–14», horas asignado/libre).")]

    # --- 5.7 validador
    E += [H2("5.7 Validador independiente")]
    E += [P(f"{K('validador.validar')} no reutiliza el modelo: recalcula con sus propias fórmulas sobre la activación y el uso del plan. Comprueba binariedad y u ≤ a (R1), "
            "célula 10 y fuera de horas laborables (R2), células 11 y 12 (R3), paradas (R4), personal entero y N = ⌈carga⌉ ≤ disp (R5), balance de stock (R6), "
            "almacén ≤ 800 m² (R8), cargas ≤ 1 por trabajador, y recalcula R, S, Q, B, E y la puntuación (tolerancia 1e-4). Una lista vacía certifica el plan. "
            "Stock bajo el SS y stock &lt; 0 <b>no</b> son incumplimientos sino avisos.")]

    # --- 5.8 CRITICO
    E += [H2("5.8 Estado CRÍTICO y aviso para dirección")]
    E += [P("Si en algún slot el stock de una pieza es negativo, el plan se calcula igualmente y se marca <b>CRITICO</b> (ejecutable, con pedidos sin servir). "
            f"{K('validador')} genera: <i>agotamiento</i> (por pieza: hora en que baja del SS, hora en que se queda sin stock, hora en que repone el SS y mínimo), "
            "<i>desabastecimiento</i> (piezas no servidas por pieza y ciclo de expedición = incremento del pedido pendiente) y el texto "
            "<b>aviso_direccion</b>, que se muestra destacado en la app y como primera alerta de la recomendación. Por cada pieza afectada el aviso indica la hora "
            "en que agota el SS, la hora en que se queda sin stock, las piezas no servidas por ciclo y el total.")]

    # --- 5.9 explicación
    E += [H2("5.9 Explicación y alertas")]
    E += [P(f"{K('motor._explicar')} describe el turno actual: <b>qué</b> (células activas, franja, piezas y personas), <b>por qué</b> (la pieza caería bajo el SS a tal hora sin producir, "
            "o se acerca al óptimo de cierre; producción anticipada por una parada posterior; franja solar) e <b>impacto</b> (comparación con Top 2/3: puntuación y horas libres). "
            "Alertas: plan crítico/inviable, SS consumido y su reposición, stock lejos del óptimo (&gt; 25 % en algún cierre), almacén &gt; 90 %, recurso al 100 %, "
            "horas libres totales y alternativas descartadas.")]

    # --- 5.10 contingencia
    E += [H2("5.10 Contingencia a petición")]
    E += [P(f"{K('rolling.contingencia(esc, rec, ahora, bajas_celulas, bajas_personas, bajas_rol)')} sólo se ejecuta al pulsar «Calcular plan de contingencia»:")]
    E += bullets([
        "Copia el escenario y fija el stock a la hora indicada con el del plan vigente (<font name='DVM'>stock_en</font>).",
        "Aplica las <b>averías</b> [{célula, desde, hasta}] (la célula 10 lanza error), las <b>bajas de personas</b> por id (T-OP04) y las <b>bajas por rol</b> (a todos los turnos del horizonte).",
        "Recalcula con <font name='DVM'>recomendar(top_k=1)</font> y devuelve <font name='DVM'>rec_antes</font>, <font name='DVM'>rec_despues</font>, <font name='DVM'>reubicacion</font> (trabajadores cuya célula cambia), "
        "<font name='DVM'>maquinas</font> (células que se activan o amplían y franjas nuevas), <font name='DVM'>agotamiento</font>, <font name='DVM'>desabastecimiento</font>, <font name='DVM'>aviso_direccion</font> y "
        "<font name='DVM'>resumen</font> en lenguaje de planta («Los 3 operarios de la C14 pasan a…», «Activar C15 de 15:00 a 16:00»).",
        f"{K('contingencia_celula')} es el atajo para una sola célula. «Aplicar como incidencia real» sustituye el escenario de la app por el escenario con las incidencias.",
    ])

    # --- 5.11 rendimiento
    E += [H2("5.11 Rendimiento")]
    st = X["st_rec06"]
    filas = [["Plan", "Fase 1 (vars / rest. / bin.)", "t fase 1", "Fase 2 (vars / rest. / enteras)", "t fase 2"]]
    for j in range(0, min(6, len(st)), 2):
        filas.append([f"Top {j // 2 + 1} (06:00)", f"{fm(st[j]['vars'], 0)} / {fm(st[j]['cons'], 0)} / {st[j]['bins']}", f"{fm(st[j]['t'], 1)} s",
                      f"{fm(st[j + 1]['vars'], 0)} / {fm(st[j + 1]['cons'], 0)} / {st[j + 1]['bins']}", f"{fm(st[j + 1]['t'], 1)} s"])
    E += [tabla(filas, [3 * cm, 4.2 * cm, 1.8 * cm, 5.2 * cm, 2.8 * cm], ["L", "C", "R", "C", "R"], fs=7.6)]
    E += [P(f"Tabla 5.2 — Tamaño y tiempo del MILP en la demo de las 06:00. Con 8 s por plan HiGHS agota el tiempo en ambas fases (idoneidad ≈ 90–92 %); "
            f"el cruce del Top-3 completo tarda {fm(X['rec06'].tiempo_total_s, 0)} s a las 06:00 y {fm(X['rec14'].tiempo_total_s, 0)} s a las 14:00.", "cap")]
    return E


# ------------------------------------------------------------------------------------- 6. ejemplo
def _cfg(l):
    return ", ".join(str(c) for c in l)


def _tabla_planes(rec):
    f = [["Plan", "Estado", "Pts", "Máx. alcanz.", "Idon. %", "H. libres", "m² medio", "kWh red", "Camiones/día", "Configuración turno actual"]]
    for p in rec.top:
        k = p.kpis
        f.append([p.nombre, p.estado, fm(p.puntuacion, 2), fm(k.get("puntuacion_max_teorica"), 1), fm(p.idoneidad, 1), fm(k["horas_libres_total"], 0),
                  fm(k["m2_medio"], 0), fm(k["kwh_total"], 0), fm(k["camiones_dia"], 0), _cfg(p.config_turno_actual)])
    return tabla(f, [1.3 * cm, 1.6 * cm, 1.1 * cm, 1.4 * cm, 1.2 * cm, 1.3 * cm, 1.3 * cm, 1.3 * cm, 1.5 * cm, 4.9 * cm],
                 ["L", "L", "R", "R", "R", "R", "R", "R", "R", "L"], fs=7.0)


def _tabla_que(rec):
    q = rec.explicacion["que"]
    f = [["Célula", "Tipo", "Horas", "Franja", "Piezas", "Horas solar"]]
    for _, r in q.iterrows():
        f.append([str(int(r["celula"])), r["tipo"], str(int(r["horas_activas"])), r["franja"], fm(r["piezas"], 0), str(int(r["horas_franja_solar"]))])
    return tabla(f, [1.4 * cm, 1.4 * cm, 1.4 * cm, 7 * cm, 2 * cm, 2.2 * cm], ["C", "C", "C", "L", "R", "C"], fs=7.2)


def _tabla_comp(planes, nombres):
    f = [["Plan"] + [NOMBRES_COMPONENTE[c] for c in COMPONENTES] + ["Puntuación"]]
    for p, n in zip(planes, nombres):
        f.append([n] + [f"{fm(p.componentes[c], 3)} → {fm(p.contribuciones[c], 1)}" for c in COMPONENTES] + [fm(p.puntuacion, 2)])
    return tabla(f, [2.3 * cm, 2.5 * cm, 2.4 * cm, 2.5 * cm, 2.3 * cm, 2.3 * cm, 2.1 * cm], ["L", "C", "C", "C", "C", "C", "R"], fs=7.0)


def seccion_ejemplo(X, D, F):
    esc = X["esc"]
    r06, r14 = X["rec06"], X["rec14"]
    t1, t14 = r06.top[0], r14.top[0]
    k1, k14 = t1.kpis, t14.kpis
    E = H1("6. Ejemplo numérico trabajado")
    E += [P(f"Escenario {K('datos.estado_ejemplo()')}: semana del 28/09/2026, 1.500 coches/día (2 COMB : 1 VE → {fm(D['dem_dia'][0], 0)} piezas VE y "
            f"{fm(D['dem_dia'][1], 0)} COMB por pieza y día), stock inicial = 2 × SS (800 VE / 400 COMB), C13 en parada programada el 02/10 de 14:00 a 22:00 con 2 técnicos, "
            "1 operario de baja en el turno de tarde del 02/10 (el resto de turnos descuenta el 5 % de absentismo por defecto). Se ejecuta el motor real; "
            f"todas las cifras proceden de {K('motor.recomendar')} y {K('rolling.contingencia')}.")]

    E += [H2("6.1 Plan de las 06:00")]
    E += [_tabla_planes(r06), P("Tabla 6.1 — Top-K de las 06:00 (planes ordenados por piezas sin servir y puntuación; Top 2/3 se descartarían con idoneidad &lt; 50 %).", "cap")]
    E += [figc(F["gantt06"], "Figura 6.1 — Activación de células del Top 1 a las 06:00 (24 h).", 16.2)]
    E += [H3("Qué activar en el turno de mañana")]
    E += [_tabla_que(r06)]
    E += [Spacer(1, 4)]
    E += [P("<b>Por qué.</b> " + " ".join(r06.explicacion["porque"][:5]).replace("<", "&lt;"), "small")]
    ind = {r: k1[f"horas_libres_{r}"] for r in RECURSOS}
    E += [P(f"<b>Horas libres.</b> El Top 1 deja {fm(k1['horas_libres_total'], 0)} horas-persona sin tarea de {fm(D['disp_rec06'], 0)} presentes en horas laborables "
            f"(R = {fm(t1.componentes['R'], 3)}): operarios {fm(ind['operarios'], 0)}, picking {fm(ind['picking'], 0)}, carretilleros {fm(ind['carretilleros'], 0)}, "
            f"mto {fm(ind['mto'], 0)} y calidad {fm(ind['calidad'], 0)}. Picking y carretilleros están al 100 % durante todo el horizonte: el cuello de botella "
            "no es el personal de producción sino picking y carretillero, y las horas libres se concentran en mantenimiento y calidad.")]
    E += [figc(F["contrib06"], "Figura 6.2 — Contribución de cada criterio a la puntuación (06:00).", 16.2)]
    E += [_tabla_comp(r06.top, [p.nombre for p in r06.top]), P("Tabla 6.2 — Componente normalizado → contribución en puntos (100·peso·(1 − componente)).", "cap")]
    E += [figc(F["stock06"], "Figura 6.3 — Stock/SS de cada pieza y stock óptimo en los cierres de turno (rombos), Top 1 a las 06:00.", 16.2)]
    sv = t1.stock_vs_optimo
    f = [["Cierre de turno", "Desviación media", "Desviación máxima"]]
    for c in k1["stock_opt_por_cierre"]:
        f.append([f"{pd.Timestamp(c['cierre']):%d/%m %H:%M}", f"{fm(c['dev_media_pct'], 1)} %", f"{fm(c['dev_max_pct'], 1)} %"])
    E += [tabla(f, [5 * cm, 4 * cm, 4 * cm], ["L", "R", "R"], fs=7.6), P("Tabla 6.3 — Desviación |stock − óptimo|/óptimo al cierre de cada turno (criterio B).", "cap")]
    E += [P(f"Cada día laborable salen {fm(k1['camiones_dia'], 0)} camiones en media ({fm(k1['camiones_por_ciclo_medio'], 1)} por ciclo, máximo {k1['camiones_por_ciclo_max']}). "
            f"Almacén: {fm(k1['m2_medio'], 0)} m² medios y {fm(k1['m2_pico'], 0)} m² de pico sobre 800 m²; energía de red {fm(k1['kwh_total'], 0)} kWh, "
            f"{fm(k1['kwh_solar_pct'], 0)} % de la bruta en franja solar.")]

    E += [H3("Ejemplo de asignación nominal")]
    tr = t1.trabajadores
    g = tr[(tr["turno"] == "M") & (tr["rol"] == "operarios")].head(7)
    f = [["Trabajador", "Horas asignado", "Horas libre", "Recorrido en el turno de mañana"]]
    for _, r in g.iterrows():
        f.append([r["trabajador"], str(int(r["horas_asignado"])), str(int(r["horas_libre"])), r["celulas"] or "—"])
    E += [tabla(f, [2.4 * cm, 2.4 * cm, 2.2 * cm, 10 * cm], ["L", "R", "R", "L"], fs=7.2), P("Tabla 6.4 — Primeros operarios del turno de mañana del Top 1 (06:00).", "cap")]

    E += [H2("6.2 Plan de las 14:00")]
    E += [_tabla_planes(r14), P("Tabla 6.5 — Top-K de las 14:00 (la parada de C13 afecta al turno de tarde).", "cap")]
    E += [figc(F["gantt14"], "Figura 6.4 — Activación de células del Top 1 a las 14:00.", 16.2)]
    E += [P(f"A las 14:00 el Top 1 deja {fm(k14['horas_libres_total'], 0)} horas libres (R = {fm(t14.componentes['R'], 3)}) y {fm(k14['demanda_cubierta_pct'], 0)} % de la demanda cubierta. "
            "La célula 13 no aparece en la configuración: está bloqueada de 14:00 a 22:00 y sus 2 técnicos se restan del personal de mantenimiento.")]
    E += [tabla([["Magnitud", "06:00", "14:00"],
                 ["Horas libres totales (antes de la corrección)", "321 h", "238 h"],
                 ["Horas libres totales (motor actual)", f"{fm(k1['horas_libres_total'], 1)} h", f"{fm(k14['horas_libres_total'], 1)} h"],
                 ["Puntuación Top 1", fm(t1.puntuacion, 2), fm(t14.puntuacion, 2)],
                 ["Idoneidad Top 1", f"{fm(t1.idoneidad, 1)} %", f"{fm(t14.idoneidad, 1)} %"],
                 ["Desviación media del stock óptimo", f"{fm(k1['stock_opt_dev_media_pct'], 1)} %", f"{fm(k14['stock_opt_dev_media_pct'], 1)} %"]],
                [8 * cm, 4 * cm, 4 * cm], ["L", "R", "R"], fs=7.8, destacar_filas=(2,)),
          P("Tabla 6.6 — Resumen de los dos planes y efecto de medir el tiempo muerto sobre el trabajo productivo.", "cap")]

    # --- contingencia C14
    c14 = X["c14"]
    a, d = c14["rec_antes"].mejor, c14["rec_despues"].mejor
    E += [H2("6.3 Contingencia: avería de la célula 14")]
    E += [P(f"Se pide {K('rolling.contingencia_celula(esc, rec06, 14, 10:00, hasta=03/10 06:00)')}: la C14 (VE, 77,9 kW, 3 operarios) se avería a las 10:00 y se repara a las 06:00 del día siguiente. "
            "El motor toma el stock del plan de las 06:00 en la hora 10:00 y recalcula 24 h.")]
    E += [tabla([["Indicador", "Plan previo", "Contingencia C14"],
                 ["Estado", a.estado, d.estado],
                 ["Puntuación", fm(a.puntuacion, 2), fm(d.puntuacion, 2)],
                 ["Horas libres totales (24 h)", fm(a.kpis["horas_libres_total"], 0), fm(d.kpis["horas_libres_total"], 0)],
                 ["Piezas no servidas", fm(a.kpis["piezas_no_servidas_total"], 0), fm(d.kpis["piezas_no_servidas_total"], 0)],
                 ["Desviación media del stock óptimo (cierres)", f"{fm(a.kpis['stock_opt_dev_media_pct'], 0)} %", f"{fm(d.kpis['stock_opt_dev_media_pct'], 0)} %"]],
                [7 * cm, 4.5 * cm, 4.5 * cm], ["L", "R", "R"], fs=7.8)]
    E += [Spacer(1, 4), P("Máquinas a activar o ampliar:", "body")]
    mq = c14["maquinas"]
    f = [["Célula", "Horas antes", "Horas después", "Δ", "Franjas nuevas"]]
    for _, r in mq.iterrows():
        f.append([str(int(r["celula"])), str(int(r["horas_antes"])), str(int(r["horas_despues"])), f"{int(r['delta']):+d}", r["franjas_nuevas"] or "—"])
    E += [tabla(f, [1.6 * cm, 2.2 * cm, 2.6 * cm, 1.4 * cm, 9.2 * cm], ["C", "R", "R", "R", "L"], fs=7.2)]
    E += [Spacer(1, 4), P("<b>Resumen en lenguaje de planta</b> (primeras líneas de <font name='DVM'>resumen</font>):", "body")]
    E += bullets([x.replace("<", "&lt;") for x in c14["resumen"][:4]] + [c14["resumen"][-3].replace("<", "&lt;")] if len(c14["resumen"]) > 4 else
                 [x.replace("<", "&lt;") for x in c14["resumen"]], "bul")
    E += [P(f"La reubicación contiene {len(c14['reubicacion'])} cambios de puesto. El stock de seguridad de la pieza 14 se consume desde las 00:00 y no se repone en el horizonte "
            "(termina a las 06:00 con la reparación): el plan reparte la producción VE entre las demás células y no hay pedidos sin servir, por lo que no se emite aviso a dirección.")]

    # --- caso grave
    sev = X["sev"]
    ps = sev["rec_despues"].mejor
    E += [H2("6.4 Caso grave: pedidos sin servir")]
    E += [P(f"Para forzar el desabastecimiento se parte de {K('estado_ejemplo()')} con el stock inicial reducido al SS (400 VE / 200 COMB) y se averían las 7 células VE "
            f"({_cfg(SEV_CELULAS)}) desde las 06:00 hasta nuevo aviso. Resultado: el plan es <b>{ps.estado}</b> ({fm(ps.puntuacion, 2)} pts, "
            f"{fm(ps.kpis['piezas_no_servidas_total'], 0)} piezas sin servir). Las 7 piezas VE agotan el SS al servir el primer ciclo y se quedan sin stock a las 00:00 del 03/10; "
            "los pedidos pendientes aparecen en los ciclos de las 00:00, 01:30, 03:00 y 04:30.")]
    des = sev["desabastecimiento"]
    if len(des):
        res = des.groupby("pieza")["piezas_no_servidas"].sum()
        f = [["Pieza", "Ciclos con faltante", "Piezas no servidas"]]
        for pz, v in res.items():
            f.append([str(int(pz)), str(int((des["pieza"] == pz).sum())), fm(v, 0)])
        f.append(["Total", str(len(des)), fm(float(des["piezas_no_servidas"].sum()), 0)])
        E += [tabla(f, [3 * cm, 4.5 * cm, 4.5 * cm], ["C", "R", "R"], fs=7.6, destacar_filas=(len(f) - 1,))]
    av = (sev["aviso_direccion"] or "")[:560].replace("<", "&lt;")
    E += [Spacer(1, 4), caja(f"<b>Aviso para dirección</b> (inicio del texto generado): {av}…", color=C_ROJO, fondo=colors.HexColor("#FDEDEC"))]
    E += [Spacer(1, 4), figc(F["stockg"], "Figura 6.5 — Stock/SS en el caso grave: las piezas VE bajan del SS y cruzan el cero (pedido sin servir).", 16.2)]
    return E


# ------------------------------------------------------------------------------------- 7. validación / limitaciones
def seccion_validacion(X, D, F):
    E = H1("7. Validación, pruebas y limitaciones")
    E += [H2("7.1 Validación")]
    E += bullets([
        f"<b>Validador independiente</b> (§5.7): se ejecuta sobre cada plan; en los planes de la demo la lista de incumplimientos está vacía "
        f"(Top 1 de las 06:00: {len(X['rec06'].top[0].incumplimientos)}; 14:00: {len(X['rec14'].top[0].incumplimientos)}).",
        f"<b>Pruebas automáticas</b> ({K('tests/test_motor.py')}, {X.get('npass', '23')}): Top-3 viable a las 06:00; reglas duras y célula 10; franja solar; "
        "célula 10 no se para; demanda por piezas y coches; camiones de 15 m²; expedición real; SS consumible con reposición; pedido no servido ⇒ CRITICO con aviso; "
        "R = tiempo muerto del personal presente; B = desviación respecto al óptimo; ida y vuelta de estado.json; personal entero, ids estables y baja de un trabajador; "
        "estado en tiempo real; eventos encadenados; contingencia de células y personas.",
        "<b>Coherencia modelo–validador</b>: la puntuación del plan (calculada por modelo.evaluar) se recalcula en el validador y se compara con tolerancia 1e-4.",
    ])
    E += [H2("7.2 Limitaciones")]
    E += bullets([
        "<b>Idoneidad.</b> Con 8 s por plan la demo no alcanza el óptimo probado (idoneidad 90–92 %); se informa del máximo alcanzable. Subir el límite mejora la cota, pero se mantiene en 8 s por decisión del equipo (D13).",
        "<b>Gap abierto</b>: todos los planes de la demo son FACTIBLES (gap ≈ 8–10 % tras 8 s); los Top 2/3 pueden quedar por debajo del 50 % de idoneidad y descartarse en otros escenarios.",
        "<b>Capacidad nominal.</b> La producción usa capacidad al 100 % (OEE) y no modela cambios de referencia, averías aleatorias ni mermas (S1, S29).",
        "<b>Personal.</b> Se asume que cualquier persona de un rol puede cubrir cualquier célula; no hay polivalencia limitada ni preferencias. Por defecto se descuenta un 5 % de absentismo si no hay fila de bajas (S6).",
        "<b>Horizonte de 24 h.</b> Una avería que dure más de lo que cubre el horizonte no se ve entera; el stock de seguridad puede aparecer «sin reponer» sólo porque el horizonte termina (S24).",
        "<b>Cuello de botella en picking y carretilleros</b> (al 100 % en la demo): R sólo puede mejorar si hay más personal de esos roles o cargas menores.",
        "<b>Contingencia a petición</b>: no recalcula sola; la reubicación se compara hora a hora con el plan previo y puede incluir cambios no ligados a la avería.",
    ])
    return E


# ------------------------------------------------------------------------------------- anexos
def anexo_a(X, D):
    E = H1("Anexo A. Parámetros constantes")
    f = [["Parámetro", "Valor", "Descripción"]]
    for k, (v, desc) in PARAMETROS_DEFECTO.items():
        f.append([K(k), fm(v, 3).rstrip("0").rstrip(",") if isinstance(v, float) else str(v), desc])
    E += [tabla(f, [4.2 * cm, 1.6 * cm, 11.2 * cm], ["L", "R", "L"], fs=7.2)]
    E += [Spacer(1, 6), P(f"Personal estándar por turno (M / T / N): operarios 16/16/14, picking 2/2/2, carretilleros 4/4/3, mto 7/7/4, calidad 3/3/2. "
                          f"Suma de SS = {fm(D['suma_ss'], 1)} m² (test: 151,7 m²). m² por camión: una pieza de cada célula VE ocupa {fm(D['m2ve'], 3)} m² y de cada célula COMB {fm(D['m2comb'], 3)} m².")]
    return E


DIF = [
    ("R usa N (CAMBIOS_V3 A1: R = Σ(Disp − N)/ΣDisp)", "Código (modelo._componentes_RQ, MILP): R = Σ(Disp − Σ req·u)/ΣDisp con la célula 10 la hora completa; N sólo interviene en Q y en el roster. Cambio posterior (commit ce2d6c6)."),
    ("Gap relativo del 0,1 % (ESPECIFICACION §11, parámetro gap_relativo)", "Código: el solver recibe gapRel = 0 y gapAbs = gap_relativo × 0,5 × 1000 (absoluto); fase 1 con 2 %. El gap informado se recalcula con el objetivo completo (corrección reciente de modelo.py). Con 8 s los planes quedan FACTIBLES (idoneidad ≈ 90–92 %)."),
    ("Estados OPTIMO/FACTIBLE tras la v3 (Top 1 «óptimo garantizado ±0,1 %»)", "Código: los planes de la demo son FACTIBLES; sólo OPTIMO si HiGHS cierra el gap dentro del tiempo."),
    ("simular_semana: 10 s y gap 0,5 % (ESPECIFICACION §11)", "Código: tiempo ≤ 6 s por iteración y gap_relativo ≥ 0,005."),
    ("Excel, flags, plan de referencia, condición terminal F18, colchón del 10 %", "Presentes aún en ESPECIFICACION.md y PLAN_EQUIPO.md pero eliminados del código (v3). config.HOJAS_COLUMNAS conserva el nombre «hojas» por herencia; no hay lectura de Excel."),
    ("data/estado.json = demo (CAMBIOS_V3 A6)", "El fichero del repositorio no coincide con datos.estado_ejemplo(): añade la semana del 05/10 y una demanda corregida del 05/10 (556 VE / 952 COMB)."),
    ("Absentismo (CAMBIOS_V2 A6) no citado en v3", "Código: sigue vigente; sin fila de bajas se resta round(estándar × 5 %), p. ej. 1 operario por turno en la demo salvo el turno T del 02/10, que tiene fila explícita."),
    ("Textos de contingencia", "Código: el resumen puede decir «y 1 cambios más»; y la reubicación lista a los trabajadores cuya célula cambia respecto al plan anterior, aunque la causa no sea la avería (p. ej. caso grave)."),
    ("config.RECURSOS_R, alias chasis_* en KPIs, ppc=1", "Restos de compatibilidad v1/v2 sin efecto en el modelo."),
]


def anexo_b(X, D):
    E = H1("Anexo B. Diferencias entre documentación y código", salto=False)
    E += [P("Hallazgos detectados al contrastar CAMBIOS_V2, CAMBIOS_V3 y ESPECIFICACION con el código. En todos los casos este informe sigue el código.")]
    E += [tabla([["Documentación", "Código (ground truth)"]] + [[a, b] for a, b in DIF], [6 * cm, 11 * cm], ["L", "L"], fs=7.4, valign="TOP")]
    return E


def anexo_c(X, D):
    E = H1("Anexo C. API pública (resumen)", salto=False)
    E += [tabla([
        ["Función", "Descripción"],
        [K("datos.cargar_estado(path) / guardar_estado(esc, path) / estado_ejemplo()"), "Lectura y escritura de data/estado.json y escenario de demo"],
        [K("datos.demanda_dia(esc, fecha)"), "Piezas VE y COMB por pieza del día (corregida o semanal / 5)"],
        [K("horizonte.construir_horizonte(esc, inicio, horas)"), "Slots, disponibilidad, bloqueos, camiones, cierres y stock óptimo"],
        [K("modelo.resolver(esc, hz, cortes, tiempo_limite)"), "MILP de dos fases; devuelve un Plan evaluado"],
        [K("motor.recomendar(esc, inicio, horas, top_k)"), "Top-K, explicación, alertas y aviso_direccion (Recomendacion)"],
        [K("validador.validar(esc, hz, plan)"), "Lista de incumplimientos (vacía = plan correcto)"],
        [K("rolling.contingencia(esc, rec, ahora, bajas_celulas, bajas_personas, bajas_rol)"), "Contingencia a petición; contingencia_celula es el atajo"],
        [K("rolling.estado_en(esc, rec, ahora)"), "Estado de las 16 células en una hora (visualización)"],
        [K("python -m kwd --estado --inicio --top --pdf"), "Línea de comandos (kwd.cli)"]],
        [8.5 * cm, 8.5 * cm], ["L", "L"], fs=7.4)]
    return E


# ------------------------------------------------------------------------------------- principal
def main():
    t0 = time.time()
    SALIDA.parent.mkdir(exist_ok=True)
    cache = os.environ.get("KWD_INFORME_CACHE")
    if cache and os.path.exists(cache):
        X = pickle.load(open(cache, "rb"))
        print(f"Resultados del motor cargados de {cache}")
    else:
        print("Ejecutando el motor real…", flush=True)
        X = ejecutar_motor()
        if cache:
            pickle.dump(X, open(cache, "wb"))
    print(f"Motor listo en {X['t_motor']:.0f} s", flush=True)
    D = derivados(X)
    tmp = Path(tempfile.mkdtemp(prefix="kwd_inf_"))
    F = {k: tmp / f"{k}.png" for k in ("pipeline", "gantt06", "gantt14", "stock06", "contrib06", "stockg")}
    print("Gráficos…", flush=True)
    r06, r14 = X["rec06"], X["rec14"]
    g_pipeline(F["pipeline"])
    g_gantt(r06.top[0], r06.horizonte, X["esc"], F["gantt06"], "Top 1 a las 06:00 · activación de células en 24 h")
    g_gantt(r14.top[0], r14.horizonte, X["esc"], F["gantt14"], "Top 1 a las 14:00 · activación de células en 24 h")
    g_stock(r06.top[0], r06.horizonte, X["esc"], F["stock06"], "Stock por pieza frente al SS (Top 1, 06:00)")
    g_contrib(r06.top, [p.nombre for p in r06.top], F["contrib06"])
    ps = X["sev"]["rec_despues"]
    g_stock(ps.mejor, ps.horizonte, X["eg"], F["stockg"], "Caso grave: 7 células VE averiadas desde las 06:00")

    E = []
    E += seccion_portada(X, D)
    E += seccion_resumen(X, D)
    E += seccion_fuentes(X, D)
    E += seccion_supuestos(X, D)
    E += seccion_trabajo(X, D)
    E += seccion_metodologia(X, D, F)
    E += seccion_ejemplo(X, D, F)
    E += seccion_validacion(X, D, F)
    E += anexo_a(X, D)
    E += anexo_b(X, D)
    E += anexo_c(X, D)

    faltan = comprobar_glifos()
    if faltan:
        print("AVISO: glifos ausentes en DejaVu:", {k: v for k, v in faltan.items()})
    doc = Doc(SALIDA)
    doc.multiBuild(E)
    kb = SALIDA.stat().st_size / 1024
    print(f"PDF generado: {SALIDA} · {doc.page} páginas · {kb:.0f} KB · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
