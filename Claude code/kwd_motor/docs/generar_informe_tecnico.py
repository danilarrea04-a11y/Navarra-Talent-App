"""Genera salida/KWD_Informe_Tecnico.pdf: informe técnico exhaustivo del motor de decisión KWD.

Uso (desde la raíz del proyecto, PowerShell 5.1):
    $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe docs\\generar_informe_tecnico.py

Todas las cifras (capacidades, cargas de camión, planes Top 1/2/3, tamaño del modelo, tiempos de
resolución, reconfiguración y simulación semanal) se calculan en cada ejecución con el motor real
(`kwd.motor.recomendar`, `kwd.rolling.reconfigurar`, `kwd.rolling.simular_semana`). Tarda ~3-4 min.
Variable de entorno opcional KWD_INFORME_CACHE=<ruta.pkl>: sólo para depurar el maquetado (reutiliza
los resultados del motor de una ejecución anterior).

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

from kwd import (baseline, config, datos, horizonte, informes, modelo, motor, plan as plan_mod, rolling,
                 validador)
from kwd.config import (COMPONENTES, FLAGS, HOJAS_COLUMNAS, NOMBRES_COMPONENTE, NOMBRES_TURNO, PARAMETROS_DEFECTO,
                        RECURSOS)

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
VERSION = "v1"

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
    try:
        X: dict = {}
        esc = datos.cargar_entrada(RAIZ / "data" / "entrada_ejemplo.xlsx")
        escc = datos.cargar_entrada(RAIZ / "data" / "escenario_contingencia.xlsx")
        X["esc"], X["escc"] = esc, escc
        t0 = time.time()
        for nombre, e, ini in [("rec06", esc, "2026-10-02 06:00"), ("rec14", esc, "2026-10-02 14:00"),
                               ("recc", escc, "2026-10-02 06:00")]:
            print(f"  motor: {nombre}…", flush=True)
            stats.clear()
            X[nombre] = motor.recomendar(e, pd.Timestamp(ini))
            X["st_" + nombre] = list(stats)
        # reconfiguración: baja de la célula 3 a las 10:00 durante 8 h (la célula 3 está en el Top 1 de las 06:00)
        ahora = pd.Timestamp("2026-10-02 10:00")
        print("  motor: reconfigurar…", flush=True)
        ev = rolling.Evento("baja_celula", {"celula": 3, "desde": ahora, "hasta": ahora + pd.Timedelta(hours=8)})
        X["ahora"], X["evento"] = ahora, ev
        stats.clear()
        X["rec_rc"] = rolling.reconfigurar(esc, X["rec06"], ev, ahora)
        X["st_rc"] = list(stats)
        otros = {}
        for c in (13, 8, 9):
            ev2 = rolling.Evento("baja_celula", {"celula": c, "desde": ahora, "hasta": ahora + pd.Timedelta(hours=8)})
            r2 = rolling.reconfigurar(esc, X["rec06"], ev2, ahora)
            otros[c] = r2
        X["rc_otros"] = otros
        print("  motor: simular_semana (≈1,5 min)…", flush=True)
        t1 = time.time()
        X["sem"] = rolling.simular_semana(esc, pd.Timestamp("2026-09-28"))
        X["t_sem"] = time.time() - t1
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


def _decor(ax, hz, esc, off=0, etiquetas=True, n=None):
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


def _gantt_ax(ax, plan, hz, esc, t_ref, nx, etiquetas=True, titulo=None):
    t = datos.tabla_celulas(esc)
    cel = sorted(t.index)
    off = int((hz.inicio - t_ref) / pd.Timedelta(hours=1))
    _decor(ax, hz, esc, off, etiquetas)
    for k, c in enumerate(cel):
        for h in hz.bloqueos.get(c, set()):
            ax.add_patch(Rectangle((off + h, k - 0.4), 1, 0.8, fc="#F5C6C0", ec=C_ROJO, hatch="////", lw=0.3,
                                   zorder=2))
        for h in range(len(hz.slots)):
            if plan.activacion.at[h, c] > 0.5:
                col = C_LOG if c == 10 else (C_VE if t.loc[c, "es_ve"] else C_COMB)
                u = float(plan.uso.at[h, c]) if c != 10 else 1.0
                ax.add_patch(Rectangle((off + h + 0.04, k - 0.36), 0.92, 0.72, fc=col, ec="white", lw=0.4,
                                       alpha=0.45 + 0.55 * min(max(u, 0), 1), zorder=3))
    ax.set_yticks(range(len(cel)))
    ax.set_yticklabels([f"Célula {c}" for c in cel], fontsize=7)
    ax.set_ylim(len(cel) - 0.4, -0.7)
    _eje_horas(ax, nx, t_ref)
    ax.grid(axis="x", color="#DDDDDD", lw=0.4, zorder=0)
    if titulo:
        ax.set_title(titulo, loc="left", pad=16 if etiquetas else 4)


_LEYENDA_G = [Patch(fc=C_VE, label="Activa VE"), Patch(fc=C_COMB, label="Activa combustión"),
              Patch(fc=C_LOG, label="Logística (10)"), Patch(fc=C_SOLAR, label="Franja solar"),
              Patch(fc="#F5C6C0", ec=C_ROJO, hatch="////", label="Baja / mantenimiento")]


def g_gantt(plan, hz, esc, ruta, titulo):
    fig_, ax = plt.subplots(figsize=(8.6, 4.3))
    _gantt_ax(ax, plan, hz, esc, hz.inicio, len(hz.slots), titulo=titulo)
    ax.set_xlabel(f"Hora (inicio {hz.inicio:%d/%m %H:%M}; la intensidad del color indica la fracción u de uso)")
    ax.legend(handles=_LEYENDA_G, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=5, frameon=False,
              fontsize=7)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_stock(plan, hz, esc, ruta, titulo):
    t = datos.tabla_celulas(esc)
    ss = datos.ss_por_celula(esc)
    k = float(esc.parametros["colchon_ss"])
    fig_, axs = plt.subplots(1, 2, figsize=(8.6, 3.3), sharey=True)
    n = len(hz.slots)
    fin = list(range(1, n + 1))
    for ax, ve, nom, col in [(axs[0], True, "Piezas de células VE", C_VE),
                             (axs[1], False, "Piezas de células de combustión", C_COMB)]:
        _decor(ax, hz, esc, 0, etiquetas=False)
        for c in plan.stock.columns:
            if bool(t.loc[c, "es_ve"]) == ve:
                viola = float((plan.stock[c] / ss[c]).min()) < 1 - 1e-6
                ax.plot(fin, plan.stock[c] / ss[c], color=C_ROJO if viola else col, lw=1.4 if viola else 0.9,
                        alpha=0.95 if viola else 0.7)
                if viola:
                    ax.text(n + 0.2, float((plan.stock[c] / ss[c]).iloc[-1]), f"C{c}", fontsize=6.5, color=C_ROJO)
        ax.axhline(1.0, color=C_ROJO, lw=1.2)
        ax.axhline(1 + k, color=C_AMBAR, lw=1, ls="--")
        ax.set_title(nom, loc="left")
        _eje_horas(ax, n, hz.inicio)
        ax.set_xlabel("Hora")
    axs[0].set_ylabel("Stock / stock de seguridad")
    axs[1].legend(handles=[Patch(fc="none", ec=C_ROJO, label="SS (obligatorio)"),
                           Patch(fc="none", ec=C_AMBAR, label=f"Colchón +{k:.0%}")], frameon=False, fontsize=7,
                  loc="upper right")
    fig_.suptitle(titulo, x=0.01, ha="left", fontsize=9, fontweight="bold", color=NAVY_HEX)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_almacen(plan, base, hz, esc, ruta):
    A = esc.area_producto_terminado()
    fig_, ax = plt.subplots(figsize=(8.6, 2.9))
    _decor(ax, hz, esc, 0, etiquetas=False)
    x = list(range(1, len(hz.slots) + 1))
    ax.plot(x, plan.espacio.values, color=NAVY_HEX, lw=2, label=plan.nombre or "Plan")
    ax.plot(x, base.espacio.values, color="#999999", lw=1.4, ls="--", label="Referencia manual")
    ax.axhline(A, color=C_ROJO, lw=1.2)
    ax.axhline(0.9 * A, color=C_AMBAR, lw=0.9, ls=":")
    ax.text(0.3, A * 0.97, f"Capacidad {A:.0f} m²", color=C_ROJO, va="top", fontsize=7.5)
    ax.text(0.3, 0.9 * A * 0.985, "Umbral de aviso 90 %", color=C_AMBAR, va="top", fontsize=7.5)
    ax.set_ylim(0, A * 1.05)
    ax.set_ylabel("m² de producto terminado")
    _eje_horas(ax, len(hz.slots), hz.inicio)
    ax.legend(frameon=False, fontsize=7.5, loc="center right")
    ax.set_title("Ocupación del almacén de producto terminado", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_energia(plan, hz, esc, ruta):
    f = hz.slots["factor_energia"].values
    fig_, ax = plt.subplots(figsize=(8.6, 2.8))
    ax.bar([i + 0.5 for i in range(len(f))], plan.energia_kwh.values, width=0.85,
           color=["#E6A700" if v < 0.99 else ("#4A5AA8" if v > 1.01 else "#8A90A6") for v in f], zorder=3)
    sol = [i for i, v in enumerate(f) if v < 0.99]
    if sol:
        ax.axvspan(min(sol), max(sol) + 1, color=C_SOLAR, alpha=0.5, lw=0, zorder=0)
    ax.set_ylim(0, max(plan.energia_kwh.max(), 1) * 1.28)
    ax.set_ylabel("kWh de red por hora")
    _eje_horas(ax, len(f), hz.inicio)
    ax.legend(handles=[Patch(fc="#E6A700", label="Franja solar (×0,85)"), Patch(fc="#8A90A6", label="Normal (×1,00)"),
                       Patch(fc="#4A5AA8", label="Noche (×1,20)")], frameon=False, fontsize=7.5, ncol=3,
              loc="upper center", bbox_to_anchor=(0.5, 1.0))
    ax.set_title("Energía de red por hora (kW · u · factor horario)", loc="left", pad=14)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_contrib(planes, nombres, ruta):
    comp = ["R", "S", "Q", "B", "E"]
    pal = ["#1F2A6B", "#3E64B8", "#6E9BD8", "#2E9E6B", "#E6A700"]
    fig_, ax = plt.subplots(figsize=(8.6, 0.55 * len(nombres) + 1.5))
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
    ax.set_yticklabels(nombres)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Contribución a la puntuación: 100·peso·(1 − componente); máximo 100 = 50+20+15+10+5")
    ax.legend(ncol=5, frameon=False, fontsize=7.2, loc="upper center", bbox_to_anchor=(0.5, -0.38))
    ax.set_title("Contribución de cada criterio KWD a la puntuación", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_camiones(hz, esc, ruta):
    cam = hz.camiones
    fig_, ax = plt.subplots(figsize=(8.6, 2.9))
    x = np.arange(len(cam))
    m2ve = cam["ve"] * M2_VE
    m2cb = cam["comb"] * M2_COMB
    ax.bar(x, m2ve, color=C_VE, label="m² por chasis VE × carga VE")
    ax.bar(x, m2cb, bottom=m2ve, color=C_COMB, label="m² por chasis COMB × carga COMB")
    ax.axhline(float(esc.parametros["m2_max_camion"]), color=C_ROJO, lw=1.2)
    ax.text(len(cam) - 0.5, float(esc.parametros["m2_max_camion"]) + 0.25, "tope 15 m² por camión", color=C_ROJO,
            ha="right", fontsize=7.5)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{pd.Timestamp(h):%H:%M}" for h in cam["hora"]], rotation=60, fontsize=6.5)
    ax.set_ylabel("m² liberados")
    ax.set_ylim(0, 21)
    ax.legend(frameon=False, fontsize=7.5, loc="upper left", ncol=2)
    ax.set_title(f"Camiones del horizonte de las {hz.inicio:%H:%M} ({len(cam)} camiones)", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_semana(sem, ruta):
    x = range(len(sem))
    fig_, (ax, ax2) = plt.subplots(2, 1, figsize=(8.6, 4.6), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax.bar([i - 0.2 for i in x], sem["puntuacion_plan_24h"], width=0.4, color=NAVY_HEX, label="Motor (plan 24 h)")
    ax.bar([i + 0.2 for i in x], sem["puntuacion_baseline"], width=0.4, color="#B9BDC9", label="Referencia manual")
    for i, e in enumerate(sem["estado"]):
        if e != "OPTIMO":
            ax.plot(i, sem["puntuacion_plan_24h"].iloc[i] + 3, marker="v", color=C_AMBAR, ms=6)
    ax.plot([], [], "v", color=C_AMBAR, label="FACTIBLE (límite 10 s)")
    ax.set_ylabel("Puntuación")
    ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper left")
    ax.set_title("Simulación semanal: puntuación del plan de 24 h de cada iteración", loc="left")
    ax.set_ylim(0, max(sem["puntuacion_plan_24h"].max(), sem["puntuacion_baseline"].max()) * 1.2)
    ids = sem["idoneidad"].astype(float)
    ax2.bar(x, ids, color=[(C_VE if v >= 99 else C_AMBAR) for v in ids], width=0.6)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Idoneidad %")
    ax2.set_xticks(list(x))
    dias = {"Mon": "Lun", "Tue": "Mar", "Wed": "Mié", "Thu": "Jue", "Fri": "Vie"}
    etiq = []
    for f_, t_ in zip(sem["fecha_turno"], sem["turno"]):
        ts = pd.Timestamp(f_)
        etiq.append(dias.get(ts.strftime("%a"), ts.strftime("%a")) + ts.strftime(" %d") + "\n" + str(t_))
    ax2.set_xticklabels(etiq, fontsize=6.5)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_tiempos(casos, ruta):
    """casos: lista (etiqueta, tiempo_s, estado)."""
    fig_, ax = plt.subplots(figsize=(8.6, 3.0))
    cols = {"OPTIMO": C_VE, "FACTIBLE": C_AMBAR, "INVIABLE": C_ROJO}
    for i, (n, t, e) in enumerate(casos):
        ax.bar(i, t, color=cols.get(e, "#8A90A6"), width=0.65)
        ax.text(i, t + 0.1, fm(t, 1), ha="center", fontsize=7)
    ax.set_xticks(range(len(casos)))
    ax.set_xticklabels([c[0] for c in casos], rotation=35, ha="right", fontsize=7)
    ax.set_ylabel("Tiempo de pared por plan (s)")
    ax.legend(handles=[Patch(fc=C_VE, label="OPTIMO"), Patch(fc=C_AMBAR, label="FACTIBLE"),
                       Patch(fc=C_ROJO, label="INVIABLE")], frameon=False, fontsize=7.5)
    ax.set_title("Tiempo de resolución por plan (resolver + evaluar)", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_reconf(rec_a, rec_d, esc, ev, ruta):
    """Gantt antes (plan de las 06:00) y después (plan recalculado desde `ahora`) con eje común."""
    t_ref = pd.Timestamp(rec_a.inicio)
    nx = 24 + int((pd.Timestamp(rec_d.inicio) - t_ref) / pd.Timedelta(hours=1))
    fig_, axs = plt.subplots(2, 1, figsize=(8.6, 6.4), sharex=True)
    _gantt_ax(axs[0], rec_a.top[0], rec_a.horizonte, esc, t_ref, nx, titulo="ANTES · Top 1 calculado a las 06:00")
    p2 = rec_d.mejor
    _gantt_ax(axs[1], p2, rec_d.horizonte, esc, t_ref, nx, etiquetas=False,
              titulo=f"DESPUÉS · {p2.nombre} recalculado a las {pd.Timestamp(rec_d.inicio):%H:%M}")
    ahora_x = int((pd.Timestamp(rec_d.inicio) - t_ref) / pd.Timedelta(hours=1))
    for ax in axs:
        ax.axvline(ahora_x, color=C_ROJO, lw=1.4)
    axs[0].text(ahora_x + 0.15, -0.55, "ahora", color=C_ROJO, fontsize=7.5)
    axs[1].set_xlabel("Hora (desde las 06:00 del 02/10 hasta las 10:00 del 03/10)")
    axs[1].legend(handles=_LEYENDA_G, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=5, frameon=False,
                  fontsize=7)
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def g_finde(ruta):
    """Horizonte de 24 h desde el viernes 22:00: turnos, fecha del turno, laborable y camiones."""
    esc = datos.crear_escenario_ejemplo()
    hz = horizonte.construir_horizonte(esc, "2026-10-02 22:00")
    s = hz.slots
    col_t = {"M": "#CFE8DA", "T": "#CFDDF2", "N": "#D9D4EA"}
    fig_, ax = plt.subplots(figsize=(8.6, 3.1))
    n = len(s)
    for i in range(n):
        ax.add_patch(Rectangle((i, 2), 1, 1, fc=col_t[s["turno"].iloc[i]], ec="white"))
        ax.text(i + 0.5, 2.5, f"{s['hora'].iloc[i]:02d}", ha="center", va="center", fontsize=7)
        ax.add_patch(Rectangle((i, 1), 1, 1, fc="#DDE3F7" if s["laborable"].iloc[i] else "#F5C6C0", ec="white"))
        ax.text(i + 0.5, 1.5, "L" if s["laborable"].iloc[i] else "no", ha="center", va="center", fontsize=6.5)
        ax.text(i + 0.5, 3.2, f"{s['inicio'].iloc[i]:%d}", ha="center", va="bottom", fontsize=6.5, color="#5A6070")
        ax.text(i + 0.5, 0.45, f"{s['fecha_turno'].iloc[i]:%d}", ha="center", va="center", fontsize=6.5)
        nc = int(s["camiones"].iloc[i])
        if nc:
            ax.plot(i + 0.5, 3.75, marker="s", color=NAVY_HEX, ms=6)
    ax.text(-0.3, 2.5, "hora", ha="right", va="center", fontsize=7, color=NAVY_HEX)
    ax.text(-0.3, 3.2, "día natural", ha="right", va="bottom", fontsize=7, color=NAVY_HEX)
    ax.text(-0.3, 1.5, "laborable", ha="right", va="center", fontsize=7, color=NAVY_HEX)
    ax.text(-0.3, 0.45, "fecha_turno", ha="right", va="center", fontsize=7, color=NAVY_HEX)
    ax.text(-0.3, 3.75, "camión", ha="right", va="center", fontsize=7, color=NAVY_HEX)
    ax.set_xlim(-4.2, n)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    ax.legend(handles=[Patch(fc=col_t["M"], label="Turno M"), Patch(fc=col_t["T"], label="Turno T"),
                       Patch(fc=col_t["N"], label="Turno N"), Patch(fc="#F5C6C0", label="No laborable")],
              frameon=False, fontsize=7, ncol=4, loc="lower center", bbox_to_anchor=(0.45, -0.12))
    ax.set_title("Horizonte de 24 h iniciado el viernes 02/10 a las 22:00 (slots 0–23)", loc="left")
    fig_.tight_layout()
    fig_.savefig(ruta, dpi=165)
    plt.close(fig_)


def _caja(ax, x, y, w, h, titulo, sub, fc, tc="white", fs=7.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.0", fc=fc, ec="none"))
    ax.text(x + w / 2, y + h * 0.64, titulo, ha="center", va="center", color=tc, fontsize=fs, fontweight="bold")
    ax.text(x + w / 2, y + h * 0.26, sub, ha="center", va="center", color=tc, fontsize=6.3)


def _fl(ax, p1, p2, color="#5A6070", estilo="-|>", rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=estilo, mutation_scale=9, color=color, lw=1.1, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}"))


def g_pipeline(ruta):
    fig_, ax = plt.subplots(figsize=(8.8, 5.6))
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 68)
    ax.axis("off")
    ax.text(50, 66.2, "Flujo de datos del motor (cada flecha corresponde a una llamada en el código)", ha="center",
            fontsize=9, color=NAVY_HEX, fontweight="bold")
    # fila 1: datos
    _caja(ax, 1, 46, 17, 11, "Excel", "11 hojas · data/*.xlsx", "#8A90A6")
    _caja(ax, 24, 46, 21, 11, "Escenario", "datos.cargar_entrada", NAVY_HEX)
    _caja(ax, 51, 46, 23, 11, "Horizonte", "horizonte.construir_horizonte", NAVY_HEX)
    _caja(ax, 80, 46, 23, 11, "Plan de referencia", "baseline.plan_referencia", "#3E64B8")
    for a, b in [((18.4, 51.5), (23.6, 51.5)), ((45.4, 51.5), (50.6, 51.5)), ((74.4, 51.5), (79.6, 51.5))]:
        _fl(ax, a, b)
    # fila 2: modelo
    _caja(ax, 51, 27, 23, 11, "MILP Top-K", "modelo.resolver · PuLP + HiGHS", "#3E64B8")
    _caja(ax, 80, 27, 23, 11, "Cortes no-good", "motor.recomendar: y_c, corte_j", "#3E64B8")
    _caja(ax, 1, 27, 28, 11, "Evaluar y validar", "modelo.evaluar · validador.validar", NAVY_HEX)
    _fl(ax, (62.5, 45.6), (62.5, 38.4))
    _fl(ax, (91.5, 45.6), (91.5, 38.4), ls="--")
    ax.text(93, 42, "arranque en caliente", fontsize=6, color="#5A6070", ha="left")
    _fl(ax, (50.6, 32.5), (29.4, 32.5))
    ax.text(40, 34, "plan", fontsize=6, ha="center", color="#5A6070")
    _fl(ax, (74.4, 29.5), (79.6, 29.5))
    ax.text(77, 27.6, "config", fontsize=6, ha="center", color="#5A6070")
    _fl(ax, (79.6, 35.5), (74.4, 35.5))
    ax.text(77, 36.3, "corte", fontsize=6, ha="center", color="#5A6070")
    # fila 3
    _caja(ax, 1, 8, 22, 11, "Explicación y alertas", "motor._explicar · _alertas", NAVY_HEX)
    _caja(ax, 29, 8, 21, 11, "Recomendacion", "top · contingencia · baseline", "#8A90A6")
    _caja(ax, 57, 8, 21, 11, "Dashboard · PDF · CLI", "dashboard · informes · cli", C_VE)
    _caja(ax, 83, 8, 20, 11, "Rolling horizon", "reconfigurar · simular_semana", "#B5651D")
    _fl(ax, (12, 26.6), (12, 19.4))
    _fl(ax, (23.4, 13.5), (28.6, 13.5))
    _fl(ax, (50.4, 13.5), (56.6, 13.5))
    _fl(ax, (78.4, 13.5), (82.6, 13.5), ls="--", color="#B5651D")
    # bucle de horizonte rodante (ruta por la derecha y por arriba hasta Escenario)
    ax.plot([103.4, 103.9, 103.9], [13.5, 13.5, 62.2], color="#B5651D", lw=1.1, ls="--")
    ax.plot([103.9, 34.5], [62.2, 62.2], color="#B5651D", lw=1.1, ls="--")
    _fl(ax, (34.5, 62.2), (34.5, 57.6), color="#B5651D", ls="--")
    ax.text(70, 63.0, "Evento (baja, recursos reales, stock…) → nuevo Escenario → recalcular 24 h", fontsize=6.8,
            color="#B5651D", ha="center")
    ax.text(52, 2.6, "Línea discontinua naranja: bucle de horizonte rodante. Las interfaces sólo llaman a "
            "motor.recomendar y rolling.*; la lógica reside en el paquete kwd.", ha="center", fontsize=6.6,
            color="#5A6070", style="italic")
    fig_.savefig(ruta, dpi=165, bbox_inches="tight")
    plt.close(fig_)


def g_topk(ruta):
    fig_, ax = plt.subplots(figsize=(8.8, 4.9))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 61)
    ax.axis("off")
    _caja(ax, 1, 40, 18, 11, "baseline", "plan_referencia → inicial", "#8A90A6")
    _caja(ax, 25, 40, 22, 11, "resolver(cortes)", "j = 0, 1, …, K−1", "#3E64B8")
    _fl(ax, (19.4, 45.5), (24.6, 45.5))
    # rombo viable
    ax.add_patch(Polygon([(62, 45.5), (70, 52), (78, 45.5), (70, 39)], closed=True, fc="#F4F6FC", ec=NAVY_HEX))
    ax.text(70, 45.5, "¿plan\nviable?", ha="center", va="center", fontsize=7.2, color=NAVY_HEX)
    _fl(ax, (47.4, 45.5), (61.6, 45.5))
    ax.text(54.5, 47.2, "Plan", fontsize=6.5, ha="center", color="#5A6070")
    _caja(ax, 80, 40, 19, 11, "top.append(plan)", "cortes += config", NAVY_HEX)
    _fl(ax, (78.2, 45.5), (79.6, 45.5))
    ax.text(79, 47.5, "sí", fontsize=7, ha="center", color=C_VE)
    ax.plot([89.5, 89.5, 36], [51.4, 56, 56], color="#3E64B8", lw=1.1)
    _fl(ax, (36, 56), (36, 51.6), color="#3E64B8")
    ax.text(62, 57.0, "siguiente j: se añade el corte Σ(1−y_c) + Σ y_c ≥ 1 y se vuelve a resolver", fontsize=6.8,
            color="#3E64B8", ha="center")
    _caja(ax, 56, 14, 25, 11, "Sin viables → contingencia", "(sólo si top está vacío); break", C_ROJO)
    _fl(ax, (70, 38.8), (68.5, 25.4), color=C_ROJO)
    ax.text(71, 32, "no", fontsize=7, color=C_ROJO)
    _caja(ax, 25, 14, 25, 11, "Ordenar por puntuación", "top.sort(key=puntuacion, desc)", NAVY_HEX)
    _fl(ax, (36.5, 39.5), (36.5, 25.4), color="#5A6070", ls="--")
    ax.text(38.3, 32, "j = K o\nModeloInfactible", fontsize=6.3, color="#5A6070")
    _caja(ax, 25, 0, 25, 10, "Renombrar Top 1..K", "explicación y alertas del Top 1", "#8A90A6")
    _fl(ax, (37.5, 13.6), (37.5, 10.4))
    ax.text(1, 59.5, "Bucle Top-K de motor.recomendar", fontsize=9, color=NAVY_HEX, fontweight="bold")
    fig_.savefig(ruta, dpi=165, bbox_inches="tight")
    plt.close(fig_)


# ------------------------------------------------------------------------------------- derivados
M2_VE = 0.0
M2_COMB = 0.0
CONF = "Confirmado por el equipo"
PEND = "Pendiente de validar con KWD"
TEC = "Supuesto técnico"


def derivados(X) -> dict:
    """Magnitudes del escenario de demo que se citan en el texto (todas calculadas con el código)."""
    global M2_VE, M2_COMB
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
    D["m2ve"] = float(sum(t.loc[c, "ppc"] / t.loc[c, "piezas_m2"] for c in D["ve"]))
    D["m2comb"] = float(sum(t.loc[c, "ppc"] / t.loc[c, "piezas_m2"] for c in D["comb"]))
    M2_VE, M2_COMB = D["m2ve"], D["m2comb"]
    D["suma_ss"] = datos.validar_ss_almacen(esc)
    D["m2_stock0"] = float(sum(D["i0"][c] / t.loc[c, "piezas_m2"] for c in prods))
    D["dem_dia"] = datos.demanda_dia(esc, "2026-10-02")
    D["dem_sem_dia"] = datos.demanda_dia(esc, "2026-10-01")
    return D


# ------------------------------------------------------------------------------------- 0. portada
def seccion_portada(X, D):
    E = []
    portada = Table([[[Spacer(1, 3.6 * cm), P("Informe técnico", "titulo"), Spacer(1, 0.3 * cm),
                       P("Motor de decisión de producción — KWD España", "sub"), Spacer(1, 0.9 * cm),
                       P("Supuestos, trabajo realizado y metodología interna del algoritmo", "sub"),
                       Spacer(1, 3.2 * cm), P("Navarra Talent Challenge 2026", "sub"),
                       P(f"Versión {VERSION} · {FECHA_DOC}", "sub"), Spacer(1, 0.6 * cm),
                       P("Equipo NTC26 con asistencia de IA", "sub"), Spacer(1, 2.6 * cm)]]], colWidths=[17 * cm])
    portada.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * cm),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * cm), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    E += [portada, Spacer(1, 0.6 * cm),
          caja("<b>Naturaleza del documento.</b> Informe técnico de referencia del motor de decisión desarrollado para "
               "el reto KWD. Recoge <b>(A)</b> todo lo que se ha asumido, <b>(B)</b> todo lo que se ha hecho y "
               "<b>(C)</b> la metodología interna completa del algoritmo. La fuente de verdad es el <b>código</b>; "
               "donde la especificación difiere del código se indica de forma explícita. Todas las cifras de este "
               "PDF se calculan al generarlo, ejecutando el motor real."),
          PageBreak()]
    # ficha
    vers = {}
    for n in ("pulp", "highspy", "pandas", "numpy", "reportlab", "matplotlib", "streamlit", "plotly", "openpyxl"):
        try:
            vers[n] = imd.version(n)
        except Exception:
            vers[n] = "—"
    E += [Paragraph("Ficha del documento", S["h1x"] if "h1x" in S else S["h1"])]
    ficha = [["Campo", "Contenido"],
             ["Título", "Informe técnico del motor de decisión de producción KWD (supuestos, trabajo realizado y metodología)"],
             ["Versión", f"{VERSION}"],
             ["Fecha", FECHA_DOC],
             ["Autores", "Equipo NTC26 con asistencia de IA"],
             ["Proyecto", "Reto KWD España S.L.U. · Navarra Talent Challenge 2026"],
             ["Objeto técnico", f"Paquete {K('src/kwd')} (motor), {K('app/')} (dashboard Streamlit), {K('tests/')} (pytest), {K('docs/')}"],
             ["Base documental", f"{K('docs/ESPECIFICACION.md')} v1 con las desviaciones de su §11 (v1.1), {K('docs/PLAN_EQUIPO.md')} y el código fuente"],
             ["Regla de precedencia", "Código &gt; especificación. Cada diferencia detectada figura en la tabla 5.0 y en el apartado 8"],
             ["Generación", f"{K('docs/generar_informe_tecnico.py')} → {K('salida/KWD_Informe_Tecnico.pdf')}; ejecuta el motor real (≈{fm(X['t_motor'], 0)} s)"],
             ["Entorno", f"Python {sys.version.split()[0]} · PuLP {vers['pulp']} · HiGHS (highspy {vers['highspy']}) · pandas {vers['pandas']} · "
                         f"numpy {vers['numpy']} · ReportLab {vers['reportlab']} · matplotlib {vers['matplotlib']} · Streamlit {vers['streamlit']}"]]
    E += [tabla(ficha, [3.6 * cm, 13.4 * cm], ["L", "L"]), Spacer(1, 8)]
    E += [Paragraph("Control de versiones", S["h2x"])]
    E += [tabla([["Versión", "Fecha", "Cambios"],
                 [VERSION, "02/10/2026", "Primera edición completa: registro de supuestos, trabajo realizado y metodología interna con cifras recalculadas del motor."]],
                [2 * cm, 2.6 * cm, 12.4 * cm], ["L", "L", "L"])]
    E += [Paragraph("Convenciones del documento", S["h2x"])]
    E += bullets([
        f"<b>Identificadores.</b> {K('D1…D16')}: decisiones del equipo del 2 de octubre de 2026; {K('F1…F19')}: flags de {K('config.FLAGS')}; "
        f"{K('I1…In')}: supuestos implícitos hallados en el código que no están marcados como flag.",
        f"<b>Referencias al código.</b> Toda mecánica se cita como {K('modulo.funcion')} (p. ej. {K('modelo.resolver')}).",
        "<b>Notación.</b> C = células (16), P = C ∖ {10} = células productivas (15), H = slots horarios (24), W ⊆ H = slots laborables; "
        "c = célula, h = slot, k = recurso. Los componentes de la puntuación R, S, Q, B, E están normalizados en [0, 1] y menor es mejor.",
        "<b>Formato numérico.</b> Coma decimal y punto de millar (convención española). Las horas se expresan en hora local sin zona (naive).",
        "<b>Estados de un supuesto.</b> «Confirmado por el equipo» (decisión del 2/10/2026), «Pendiente de validar con KWD» "
        "y «Supuesto técnico» (decisión de implementación sin respaldo en las fuentes).",
    ])
    E += [PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    E += [Paragraph("Índice", S["h1x"] if "h1x" in S else S["h1"]), toc]
    return E


# ------------------------------------------------------------------------------------- 1. resumen
def seccion_resumen(X, D):
    r06, rb = X["rec06"], X["rec06"].baseline
    t1 = r06.top[0]
    k1, kb = t1.kpis, rb.kpis
    sem = X["sem"]
    n_fact = int((sem["estado"] != "OPTIMO").sum())
    E = H1("1. Objeto, alcance y resumen ejecutivo")
    E += [P("<b>Objeto.</b> Documentar con precisión y de forma reproducible el motor de decisión que recomienda, para cada "
            "turno de la planta de soldadura de KWD España, <b>qué células activar, por qué y con qué impacto</b>, "
            "cumpliendo reglas obligatorias y optimizando los cinco criterios ponderados del reto (recursos 50 %, espacio 20 %, "
            "calidad y mantenimiento 15 %, stock de seguridad 10 %, energía 5 %).")]
    E += [P("<b>Alcance.</b> (A) registro completo de supuestos (§3: 16 decisiones, 19 flags y los supuestos implícitos del código); "
            "(B) trabajo realizado, ficheros, pruebas y correcciones (§4); (C) metodología interna: carga de datos, horizonte, "
            "formulación MILP, resolución, Top-K, plan de referencia, validador, explicación, KPIs, horizonte rodante y rendimiento (§5); "
            "ejemplo numérico trabajado, evidencias de idoneidad y limitaciones (§6–§8).")]
    ncel = len(D["prods"])
    kp = [["Resultado (demo, 02/10/2026)", "Valor"],
          ["Top 1 a las 06:00", f"{t1.estado}, puntuación {fm(t1.puntuacion, 2)} (referencia manual {fm(rb.puntuacion, 2)}), idoneidad {t1.idoneidad_txt().replace('.', ',')}"],
          ["Impacto frente a la referencia (24 h)", f"horas-operario {sg(k1['operarios_horas'] - kb['operarios_horas'], 1)} h; m² medios {sg(k1['m2_medio'] - kb['m2_medio'], 1)}; kWh de red {sg(k1['kwh_total'] - kb['kwh_total'], 0)}"],
          ["Top 1 a las 14:00", f"{X['rec14'].top[0].estado}, puntuación {fm(X['rec14'].top[0].puntuacion, 2)}, idoneidad {X['rec14'].top[0].idoneidad_txt().replace('.', ',')}"],
          ["Escenario con la célula 14 de baja", f"{X['recc'].contingencia.estado} → plan de contingencia con {len(X['recc'].contingencia.incumplimientos)} incumplimientos (nunca se recomienda)"],
          ["Tamaño del MILP (24 h)", f"{fm(X['st_rec06'][0]['vars'], 0)} variables, {fm(X['st_rec06'][0]['cons'], 0)} restricciones, {X['st_rec06'][0]['bins']} binarias"],
          ["Simulación semanal (15 turnos)", f"{len(sem) - n_fact} OPTIMO / {n_fact} FACTIBLE; puntuación media {fm(sem['puntuacion_plan_24h'].mean(), 1)} vs {fm(sem['puntuacion_baseline'].mean(), 1)} manual"],
          ["Pruebas automáticas", "21 pruebas pytest en verde; validador sin incumplimientos en todos los Top"]]
    E += [tabla(kp, [5.2 * cm, 11.8 * cm], ["L", "L"], fs=7.6)]
    E += [H3("Conclusiones principales")]
    E += bullets([
        "El motor es un <b>MILP determinista</b> (PuLP + HiGHS) con 384 binarias de activación y variables continuas de uso; las reglas "
        "obligatorias son duras y sus violaciones sólo existen como holguras penalizadas (1000) que marcan el plan como INVIABLE.",
        "La <b>idoneidad</b> es 100·(1 − gap de HiGHS) con gap aceptado del <b>0,1 %</b> (la especificación pedía 0; ver §5.5). Un "
        "validador independiente recalcula todas las reglas y la puntuación a partir de la activación del plan.",
        "El <b>Top-K</b> se obtiene con cortes no-good sobre la configuración del turno actual y se reordena por puntuación global.",
        "Las <b>limitaciones</b> relevantes (gap 0,1 %, iteraciones de la semana con idoneidad baja por el límite de 10 s, noches con sólo "
        "la célula 10, cobertura del lunes tras el fin de semana, modelo de piezas simplificado) se detallan en §8.",
    ])
    return E


# ------------------------------------------------------------------------------------- 2. fuentes
DECISIONES = [
    ("D1", "Objetivos: se usan sólo los pesos KWD 50/20/15/10/5; se descarta «minimizar nº de máquinas» como criterio propio."),
    ("D2", "Alinear máquinas de alto consumo con el pico solar: criterio dentro del 5 % de energía, no restricción."),
    ("D3", "Stock de seguridad: restricción obligatoria (descarta la solución)."),
    ("D4", "Cobertura de los ratios de Calidad y Mantenimiento: obligatoria."),
    ("D5", "Célula 10: prima la tabla (carretilleros 0,5); las fracciones significan una persona repartida entre varios puestos."),
    ("D6", "Cada célula fabrica una pieza distinta que se ensambla en el conjunto (flag F1); un ciclo = una pieza (flag F2)."),
    ("D7", "OEE = 100 % (F8)."),
    ("D8", "El 5 % de variabilidad es personal ausente (F7)."),
    ("D9", "Las células pueden activarse sólo unas horas del turno."),
    ("D10", "3 turnos × 8 h sin pausas, lunes a viernes (F4)."),
    ("D11", "La fotovoltaica cubre ≈15 % de la potencia en pico (no crítico) (F5)."),
    ("D12", "Mantenimiento: es un input (día / turno / célula), no se calcula por horas (F3)."),
    ("D13", "Demanda: chasis semanales VE/COMB más corrección diaria."),
    ("D14", "Stock de seguridad ≈150 m² es una estimación; el espacio se calcula como piezas / (piezas por m²) (F10)."),
    ("D15", "Camiones: 15/día, uno cada 1,5 h, máximo 15 m² cada uno; la carga real de cada camión es un input (F6, F15)."),
    ("D16", "Stack: Python + HiGHS + Streamlit + ReportLab, interfaz en español, desarrollo en VS Code."),
]


def seccion_fuentes(X, D):
    esc = X["esc"]
    E = H1("2. Fuentes de información")
    E += [P("Este apartado recoge los documentos analizados, los datos que se han extraído de cada uno, las inconsistencias "
            "encontradas entre fuentes y respecto al planteamiento inicial del equipo, y cómo se resolvió cada una. Las decisiones "
            "del equipo se tomaron el 2 de octubre de 2026 y se identifican como D1–D16.")]
    E += [H2("2.1 Documentos analizados")]
    docs = [["Documento", "Naturaleza", "Datos extraídos", "Uso en el motor"],
            [K("KWD_AUTOMOTIVE_DOSSIER_RETO_2026.pptx"), "Dossier del reto (28 diapositivas)",
             "Contexto de empresa (diap. 2–16). Reto (diap. 21–27): definición, premisas, requisitos de la aplicación, criterios de prioridad "
             "(50/20/15/10/5), resultado esperado (Top 1, indicadores, pronóstico de 2 turnos, Top 2–3, reconfiguración). Datos base "
             "(diap. 22–23): célula 10 siempre activa con operario y carretillero; 11 y 12 a la vez; variabilidad ≈5 % por turno; SS 400 VE y 200 "
             "combustión ≈150 m². Premisas (SmartArt): 15 camiones/día, cada 1,5 h se liberan 15 m²; almacén nunca vacío; almacén externo.",
             "Reglas duras, pesos de la puntuación, forma de las salidas, camiones, SS, alcance del dashboard."],
            [K("KWD_AUTOMOTIVE_PRESENTACIÓN_NTC26.pptx"), "Presentación del reto (26 diapositivas, con notas del orador)",
             "Mismo reto en 8 diapositivas (18–25): tabla de 16 células, reglas obligatorias y recursos estándar, planta dinámica (horizonte "
             "de 24 h, camiones, variabilidad 5 %), pesos («Top 1 = alternativa viable con mejor puntuación global»), entregable. Notas: "
             "historia (MQB-A0 2017, MEB21, MES DOEET), secuenciación, capacidad técnica &gt; 3.000 juegos MQB y &gt; 1.500 MEB21, centro de "
             "producción 10.700 m² (antes 9.500), «workforce close to 100» frente a 107 empleados en la diapositiva.",
             "Confirma tabla de células y reglas; fija la definición de Top 1 (orden por puntuación); contexto cualitativo."],
            [K("ttablas.xlsx"), "Tablas de datos base (3 hojas)",
             "«Células»: 16 filas × (tipo, ciclo s, piezas/m², operarios, picking, carretilleros, ratios Mto, ratios Calidad, kW). «Turnos»: recursos "
             "estándar M/T/N = 16/16/14 operarios, 2/2/2 picking, 4/4/3 carretilleros, 7/7/4 Mto, 3/3/2 Calidad. «Superficie almacén»: 1.300 m² = "
             "400 materia prima + 100 preparación de cargas + 800 producto terminado (incluye SS).",
             f"Hojas {K('Celulas')}, {K('Turnos')} y {K('Almacen')} de la plantilla ({K('datos._CELULAS_BASE')})."],
            [K("layout_nave.pdf"), "Plano A3 «KWD-ES-PE-013-02 Layout general», NAVE I, Arazuri-Orcoyen, 06/05/2019",
             "Imagen escaneada sin capa de texto ni escala: líneas 1–16 (una por célula), líneas 11 y 12 contiguas, metrología y almacén de "
             "mantenimiento, zona de carga/descarga (canopy). No contiene m², distancias ni tiempos.",
             "Sólo cualitativo: confirma 16 líneas y la contigüidad 11–12 que justifica su regla de pareja. No alimenta el modelo."],
            [K("2026_Agenda_NTC.pdf"), "Agenda del evento (2–3 oct 2026)",
             "Viernes: 08:30 presentación de empresas y sorteo de retos, 11:00 inicio del trabajo, 16:00 espacio RRHH, 21:00 cena. Sábado: 08:30 "
             "presentación de los retos, 09:00 selección de finalistas, 10:00 presentaciones de finalistas, 11:30 premios.",
             f"Planificación del equipo ({K('docs/PLAN_EQUIPO.md')}); sin impacto en el modelo."]]
    E += [tabla(docs, [3.1 * cm, 2.6 * cm, 7.3 * cm, 4.0 * cm], ["L"] * 4, fs=7.1, valign="TOP")]
    E += [Spacer(1, 4), P("Nota de la propia presentación (diapositiva 20): <i>los datos de las células sirven para comparar alternativas, no "
                          "para planificar cada célula de forma aislada</i>. El motor sigue este criterio: evalúa configuraciones completas "
                          "del turno con una única función de puntuación.", "small")]
    E += [H2("2.2 Datos base extraídos (ttablas.xlsx) y su reflejo en el escenario de demo")]
    trn = esc.turnos.set_index("recurso")
    rt = [["Recurso estándar", "Mañana 06–14", "Tarde 14–22", "Noche 22–06", "Disponible con absentismo 5 % (M / T / N)"]]
    for r in RECURSOS:
        bs = [float(trn.loc[r, x]) for x in "MTN"]
        ef = [b - horizonte.redondeo_comercial(b * float(esc.parametros["absentismo"])) for b in bs]
        rt.append([r.capitalize(), fm(bs[0], 0), fm(bs[1], 0), fm(bs[2], 0), " / ".join(fm(e, 0) for e in ef)])
    E += [tabla(rt, [3.4 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 5.2 * cm], ["L", "C", "C", "C", "C"])]
    E += [P("Con el redondeo comercial de F17, el 5 % de absentismo sólo modifica realmente los <b>operarios</b> (16 → 15 y 14 → 13), "
            "porque para el resto de recursos 0,05 × estándar &lt; 0,5. La tabla completa de las 16 células está en el Anexo A.", "small")]
    E += [H2("2.3 Inconsistencias y ambigüedades detectadas, y su resolución")]
    inc = [["#", "Hallazgo (fuentes implicadas)", "Resolución", "Ref."],
           ["1", "<b>Objetivos.</b> El planteamiento inicial del equipo incluía «minimizar nº de máquinas»; el dossier (criterios de prioridad) y la presentación "
                 "(diap. 23) sólo definen los cinco pesos 50/20/15/10/5.", "Se usan sólo los pesos KWD. El nº de células activas se reduce únicamente de forma indirecta "
                 "(menor uso de recursos y de energía).", "D1, I1"],
           ["2", "<b>Pico solar.</b> El equipo quería alinear máquinas de alto consumo con el pico solar; KWD sólo pide menor consumo «en horas nocturnas» (diap. 26 del dossier) "
                 "y no aporta curva fotovoltaica.", "Criterio dentro del 5 % de energía mediante un factor horario (0,85 en 11–17; 1,20 de noche); no es restricción.", "D2, D11, F5"],
           ["3", "<b>Stock de seguridad.</b> Se describe como «mínimo» que «puede ser más alto» (diap. 23) y a la vez como criterio con peso 10 % («no quedar por debajo del mínimo»).",
            "Doble tratamiento: restricción obligatoria (regla 7) y, dentro del 10 %, penalización del déficit frente a un colchón SS + 10 %.", "D3, F9, I18"],
           ["4", "<b>Calidad y Mantenimiento.</b> La tabla habla de «ratios» por célula; el criterio KWD (15 %) habla de «disponibilidad de recursos» y «cubrir los ratios requeridos».",
            "Los ratios se tratan como personas equivalentes consumidas por célula activa: su cobertura es obligatoria (regla 5) y el 15 % puntúa el margen libre (menor uso = mejor).", "D4, I1"],
           ["5", "<b>Célula 10.</b> El dossier dice que necesita «operario y carretillero»; la tabla da carretilleros = 0,5.", "Prima la tabla (0,5). Las fracciones son una persona repartida entre varios puestos.", "D5, I2"],
           ["6", "<b>Unidad del stock de seguridad.</b> «400 coches eléctricos y 200 de combustión, ocupa ≈150 m²» no dice si son piezas por célula o conjuntos.",
            f"Con 400 piezas por célula VE y 200 por célula de combustión, ΣSS/densidad = {fm(D['suma_ss'], 2)} m² ≈ 150 m²: sólo es coherente si cada célula fabrica una pieza distinta y un ciclo es una pieza.", "D6, D14, F1, F2, F10"],
           ["7", "<b>«Variabilidad en torno al 5 %».</b> Podría ser plus/minus sobre los recursos o ausencias.", "Se interpreta como personal ausente (sólo reduce); se aplica al estándar con redondeo comercial.", "D8, F7, F17"],
           ["8", "<b>Camiones.</b> «Aprox. 15 camiones/día, cada 1,5 h se liberan unos 15 m²»: 15 × 1,5 h = 22,5 h; no se da hora de inicio ni si 15 m² es media o tope.",
            "Primer camión 06:00, intervalo 1,5 h (último 03:00 del día siguiente), 15 m² como máximo por camión; la carga real puede introducirse como input.", "D15, F6, F15, I10"],
           ["9", "<b>Almacén.</b> Hay 1.300 m² (400 + 100 + 800), pero el dossier cita además un almacén externo. El SS «ocupa 150 m²» dentro de los 800 m².",
            "Sólo se modela producto terminado (800 m²); espacio = Σ piezas / (piezas por m²). El almacén externo no se modela.", "D14, I9"],
           ["10", "<b>Célula 10 tipada «Combustión»</b> en la tabla con ciclo 60 s y 0 piezas/m².", "No produce piezas ni ocupa almacén: se excluye del conjunto de células productivas P.", "F11, I8"],
           ["11", "<b>Nomenclatura:</b> «Eléctrico»/«Combustión» (tabla) frente a «VE»/«Comb.» (presentación).", "Se normaliza a VE/COMB al cargar ({0}).".format(K("datos.cargar_entrada")), "—"],
           ["12", "<b>Unidad de consumo:</b> «KW/h» por célula (ambigua: kW de potencia o kWh por hora).", "Se interpreta como potencia kW: energía = kW × fracción de uso × 1 h × factor horario.", "F16, I15"],
           ["13", "<b>Demanda.</b> Ninguna fuente da cifras de demanda; el cliente envía «programas de varios meses» y la capacidad técnica citada (&gt; 3.000 juegos MQB diarios) es muy superior a la demo.",
            "Demanda semanal de chasis VE/COMB más corrección diaria como inputs; la demo (2.400 VE y 1.600 COMB por semana, 520/300 el 02/10) es ilustrativa.", "D13, F12, I13"],
           ["14", "<b>Mantenimiento.</b> Las fuentes sólo piden poder «excluir células» por mantenimiento preventivo o avería; no dan planificación ni duraciones.",
            "Input (fecha, turno, célula, técnicos); no se calcula por horas.", "D12, F3"],
           ["15", "<b>Calendario.</b> No se indican turnos, pausas ni festivos.", "3 turnos de 8 h sin pausas (M 06–14, T 14–22, N 22–06), lunes–viernes, sin calendario de festivos.", "D10, F4, I11"],
           ["16", "<b>Horizonte.</b> «Pronóstico para los dos turnos siguientes (Overview 24 h)»: dos turnos más el actual = 24 h sólo si el cálculo empieza al inicio de turno.",
            "Horizonte fijo de 24 h en slots de 1 h desde la hora de cálculo; con inicio a mitad de turno, el «turno actual» es el resto del turno.", "I25"],
           ["17", "<b>Alcance temporal de la entrega.</b> La agenda indica «Presentación de los retos» a las 08:30 del sábado, antes de la selección de finalistas (09:00) y de las presentaciones finalistas (10:00).",
            "Se mantiene la planificación del equipo (pitch ≤ 10 min a las 08:30); la interpretación exacta del formato debe confirmarse con la organización.", "—"],
           ["18", "<b>Cifras de contexto contradictorias:</b> «107 empleados» (diapositiva) frente a «workforce close to 100» (notas); plano de 2019 anterior a MEB21.",
            "Sin efecto en el modelo (sólo contexto). El plano no se usa cuantitativamente.", "—"],
           ["19", "<b>Orden del Top.</b> El dossier define Top 1 como la alternativa viable con mejor puntuación global; el objetivo del MILP incluye penalizaciones auxiliares (arranques, cobertura final).",
            "Se reordena el Top por puntuación global tras resolver (tras la revisión del 2/10).", "I27"]]
    E += [tabla(inc, [0.6 * cm, 7.9 * cm, 7.0 * cm, 1.5 * cm], ["C", "L", "L", "L"], fs=7.0, valign="TOP")]
    E += [H2("2.4 Decisiones del equipo del 2 de octubre de 2026")]
    E += [tabla([["ID", "Decisión"]] + [[a, b] for a, b in DECISIONES], [1.2 * cm, 15.8 * cm], ["C", "L"], fs=7.6)]
    E += [P("Cada decisión se detalla, con su impacto y su punto de modificación, en el registro de supuestos del apartado 3.", "small")]
    return E


# ------------------------------------------------------------------------------------- 3. registro de supuestos
def filas_supuestos(X, D):
    t = D["tc"]
    nve, ncb = len(D["ve"]), len(D["comb"])
    W = []  # (id, descripción, valor, justificación, impacto, cómo cambiarlo, estado)
    # ---- decisiones del equipo
    W += [
        ("D1", dict(DECISIONES)["D1"].replace("Objetivos: ", "<b>Objetivos.</b> "),
         f"{K('peso_recursos')} 0,50; {K('peso_espacio')} 0,20; {K('peso_calidad_mto')} 0,15; {K('peso_stock')} 0,10; {K('peso_energia')} 0,05",
         "Dossier (criterios de prioridad) y presentación diap. 23. «Minimizar nº de máquinas» no es criterio KWD.",
         "Puntuación = 100·Σ peso·(1 − componente). El nº de células activas sólo baja de forma indirecta vía R y E.",
         f"Hoja {K('Parametros')} (claves {K('peso_*')}); {K('config.PESO_PARAM')}", CONF),
        ("D2", dict(DECISIONES)["D2"], f"{K('factor_solar')} 0,85 en 11–17 h; {K('peso_energia')} 0,05",
         "KWD sólo pide menos consumo nocturno; el equipo quiere premiar el pico solar sin imponerlo.",
         "Las células de alta potencia tienden a colocarse en 11–17 h porque cuesta menos en E; ninguna regla lo obliga.",
         f"{K('Parametros')}: {K('solar_ini')}, {K('solar_fin')}, {K('factor_solar')}, {K('peso_energia')}", CONF),
        ("D3", dict(DECISIONES)["D3"], f"SS: {K('ss_ve')} = 400, {K('ss_comb')} = 200 piezas por célula; holgura penalizada 1000",
         "Dossier: «el stock de seguridad mínimo es…». Se trata como regla dura (regla 7).",
         "Una violación convierte el plan en INVIABLE; el Top sólo contiene planes sin violaciones.",
         f"{K('Parametros')}: {K('ss_ve')}, {K('ss_comb')}; {K('modelo.resolver')} (r7)", CONF),
        ("D4", dict(DECISIONES)["D4"], "Regla 5 sobre los 5 recursos (incl. mto y calidad)",
         "Criterio KWD «cubrir los ratios requeridos». Los ratios son personas equivalentes por célula activa.",
         "Una combinación que exceda Mto o Calidad disponibles es inviable. Además el 15 % puntúa el margen libre.",
         f"Hoja {K('Celulas')} (columnas {K('mto')}, {K('calidad')}); {K('Turnos')}", CONF),
        ("D5", dict(DECISIONES)["D5"], f"{K('Celulas.carretilleros')}[10] = {fm(float(t.loc[10, 'carretilleros']), 1)}; operarios 1,0",
         "La tabla (ttablas.xlsx) prima sobre el texto del dossier. Fracción = capacidad compartida entre puestos.",
         "Las restricciones de recursos son continuas: Σ req·a ≤ disp admite fracciones.",
         f"Hoja {K('Celulas')}", CONF),
        ("D6", dict(DECISIONES)["D6"], f"{K('piezas_por_conjunto')} = 1 en las 16 células", f"Σ SS/densidad = {fm(D['suma_ss'], 2)} m² ≈ 150 m².",
         "Cada chasis consume una pieza de cada célula de su tipo; envíos de pieza = chasis × ppc.", f"Hoja {K('Celulas')}, columna {K('piezas_por_conjunto')}", CONF),
        ("D7", dict(DECISIONES)["D7"], "cap_h = 3600 / ciclo_s", "Simplificación acordada.", "La capacidad real puede ser menor: las horas necesarias para reponer stock se subestiman.",
         f"{K('datos.tabla_celulas')} (dividir por OEE)", CONF),
        ("D8", dict(DECISIONES)["D8"], f"{K('absentismo')} = 0,05", "Interpretación del «≈5 %» de variabilidad (diap. 23).",
         "Operarios: 16 → 15 (M, T) y 14 → 13 (N); el resto de recursos no cambia por el redondeo.", f"{K('Parametros.absentismo')}; hoja {K('RecursosReales')}", CONF),
        ("D9", dict(DECISIONES)["D9"], f"slot de 1 h; a ∈ {{0,1}}, u ∈ [0,1]", "«Células activables sólo unas horas».",
         "Cada célula puede activarse en cualquier subconjunto de horas del turno; permite producción parcial dentro de la hora.",
         f"{K('Parametros.horas_horizonte')} (sólo longitud); granularidad en {K('horizonte.construir_horizonte')}", CONF),
        ("D10", dict(DECISIONES)["D10"], "M 06–14, T 14–22, N 22–06; lun–vie", "Acuerdo del equipo; no figura en las fuentes.",
         "Fin de semana sin producción; la noche del viernes termina el sábado 06:00.", f"{K('config.TURNOS')}, {K('config.DIAS_LABORABLES')}", CONF),
        ("D11", dict(DECISIONES)["D11"], f"{K('factor_solar')} = 0,85 (11–17 h)", "FV cubre ≈15 % de la potencia: factor energético de red 1 − 0,15.",
         "Reduce el coste energético de producir entre 11 y 17 h; peso 5 % limita su efecto.", f"{K('Parametros')}", CONF),
        ("D12", dict(DECISIONES)["D12"], f"Hoja {K('Mantenimientos')}: fecha, turno (M/T/N/DIA), célula, técnicos", "No hay planificación de mantenimiento en las fuentes.",
         "Bloquea la célula en el turno y resta técnicos a la disponibilidad de Mto.", f"Hoja {K('Mantenimientos')} o evento {K('mantenimiento')}", CONF),
        ("D13", dict(DECISIONES)["D13"], f"Demo: 2.400 VE y 1.600 COMB (semana del 28/09); 02/10: 520 y 300", "Entrada por el usuario; la demo es ilustrativa.",
         "Define la carga de cada camión y las piezas que salen por slot.", f"Hojas {K('DemandaSemanal')} y {K('CorreccionDiaria')}; {K('datos.demanda_dia')}", CONF),
        ("D14", dict(DECISIONES)["D14"], f"espacio = Σ stock_c / piezas_m2_c; SS total = {fm(D['suma_ss'], 2)} m²", "150 m² es una estimación de KWD.",
         "El almacén de 800 m² limita el stock de piezas terminadas (regla 8); la densidad define cuánto ocupa cada pieza.", f"Hoja {K('Celulas')} (piezas_m2); {K('Almacen')}", CONF),
        ("D15", dict(DECISIONES)["D15"], f"{K('camiones_dia')} 15; {K('intervalo_camion_h')} 1,5; {K('m2_max_camion')} 15", "Dossier/presentación; hora de inicio 06:00 supuesta.",
         "Fija cuándo y cuántas piezas salen; el tope de 15 m² puede dejar chasis sin expedir (alerta).", f"{K('Parametros')}; hoja {K('Expediciones')}", CONF),
        ("D16", dict(DECISIONES)["D16"], "Python, PuLP + HiGHS, Streamlit, ReportLab, VS Code", "Decisión del equipo.", "Sin impacto numérico.", f"{K('requirements.txt')}", CONF),
    ]
    # ---- flags
    FI = {
        "F1": (f"{nve} células VE ({', '.join(map(str, D['ve']))}) y {ncb} de combustión productivas ({', '.join(map(str, D['comb']))})",
               f"Σ SS/dens = {fm(D['suma_ss'], 2)} m² ≈ 150 m².", "Cada camión retira una pieza de cada célula del tipo de sus chasis.", f"Hoja {K('Celulas')}.{K('piezas_por_conjunto')}", CONF),
        "F2": (f"{K('piezas_por_conjunto')} = 1", "Idem F1.", "Capacidad en piezas = capacidad en ciclos.", f"Hoja {K('Celulas')}", CONF),
        "F3": ("Demo: 02/10 turno T, célula 13, 2 técnicos", "Sin datos de planificación de mantenimiento.",
               "Célula 13 bloqueada en T (14–22 h) y Mto disp. 7 − 2 = 5 en T; el motor adelanta su producción al turno M.",
               f"Hoja {K('Mantenimientos')}", CONF),
        "F4": ("M 06–14, T 14–22, N 22–06; lun–vie", "Acuerdo del equipo.", "Define laborable por fecha_turno; no hay festivos.",
               f"{K('config.TURNOS')}, {K('config.DIAS_LABORABLES')} (código)", CONF),
        "F5": ("0,85 en 11–17; 1,20 de 22 a 06; 1,00 resto", "0,85 acordado por el equipo (FV ≈15 %). El 1,20 nocturno es una decisión de implementación sin cifra de KWD.",
               "Pondera la energía en E y en kwh_total; la noche penaliza producir.", f"{K('Parametros')}: solar_ini/fin, factor_solar, factor_noche (22–06 fijo en código)", "Confirmado (0,85) / Pendiente de validar con KWD (1,20)"),
        "F6": ("15 camiones/día, 06:00 + 1,5·i h, tope 15 m²", "Dossier y presentación; hora de inicio supuesta.",
               "Calendario de envíos y piezas por slot.", f"{K('Parametros')}; hoja {K('Expediciones')}", CONF),
        "F7": (f"{K('absentismo')} = 0,05", "Interpretación del 5 % de variabilidad.", "Sólo operarios cambian (16 → 15; 14 → 13).", f"{K('Parametros.absentismo')}; hoja {K('RecursosReales')}", CONF),
        "F8": ("OEE = 100 %", "Acuerdo del equipo.", "Producción real posiblemente menor que la planificada.", f"{K('datos.tabla_celulas')}", CONF),
        "F9": (f"{K('colchon_ss')} = 0,10", "No figura en las fuentes; sirve para dar contenido al 10 % de stock de seguridad.",
               "Componente B puntúa déficit frente a 1,1·SS; alerta de stock bajo colchón.", f"{K('Parametros.colchon_ss')}", PEND),
        "F10": ("SS 400 por célula VE y 200 por COMB", f"Dato KWD («400 VE y 200 combustión ≈150 m²»); verificación {fm(D['suma_ss'], 2)} m².",
                "Regla 7 y componente B.", f"{K('Parametros')}: ss_ve, ss_comb", CONF),
        "F11": ("Célula 10: a = 1 en horas laborables, sin producción", "Dossier: servicio logístico que se efectúa siempre.",
                "Consume 1 operario y 0,5 carretilleros en todas las horas laborables.", f"{K('config.CELULA_LOGISTICA')}", CONF),
        "F12": ("Semana ausente → última anterior (o primera); sáb/dom sin corrección = 0", "Evita errores con calendarios incompletos.",
                "Puede ocultar un dato faltante: se usa demanda de otra semana.", f"{K('datos.demanda_dia')}", TEC),
        "F13": ("Bloqueo si el slot solapa el intervalo", "Granularidad horaria.", "Una baja desde las 10:30 bloquea el slot 10–11.", f"{K('horizonte.construir_horizonte')}", TEC),
        "F14": ("Célula 10 no obligatoria si bloqueada o sin recursos", "Evita modelos infactibles por construcción.",
                "Se avisa en alertas; el plan puede carecer de servicio logístico en esas horas.", f"{K('horizonte.construir_horizonte')} (logistica_exigida)", TEC),
        "F15": ("Expedición real sustituye al camión previsto más cercano (±45 min)", "Necesario para integrar cargas reales.",
                "Una expedición lejana de todo camión previsto se añade como camión extra.", f"{K('horizonte._camiones_previstos')}", TEC),
        "F16": ("energia_kwh = kW·u·factor; kwh_bruto = kW·u", "Distingue energía de red y bruta.", "KPI de energía total con factor; % solar sobre energía bruta.",
                f"{K('modelo.evaluar')}", TEC),
        "F17": ("round half up (0,5 hacia arriba)", "Evita el redondeo bancario de Python.", "Con half-even, 0,5 → 0 en algunos casos.", f"{K('horizonte.redondeo_comercial')}", TEC),
        "F18": (f"{K('cobertura_final_h')} = 8; penalización 10", "Evita que el horizonte «vacíe» el stock al final.",
                "Condición blanda: no hace inviable el plan; genera aviso. Empuja a producir antes de 22:00 del viernes.", f"{K('Parametros.cobertura_final_h')}", TEC),
        "F19": ("Stock inicial = round(1,8 × SS): 720 VE / 360 COMB", "Con 1,3 × SS el escenario es inviable (0,3·SS no cubre un turno de envíos).",
                "Sólo afecta a la demo.", f"Hoja {K('StockActual')}; {K('datos.FACTOR_STOCK_DEMO')}", TEC),
    }
    for k in FLAGS:
        v = FI[k]
        W.append((k, FLAGS[k], v[0], v[1], v[2], v[3], v[4]))
    # ---- supuestos implícitos (no marcados como flag)
    I = [
        ("Orientación de R y Q: menor uso de recursos = mejor", "R = media(usado/disp) y Q = media(uso Mto, Cal) se minimizan",
         "«Ocupación adecuada» y «cumplir la demanda sin sobrepasar los recursos» (presentación diap. 23).",
         "Sesgo a producir justo a tiempo con pocas células; noches con sólo la célula 10 (ver §8).", f"{K('modelo.resolver')} (expr_R, expr_Q)", PEND),
        ("Recursos fraccionarios y continuos", "Σ req·a ≤ disp con req ∈ {0; 0,05…4}", "D5: persona repartida entre puestos.", "No se asignan personas concretas.", f"{K('modelo.resolver')} (r5)", TEC),
        ("Compromiso de recursos por hora completa", "a = 1 compromete req completo aunque u &lt; 1", "Variable binaria horaria.", "Activar una célula pocos minutos cuesta recursos de toda la hora.", f"{K('modelo.resolver')}", TEC),
        ("Piezas, stock y camiones fraccionarios", "520 / 15 = 34,67 chasis por camión", "Evita enteros que no aportan.", "El stock continuo puede diferir de enteros reales ±1.", f"{K('horizonte')}, {K('modelo')}", TEC),
        ("Granularidad horaria del balance", "Envío de 07:30 → slot 07:00; la producción del mismo slot puede cubrirlo", "Slots de 1 h.", "Permite cubrir envíos con producción de la misma hora.", f"{K('horizonte.construir_horizonte')} (slot)", TEC),
        ("Sin cambios de utillaje, arranque ni rampa", "Arranque penalizado 1e-4 por arranque (sólo estabilidad)", "No hay datos.", "Planes algo optimistas.", f"{K('modelo.PESO_ARRANQUES')}", TEC),
        ("Envío de piezas = chasis × ppc, igual para todas las células del mismo tipo", "env[c,h] = chasis_tipo(c,h)·ppc_c", "F1/F2.", "No hay variantes ni mezcla de modelos dentro del tipo.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("Célula 10 tipada COMB sin piezas", "excluida de P", "Tabla: 0 piezas/m².", "No genera stock, energía (0 kW) ni envíos.", f"{K('datos.celulas_productivas')}", TEC),
        ("Sólo se modela el almacén de producto terminado", "A = 800 m²", "Es la zona que limita el plan.", "MP (400) y cargas (100) no se consideran; el almacén externo no se modela.", f"{K('datos.Escenario.area_producto_terminado')}", TEC),
        ("Calendario de camiones", "i = 0…14: 06:00 + 1,5·i h; los de 00:00–03:00 son del día anterior", "F6.", "El viernes hay camiones hasta el sábado 03:00.", f"{K('horizonte._camiones_previstos')}", TEC),
        ("Fin de semana y festivos", "laborable = lunes–viernes por fecha_turno; sin festivos", "F4.", "La noche del domingo no es laborable: el primer turno productivo es el lunes 06:00.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("Chasis no expedibles por el tope de 15 m² se pierden", "escalado proporcional y alerta", "Evita backlog infinito.", "Subestima el stock necesario futuro.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("Demanda semanal ÷ 5 y ÷ 15 camiones; la última fila de corrección manda", "480 y 320 por día laborable sin corrección", "Reparto uniforme.", "Sin estacionalidad intradía.", f"{K('datos.demanda_dia')}", TEC),
        ("Normalización de E", "E = Σ f·kW·u / (|W|·Σ kW·máx f), máx f = 1,20", "Mantener E en [0,1].", "E es relativo a la potencia total de la planta.", f"{K('modelo.evaluar')}", TEC),
        ("kW por célula = potencia", "energía = kW · u · 1 h", "La tabla dice «KW/h».", "Si fueran kWh/h el resultado es idéntico en forma.", f"{K('modelo.evaluar')}", TEC),
        ("Franja nocturna fija en 22–06", "factor_noche aplica a 22 ≤ h ó h &lt; 6", "Coincide con el turno N.", "Cambiar el turno N exige cambiar el código.", f"{K('horizonte.construir_horizonte')}, {K('modelo._kpis_tramo')}", TEC),
        ("La puntuación no se trunca", "100·(1 − Σ w·comp)", "Interpretabilidad.", "Un componente &gt; 1 daría contribuciones negativas.", f"{K('modelo.evaluar')}", TEC),
        ("B puntúa el déficit frente al colchón, no la violación", "short = máx(0, (1+k)·SS − I)", "La violación es dura (D3).", "El 10 % mide margen sobre el SS.", f"{K('modelo.resolver')}", TEC),
        ("Condición terminal: cobertura de 8 h tras el horizonte", "env_final = camiones en [t1, t1 + 8 h)", "F18.", "En fin de semana (sin camiones) vale 0: no cubre el lunes.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("RecursosReales sobrescribe recurso a recurso; los técnicos de Mto se restan después", "Mto_disp = máx(0, real − técnicos)", "Un técnico asignado no está disponible.", "Mto real ya descontado se resta otra vez si el usuario no lo tuvo en cuenta.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("Sólo el estado BAJA bloquea; intervalo por solape", "desde/hasta vacíos = ilimitado", "Un solo estado definido.", "Otros estados se ignoran.", f"{K('horizonte.construir_horizonte')}", TEC),
        ("Pareja 11–12 cableada por número de célula", "CELULAS_PAREJA = (11, 12)", "Dossier.", "Una baja de 11 o de 12 deja ambas fuera.", f"{K('config.CELULAS_PAREJA')}", CONF),
        ("Absentismo por recurso con round half up", "Sólo operarios cambian", "F7/F17.", "Picking, carretilleros, Mto y Calidad nunca se reducen en el estándar.", f"{K('horizonte.redondeo_comercial')}", TEC),
        ("Stock inicial ausente = 0; célula 10 ignorada", "StockActual", "Valor seguro.", "Una célula olvidada arranca vacía y fuerza producir.", f"{K('datos.stock_inicial')}", TEC),
        ("Configuración del turno actual = células activas ≥ 1 h en el tramo contiguo del turno del slot 0", "p. ej. inicio 15:00 → 7 h", "Definición operativa del Top.", "Con inicio a mitad de turno sólo cuenta el resto del turno.", f"{K('horizonte.Horizonte.slots_turno_actual')}", TEC),
        ("Distinción del Top sólo por el turno actual", "frozenset(config_turno_actual)", "Especificación §4.", "Dos planes pueden diferir sólo en turnos futuros y contarse como iguales.", f"{K('motor.recomendar')}", TEC),
        ("Top reordenado por puntuación; Top 2/3 con cortes", "top.sort(key=puntuacion, reverse=True)", "Dossier: Top 1 = mejor puntuación.", "Top 2/3 no son necesariamente los 2.º/3.º mejores globales exactos.", f"{K('motor.recomendar')}", TEC),
        ("Gap y tiempo límite", f"{K('gap_relativo')} = 0,001; {K('tiempo_limite_s')} = 30 s (semana: 0,005 y 10 s)", "Rendimiento (§5.12).", "Planes óptimos hasta ±0,1 %; en la semana puede ser peor.", f"{K('Parametros')}", TEC),
        ("Arranque en caliente sólo para el Top 1 y sólo con la activación", "setInitialValue(a)", "El plan de referencia es una solución casi factible.", "No afecta al óptimo, sólo al tiempo.", f"{K('modelo.resolver')}", TEC),
        ("Plan de referencia: heurística de 8 h, orden por célula, u = 1", "HORIZONTE_MIRADA = 8", "Modelo de «plan manual».", "No garantiza las reglas 7 y 8.", f"{K('baseline')}", TEC),
        ("Tolerancias numéricas", "stock 1e-3 piezas; espacio 1e-3 m²; holgura 1e-6; puntuación 1e-4", "Evitar falsos positivos numéricos.", "Un incumplimiento menor que la tolerancia no se detecta.", f"{K('validador')}, {K('modelo')}", TEC),
        ("La reconfiguración supone que el plan vigente se ejecutó tal cual hasta «ahora»", "stock(ahora) = stock del plan al cierre de la hora anterior", "No hay lectura de planta en el motor.", "Si el stock real diverge hay que usar el evento stock_real.", f"{K('rolling.reconfigurar')}", TEC),
        ("La simulación semanal consolida el Top 1 sin incidencias", "una iteración por turno, 8 h consolidadas", "Estimar la dinámica semanal.", "No refleja incidencias reales.", f"{K('rolling.simular_semana')}", TEC),
        ("Idoneidad = 100·(1 − gap de HiGHS)", "gap relativo sobre el objetivo MILP completo", "Especificación §3.", "No es un % de optimalidad de la puntuación.", f"{K('modelo.evaluar')}", TEC),
        ("Fechas sin zona horaria ni cambio horario", "pandas Timestamp naive", "Simplicidad.", "El cambio de hora (25/10/2026) no se trata.", f"{K('horizonte')}", TEC),
        ("Resolución no determinista en tiempo", "hilos de HiGHS por defecto", "—", "Los tiempos varían entre ejecuciones; el óptimo no.", f"{K('modelo.resolver')}", TEC),
        ("El validador comparte datos con el modelo", "usa el mismo Horizonte y Escenario", "Validación independiente de la lógica, no de los datos.", "Un error de datos o de horizonte no se detecta.", f"{K('validador.validar')}", TEC),
    ]
    for i, (d, v, j, im, ch, es) in enumerate(I, 1):
        W.append((f"I{i}", d, v, j, im, ch, es))
    return W


def seccion_supuestos(X, D):
    W = filas_supuestos(X, D)
    E = [NextPageTemplate("land"), PageBreak()]
    E += H1("3. Registro completo de supuestos", salto=False)
    E += [P("Tabla maestra de todo lo asumido. <b>D</b> = decisiones del equipo (2/10/2026); <b>F</b> = flags de "
            f"{K('config.FLAGS')} (descripción literal del código); <b>I</b> = supuestos implícitos hallados en el código que no están marcados "
            "como flag. «Cómo cambiarlo» indica la hoja/parámetro del Excel o la ubicación en el código.", "body")]
    cab = [["ID", "Descripción", "Valor usado", "Justificación", "Impacto en resultados", "Cómo cambiarlo", "Estado"]]
    filas = cab + [[a, b, c, d, e, f, g] for a, b, c, d, e, f, g in W]
    anchos = [1.0 * cm, 5.9 * cm, 3.6 * cm, 4.5 * cm, 4.4 * cm, 3.9 * cm, 2.4 * cm]
    E += [tabla(filas, anchos, ["C"] + ["L"] * 6, fs=6.7, valign="TOP")]
    E += [NextPageTemplate("port")]
    return E


# ------------------------------------------------------------------------------------- 4. trabajo realizado
PROPOSITO = {
    "src/kwd/__init__.py": "Marca del paquete {kwd}.",
    "src/kwd/__main__.py": "Permite {python -m kwd}; delega en {cli.main}.",
    "src/kwd/config.py": "FLAGS F1–F19, constantes (recursos, turnos, célula 10, pareja 11–12), hojas/columnas del Excel y parámetros por defecto.",
    "src/kwd/datos.py": "Dataclass {Escenario}; lectura/escritura del Excel; tabla de células; SS, stock inicial y demanda diaria; escenarios de ejemplo y contingencia.",
    "src/kwd/horizonte.py": "Horizonte de 24 slots: turnos, laborables, recursos, bloqueos, camiones, envíos de piezas, factor energético, cobertura final.",
    "src/kwd/plan.py": "Dataclass {Plan} (estructura de un plan de producción y su evaluación).",
    "src/kwd/modelo.py": "MILP (PuLP + HiGHS): {preparar}, {resolver}, {evaluar}, KPIs y puntuación.",
    "src/kwd/baseline.py": "Plan de referencia «manual» (heurística de 8 h).",
    "src/kwd/motor.py": "Orquestación Top-K, contingencia, explicación (qué/por qué/impacto) y alertas.",
    "src/kwd/validador.py": "Validador independiente de reglas y puntuación; avisos de condición terminal.",
    "src/kwd/rolling.py": "Eventos, {aplicar_evento}, {reconfigurar} y {simular_semana}.",
    "src/kwd/cli.py": "Línea de comandos: imprime Top 3, KPIs, explicación y alertas; opcionalmente genera el PDF.",
    "src/kwd/informes.py": "Informe PDF de dirección (ReportLab + matplotlib).",
    "app/dashboard.py": "Dashboard Streamlit de 8 pestañas (Recomendación, KPIs, Overview 24 h, Alternativas, Incidencias, Datos, Semana, Informe).",
    "app/graficos.py": "Gráficos plotly del dashboard (Gantt, stock, almacén, recursos, energía, contribuciones).",
    "tests/test_motor.py": "21 pruebas pytest del motor.",
    "data/entrada_ejemplo.xlsx": "Escenario de demostración (semana 28/09/2026, corrección 02/10).",
    "data/escenario_contingencia.xlsx": "Demo + baja de la célula 14 (02/10 06:00 → 03/10 06:00): produce plan INVIABLE.",
    "docs/ESPECIFICACION.md": "Especificación v1 y desviaciones de la implementación (§11).",
    "docs/PLAN_EQUIPO.md": "Plan de acción del equipo (roles, cronograma, mensajes clave).",
    "docs/generar_documento.py": "Genera {KWD_Solucion_y_Plan_de_Accion.pdf} ejecutando el motor.",
    "docs/generar_informe_tecnico.py": "Genera este informe técnico ejecutando el motor.",
    "requirements.txt": "Dependencias fijadas (highspy, pulp, pandas, openpyxl, streamlit, plotly, matplotlib, reportlab, pytest).",
    "iniciar_dashboard.bat": "Arranque del dashboard con doble clic en Windows ({PYTHONPATH=src}).",
    ".vscode/launch.json": "Configuraciones de depuración: Dashboard, Plan por consola, Tests.",
    ".vscode/settings.json": "Intérprete {.venv}, pytest sobre {tests}, codificación UTF-8.",
    ".vscode/extensions.json": "Extensiones recomendadas de VS Code.",
    ".streamlit/config.toml": "Tema claro de Streamlit con color primario navy (corrección del tema).",
    "salida/KWD_Solucion_y_Plan_de_Accion.pdf": "Documento «Solución paso a paso y plan de acción» (equipo).",
    "salida/informe_ejemplo.pdf": "Informe PDF de dirección del escenario de ejemplo.",
    "salida/prueba_viable.pdf": "Informe PDF de prueba (escenario viable).",
    "salida/prueba_contingencia.pdf": "Informe PDF de prueba (escenario de contingencia).",
    "salida/KWD_Informe_Tecnico.pdf": "Este documento.",
}


def _k(txt):
    return re.sub(r"\{([^}]+)\}", lambda m: K(m.group(1)), txt)


def inventario():
    filas = []
    for rel in PROPOSITO:
        p = RAIZ / rel
        if rel == "salida/KWD_Informe_Tecnico.pdf":
            tam = "—"
        elif not p.exists():
            continue
        elif p.suffix in (".py", ".md", ".txt", ".bat", ".json", ".toml"):
            tam = f"{len(p.read_text(encoding='utf-8-sig').splitlines())} líneas"
        else:
            tam = f"{p.stat().st_size / 1024:,.0f} KB".replace(",", ".")
        filas.append([K(rel), tam, _k(PROPOSITO[rel])])
    return filas


TESTS_DESC = {
    "test_hay_top3_viables": "Escenario holgado (stock 2×SS) a las 14:00: 3 planes Top, sin contingencia, estados OPTIMO/FACTIBLE e idoneidad en [0, 100].",
    "test_reglas_duras_nunca_violadas": "El validador devuelve lista vacía para cada Top; {viable} es verdadero y {incumplimientos} vacío.",
    "test_celula_10_siempre_activa_en_horas_laborables": "a[10,h] = 1 en todos los slots laborables y toda activación es 0 en los no laborables.",
    "test_celulas_11_y_12_misma_activacion": "En Top 1–3 y en la referencia, 11 y 12 tienen la misma activación y el mismo uso.",
    "test_celula_de_baja_no_aparece_en_ninguna_config": "Baja de la célula 8 durante 30 h: no aparece en ninguna configuración ni activación, ni en la referencia.",
    "test_top_configs_distintas": "Las tres configuraciones del turno actual son distintas (cortes no-good).",
    "test_puntuacion_es_100_por_uno_menos_componentes_ponderados": "puntuación = 100·(1 − Σ w·componente); Σ contribuciones = puntuación; componentes en [0, 1].",
    "test_validacion_stock_seguridad_151_7_m2": "ΣSS/densidad de la demo = 151,7 m² (±0,1), validación de F1/F2/F10.",
    "test_escenario_imposible_es_inviable_como_contingencia": "Célula 3 de baja con stock 50: Top vacío, contingencia INVIABLE con incumplimientos y alerta «INVIABLE».",
    "test_reconfigurar_baja_celula_retira_la_celula": "Baja de la célula 9 en +2 h: el nuevo inicio es «ahora», la célula no se activa en sus 6 slots y los bloqueos son range(0, 6).",
    "test_simular_semana_devuelve_15_filas": "15 filas (M, T, N, …), tiempo por iteración &lt; 15 s, ninguna INVIABLE y stock mínimo ≥ SS en todos los turnos.",
    "test_horizonte_cruza_fin_de_semana": "Inicio viernes 22:00: 8 slots laborables, resto no; sin envíos tras el slot 8; último camión ≤ sábado 03:00; sin activación tras el slot 8.",
    "test_recurso_con_disponibilidad_cero_no_rompe": "Picking = 0 en el turno T: ninguna célula con picking se activa, validador vacío.",
    "test_hojas_opcionales_vacias_y_roundtrip": "Hojas opcionales vacías, guardar/cargar el Excel, demanda_dia = (480, 320) y el motor sigue funcionando.",
    "test_plantilla_ejemplo_y_demanda": "Plantilla de ejemplo: corrección diaria (520, 300), semanal ÷ 5 (480, 320), sábado (0, 0) y FLAGS ⊇ F1–F11.",
    "test_expedicion_real_sustituye_prevision": "Expedición real 15:00 (10 VE, 5 COMB) sustituye al camión de las 15:00; 9 camiones en el horizonte de las 14:00.",
    "test_demo_06_tiene_top3_viables_distintos": "Demo a las 06:00: 3 Top viables, configuraciones distintas e idoneidad &gt; 99.",
    "test_demo_adelanta_produccion_de_celula_13": "La célula 13 está en el Top 1 de las 06:00 y la explicación incluye «Se adelanta producción de la célula 13 por mantenimiento en turno T».",
    "test_condicion_terminal_de_stock_en_la_demo": "Jueves 14:00: stock final ≥ SS + envío_final en todas las piezas y sin avisos (F18).",
    "test_demo_14_es_viable": "Demo a las 14:00: al menos un Top, sin contingencia, todos viables.",
    "test_escenario_contingencia_es_inviable": "Excel de contingencia: Top vacío, contingencia INVIABLE, idoneidad y gap None, incumplimiento que cita la célula 14, texto «—».",
}


def seccion_trabajo(X, D):
    import ast
    E = H1("4. Trabajo realizado")
    E += [H2("4.1 Cronología de la construcción")]
    crono = [["Fase", "Qué se hizo", "Resultado / ficheros"],
             ["1. Análisis y preguntas", "Lectura de dossier, presentación (con notas), ttablas.xlsx, plano de planta y agenda. Detección de inconsistencias entre fuentes y con el planteamiento del equipo (§2.3) y elaboración de la lista de preguntas.",
              "Decisiones D1–D16 del 2/10/2026."],
             ["2. Especificación", "Redacción de la especificación v1: flags F1–F11, hojas del Excel, horizonte, MILP, Top-K, referencia, explicación, KPIs, rolling, API y dashboard.", K("docs/ESPECIFICACION.md")],
             ["3. Motor", "Implementación del paquete {kwd}: configuración, datos, horizonte, plan, modelo MILP, referencia, orquestación Top-K, validador independiente, horizonte rodante y CLI.".replace("{kwd}", K("kwd")), K("src/kwd/*.py")],
             ["4. Dashboard", "Streamlit con 8 pestañas (recomendación, KPIs, overview, alternativas, incidencias/reconfigurar, datos editables, semana, informe) y gráficos plotly.", K("app/dashboard.py") + ", " + K("app/graficos.py")],
             ["5. Informes", "Informe PDF de dirección (ReportLab + matplotlib), documento «Solución y plan de acción» y plan del equipo.", K("src/kwd/informes.py") + ", " + K("docs/")],
             ["6. QA", "Batería pytest (21 pruebas), ejecución de la CLI, pruebas de la app con {AppTest} en los dos escenarios y arranque «headless» del servidor.".replace("{AppTest}", K("AppTest")), K("tests/test_motor.py")],
             ["7. Correcciones tras la revisión", "Condición terminal F18; stock de la demo 1,8×SS (F19); idoneidad {None} en planes INVIABLE; Top ordenado por puntuación; escenario de contingencia en el dashboard; arreglo del tema claro; gap 0,1 % en lugar de 0 (ver 4.3).".replace("{None}", K("None")), "Cambios en {modelo}, {motor}, {datos}, {config}, {dashboard}".replace("{modelo}", K("modelo.py")).replace("{motor}", K("motor.py")).replace("{datos}", K("datos.py")).replace("{config}", K("config.py")).replace("{dashboard}", K("dashboard.py"))],
             ["8. Informe técnico", "Este documento: registro de supuestos, trabajo realizado y metodología con cifras recalculadas.", K("docs/generar_informe_tecnico.py")]]
    E += [tabla(crono, [3.2 * cm, 9.2 * cm, 4.6 * cm], ["L", "L", "L"], fs=7.4, valign="TOP")]
    E += [H2("4.2 Organización del trabajo")]
    E += [P("El trabajo se organizó por roles de modelos de IA bajo la dirección del equipo humano:")]
    E += [tabla([["Función", "Modelo", "Responsabilidad"],
                 ["Orquestación y razonamiento", "Opus", "Interpretación del reto, resolución de ambigüedades, diseño de la formulación MILP, revisión crítica y decisión de correcciones."],
                 ["Codificación y documentación", "Sonnet", "Implementación del motor, dashboard, informes y documentos; mantenimiento de la especificación."],
                 ["Despliegue y pruebas", "Haiku", "Instalación del entorno, ejecución de pytest, de la CLI y de las pruebas de la app, arranque del servidor."]],
                [4.2 * cm, 2.2 * cm, 10.6 * cm], ["L", "L", "L"])]
    E += [P("Las decisiones de modelado (D1–D16) las tomó el equipo humano; la IA propuso alternativas y documentó su impacto. El equipo de "
            "cinco personas definido en " + K("docs/PLAN_EQUIPO.md") + " (modelado, datos/KWD, dashboard, informes, pruebas) usa estos entregables como base.", "small")]
    E += [H2("4.3 Correcciones realizadas tras la revisión")]
    corr = [["Corrección", "Motivo", "Dónde en el código", "Efecto verificable"],
            ["Condición terminal de stock (F18)", "El plan podía consumir el stock al final del horizonte y dejar sin cobertura los envíos inmediatamente posteriores.",
             f"{K('horizonte.construir_horizonte')} (envio_final), {K('modelo.resolver')} (term_c, sf), {K('validador.avisos_plan')}",
             "Stock final ≥ SS + envíos de las 8 h siguientes (prueba 19); la semana simulada mantiene stock ≥ SS."],
            ["Stock inicial de la demo 1,8 × SS (F19)", "Con 1,3 × SS (especificación v1) el escenario es inviable: 0,3·SS no cubre un turno de envíos.",
             f"{K('datos.FACTOR_STOCK_DEMO')}", "La demo da Top 1/2/3 viables; el escenario de contingencia sigue siendo inviable por la baja."],
            ["Idoneidad None en planes INVIABLE/REFERENCIA", "La idoneidad (100·(1 − gap)) no tiene sentido para un plan que viola reglas ni para una heurística.",
             f"{K('modelo.evaluar')}", "El dashboard y el PDF muestran «—» / «No aplica: plan de contingencia»."],
            ["Top ordenado por puntuación", "KWD define Top 1 como la alternativa viable con mejor puntuación; el objetivo MILP incluye términos auxiliares.",
             f"{K('motor.recomendar')} (top.sort)", "El Top 1 es siempre el de mayor puntuación entre los viables encontrados."],
            ["Escenario de contingencia en el dashboard", "Poder demostrar el caso INVIABLE sin editar el Excel.", f"{K('data/escenario_contingencia.xlsx')}, {K('datos.crear_escenario_contingencia')}, {K('app/dashboard.py')} (selector)",
             "Opción «Escenario de contingencia (célula 14 de baja)» en la barra lateral."],
            ["Tema claro", "El CSS del dashboard (tarjetas y cabecera) está diseñado para fondo claro; se fija el tema claro para no depender del tema del sistema.", f"{K('.streamlit/config.toml')} (base = light)", "Aspecto consistente en cualquier equipo."],
            ["Gap 0,1 % en lugar de 0", "Demostrar gap 0 supera 30 s por degeneración y simetrías.", f"{K('modelo.resolver')} (gapRel), parámetro {K('gap_relativo')}",
             "Óptimo garantizado ±0,1 %; la idoneidad se etiqueta «óptimo garantizado ±gap»."]]
    E += [tabla(corr, [3.3 * cm, 4.9 * cm, 4.6 * cm, 4.2 * cm], ["L"] * 4, fs=7.2, valign="TOP")]
    E += [P("Otros cambios documentados en la especificación §11: arranque en caliente con la activación de la referencia, flags F12–F17, "
            "campos adicionales de " + K("Plan") + " y " + K("Recomendacion") + " y adaptación a PuLP 4 (" + K("prob.add_variable") + ").", "small")]
    E += [H2("4.4 Inventario de ficheros")]
    E += [tabla([["Fichero", "Tamaño", "Propósito"]] + inventario(), [5.7 * cm, 1.9 * cm, 9.4 * cm], ["L", "R", "L"], fs=7.2)]
    E += [H2("4.5 Pruebas automáticas")]
    src = (RAIZ / "tests" / "test_motor.py").read_text(encoding="utf-8-sig")
    nombres = [n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    filas = [["#", "Prueba", "Qué verifica"]]
    for i, n in enumerate(nombres, 1):
        filas.append([str(i), K(n), _k(TESTS_DESC.get(n, "—"))])
    E += [P(f"El fichero {K('tests/test_motor.py')} contiene <b>{len(nombres)}</b> pruebas. Las que usan el escenario holgado "
            f"({K('_escenario_holgado')}: stock inicial = 2 × SS) comprueban propiedades generales; las de «demo» usan el escenario oficial.")]
    E += [tabla(filas, [0.7 * cm, 6.5 * cm, 9.8 * cm], ["C", "L", "L"], fs=7.0, valign="TOP")]
    E += [H2("4.6 Resultados de QA")]
    E += [tabla([["Comprobación", "Resultado"],
                 ["pytest (21 pruebas)", "21 passed en 114,5 s (ejecución del 02/10/2026, 06:00–14:00 y semana completa incluidas)."],
                 ["CLI " + K("python -m kwd.cli --entrada data/entrada_ejemplo.xlsx"), "Imprime Top 3, KPIs, explicación, impacto y alertas (comprobado por el equipo de pruebas)."],
                 ["AppTest de Streamlit", "Escenario de ejemplo y escenario de contingencia: la app carga, calcula y muestra las 8 pestañas sin excepciones (comprobado por el equipo de pruebas)."],
                 ["Arranque headless", "El servidor Streamlit arranca sin interfaz y responde (comprobado por el equipo de pruebas)."],
                 ["Validador", f"Sin incumplimientos en el Top 1 de las 06:00 y de las 14:00; detecta {len(X['recc'].contingencia.incumplimientos)} en el plan de contingencia (§7)."]],
                [5.6 * cm, 11.4 * cm], ["L", "L"], fs=7.6)]
    E += [H2("4.7 Entregables producidos")]
    ent = [["Entregable", "Ruta", "Descripción"]]
    for rel, desc in [("salida/KWD_Solucion_y_Plan_de_Accion.pdf", "Solución paso a paso y plan de acción del equipo."),
                      ("salida/KWD_Informe_Tecnico.pdf", "Este informe técnico."),
                      ("salida/informe_ejemplo.pdf", "Informe PDF de dirección (ejemplo)."),
                      ("salida/prueba_viable.pdf", "Informe PDF de prueba, escenario viable."),
                      ("salida/prueba_contingencia.pdf", "Informe PDF de prueba, escenario de contingencia."),
                      ("data/entrada_ejemplo.xlsx", "Plantilla Excel con el escenario de demostración."),
                      ("data/escenario_contingencia.xlsx", "Plantilla Excel del escenario de contingencia."),
                      ("iniciar_dashboard.bat", "Arranque del dashboard con doble clic.")]:
        ent.append([Path(rel).name, K(rel), desc])
    zip_ = RAIZ.parent / "KWD_Motor_Decision_Entregable_v1.zip"
    if zip_.exists():
        ent.append([zip_.name, K("../" + zip_.name), f"Paquete de entrega v1 del proyecto ({zip_.stat().st_size / 1024:,.0f} KB).".replace(",", ".")])
    E += [tabla(ent, [5.3 * cm, 5.5 * cm, 6.2 * cm], ["L", "L", "L"], fs=7.3)]
    return E


# ------------------------------------------------------------------------------------- 5. metodología (a)
def seccion_metodologia_a(X, D, F):
    esc, rec06 = X["esc"], X["rec06"]
    hz = rec06.horizonte
    t = D["tc"]
    E = H1("5. Metodología interna del algoritmo")
    E += [P("Este apartado describe, módulo a módulo y tal como está implementado, cómo funciona el motor. Cada mecanismo cita el módulo y la "
            "función del código. Las cifras proceden de la ejecución de la demo del 02/10/2026.")]
    # ---------------------------------------------------------------- 5.0
    E += [H2("5.0 Diferencias entre la especificación y el código (el código prevalece)")]
    dif = [["#", "Tema", "Especificación", "Código (válido)"],
           ["1", "Gap del solver", "<font name='DVM' size='8'>mip_rel_gap = 0</font> (§3)", f"Parámetro {K('gap_relativo')} = 0,001 (0,1 %); la simulación semanal usa 0,005 (§11)."],
           ["2", "Stock inicial de la demo", "round(1,3 × SS)", "round(1,8 × SS) (F19): con 1,3 × SS el escenario es inviable."],
           ["3", "Condición terminal de stock", "No existe", f"Restricción blanda {K('term_c')}: I[c,H−1] ≥ SS + envíos de las 8 h siguientes − sf[c]; penalización 10 (F18)."],
           ["4", "Idoneidad y gap", "100·(1 − gap) para todo plan; {gap, idoneidad} son float", f"{K('None')} para planes INVIABLE y de REFERENCIA (campos {K('float | None')}); se muestra «—»."],
           ["5", "Contingencia en el Top-K", "Si ningún plan es viable se devuelve el mejor como contingencia", f"Sólo si el <b>primer</b> plan no es viable. Si un plan posterior (con cortes) no es viable el bucle se detiene y el Top queda con menos de K planes (alerta «Sólo se han encontrado n…»)."],
           ["6", "Orden del Top", "El de resolución", f"Se reordena por puntuación descendente ({K('motor.recomendar')}) y se renombra Top 1..K."],
           ["7", "Sin solución con holguras", "No contemplado", "Si HiGHS no devuelve solución, el plan de referencia se marca INVIABLE y actúa de contingencia."],
           ["8", "Reglas 2 y 4", "Restricciones", f"Cotas de las variables a[c,h] ({K('modelo.resolver')}: lo/up), equivalentes a las restricciones."],
           ["9", "Componente Q", "Media sobre W de ½(mto + cal)", f"Media sobre los recursos con disponibilidad &gt; 0 (si una vale 0 se usa el otro). El MILP divide por |W| y {K('evaluar')} / {K('validar')} por las horas con algún término: coinciden salvo caso degenerado (Mto y Calidad = 0 en una hora laborable)."],
           ["10", "Plan de referencia", "Respeta reglas 2–4", f"Además resta {K('env_final')} cuando su mirada de 8 h alcanza el final del horizonte; no comprueba reglas 7 y 8 (puede violarlas)."],
           ["11", "Expediciones en fin de semana", "F4: «sin expediciones el fin de semana»", "Los camiones del viernes salen hasta el sábado 03:00 (coherente con F6: «06:00 … 03:00»); el resto del fin de semana no hay envíos."],
           ["12", "Hoja Parametros", "19 claves", f"21 claves: se añaden {K('gap_relativo')} y {K('cobertura_final_h')}."],
           ["13", "Estructura de {Horizonte}".replace("{Horizonte}", "Horizonte"), "{envios}: célula × slot".replace("{envios}", "envios"), f"{K('envios')}: slot × célula; campos extra {K('camiones')}, {K('logistica_exigida')}, {K('inicio')}, {K('envio_final')}."],
           ["14", "Explicación «por qué»", "Hora de incumplimiento sin producir; franja solar", f"Además añade «Se adelanta producción… por mantenimiento/baja»; la elección entre «mantenimiento» y «baja» se basa en que la célula figure en la hoja {K('Mantenimientos')} (aunque no sea la causa del bloqueo)."],
           ["15", "Dashboard", "8 pestañas", "8 pestañas más selector de origen con «Escenario de contingencia (célula 14 de baja)»."],
           ["16", "Σ SS/densidad", "151,7 m² (F10)", f"{fm(D['suma_ss'], 2)} m² calculado por {K('validar_ss_almacen')}; la prueba acepta 151,7 ± 0,1, de modo que 151,7 es el redondeo de la especificación."]]
    E += [tabla(dif, [0.6 * cm, 3.0 * cm, 4.6 * cm, 8.8 * cm], ["C", "L", "L", "L"], fs=7.2, valign="TOP")]
    # ---------------------------------------------------------------- 5.1
    E += [KeepTogether([H2("5.1 Visión general del pipeline"), fig(F["pipeline"], 17.0, 11),
                        P("Figura 5.1 · Pipeline del motor: de las hojas Excel a las salidas y bucle de horizonte rodante.", "cap")])]
    pip = [["Paso", "Función del código", "Entrada → salida"],
           ["1", K("datos.cargar_entrada"), "Excel (11 hojas) → Escenario con parámetros completados"],
           ["2", K("horizonte.construir_horizonte"), "Escenario + inicio → Horizonte (slots, recursos, bloqueos, camiones, envíos)"],
           ["3", K("baseline.plan_referencia"), "Escenario + Horizonte → plan heurístico (REFERENCIA) y arranque en caliente"],
           ["4", K("modelo.resolver"), "Escenario + Horizonte + cortes → plan óptimo (a, u) evaluado"],
           ["5", K("modelo.evaluar") + " + " + K("validador.validar"), "(a, u) → stock, recursos, energía, KPIs, puntuación, incumplimientos, estado"],
           ["6", K("motor.recomendar"), "Bucle Top-K con cortes, reordenación y contingencia"],
           ["7", K("motor._explicar") + ", " + K("motor._alertas"), "Qué / por qué / impacto y alertas"],
           ["8", K("app/dashboard.py") + ", " + K("informes.generar_informe_pdf") + ", " + K("cli.main"), "Recomendacion → pantalla, PDF o consola"],
           ["9", K("rolling.reconfigurar") + ", " + K("rolling.simular_semana"), "Evento → nuevo Escenario → paso 2; semana = 15 iteraciones"]]
    E += [tabla(pip, [1.2 * cm, 6.2 * cm, 9.6 * cm], ["C", "L", "L"], fs=7.4)]
    # ---------------------------------------------------------------- 5.2
    E += [H2("5.2 Carga y validación de datos (módulo datos)")]
    E += [P(f"{K('datos.cargar_entrada')} lee con {K('pandas.read_excel')} todas las hojas del libro (los nombres se recortan con {K('strip()')}). "
            f"Sólo {K('Celulas')} es obligatoria (si falta o está vacía se lanza {K('ValueError')}); las demás hojas ausentes o vacías se tratan como "
            f"tablas vacías, es decir, «sin incidencias».")]
    E += bullets([
        f"<b>Normalización</b> ({K('_normalizar_hoja')}): garantiza las columnas y su orden según {K('config.HOJAS_COLUMNAS')}, descarta filas totalmente vacías, convierte columnas de fecha con {K('pd.to_datetime')} y normaliza a medianoche las columnas {K('fecha')} y {K('semana_inicio')}.",
        f"<b>Células</b>: {K('piezas_por_conjunto')} vacío = 1 (F2); columnas numéricas convertidas a float; {K('tipo')} en mayúsculas sin espacios.",
        f"<b>Parámetros</b>: parte de {K('config.PARAMETROS_DEFECTO')} (21 claves) y sobrescribe con la hoja {K('Parametros')} (valores como float; descripciones opcionales).",
        f"<b>Turnos y mantenimientos</b>: los códigos de turno se pasan a mayúsculas (M, T, N, DIA).",
        f"<b>Escenario</b> ({K('datos.Escenario')}): dataclass con un DataFrame por hoja, el diccionario {K('parametros')} y las descripciones; {K('copiar()')} hace una copia profunda (la usan los eventos para no tocar el original).",
        f"<b>Sin validación de esquema</b>: no se comprueban rangos ni coherencias salvo lo anterior. Valores por defecto silenciosos: {K('area_producto_terminado()')} = 800 m² si falta la zona; {K('disp_estandar()')} = 0 si falta el recurso; stock inicial 0 si falta la célula. Es una limitación (§8).",
    ])
    E += [H3("Funciones derivadas")]
    E += [tabla([["Función", "Definición exacta"],
                 [K("datos.tabla_celulas"), "Índice = nº de célula; {es_ve} = (tipo == «VE»); {ppc} = piezas_por_conjunto (1 si vacío); {cap_h} = 3600 / ciclo_s piezas/hora (F8, OEE 100 %).".replace("{es_ve}", K("es_ve")).replace("{ppc}", K("ppc")).replace("{cap_h}", K("cap_h"))],
                 [K("datos.celulas_productivas"), "Todas las células salvo la 10 (F11), en orden creciente: |P| = 15."],
                 [K("datos.ss_por_celula"), "SS[c] = ss_ve (400) si la célula es VE, ss_comb (200) si no (F10)."],
                 [K("datos.stock_inicial"), "Hoja StockActual; 0 para células que no figuran; ignora la 10."],
                 [K("datos.demanda_dia"), "Ver regla siguiente."],
                 [K("datos.validar_ss_almacen"), f"Σ SS[c]/piezas_m2[c] sobre las células con densidad &gt; 0 = {fm(D['suma_ss'], 2)} m² (F10)."]],
                [4.4 * cm, 12.6 * cm], ["L", "L"], fs=7.5)]
    E += [H3("Regla de la demanda diaria (datos.demanda_dia)")]
    E += [formulas([
        "demanda_dia(D) → (chasis_VE, chasis_COMB):",
        "  1. si hay fila en CorreccionDiaria con fecha = D → la última fila (ve, comb)",
        "     (la corrección sustituye el día completo)",
        "  2. si no, y D es sábado o domingo → (0, 0)                                      [F12]",
        "  3. si no: lunes = D − weekday(D); si hay fila en DemandaSemanal con",
        "     semana_inicio = lunes → (chasis_ve / 5, chasis_comb / 5)",
        "  4. si no hay fila de esa semana: la última semana anterior; si no, la primera   [F12]",
        "  5. sin ninguna fila en DemandaSemanal → (0, 0)"])]
    E += [P(f"Demo: el 02/10/2026 (viernes) hay corrección → ({fm(D['dem_dia'][0], 0)}, {fm(D['dem_dia'][1], 0)}); el 01/10 no la hay → "
            f"2.400/5 = {fm(D['dem_sem_dia'][0], 0)} VE y 1.600/5 = {fm(D['dem_sem_dia'][1], 0)} COMB; el sábado 03/10 → (0, 0).", "small")]
    # ---------------------------------------------------------------- 5.3
    E += [H2("5.3 Construcción del horizonte (módulo horizonte)")]
    E += [P(f"{K('horizonte.construir_horizonte')}(esc, inicio, horas) devuelve un {K('Horizonte')}. {K('inicio')} se redondea a la hora ({K('floor(\"h\")')}), "
            f"H = {K('horas_horizonte')} (24) y t1 = t0 + H horas.")]
    E += [H3("a) Slots, turnos y fechas de turno")]
    E += [formulas([
        "slot h = 0..H−1:  inicio = t0 + h horas;  fin = inicio + 1 h;  hora = inicio.hour",
        "turno(hora)       = M si 6 ≤ hora < 14;  T si 14 ≤ hora < 22;  N en otro caso        [F4]",
        "fecha_turno       = inicio.normalizado − (1 día si hora < 6)      (noche 00–06: día anterior)",
        "laborable         = weekday(fecha_turno) ∈ {lun, mar, mié, jue, vie}                  [F4]",
        "factor_energia[h] = 1,00 por defecto",
        "                  = factor_solar (0,85)  si solar_ini ≤ hora < solar_fin (11–17)     [F5]",
        "                  = factor_noche (1,20)  si hora ≥ 22 ó hora < 6 (22 y 6 fijos)      [F5]"])]
    E += [P("Consecuencia: una hora del sábado entre 00:00 y 06:00 pertenece al turno N del viernes (laborable); la noche del domingo es de fecha_turno "
            "domingo (no laborable); el primer turno productivo de la semana es el lunes 06:00.")]
    E += [figc(F["finde"], "Figura 5.2 · Asignación de turno, fecha de turno y laborable en un horizonte que cruza el fin de semana (ejemplo del test test_horizonte_cruza_fin_de_semana).", alto_max_cm=7.5)]
    E += [H3("b) Disponibilidad de recursos por slot")]
    E += [formulas([
        "para cada slot h y recurso r ∈ {operarios, picking, carretilleros, mto, calidad}:",
        "  base       = Turnos[r][turno(h)]",
        "  real       = última fila de RecursosReales con (fecha_turno(h), turno(h)); puede ser NaN",
        "  disp0[r,h] = real                                      si real existe y no es NaN",
        "             = base − redondeo_comercial(base·absentismo)   en otro caso        [F7, F17]",
        "  disp0[r,h] = máx(0, disp0[r,h])",
        "  tec[h]     = Σ tecnicos de Mantenimientos con fecha = fecha_turno(h)",
        "               y turno ∈ {turno(h), \"DIA\"}",
        "  disp[mto,h] = máx(0, disp0[mto,h] − tec[h])                                  [F3]",
        "  disp[r,h]   = disp0[r,h]   para los demás recursos",
        "redondeo_comercial(x) = floor(x + 0,5)      (round half up, F17)"])]
    E += [P(f"Demo: el 02/10 en turno T hay un valor real de operarios = 15 (igual al resultado con absentismo) y una intervención de mantenimiento con 2 "
            f"técnicos: disp[mto] = 7 − 2 = 5 durante T. Para los cálculos de «ocupación» una disponibilidad 0 se omite (evita divisiones por cero).")]
    E += [H3("c) Bloqueos de células (F13)")]
    E += bullets([
        f"<b>Bajas</b> (hoja {K('Disponibilidad')}, estado «BAJA»): se bloquean los slots con inicio &lt; hasta y fin &gt; desde (solape; {K('desde')} o {K('hasta')} vacíos = ilimitado). Otros estados se ignoran.",
        f"<b>Mantenimientos</b>: se bloquean los slots con fecha_turno = fecha y turno igual al indicado (o todos los del día si «DIA»).",
        f"Resultado: {K('bloqueos')}[c] = conjunto de slots en los que la célula no puede activarse (cotas a ≤ 0). Las filas con células inexistentes se ignoran."])
    E += [H3("d) Camiones, cargas y envíos de piezas (F6, F15)")]
    E += [formulas([
        "días D desde (t0 − 1 día) hasta t1 (+ cobertura final); sólo si weekday(D) es laborable:",
        "  para i = 0..14 (camiones_dia = 15):",
        "     hora = D + (primer_camion_h + i·intervalo_camion_h) h      → 06:00, 07:30, …, 03:00 (D+1)",
        "     ve = demanda_dia(D).VE / 15;   comb = demanda_dia(D).COMB / 15      (fraccional)",
        "expedición real (hoja Expediciones):",
        "  día = fecha_hora.date si hora ≥ 6, si no el día anterior",
        "  camión previsto no real de ese día más cercano en el tiempo:",
        "     si |Δ| ≤ 45 min → se sustituye (hora, ve, comb, real = True)",
        "     si no hay candidato a ≤ 45 min → se añade como camión extra                     [F15]",
        "m2_ve   = Σ_{c∈VE∩P}   ppc_c / dens_c        (m² por chasis VE)",
        "m2_comb = Σ_{c∈COMB∩P} ppc_c / dens_c        (m² por chasis COMB)",
        "m2_camión = ve·m2_ve + comb·m2_comb;   si m2_camión > m2_max_camion (15 m²):",
        "     esc = 15 / m2_camión;  ve·esc, comb·esc  (los chasis no expedidos se pierden y se avisa)",
        "slot del camión = ⌊(hora − t0) / 1 h⌋;   chasis_ve[h], chasis_comb[h] = Σ camiones del slot",
        "envios[h, c] = chasis_tipo(c)[h] · ppc_c        (célula 10 sin envíos)"])]
    E += [P(f"Demo: m2_ve = {fm(D['m2ve'], 4)} m²/chasis y m2_comb = {fm(D['m2comb'], 4)} m²/chasis. Un camión de las 06:00 del 02/10 lleva "
            f"{fm(D['dem_dia'][0] / 15, 2)} chasis VE y {fm(D['dem_dia'][1] / 15, 2)} COMB = {fm(D['dem_dia'][0] / 15 * D['m2ve'] + D['dem_dia'][1] / 15 * D['m2comb'], 2)} m² "
            f"(&lt; 15 m²: no hay escalado). El detalle completo está en el apartado 6.")]
    E += [H3("e) Envíos posteriores al horizonte (F18) y célula 10 (F11, F14)")]
    E += [formulas([
        "t2 = t1 + cobertura_final_h (8 h)",
        "post_ve, post_comb = Σ (ve·esc, comb·esc) de los camiones con t1 ≤ hora < t2",
        "envio_final[c]     = post_tipo(c) · ppc_c           (piezas a cubrir tras el horizonte)",
        "logistica_exigida[h] = laborable[h] ∧ h ∉ bloqueos[10] ∧ ∀r: req[10,r] ≤ disp[r,h]",
        "alerta si existe una hora laborable con logistica_exigida = falso            (F14)"])]
    E += [P("Alertas del horizonte ({hz.alertas}): «Capacidad de expedición insuficiente: X chasis no expedibles el dd/mm/aaaa» (por día con escalado) y "
            "«La célula 10 (servicio logístico) no puede garantizarse en n hora(s) laborable(s)»."
            .replace("{hz.alertas}", K("hz.alertas")), "small")]
    return E



# ------------------------------------------------------------------------------------- 5. metodología (b)
def seccion_metodologia_b(X, D, F):
    esc, rec06 = X["esc"], X["rec06"]
    hz = rec06.horizonte
    d = modelo.preparar(esc, hz)
    H, nP, nC, nW = d.H, len(d.prods), len(d.todas), int(d.W.sum())
    turno_n = len(hz.slots_turno_actual())
    n_r = sum(1 for k in [RECURSOS.index(r) for r in config.RECURSOS_R] for h in np.where(d.W)[0] if d.disp[h, k] > 1e-9)
    kw_sum = float(d.kw.sum())
    den_e = nW * kw_sum * d.fmax
    E = [H2("5.4 Formulación MILP exacta (módulo modelo, función resolver)")]
    E += [P(f"{K('modelo.resolver')} construye con PuLP un problema de minimización ({K('LpProblem(\"kwd_plan\", LpMinimize)')}); las variables se crean con "
            f"{K('prob.add_variable')} (PuLP 4) mediante el ayudante {K('_var')}. Los parámetros se preparan en {K('modelo.preparar')} como arrays NumPy.")]
    E += [H3("Conjuntos y parámetros")]
    par = [["Símbolo", "Definición", "Origen en el código", "Valor demo"],
           ["C, P, H, W", "Células (16), productivas C ∖ {10} (15), slots, slots laborables", f"{K('d.todas')}, {K('d.prods')}, {K('d.H')}, {K('d.W')}", f"|C| = {nC}, |P| = {nP}, |H| = {H}, |W| = {nW}"],
           ["cap[c]", "3600 / ciclo_s (piezas/h)", f"{K('d.cap')}", "ver apartado 6"],
           ["req[c,k]", "Recurso k que exige la célula c activa", f"{K('d.req')} (5 columnas: {', '.join(RECURSOS)})", "Anexo A"],
           ["disp[h,k]", "Disponibilidad por slot y recurso", f"{K('d.disp')} (de {K('hz.slots')})", "disp = estándar − absentismo / real / − técnicos"],
           ["dens[c], ppc[c]", "Piezas/m² y piezas por conjunto", f"{K('d.dens')}, {K('d.ppc')}", "Anexo A"],
           ["SS[c], I0[c]", "Stock de seguridad y stock inicial (piezas)", f"{K('d.ss')}, {K('d.i0')}", "400/200; 720/360 (F19)"],
           ["env[h,c]", "Piezas que salen en el slot h", f"{K('d.env')} (de {K('hz.envios')})", "apartado 6"],
           ["envF[c]", "Piezas a cubrir tras el horizonte (F18)", f"{K('d.env_final')}", "0 si no hay camiones tras el horizonte"],
           ["kW[c], f[h]", "Potencia y factor energético", f"{K('d.kw')}, {K('d.f')}", f"Σ kW = {fm(kw_sum, 2)}; máx f = {fm(d.fmax, 2)}"],
           ["A, k", "Área de producto terminado y colchón", f"{K('d.A')}, {K('d.k')}", f"{fm(d.A, 0)} m²; {fm(d.k, 2)}"],
           ["w_R … w_E", "Pesos de los criterios", f"{K('d.pesos')}", "0,50 / 0,20 / 0,15 / 0,10 / 0,05"]]
    E += [tabla(par, [2.4 * cm, 5.6 * cm, 5.0 * cm, 4.0 * cm], ["L"] * 4, fs=7.2)]
    E += [H3("Variables de decisión")]
    var = [["Variable", "Nombre PuLP", "Dominio y cotas", "Significado", "Nº (Top 1, 06:00)"],
           ["a[c,h]", "a_c_h", "Binaria; lo = 1 si c = 10 y logistica_exigida[h]; up = 0 si h ∈ bloqueos[c] o h ∉ W", "Célula c activa (recursos comprometidos) en el slot h", f"{nC}·{H} = {nC * H}"],
           ["u[c,h]", "u_c_h", "Continua [0, 1], c ∈ P", "Fracción de la hora produciendo", f"{nP}·{H} = {nP * H}"],
           ["I[c,h]", "I_c_h", "Continua libre, c ∈ P", "Stock de la pieza al final del slot (piezas)", f"{nP * H}"],
           ["s[c,h]", "s_c_h", "Continua ≥ 0", "Holgura de stock de seguridad", f"{nP * H}"],
           ["st[c,h]", "st_c_h", "Continua ≥ 0", "Arranque (0→1) de la célula", f"{nP * H}"],
           ["short[c,h]", "short_c_h", "Continua ≥ 0 (sólo si k &gt; 0)", "Déficit frente al colchón (1+k)·SS", f"{nP * H}"],
           ["sa[h]", "sa_h", "Continua ≥ 0", "Holgura de espacio", f"{H}"],
           ["sf[c]", "sf_c", "Continua ≥ 0", "Holgura de la condición terminal (F18)", f"{nP}"],
           ["y[c]", "y_c", "Binaria, c ∈ C; sólo existe si hay cortes", "Célula c activa alguna vez en el turno actual", f"0 (Top 1); {nC} (Top 2 y 3)"]]
    E += [tabla(var, [1.7 * cm, 1.9 * cm, 5.0 * cm, 5.4 * cm, 3.0 * cm], ["L"] * 5, fs=7.0, valign="TOP")]
    E += [P(f"Total Top 1: {nC * H} + 5·{nP * H} + {H} + {nP} = <b>{nC * H + 5 * nP * H + H + nP}</b> variables, de ellas {nC * H} binarias. "
            f"Medido sobre el modelo construido: {X['st_rec06'][0]['vars']} variables, {X['st_rec06'][0]['bins']} binarias.", "small")]
    E += [H3("Restricciones (numeradas; nombre de PuLP entre paréntesis)")]
    cnt = {"C1": nP * H, "C3": 2 * H, "C5": 5 * nW, "C6": nP * H, "C7": nP * H, "C8": H, "C9": nP * H, "C10": nP * H, "C11": nP}
    res = [["Nº", "Regla", "Formulación", "Código", "Nº filas"],
           ["C1", "Regla 1", "u[c,h] ≤ a[c,h]   ∀c∈P, h", f"r1_c_h", fm(cnt["C1"], 0)],
           ["C2", "Regla 2", "a[10,h] = 1 si logistica_exigida[h];  a[c,h] = 0 ∀h ∉ W", "cotas de a (lo / up)", "cotas"],
           ["C3", "Regla 3", "a[11,h] = a[12,h];  u[11,h] = u[12,h]   ∀h", "r3a_h, r3u_h", fm(cnt["C3"], 0)],
           ["C4", "Regla 4", "a[c,h] = 0  si h ∈ bloqueos[c] (baja o mantenimiento)", "cotas de a (up)", "cotas"],
           ["C5", "Regla 5", "Σ_{c: req[c,k]&gt;0} req[c,k]·a[c,h] ≤ disp[h,k]   ∀k∈{5 recursos}, ∀h∈W", "r5_k_h", fm(cnt["C5"], 0)],
           ["C6", "Regla 6", "I[c,h] = I[c,h−1] + cap[c]·u[c,h] − env[h,c];  I[c,−1] = I0[c]", "r6_c_h", fm(cnt["C6"], 0)],
           ["C7", "Regla 7", "I[c,h] ≥ SS[c] − s[c,h]", "r7_c_h", fm(cnt["C7"], 0)],
           ["C8", "Regla 8", "Σ_{c∈P, dens&gt;0} I[c,h] / dens[c] ≤ A + sa[h]", "r8_h", fm(cnt["C8"], 0)],
           ["C9", "Colchón", "short[c,h] ≥ (1+k)·SS[c] − I[c,h]", "r9_c_h", fm(cnt["C9"], 0)],
           ["C10", "Arranques", "st[c,h] ≥ a[c,h] − a[c,h−1];  a[c,−1] = 0", "r10_c_h", fm(cnt["C10"], 0)],
           ["C11", "Terminal (F18)", "I[c,H−1] ≥ SS[c] + envF[c] − sf[c]", "term_c", fm(cnt["C11"], 0)],
           ["C12", "Top-K: enlace", "y[c] ≥ a[c,h] ∀h∈turno actual;  y[c] ≤ Σ_{h∈turno actual} a[c,h]", "y1_c_h, y2_c", f"{nC}·{turno_n} + {nC} = {nC * turno_n + nC} (con cortes)"],
           ["C13", "Top-K: corte j", "Σ_{c∈S_j}(1 − y[c]) + Σ_{c∉S_j} y[c] ≥ 1", "corte_j", "1 por plan previo"]]
    E += [tabla(res, [1.0 * cm, 2.2 * cm, 8.1 * cm, 3.2 * cm, 2.5 * cm], ["C", "L", "L", "L", "L"], fs=7.0, valign="TOP")]
    suma = sum(cnt.values())
    E += [P(f"Total Top 1: {fm(suma, 0)} restricciones (medido: {X['st_rec06'][0]['cons']}). Top 2: +{nC * turno_n + nC} (enlace) + 1 corte "
            f"(medido: {X['st_rec06'][1]['cons']}); Top 3: +1 corte más ({X['st_rec06'][2]['cons']}). Las reglas 2 y 4 no generan filas porque se "
            f"implementan como cotas de las variables binarias. Las filas de C5 sólo se generan para slots laborables (|W| = {nW} aquí).", "small")]
    E += [aviso("<b>Nota.</b> C7 (stock de seguridad) y C8 (espacio) son «duras» sólo porque sus holguras s y sa se penalizan con "
                f"{fm(modelo.PENALIZACION, 0)} en el objetivo: con cualquier solución viable las holguras valen 0; si fuese imposible, el solver usa holgura y el plan "
                "resultante se marca INVIABLE. La holgura terminal sf se penaliza con "
                f"{fm(modelo.PENALIZACION_FINAL, 0)} y no convierte el plan en inviable.")]
    E += [H3("Función objetivo")]
    E += [formulas([
        "min  w_R·R + w_S·S + w_Q·Q + w_B·B + w_E·E",
        "     + 1000 · ( Σ_{c,h} s[c,h]/SS[c]  +  Σ_h sa[h]/A )",
        "     + 10   · Σ_c sf[c]/SS[c]",
        "     + 1e-4 · Σ_{c,h} st[c,h]",
        "",
        "R = (1/n_R) · Σ_{k∈{op,pick,carr}} Σ_{h∈W, disp[h,k]>0} Σ_{c: req[c,k]>0}",
        "                                          ( req[c,k] / disp[h,k] ) · a[c,h]",
        "    n_R = nº de pares (h∈W, k∈{op,pick,carr}) con disp[h,k] > 0",
        "S = Σ_{c,h: dens[c]>0} ( 1/dens[c] ) / (A·H) · I[c,h]          (= media_h de m² ocupados / A)",
        "Q = (1/|W|) · Σ_{h∈W} Σ_{k∈{mto,cal}: disp[h,k]>0} Σ_c",
        "                                    ( req[c,k] / disp[h,k] / n_k(h) ) · a[c,h]",
        "    n_k(h) = nº de recursos de {mto, cal} con disp[h,k] > 0",
        "B = Σ_{c,h} short[c,h] / ( k·SS[c] ) / ( |P|·H )",
        "E = Σ_{c∈P,h: kW[c]>0} f[h]·kW[c] / den_E · u[c,h]",
        "    den_E = |W| · Σ_c kW[c] · máx(f_solar, f_noche, 1)"])]
    E += [P(f"Constantes de {K('modelo.py')}: {K('PENALIZACION')} = {fm(modelo.PENALIZACION, 0)}, {K('PENALIZACION_FINAL')} = {fm(modelo.PENALIZACION_FINAL, 0)}, "
            f"{K('PESO_ARRANQUES')} = {modelo.PESO_ARRANQUES:g}, {K('TOL_HOLGURA')} = {modelo.TOL_HOLGURA:g}. "
            f"Demo: n_R = {n_r}, A·H = {fm(d.A * H, 0)}, |W| = {nW}, den_E = {nW}·{fm(kw_sum, 2)}·{fm(d.fmax, 2)} = {fm(den_e, 1)}.")]
    E += [H3("Puntuación y contribuciones (modelo.evaluar)")]
    E += [formulas([
        "puntuación     = 100 · ( 1 − Σ_{x∈{R,S,Q,B,E}} w_x · x )",
        "                 (no depende de los términos auxiliares del objetivo)",
        "contribución_x = 100 · w_x · (1 − x)      →  Σ_x contribución_x = puntuación (máx. 100)",
        "En evaluar (a partir de a, u):",
        "  stock = I0 + cumsum(cap·u − env);  espacio = Σ_c stock/dens;  usado = a · req",
        "  R, Q: _fracciones_recursos(usado, disp, W);   S = media(espacio / A)",
        "  B = media( máx(0, (1+k)·SS − stock) / (k·SS) );   E = Σ_h f[h]·Σ_c kW[c]·u[c,h] / den_E"])]
    E += [P("Interpretación: todos los componentes se <b>minimizan</b>; una puntuación alta significa poco uso de recursos, poco almacén, margen libre de "
            "Mantenimiento y Calidad, ausencia de déficit sobre el colchón y baja energía ponderada. Como la producción sólo se necesita para reponer "
            "los envíos, el óptimo tiende a producir lo justo y a colocarlo en horas baratas en energía (franja solar).", "small")]
    # ---------------------------------------------------------------- 5.5
    E += [H2("5.5 Resolución con HiGHS (módulo modelo)")]
    E += [formulas([
        "solver = pulp.HiGHS(msg=False, timeLimit=tl, gapRel=gap_relativo, warmStart=caliente)",
        "tl = parámetro tiempo_limite_s (30 s);  gap_relativo = parámetro (0,001);  prob.solve(solver)",
        "ms   = prob.solverModel.getModelStatus() ;   info = getInfo()",
        "óptimo   = (ms == kOptimal)",
        "hay_sol  = óptimo  ó  ( ms ∈ {kTimeLimit, kIterationLimit, kSolutionLimit, kInterrupt}",
        "                        y  info.primal_solution_status == 2 )",
        "si no hay_sol  →  ModeloInfactible",
        "gap      = máx(0, info.mip_gap) si es finito;  en otro caso 0 si óptimo y 1 si no",
        "estado   = OPTIMO si óptimo, si no FACTIBLE;   INVIABLE si Σ s > 1e-6  ó  Σ sa > 1e-6"])]
    E += [P(f"<b>Arranque en caliente.</b> Sólo en el primer plan del Top-K ({K('motor.recomendar')} pasa {K('inicial=baseline.activacion')} si no hay cortes): "
            f"{K('setInitialValue')} sobre las variables a con la activación del plan de referencia (no se da valor inicial a u ni I). "
            "En los planes siguientes no se usa.")]
    E += [H3("Postproceso")]
    E += bullets([
        f"Las binarias se redondean ({K('round(a.value())')}); u se acota a [0, a] y se pone a 0 si &lt; 1e-7.",
        f"Las holguras sumadas se guardan en {K('plan.holguras')} ({K('stock')}, {K('espacio')}).",
        f"{K('modelo.evaluar')} <b>recalcula todo</b> desde (a, u): stock, espacio, recursos, energía, KPIs y puntuación; {K('validador.validar')} comprueba las reglas; "
        f"{K('viable')} = lista de incumplimientos vacía; si no es viable y el estado era OPTIMO/FACTIBLE pasa a INVIABLE.",
        f"{K('plan.objetivo')} = valor objetivo del solver (incluye penalizaciones); {K('plan.tiempo_s')} = tiempo total de {K('resolver')}."])
    E += [tabla([["Estado", "Condición exacta", "idoneidad / gap", "Se lista como"],
                 ["OPTIMO", "HiGHS devuelve kOptimal (gap ≤ gap_relativo) y el plan es viable", "100·(1 − gap) / gap de HiGHS", "Top n"],
                 ["FACTIBLE", "Hay solución pero HiGHS paró por tiempo/iteraciones; el plan es viable", "100·(1 − gap) con el gap real", "Top n"],
                 ["INVIABLE", "Σ holguras s o sa &gt; 1e-6, o el validador encuentra incumplimientos", "None / None (se muestra «—»)", "Plan de contingencia (nunca Top)"],
                 ["REFERENCIA", "Plan heurístico de baseline.plan_referencia", "None / None", "Referencia manual"],
                 ["EVALUADO", "Estado por defecto de modelo.evaluar para una activación dada", "100·(1 − gap_dado), gap por defecto 0", "—"]],
                [2.2 * cm, 7.4 * cm, 4.2 * cm, 3.2 * cm], ["L"] * 4, fs=7.2, valign="TOP")]
    E += [P("<b>Idoneidad</b> = 100·(1 − gap relativo de HiGHS), acotada a [0, 100]; el gap es la distancia relativa entre la mejor solución y la cota inferior del "
            "branch-and-bound sobre el objetivo completo (incluidas penalizaciones). Con el gap aceptado de 0,1 %, el plan es óptimo garantizado ±0,1 % "
            "salvo que el solver pare por tiempo, caso en que se informa del gap real.", "body")]
    # ---------------------------------------------------------------- 5.6
    E += [KeepTogether([H2("5.6 Top-K por cortes no-good (módulo motor, función recomendar)"), fig(F["topk"], 17.0, 9),
                        P("Figura 5.3 · Bucle Top-K de motor.recomendar.", "cap")])]
    E += [formulas([
        "hz        = construir_horizonte(esc, inicio, horas)",
        "baseline  = plan_referencia(esc, hz)",
        "para j = 0 .. K−1 (K = 3 por defecto):",
        "    plan = resolver(esc, hz, cortes, inicial = baseline.activacion si cortes = [] en otro caso None)",
        "    si ModeloInfactible  →  salir del bucle",
        "    plan.nombre = «Top j+1»",
        "    si no plan.viable:  si top = []  →  contingencia = plan (nombre «Plan de contingencia»)",
        "                        salir del bucle",
        "    top.append(plan);   cortes.append( frozenset(plan.config_turno_actual) )",
        "top.sort(key = puntuacion, descendente);  renombrar Top 1..",
        "si top = [] y contingencia = None  →  baseline.estado = INVIABLE; contingencia = baseline"])]
    E += bullets([
        f"<b>Configuración del turno actual</b> S_j = {K('config_turno_actual')} = conjunto de células con a = 1 en al menos un slot de {K('Horizonte.slots_turno_actual')} "
        f"(tramo contiguo desde el slot 0 con la misma fecha_turno y turno; aquí {turno_n} slots), incluida la célula 10.",
        f"<b>Enlace y corte</b> (C12–C13): {K('y_c')} vale 1 si la célula se usa en el turno actual; el corte prohíbe exactamente la configuración S_j ya obtenida "
        f"(distancia de Hamming ≥ 1). Las variables y y sus filas sólo existen si {K('cortes')} no está vacío.",
        "<b>Cada plan</b> de la lista se resuelve independientemente con el mismo horizonte; las configuraciones de turnos futuros pueden coincidir entre planes.",
        f"<b>Condiciones de parada</b>: j = K; {K('ModeloInfactible')} (HiGHS sin solución); primer plan no viable (contingencia); plan no viable posterior (se descarta y se detiene). "
        f"Si {K('len(top) < K')} se emite la alerta «Sólo se han encontrado n configuraciones viables distintas».",
        "<b>Reordenación</b>: el objetivo MILP incluye términos auxiliares (arranques, cobertura final), por lo que el orden de resolución puede no coincidir con el de puntuación. "
        "Se ordena por puntuación global (definición de KWD de Top 1) y se renombra. Las alternativas Top 2/3 son las mejores sin repetir configuración, no los segundos y terceros "
        "óptimos globales exactos.",
        f"<b>Contingencia</b>: si el primer plan no es viable no existe configuración que cumpla las reglas obligatorias; se devuelve ese plan como {K('contingencia')} con "
        f"sus incumplimientos, nunca como recomendación. {K('Recomendacion.mejor')} devuelve el Top 1 o, en su defecto, la contingencia."])
    # ---------------------------------------------------------------- 5.7
    E += [H2("5.7 Plan de referencia (módulo baseline, función plan_referencia)")]
    E += [P("Heurística que imita a un planificador sin optimizador. Sirve para cuantificar el impacto del motor y como arranque en caliente. Algoritmo exacto:")]
    E += [formulas([
        f"HORIZONTE_MIRADA = {baseline.HORIZONTE_MIRADA};   objetivo[c] = (1 + k)·SS[c];   stock = I0",
        "para h = 0 .. H−1:",
        "  si h ∈ W:",
        "    libre = disp[h, :]",
        "    si la célula 10 es exigida en h:  a[h,10] = 1;  libre −= req[10, :]",
        "    fin = mín(H, h + 8);   previsto = stock − Σ_{t=h}^{fin−1} env[t, :]   (sin producir más)",
        "    si fin = H:  previsto −= env_final                                         (F18)",
        "    hechas = ∅",
        "    para c ∈ P en orden creciente:",
        "      si c ∈ hechas o c bloqueada en h:  continuar",
        "      grupo = [11, 12] si c ∈ {11, 12}, si no [c];   si alguna del grupo está bloqueada: continuar",
        "      hechas ∪= grupo",
        "      si ninguna del grupo tiene previsto < objetivo:  continuar",
        "      si Σ_{g∈grupo} req[g, :] ≤ libre (5 recursos):  libre −= req;  a[h,g] = 1, u[h,g] = 1 ∀g",
        "  stock += cap · u[h, :] − env[h, :]",
        "evaluar(esc, hz, a, u, estado = «REFERENCIA»)"])]
    E += [P("Propiedades: activa a tope (u = 1) cualquier célula cuya pieza quedaría por debajo de 1,1·SS en 8 h, por orden de número de célula y mientras haya recursos; no "
            "considera el espacio (regla 8), el stock de seguridad (regla 7), la energía ni la franja solar; una célula descartada por falta de recursos no se reintenta en esa hora. "
            "Por ello suele activar muchas más células que el óptimo y puede incumplir reglas (se informa de ello en §7).", "small")]
    return E


# ------------------------------------------------------------------------------------- 5. metodología (c)
def seccion_metodologia_c(X, D, F):
    esc, rec06, sem = X["esc"], X["rec06"], X["sem"]
    E = [H2("5.8 Validador independiente (módulo validador)")]
    E += [P(f"{K('validador.validar')}(esc, hz, plan) recalcula las reglas obligatorias y la puntuación con <b>bucles explícitos</b> sobre {K('plan.activacion')} y "
            f"{K('plan.uso')}, sin reutilizar el modelo MILP ni {K('modelo.evaluar')}. Devuelve la lista de incumplimientos (texto en español con regla, célula y hora); una lista "
            f"vacía es el <b>certificado de factibilidad</b>. Tolerancias: stock {validador.TOL_STOCK:g} piezas, espacio {validador.TOL_ESPACIO:g} m², binarias {validador.TOL_BIN:g}, "
            f"puntuación {validador.TOL_PUNT:g}.")]
    val = [["Comprobación", "Condición que se verifica", "Tipo"],
           ["Regla 1", "a ∈ {0,1} (|a| ≤ tol ó |a − 1| ≤ tol);  0 ≤ u ≤ 1;  u ≤ a", "Violación"],
           ["Regla 2", "a[10,h] = 1 donde logistica_exigida[h]; ninguna célula activa en slots no laborables", "Violación"],
           ["Regla 3", "|a[11,h] − a[12,h]| ≤ tol y |u[11,h] − u[12,h]| ≤ tol", "Violación"],
           ["Regla 4", "a[c,h] = 0 para todo slot de bloqueos[c] (baja o mantenimiento)", "Violación"],
           ["Regla 5", "Σ_c req[c,r]·a[c,h] ≤ disp[h,r] + 1e-6 para los 5 recursos en slots laborables", "Violación"],
           ["Regla 6", "Balance de stock: I = I0 + cap·u − env recalculado coincide con plan.stock (tol 1e-3)", "Violación"],
           ["Regla 7", "stock ≥ SS − 1e-3 en toda pieza y slot", "Violación"],
           ["Regla 8", "Σ stock/dens ≤ A + 1e-3 m² en todo slot", "Violación"],
           ["Puntuación", "Recalcula R, S, Q, B, E y la puntuación (con sus propias fórmulas) y las compara con plan.componentes (tol 1e-4) y plan.puntuacion (tol 1e-2)", "Violación"],
           [K("avisos_plan"), "Stock del último slot ≥ SS + envio_final − 1e-3 (condición terminal F18)", "Aviso (no invalida el plan)"]]
    E += [tabla(val, [2.6 * cm, 11.0 * cm, 3.4 * cm], ["L", "L", "L"], fs=7.3)]
    E += [P("Alcance y límites: el validador comparte con el modelo los datos del escenario y el horizonte (envíos, disponibilidades, bloqueos). Certifica que el plan cumple "
            "las reglas <i>dado ese horizonte</i>, no que el horizonte sea correcto. Se invoca en cada {evaluar} (viable = lista vacía) y en las pruebas.".replace("{evaluar}", K("modelo.evaluar")), "small")]
    # ---------------------------------------------------------------- 5.9
    E += [H2("5.9 Explicación automática y alertas (módulo motor)")]
    E += [P(f"{K('motor._explicar')} produce el diccionario {K('explicacion')} con tres bloques sobre el turno actual del plan principal (Top 1 o contingencia). "
            f"{K('motor.franjas')} agrupa slots contiguos en franjas «HH:MM-HH:MM».")]
    E += [H3("«Qué activar»")]
    E += [P(f"DataFrame con una fila por célula activa al menos una hora del turno actual: célula, tipo, horas activas, franja, piezas producidas en esas horas, los 5 recursos que exige y "
            f"las horas dentro de la franja solar.")]
    E += [H3("«Por qué» (una frase por célula; reglas aplicadas en este orden)")]
    por = [["Caso", "Texto generado", "Regla del código"],
           ["Célula 10 activa", "«Célula 10: servicio logístico, obligatoria en todas las horas laborables.»", "c = CELULA_LOGISTICA"],
           ["Célula activa", "«Célula c activa franjas: motivo; solar.»", "ver abajo"],
           ["   motivo = riesgo", "«su pieza caería por debajo del stock de seguridad a las HH:MM (dd/mm) sin producir»", f"existe un slot h1 donde I0 − cumsum(env) &lt; SS − 1e-9 ({K('_primer_incumplimiento_sin_produccion')}, en todo el horizonte)"],
           ["   motivo = colchón", "«repone el colchón de stock de seguridad (objetivo +10 %)»", "no existe tal h1"],
           ["   anticipación", "«Se adelanta producción de la célula c por mantenimiento|baja en turno X»", "existen slots bloqueados posteriores al último slot del turno; X = turno del primero; «mantenimiento» si la célula figura en la hoja Mantenimientos"],
           ["   solar", "«colocada en franja solar (n de m h)» o «fuera de franja solar»", "n = horas activas con solar_ini ≤ hora &lt; solar_fin"],
           ["Célula inactiva con bloqueo", "«Célula c inactiva: BAJA/MANTENIMIENTO en el turno.»", "hay slots bloqueados dentro del turno actual"],
           ["Célula inactiva sin bloqueo", "«Célula c inactiva: stock suficiente hasta HH:MM (dd/mm)» o «hasta todo el horizonte»", "mismo h1 de arriba"]]
    E += [tabla(por, [3.4 * cm, 7.2 * cm, 6.4 * cm], ["L", "L", "L"], fs=7.0, valign="TOP")]
    E += [H3("«Con qué impacto»")]
    E += [P(f"{K('explicacion[\"impacto\"]')} contiene el resumen del plan, del baseline y de cada alternativa (puntuación, idoneidad, estado, horas-operario, horas-picking, "
            f"horas-carretillero, m² medio y pico, kWh de red y bruto, % solar y demanda cubierta) y los deltas: <b>Δ vs baseline</b> (horas-operario, m² medio, kWh, puntuación) y "
            f"<b>Δ vs alternativas</b> (diferencia de puntuación con cada Top 2/3). Un Δ negativo en horas, m² o kWh es un ahorro frente a la referencia manual.")]
    E += [H3("Alertas (motor._alertas)")]
    al = [["Alerta", "Condición y umbral"],
          ["Alertas del horizonte", "Capacidad de expedición insuficiente (escalado a 15 m²); célula 10 no garantizable (F14)"],
          ["PLAN INVIABLE", "Existe contingencia: se añade el mensaje, los primeros 8 incumplimientos («Incumplimiento: …») y «… y n incumplimientos más»"],
          ["Sólo n configuraciones", "len(top) &lt; K y no hay contingencia"],
          ["Condición terminal", "plan.avisos: stock final &lt; SS + envio_final − 1e-3 (por célula)"],
          ["Colchón objetivo", "algún stock &lt; (1 + k)·SS − 1e-6 → «Stock por debajo del colchón objetivo (SS +10%) en las células […]»"],
          ["Ocupación de almacén", "máx(espacio) &gt; 0,9·A → «Ocupación … % (&gt; 90 %)»"],
          ["Recurso al 100 %", "para operarios, picking y carretilleros: nº de horas laborables con usado ≥ disp − 1e-9 y disp &gt; 0"]]
    E += [tabla(al, [4.2 * cm, 12.8 * cm], ["L", "L"], fs=7.4)]
    # ---------------------------------------------------------------- 5.10
    E += [H2("5.10 KPIs: definición de cada indicador (modelo._kpis_tramo, _kpis)")]
    E += [P("Los KPIs se calculan para el plan completo y para cada turno del horizonte (tabla {resumen_turnos}). Para un tramo de slots idx:".replace("{resumen_turnos}", K("resumen_turnos")))]
    kp = [["KPI (clave)", "Definición"],
          [K("horas, horas_laborables"), "Nº de slots del tramo y nº de ellos laborables"],
          [K("chasis_ve_a_expedir, chasis_comb_a_expedir"), "Σ chasis_ve[h], Σ chasis_comb[h] del tramo (tras el escalado a 15 m²)"],
          [K("chasis_*_cubiertos, demanda_cubierta_pct"), "falt[c] = máx(0, −mín stock[c]) / ppc; cubiertos = a_expedir − mín(a_expedir, máx falt del tipo); % = 100·(cubiertos VE + COMB)/(a expedir VE + COMB). En planes viables es 100 % porque el stock nunca es negativo"],
          [K("<recurso>_horas"), "Σ usado[h, r] sobre los slots laborables (horas-persona)"],
          [K("<recurso>_ocup_media_pct, _pico_pct"), "100·media y 100·máx de usado/disp sobre slots laborables con disp &gt; 0"],
          [K("m2_medio, m2_pico, m2_medio_pct, m2_pico_pct"), "Media y máximo del espacio (m²) y su porcentaje sobre A = 800 m²"],
          [K("stock_min, stock_min_vs_ss"), "Mínimo del stock de cada pieza en el tramo y su cociente con SS"],
          [K("cobertura_horas"), "stock_min / media de env por slot del tramo (horas de cobertura); None si el envío medio es 0"],
          [K("kwh_total"), "Σ energia_kwh (energía de red, con factor horario, F16)"],
          [K("kwh_bruto, kwh_solar, kwh_solar_pct, kwh_noche"), "Σ kW·u sin factor; la parte en franja solar y su % sobre la bruta; la parte en 22–06"],
          [K("piezas_producidas, horas_celula, celulas_activas"), "Σ producción; Σ a (horas-célula); lista de células con a &gt; 0"],
          [K("puntuacion, idoneidad"), "Puntuación global del plan e idoneidad (None en INVIABLE/REFERENCIA)"],
          [K("arranques"), "Σ máx(0, a[h] − a[h−1]) con a[−1] = 0 (todas las células, incluida la 10)"],
          [K("horas_celula_productivas"), "Σ a sobre las células de P"],
          [K("stock_min_ratio_ss"), "(sólo por turno) mínimo de stock_min_vs_ss entre piezas"]]
    E += [tabla(kp, [5.6 * cm, 11.4 * cm], ["L", "L"], fs=7.2, valign="TOP")]
    # ---------------------------------------------------------------- 5.11
    E += [H2("5.11 Horizonte rodante: reconfiguración y simulación semanal (módulo rolling)")]
    E += [P(f"{K('rolling.aplicar_evento')}(esc, evento) devuelve una <b>copia</b> del escenario ({K('Escenario.copiar')}) con el evento aplicado; el original no se modifica.")]
    ev = [["Evento", "Datos", "Efecto sobre el escenario"],
          [K("baja_celula"), "celula, desde, hasta", "Añade una fila BAJA a Disponibilidad (bloqueo por solape, F13)"],
          [K("alta_celula"), "celula", "Elimina todas las filas de Disponibilidad de esa célula (también bajas futuras)"],
          [K("recursos_reales"), "fecha, turno, valores{recurso: n}", "Sustituye la fila (fecha, turno) de RecursosReales conservando los valores previos de los recursos no indicados"],
          [K("correccion_demanda"), "fecha, ve, comb", "Sustituye la fila diaria de CorreccionDiaria"],
          [K("expedicion_real"), "fecha_hora, ve, comb", "Añade una fila a Expediciones (sustituye al camión previsto ±45 min o añade uno extra, F15)"],
          [K("mantenimiento"), "fecha, turno, celula, tecnicos (0 si no se da)", "Añade una fila a Mantenimientos (bloqueo + resta técnicos de Mto)"],
          [K("stock_real"), "{celula: piezas}", f"Fija en StockActual el stock de las células indicadas ({K('_fijar_stock')})"]]
    E += [tabla(ev, [3.2 * cm, 4.6 * cm, 9.2 * cm], ["L", "L", "L"], fs=7.3)]
    E += [formulas([
        "reconfigurar(esc, rec, evento, ahora):",
        "  ahora = ahora.floor(hora);  plan = rec.mejor;  n = (ahora − rec.horizonte.inicio) / 1 h",
        "  e2 = copia(esc)",
        "  si plan ≠ None y n ≥ 1:  n = mín(n, len(plan.stock));",
        "                           stock de todas las piezas = plan.stock[fila n−1]",
        "                           (stock al cierre de la hora anterior a «ahora»)",
        "  si n ≤ 0: el stock del escenario ya corresponde al inicio del plan vigente (no se toca)",
        "  e2 = aplicar_evento(e2, evento)         (un evento stock_real sobrescribe después el stock)",
        "  devolver recomendar(e2, ahora, top_k = 3)         (24 h nuevas desde «ahora»)"])]
    E += [formulas([
        "simular_semana(esc, lunes):",
        "  inicio = lunes 06:00;  e = copia(esc)",
        "  e.tiempo_limite_s = mín(tl, 10);  e.gap_relativo = máx(gap, 0,005)",
        "  para it = 1 .. 15:",
        "    rec = recomendar(e, inicio, top_k = 1);  plan = rec.mejor;  idx = slots del turno actual (8)",
        "    fila = plan.resumen_turnos[0] + {iteracion, estado, idoneidad, puntuacion_plan_24h,",
        "                                     configuracion, puntuacion_baseline, tiempo_s}",
        "    e.stock = plan.stock[último slot de idx]                (consolida las 8 h del turno actual)",
        "    inicio += len(idx) horas",
        "  devolver DataFrame de 15 filas"])]
    E += [P("La simulación consolida únicamente el turno actual de cada plan de 24 h (horizonte rodante: se replanifica cada turno con el stock resultante), no aplica incidencias y "
            "emplea el límite de 10 s y gap 0,5 % para acotar el tiempo (15 resoluciones). Si el plan principal fuese una contingencia, su stock también se consolidaría.", "small")]
    # ---------------------------------------------------------------- 5.12
    E += [H2("5.12 Complejidad y rendimiento medido")]
    tam = [["Caso", "Plan", "Variables", "Binarias", "Restricciones", "Tiempo de solve (s)"]]
    for nom, key in [("Demo 06:00", "st_rec06"), ("Demo 14:00", "st_rec14"), ("Contingencia 06:00", "st_recc"), ("Reconfiguración 10:00", "st_rc")]:
        for j, s in enumerate(X[key]):
            tam.append([nom if j == 0 else "", f"solve {j + 1}", fm(s["vars"], 0), fm(s["bins"], 0), fm(s["cons"], 0), fm(s["t"], 2)])
    E += [P(f"Tamaño y tiempo medidos con el modelo construido por {K('modelo.resolver')} (se contabilizan en cada llamada a {K('LpProblem.solve')}; el orden es el de resolución, "
            f"antes de la reordenación por puntuación):")]
    E += [tabla(tam, [3.6 * cm, 2.2 * cm, 2.6 * cm, 2.4 * cm, 3.0 * cm, 3.2 * cm], ["L", "L", "R", "R", "R", "R"], fs=7.4)]
    casos = []
    for r, etq in [(X["rec06"], "06:00"), (X["rec14"], "14:00")]:
        for p in sorted(r.top, key=lambda p: p.nombre):
            casos.append((f"{etq} {p.nombre}", p.tiempo_s, p.estado))
    casos.append(("Contingencia", X["recc"].contingencia.tiempo_s, "INVIABLE"))
    for p in sorted(X["rec_rc"].top, key=lambda p: p.nombre):
        casos.append((f"Reconf. {p.nombre}", p.tiempo_s, p.estado))
    for _, f in sem.iterrows():
        casos.append((f"Sem. {int(f['iteracion'])}", f["tiempo_s"], f["estado"]))
    g_tiempos(casos, F["tiempos"])
    E += [figc(F["tiempos"], "Figura 5.4 · Tiempo de pared por plan (resolver + evaluar) en cada caso; en la simulación semanal es el tiempo total de la iteración (incluye baseline).", alto_max_cm=7.5)]
    tp = [p.tiempo_s for p in rec06.top] + [p.tiempo_s for p in X["rec14"].top]
    E += bullets([
        f"<b>Tamaño</b>: O(|C|·H) variables y binarias (16·24 = 384 de activación, +16 de enlace con cortes) y O(|P|·H) restricciones; el modelo es pequeño para HiGHS "
        f"(≈{fm(X['st_rec06'][0]['vars'], 0)} variables).",
        f"<b>Tiempos medidos</b>: Top 1/2/3 de las 06:00 en {fm(rec06.tiempo_total_s, 1)} s en total; de las 14:00 en {fm(X['rec14'].tiempo_total_s, 1)} s; contingencia en "
        f"{fm(X['recc'].tiempo_total_s, 2)} s; reconfiguración completa (3 planes) en {fm(X['rec_rc'].tiempo_total_s, 1)} s; semana completa (15 iteraciones) en {fm(X['t_sem'], 0)} s "
        f"(máx. por iteración {fm(sem['tiempo_s'].max(), 1)} s; límite de la prueba &lt; 15 s).",
        "<b>Dificultad</b>: la degeneración y las simetrías (células con perfiles similares) dificultan cerrar el gap hasta 0; por eso se acepta 0,1 %. Los tiempos de resolución no son "
        "deterministas (hilos de HiGHS) y varían entre ejecuciones.",
        "<b>Escalado</b>: no se ha medido con horizontes de 48 h o más; el coste crece con H por el número de binarias. El límite de tiempo (30 s, 10 s en la semana) garantiza respuesta, "
        "devolviendo el mejor plan factible con su gap real."])
    return E


# ------------------------------------------------------------------------------------- 6. ejemplo numérico
def _cfg(l):
    return ", ".join(str(int(c)) for c in l) or "—"


def _tabla_planes(rec, esc, con_baseline=True):
    f = [["Plan", "Estado", "Punt.", "Idon.", "Gap", "t (s)", "Células del turno actual", "m² med.", "kWh red", "h-oper."]]
    ps = list(rec.top) + ([rec.contingencia] if rec.contingencia is not None and not rec.top else [])
    if con_baseline:
        ps.append(rec.baseline)
    for p in ps:
        k = p.kpis
        f.append([p.nombre, p.estado, fm(p.puntuacion, 2), p.idoneidad_txt().replace(".", ","),
                  "—" if p.gap is None else fm(100 * p.gap, 3) + " %", fm(p.tiempo_s, 1) if p.tiempo_s else "—",
                  _cfg(p.config_turno_actual), fm(k["m2_medio"], 1), fm(k["kwh_total"], 0), fm(k["operarios_horas"], 1)])
    return tabla(f, [2.0 * cm, 2.2 * cm, 1.3 * cm, 1.5 * cm, 1.5 * cm, 0.9 * cm, 3.8 * cm, 1.3 * cm, 1.2 * cm, 1.3 * cm],
                 ["L", "L", "R", "R", "R", "R", "L", "R", "R", "R"], fs=6.8)


def _tabla_que(rec):
    q = rec.explicacion["que"]
    f = [["Cél.", "Tipo", "Horas", "Franja", "Piezas", "Oper.", "Pick.", "Carr.", "Mto", "Cal.", "H solar"]]
    for _, r in q.iterrows():
        f.append([int(r["celula"]), r["tipo"], int(r["horas_activas"]), r["franja"], fm(r["piezas"], 0), fm(r["operarios"], 1),
                  fm(r["picking"], 1), fm(r["carretilleros"], 1), fm(r["mto"], 2), fm(r["calidad"], 2), int(r["horas_franja_solar"])])
    return tabla(f, [1.0 * cm, 1.2 * cm, 1.2 * cm, 4.6 * cm, 1.6 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.2 * cm, 1.3 * cm],
                 ["C", "C", "C", "L", "R", "R", "R", "R", "R", "R", "C"], fs=7.0)


def _txt_noche(rec):
    p = rec.top[0]
    s = rec.horizonte.slots
    n = (s["turno"] == "N").values
    act = [c for c in p.activacion.columns if p.activacion.loc[n, c].sum() > 0]
    act_h = [h for h in range(len(s)) if p.activacion.iloc[h].drop(labels=[10], errors="ignore").sum() > 0]
    ini = f"{s['inicio'].iloc[min(act_h)]:%H:%M}" if act_h else "—"
    fin = f"{s['fin'].iloc[max(act_h)]:%H:%M}" if act_h else "—"
    if act == [10]:
        return f"La producción de las células productivas se reparte entre las {ini} y las {fin}; en el turno de noche sólo está activa la célula 10."
    return f"En el turno de noche están activas las células {', '.join(map(str, act))}."


def seccion_ejemplo(X, D, F):
    esc, rec06, rec14, recc, sem = X["esc"], X["rec06"], X["rec14"], X["recc"], X["sem"]
    t = D["tc"]
    hz06 = rec06.horizonte
    E = H1("6. Ejemplo numérico trabajado")
    E += [P("Se recorre el cálculo completo con el escenario de demostración "
            f"({K('data/entrada_ejemplo.xlsx')}): semana del 28/09/2026, corrección diaria del 02/10 (520 VE y 300 COMB), mantenimiento de la célula 13 en el turno T del 02/10 y 15 operarios reales en ese turno.")]
    # ------------------------------------------------------------ 6.1
    E += [H2("6.1 Capacidades, stock de seguridad y stock inicial por célula")]
    dem_ve, dem_cb = D["dem_dia"]
    f = [["Cél.", "Tipo", "Ciclo (s)", "Cap. (pz/h)", "Cap. (pz/turno 8 h)", "pz/m²", "SS (pz)", "SS (m²)", "Stock inicial (pz)", "Stock inicial (m²)", "Envío día (pz)", "h-cél./día"]]
    s_ss = s_i0 = s_h = 0.0
    for c in sorted(t.index):
        r = t.loc[c]
        if c == 10:
            f.append([10, "COMB*", fm(r["ciclo_s"], 0), "—", "—", "0", "—", "—", "—", "—", "—", "—"])
            continue
        cap = float(r["cap_h"])
        ss_m2 = D["ss"][c] / r["piezas_m2"]
        i0_m2 = D["i0"][c] / r["piezas_m2"]
        env = (dem_ve if r["es_ve"] else dem_cb) * r["ppc"]
        s_ss += ss_m2
        s_i0 += i0_m2
        s_h += env / cap
        f.append([c, r["tipo"], fm(r["ciclo_s"], 0), fm(cap, 1), fm(8 * cap, 0), fm(r["piezas_m2"], 0), fm(D["ss"][c], 0), fm(ss_m2, 2),
                  fm(D["i0"][c], 0), fm(i0_m2, 2), fm(env, 0), fm(env / cap, 2)])
    f.append(["Σ", "", "", "", "", "", fm(sum(D["ss"].values()), 0), fm(s_ss, 2), fm(sum(D["i0"].values()), 0), fm(s_i0, 1), "", fm(s_h, 1)])
    E += [tabla(f, [1.0 * cm, 1.3 * cm, 1.3 * cm, 1.5 * cm, 1.8 * cm, 1.2 * cm, 1.2 * cm, 1.3 * cm, 1.6 * cm, 1.6 * cm, 1.6 * cm, 1.6 * cm],
                ["C"] * 12, fs=6.9, destacar_filas=(len(f) - 1,))]
    E += [P("<b>Cálculo.</b> Capacidad = 3600 / ciclo (F8): célula 3 → 3600/60 = 60 pz/h. SS = 400 (VE) o 200 (COMB); SS en m² = SS / (piezas por m²): célula 3 → 400/31 = 12,90 m². "
            f"Stock inicial = round(1,8·SS) = 720 (VE) o 360 (COMB) (F19). «Envío día» = chasis del día × piezas por conjunto (520 por célula VE, 300 por célula COMB); "
            f"«h-célula/día» = envío / capacidad: horas de producción de la célula necesarias para reponer lo que sale ese día. (*) La célula 10 figura como COMB en la tabla pero no produce.", "small")]
    E += [aviso(f"<b>Validación del SS.</b> ΣSS/densidad = <b>{fm(s_ss, 2)} m²</b> frente a los ≈150 m² que cita KWD (desviación {fm(100 * abs(s_ss - 150) / 150, 1)} %): "
                f"confirma F1/F2/F10. El stock inicial ocupa {fm(s_i0, 1)} m² de los {fm(D['A'], 0)} m² disponibles ({fm(100 * s_i0 / D['A'], 1)} %).")]
    # ------------------------------------------------------------ 6.2
    E += [H2("6.2 Demanda, camiones y m² por camión")]
    cargas = [["Concepto", "Valor"],
              ["Demanda del día 02/10 (corrección diaria)", f"{fm(dem_ve, 0)} chasis VE y {fm(dem_cb, 0)} chasis COMB"],
              ["Demanda sin corrección (01/10): semanal ÷ 5", f"{fm(D['dem_sem_dia'][0], 0)} VE y {fm(D['dem_sem_dia'][1], 0)} COMB"],
              ["Carga prevista por camión (÷ 15)", f"{fm(dem_ve / 15, 3)} VE y {fm(dem_cb / 15, 3)} COMB"],
              ["m² por chasis VE", f"Σ_(VE) 1/dens = {fm(D['m2ve'], 4)} m² ({len(D['ve'])} piezas: células {', '.join(map(str, D['ve']))})"],
              ["m² por chasis COMB", f"Σ_(COMB) 1/dens = {fm(D['m2comb'], 4)} m² ({len(D['comb'])} piezas: células {', '.join(map(str, D['comb']))})"],
              ["m² por camión", f"{fm(dem_ve / 15, 2)} × {fm(D['m2ve'], 4)} + {fm(dem_cb / 15, 2)} × {fm(D['m2comb'], 4)} = {fm(dem_ve / 15 * D['m2ve'] + dem_cb / 15 * D['m2comb'], 2)} m² (tope 15 m²: sin escalado)"],
              ["Piezas por camión y célula", f"{fm(dem_ve / 15, 2)} (cada célula VE) y {fm(dem_cb / 15, 2)} (cada célula COMB)"]]
    E += [tabla(cargas, [6.0 * cm, 11.0 * cm], ["L", "L"], fs=7.6)]
    cam = hz06.camiones
    f = [["Camión", "Hora", "Slot", "Chasis VE", "Chasis COMB", "m²", "Escalado", "Real"]]
    for i, c in cam.iterrows():
        f.append([i + 1, f"{pd.Timestamp(c['hora']):%d/%m %H:%M}", int(c["slot"]), fm(c["ve"], 2), fm(c["comb"], 2), fm(c["m2"], 2),
                  "sí" if c["escalado"] else "no", "sí" if c["real"] else "no"])
    E += [P(f"Camiones del horizonte de las 06:00 ({len(cam)}): salen cada 1,5 h entre las 06:00 del viernes y las 03:00 del sábado.", "small")]
    E += [tabla(f, [1.6 * cm, 2.6 * cm, 1.4 * cm, 2.4 * cm, 2.6 * cm, 1.8 * cm, 2.0 * cm, 1.6 * cm], ["C"] * 8, fs=7.0)]
    E += [figc(F["camiones"], "Figura 6.1 · m² liberados por cada camión (tope de 15 m²).", alto_max_cm=6.5)]
    # ------------------------------------------------------------ 6.3
    t1 = rec06.top[0]
    kb = rec06.baseline
    E += [H2("6.3 Resultado a las 06:00 (viernes 02/10)")]
    E += [P(f"Horizonte 02/10 06:00 → 03/10 06:00 (24 h, todos los slots laborables). Turno actual: Mañana (8 slots). Cálculo total {fm(rec06.tiempo_total_s, 1)} s.")]
    E += [_tabla_planes(rec06, esc), Spacer(1, 4)]
    d1 = abs(rec06.top[0].puntuacion - rec06.top[1].puntuacion)
    E += [P(f"Las puntuaciones de los tres planes difieren en {fm(d1, 2)}–{fm(abs(rec06.top[0].puntuacion - rec06.top[2].puntuacion), 2)} puntos: son alternativas prácticamente equivalentes "
            f"(ver §8, tolerancia del gap). Frente al plan de referencia, el Top 1 mejora {fm(t1.puntuacion - kb.puntuacion, 2)} puntos con "
            f"{fm(kb.kpis['operarios_horas'] - t1.kpis['operarios_horas'], 1)} horas-operario menos.", "small")]
    comp = [["Criterio", "Peso", "Componente Top 1", "Contribución Top 1", "Componente referencia", "Contribución referencia"]]
    for c in COMPONENTES:
        comp.append([f"{c} · {NOMBRES_COMPONENTE[c]}", fm(100 * float(esc.parametros[config.PESO_PARAM[c]]), 0) + " %", fm(t1.componentes[c], 4), fm(t1.contribuciones[c], 2),
                     fm(kb.componentes[c], 4), fm(kb.contribuciones[c], 2)])
    comp.append(["<b>Puntuación</b>", "100 %", "", f"<b>{fm(t1.puntuacion, 2)}</b>", "", f"<b>{fm(kb.puntuacion, 2)}</b>"])
    E += [tabla(comp, [4.6 * cm, 1.4 * cm, 2.8 * cm, 2.9 * cm, 2.8 * cm, 2.5 * cm], ["L", "C", "R", "R", "R", "R"], fs=7.4, destacar_filas=(len(comp) - 1,))]
    E += [P("Lectura: la puntuación es 100 − 100·Σ w·componente. Un componente menor es mejor; por ejemplo R = media de usado/disponible de operarios, picking y carretilleros.", "small")]
    E += [figc(F["contrib06"], "Figura 6.2 · Contribución de cada criterio (Top 1, 2, 3 y referencia).", alto_max_cm=6)]
    E += [H3("Qué activar en el turno de mañana (Top 1)")]
    E += [_tabla_que(rec06)]
    E += [H3("Por qué (texto generado por motor._explicar)")]
    E += bullets(rec06.explicacion["porque"], "small") if False else [P("• " + m, "small") for m in rec06.explicacion["porque"]]
    E += [H3("Alertas")]
    E += [P("• " + m, "small") for m in rec06.alertas] or [P("Sin alertas.", "small")]
    E += [H3("Impacto frente al plan de referencia (24 h)")]
    fi = informes.filas_impacto(rec06)
    E += [tabla([["Indicador", "Top 1", "Referencia manual", "Diferencia"]] + [list(x) for x in fi], [6.0 * cm, 3.4 * cm, 4.0 * cm, 3.6 * cm], ["L", "R", "R", "R"], fs=7.4)]
    E += [figc(F["gantt06"], f"Figura 6.3 · Gantt del Top 1 a las 06:00. {_txt_noche(rec06)}", alto_max_cm=10.5)]
    E += [figc(F["stock06"], "Figura 6.4 · Stock de cada pieza dividido por su SS (Top 1, 06:00). Línea roja = SS (1,0); discontinua ámbar = colchón (1,1).", alto_max_cm=6.5)]
    E += [figc(F["almacen06"], "Figura 6.5 · Ocupación del almacén de producto terminado frente a la referencia manual.", alto_max_cm=5.8)]
    E += [figc(F["energia06"], "Figura 6.6 · Energía de red por hora (Top 1, 06:00).", alto_max_cm=5.8)]
    # ------------------------------------------------------------ 6.4
    t14 = rec14.top[0]
    E += [H2("6.4 Resultado a las 14:00 (viernes 02/10)")]
    E += [P(f"Horizonte 02/10 14:00 → 03/10 14:00: las 8 horas del sábado 06:00–14:00 no son laborables (a = 0, sin envíos); hay {len(rec14.horizonte.camiones)} camiones. La célula 13 "
            f"está en mantenimiento durante todo el turno T. Cálculo total {fm(rec14.tiempo_total_s, 1)} s.")]
    E += [_tabla_planes(rec14, esc), Spacer(1, 4)]
    E += [_tabla_que(rec14)]
    E += [P("• " + m, "small") for m in rec14.explicacion["porque"][:6]] + [P("(…resto de células en la salida del motor)", "small")]
    E += [P("Alertas: " + " | ".join(rec14.alertas), "small")]
    E += [figc(F["gantt14"], "Figura 6.7 · Gantt del Top 1 a las 14:00 (la zona rayada de la célula 13 es el mantenimiento).", alto_max_cm=10)]
    E += [figc(F["stock14"], "Figura 6.8 · Stock de cada pieza dividido por su SS (Top 1, 14:00).", alto_max_cm=6.5)]
    # ------------------------------------------------------------ 6.5
    pc = recc.contingencia
    E += [H2("6.5 Escenario de contingencia (célula 14 de baja, 06:00)")]
    E += [P(f"{K('data/escenario_contingencia.xlsx')}: la célula 14 está de baja del 02/10 06:00 al 03/10 06:00. Su pieza (VE, 720 piezas de stock, SS 400, 520 piezas por día de envíos) no puede reponerse: "
            f"el stock cae por debajo del SS en cuanto se han expedido 320 piezas. El MILP sólo encuentra soluciones con holgura de stock (suma de holguras = {fm(pc.holguras.get('stock', 0), 1)} piezas), "
            f"por lo que el plan se marca <b>INVIABLE</b>, idoneidad «—» y se presenta sólo como contingencia. Tiempo: {fm(pc.tiempo_s, 2)} s (una única resolución).")]
    E += [_tabla_planes(recc, esc), Spacer(1, 4)]
    inc = pc.incumplimientos
    E += [P(f"<b>{len(inc)} incumplimientos</b> detectados por el validador:", "small")]
    E += [tabla([["#", "Incumplimiento"]] + [[i + 1, m] for i, m in enumerate(inc)], [1.0 * cm, 16.0 * cm], ["C", "L"], fs=6.9)]
    E += [P("Alertas del motor (primeras líneas): " + " | ".join(recc.alertas[:3]), "small")]
    E += [figc(F["ganttc"], "Figura 6.9 · Plan de contingencia (no recomendable): la célula 14 aparece rayada (baja).", alto_max_cm=9.5)]
    E += [figc(F["stockc"], "Figura 6.10 · Stock dividido por SS en el plan de contingencia: la célula 14 (en rojo) cae por debajo de 1,0.", alto_max_cm=6.5)]
    # ------------------------------------------------------------ 6.6
    r2, ev = X["rec_rc"], X["evento"]
    ahora = X["ahora"]
    p2 = r2.mejor
    E += [H2("6.6 Reconfiguración: baja de la célula 3 a las 10:00")]
    E += [P(f"Evento: {K('Evento(\"baja_celula\", {celula: 3, desde: 10:00, hasta: 18:00})')} registrado a las 10:00 (la célula 3 estaba en el Top 1 de las 06:00 con producción de 10:00 a 14:00). "
            f"{K('rolling.reconfigurar')} toma el stock del plan vigente al final del slot 09:00–10:00 (fila n−1 = 3 del plan, con n = 4 horas desde el inicio), aplica la baja y recalcula 24 h desde las 10:00 "
            f"({fm(r2.tiempo_total_s, 1)} s, 3 planes).")]
    f = [["Cél.", "Stock a las 06:00 (escenario)", "Stock tras 4 h (plan Top 1, entrada de la reconfiguración)", "SS"]]
    for c in D["prods"]:
        f.append([c, fm(D["i0"][c], 0), fm(float(rec06.top[0].stock.iloc[3][c]), 1), fm(D["ss"][c], 0)])
    E += [tabla(f, [1.4 * cm, 4.6 * cm, 8.6 * cm, 2.4 * cm], ["C", "R", "R", "R"], fs=7.0)]
    E += [_tabla_planes(r2, esc, con_baseline=True), Spacer(1, 4)]
    antes, despues = rec06.top[0], p2
    sh = 4  # slots 10:00-14:00 del plan de las 06:00
    f = [["Cél.", "Horas activas 10:00–14:00 ANTES", "Horas activas 10:00–14:00 DESPUÉS"]]
    for c in sorted(t.index):
        a_ = int(antes.activacion.iloc[sh:sh + 4][c].sum())
        d_ = int(despues.activacion.iloc[0:4][c].sum())
        if a_ or d_:
            f.append([c, a_, d_])
    E += [tabla(f, [2.0 * cm, 7.5 * cm, 7.5 * cm], ["C", "C", "C"], fs=7.2)]
    E += [P(f"Antes: configuración del turno de mañana {_cfg(antes.config_turno_actual)}. Después (resto del turno, 10:00–14:00): {_cfg(despues.config_turno_actual)}. "
            f"La célula 3 queda bloqueada de 10:00 a 18:00; durante la baja sus envíos se cubren con el stock (≥ SS) y el plan nuevo la reactiva después de las 18:00 ({int(despues.activacion[3].sum())} h en el horizonte). Puntuación: antes {fm(antes.puntuacion, 2)} (24 h desde las 06:00), "
            f"después {fm(despues.puntuacion, 2)} (24 h desde las 10:00; horizontes distintos, no estrictamente comparables).", "small")]
    E += [figc(F["reconf"], "Figura 6.11 · Antes y después de la baja de la célula 3 (línea roja = instante de la reconfiguración).", alto_max_cm=13)]
    f = [["Baja a las 10:00 (8 h) de la célula…", "Resultado", "Puntuación", "Idoneidad", "Células turno actual"]]
    f.append(["3 (ejemplo)", p2.estado, fm(p2.puntuacion, 2), p2.idoneidad_txt().replace(".", ","), _cfg(p2.config_turno_actual)])
    for c, r in X["rc_otros"].items():
        p = r.mejor
        f.append([str(c), p.estado + (" (contingencia)" if not r.top else ""), fm(p.puntuacion, 2), p.idoneidad_txt().replace(".", ","), _cfg(p.config_turno_actual)])
    E += [P("Sensibilidad: la misma reconfiguración con otras células (calculada con el motor). La baja de la célula 13 es inviable porque, con el mantenimiento del turno T, la célula queda bloqueada de 10:00 a 22:00 "
            "y no queda ventana para producir su pieza.", "small")]
    E += [tabla(f, [4.4 * cm, 3.2 * cm, 2.2 * cm, 2.2 * cm, 5.0 * cm], ["L", "L", "R", "R", "L"], fs=7.2)]
    # ------------------------------------------------------------ 6.7
    E += [CondPageBreak(11 * cm), H2("6.7 Resumen semanal (simulación con horizonte rodante)")]
    E += [P(f"{K('rolling.simular_semana')}(esc, 28/09/2026): 15 iteraciones (M, T, N de lunes a viernes) desde el lunes 06:00; duración total {fm(X['t_sem'], 0)} s.")]
    f = [["It.", "T", "Fecha", "Estado", "Idon.", "P. 24 h", "P. ref.", "Δ", "t (s)", "m²", "kWh", "h-op.", "Stock/SS", "Células activas"]]
    for _, r in sem.iterrows():
        f.append([int(r["iteracion"]), r["turno"], f"{pd.Timestamp(r['fecha_turno']):%d/%m}", r["estado"], fm(r["idoneidad"], 1) + " %",
                  fm(r["puntuacion_plan_24h"], 1), fm(r["puntuacion_baseline"], 1), sg(r["puntuacion_plan_24h"] - r["puntuacion_baseline"], 1), fm(r["tiempo_s"], 1),
                  fm(r["m2_medio"], 0), fm(r["kwh_total"], 0), fm(r["operarios_horas"], 1), fm(r["stock_min_ratio_ss"], 2), _cfg(r["configuracion"])])
    E += [tabla(f, [0.7 * cm, 0.6 * cm, 1.1 * cm, 1.6 * cm, 1.4 * cm, 1.2 * cm, 1.2 * cm, 1.0 * cm, 0.9 * cm, 0.9 * cm, 1.1 * cm, 1.0 * cm, 1.3 * cm, 3.0 * cm],
                ["C"] * 14, fs=6.3)]
    dlt = sem["puntuacion_plan_24h"] - sem["puntuacion_baseline"]
    E += [P(f"Resumen: {int((sem['estado'] == 'OPTIMO').sum())} iteraciones OPTIMO y {int((sem['estado'] != 'OPTIMO').sum())} FACTIBLE; mejora media sobre la referencia {fm(dlt.mean(), 1)} puntos "
            f"(mín. {fm(dlt.min(), 1)}, máx. {fm(dlt.max(), 1)}); stock mínimo / SS en toda la semana {fm(sem['stock_min_ratio_ss'].min(), 2)}; idoneidad mínima {fm(sem['idoneidad'].min(), 1)} %; "
            f"demanda cubierta {fm(sem['demanda_cubierta_pct'].min(), 0)} % en todos los turnos. Las columnas de energía, horas-operario, m² y puntuación corresponden al <b>turno consolidado</b> "
            f"(8 h) salvo «Punt. plan 24 h» y «Punt. ref.», que son del plan completo de 24 h de esa iteración.", "small")]
    E += [figc(F["semana"], "Figura 6.12 · Simulación semanal: puntuación por iteración y idoneidad (naranja: FACTIBLE por el límite de 10 s).", alto_max_cm=10)]
    return E


# ------------------------------------------------------------------------------------- 7. validación
def seccion_validacion(X, D, F):
    esc, escc, sem = X["esc"], X["escc"], X["sem"]
    E = H1("7. Validación e idoneidad")
    E += [P("Este apartado reúne las evidencias de que las soluciones son óptimas (hasta el gap), factibles y mejores que una planificación manual. Todos los números se vuelven a "
            f"calcular aquí llamando de nuevo a {K('validador.validar')} sobre cada plan.")]
    f = [["Caso", "Plan", "Estado", "Gap", "Idoneidad", "Incumpl. (validador)", "Avisos", "Punt.", "Punt. ref.", "Δ punt.", "Incumpl. ref."]]
    # escenario de la reconfiguración: stock del plan de las 06:00 al final del slot 09:00-10:00 + evento (como rolling.reconfigurar)
    e_rc = esc.copiar()
    n_rc = int((X["ahora"] - X["rec06"].horizonte.inicio) / pd.Timedelta(hours=1))
    rolling._fijar_stock(e_rc, {int(c): float(v) for c, v in X["rec06"].mejor.stock.iloc[n_rc - 1].items()})
    e_rc = rolling.aplicar_evento(e_rc, X["evento"])
    casos = [("Demo 06:00", X["rec06"], esc), ("Demo 14:00", X["rec14"], esc), ("Contingencia 06:00", X["recc"], escc), ("Reconf. 10:00", X["rec_rc"], e_rc)]
    n_ok = n_tot = 0
    for nom, r, e in casos:
        ps = list(r.top) if r.top else [r.contingencia]
        for p in ps:
            inc = validador.validar(e, r.horizonte, p)
            n_tot += 1
            n_ok += int(len(inc) == 0)
            f.append([nom, p.nombre, p.estado, "—" if p.gap is None else fm(100 * p.gap, 3) + " %", p.idoneidad_txt().replace(".", ","),
                      len(inc), len(p.avisos), fm(p.puntuacion, 2), fm(r.baseline.puntuacion, 2), sg(p.puntuacion - r.baseline.puntuacion, 2), len(r.baseline.incumplimientos)])
            nom = ""
    E += [tabla(f, [2.4 * cm, 2.1 * cm, 1.6 * cm, 1.3 * cm, 1.5 * cm, 1.4 * cm, 1.0 * cm, 1.2 * cm, 1.3 * cm, 1.3 * cm, 1.5 * cm],
                ["L", "L", "L", "R", "R", "C", "C", "R", "R", "R", "C"], fs=6.7)]
    E += [P("«Incumpl. (validador)» = nº de incumplimientos que devuelve una llamada nueva a validar(); «Incumpl. ref.» = incumplimientos de la referencia manual en ese caso. "
            "En la contingencia los incumplimientos son esperados (plan INVIABLE).", "small")]
    E += [H3("Evidencias")]
    topg = [p.gap for r in (X["rec06"], X["rec14"], X["rec_rc"]) for p in r.top if p.gap is not None]
    obj1 = X["rec06"].top[0].objetivo
    ev = [
        f"<b>Factibilidad.</b> {n_ok} de {n_tot} planes revisados tienen 0 incumplimientos; el único con incumplimientos es el plan de contingencia del escenario inviable (esperado). "
        f"Ninguna recomendación viable viola reglas duras (prueba {K('test_reglas_duras_nunca_violadas')}).",
        f"<b>Optimalidad.</b> Gap máximo observado en los Top: {fm(100 * max(topg), 3)} % (límite aceptado {fm(100 * float(esc.parametros['gap_relativo']), 1)} %). "
        f"Idoneidad mínima de los Top: {fm(100 * (1 - max(topg)), 2)} %. La mayoría de los planes de la demo se resuelven hasta gap 0 o ≤ 0,1 %.",
        f"<b>Coherencia puntuación-modelo.</b> La puntuación se recalcula en {K('modelo.evaluar')} y de forma independiente en {K('validador._validar_puntuacion')}; ambas coinciden "
        "(tolerancia 1e-4 en componentes), de modo que el motor no puede publicar una puntuación inconsistente.",
        f"<b>Frente a la referencia manual.</b> En la semana simulada el motor mejora la puntuación en {fm((sem['puntuacion_plan_24h'] - sem['puntuacion_baseline']).min(), 1)}–"
        f"{fm((sem['puntuacion_plan_24h'] - sem['puntuacion_baseline']).max(), 1)} puntos por iteración (media {fm((sem['puntuacion_plan_24h'] - sem['puntuacion_baseline']).mean(), 1)}).",
        f"<b>Detección de inviabilidad.</b> El escenario con la célula 14 de baja se declara INVIABLE con {len(X['recc'].contingencia.incumplimientos)} incumplimientos de la regla 7; el plan de la "
        f"referencia manual en ese mismo escenario tiene {len(X['recc'].baseline.incumplimientos)} incumplimiento(s).",
        "<b>Pruebas automáticas.</b> 21 pruebas en verde que cubren reglas, Top-K, contingencia, reconfiguración, semana, calendario y E/S de Excel (§4.5).",
    ]
    E += bullets(ev)
    E += [aviso(f"<b>Matiz sobre la idoneidad.</b> 100·(1 − gap) mide la distancia a la cota del solver sobre el objetivo completo, no un porcentaje de calidad de negocio. En la demo, un gap del 0,1 % sobre un objetivo "
                f"≈ {fm(obj1, 3)} equivale a ≈ {fm(100 * float(esc.parametros['gap_relativo']) * obj1, 3)} puntos de puntuación: las diferencias entre Top 1, 2 y 3 son del mismo orden.")]
    return E


# ------------------------------------------------------------------------------------- 8. limitaciones
def seccion_limitaciones(X, D, F):
    sem, esc = X["sem"], X["esc"]
    noches = sem[sem["turno"] == "N"]
    solo10 = int(sum(1 for c in noches["configuracion"] if list(c) == [10]))
    n_fact = int((sem["estado"] != "OPTIMO").sum())
    bajas = sem[sem["idoneidad"] < 90]
    E = H1("8. Limitaciones, riesgos y líneas futuras")
    E += [P("Se enumeran de forma honesta las limitaciones conocidas del motor y del informe, los riesgos derivados de los supuestos pendientes y las líneas de mejora.")]
    E += [H2("8.1 Limitaciones del modelo y de la resolución")]
    obj1 = X["rec06"].top[0].objetivo
    lim = [["Limitación", "Descripción y alcance", "Evidencia"],
           ["Gap del 0,1 % (no 0)", f"El óptimo está garantizado sólo con gap ≤ {fm(100 * float(esc.parametros['gap_relativo']), 1)} %. Las diferencias de puntuación entre Top 1, 2 y 3 ({fm(abs(X['rec06'].top[0].puntuacion - X['rec06'].top[1].puntuacion), 2)}–"
            f"{fm(abs(X['rec06'].top[0].puntuacion - X['rec06'].top[2].puntuacion), 2)} puntos) son del orden de ese margen (≈ {fm(100 * float(esc.parametros['gap_relativo']) * obj1, 3)} puntos).", "§5.5, §7"],
           ["Simulación semanal con límite de 10 s", f"Con 10 s y gap 0,5 %, {n_fact} de 15 iteraciones terminan FACTIBLE (no demostrado óptimo). Idoneidad mínima {fm(sem['idoneidad'].min(), 1)} % "
            f"({len(bajas)} iteración(es) por debajo del 90 %), aunque la puntuación y el stock del turno siguen siendo viables (stock mínimo ≥ {fm(sem['stock_min_ratio_ss'].min(), 2)}·SS).", "§6.7"],
           ["Viernes noche y lunes", "Con inicio el viernes (14:00 o 22:00) el horizonte termina en fin de semana sin camiones: la condición terminal F18 vale 0 y sólo exige SS. El motor no anticipa la "
            "cobertura de los primeros camiones del lunes; la simulación semanal parte del stock de la demo (no de un stock tras el fin de semana) y no verifica la transición viernes → lunes.", "F18, I11, I19"],
           ["Noches sólo con la célula 10", f"En {solo10} de {len(noches)} turnos de noche de la simulación el Top 1 sólo activa la célula 10 (y en el resto una o pocas más). Resultado de minimizar uso de recursos (I1) y "
            "energía (factor nocturno 1,20, F5) con stock suficiente para los 4 camiones nocturnos. Puede no ser deseable operativamente.", "I1, F5, §6.7"],
           ["Supuestos pendientes con KWD", "Factor nocturno 1,20 (F5), colchón +10 % (F9), orientación «menor uso = mejor» de R y Q (I1), hora de inicio de camiones y tope de 15 m² como máximo (F6).", "§3"],
           ["Modelo de piezas simplificado (BOM)", "Una pieza por célula y chasis (F1/F2); no hay variantes, lista de materiales por modelo ni piezas compartidas, ni materia prima (400 m²) ni cargas (100 m²) ni almacén externo.", "F1, F2, I9"],
           ["Mantenimiento externo", "El mantenimiento es un input (F3): el motor no lo planifica ni lo optimiza.", "F3, D12"],
           ["OEE 100 % y sin tiempos de cambio", "Capacidades optimistas; la producción real puede ser inferior y necesitar más horas.", "F8, I6"],
           ["Producción continua y horaria", "Piezas y camiones fraccionarios; resolución de 1 h; balance dentro de la hora; sin tiempos de transporte.", "I4, I5"],
           ["Sin validación de esquema del Excel", "Hojas con valores fuera de rango o columnas mal tipadas pueden fallar de forma poco informativa o usar valores por defecto silenciosos.", "§5.2"],
           ["Validador no independiente en datos", "Comparte escenario y horizonte con el modelo.", "§5.8"],
           ["Calendario", "Sólo lunes–viernes sin festivos; sin cambio de hora; turnos y franja nocturna fijos en código.", "F4, I11, I16"],
           ["KPI «Demanda cubierta»", "En planes viables siempre es 100 % porque el stock nunca es negativo; no distingue retrasos de servicio.", "§5.10"],
           ["Datos de demanda ilustrativos", "La demo usa cifras supuestas; los resultados dependen de la demanda real.", "D13"]]
    E += [tabla(lim, [3.6 * cm, 10.7 * cm, 2.7 * cm], ["L", "L", "L"], fs=7.2, valign="TOP")]
    E += [H2("8.2 Hallazgos sobre el código (no corregidos)")]
    E += [P("Durante la redacción se contrastó el código con la especificación. Se registran las observaciones sin modificar el motor ni la aplicación:")]
    hall = [["#", "Hallazgo", "Impacto", "Ubicación"],
            ["H1", "<b>Stock obsoleto en una segunda reconfiguración desde el dashboard.</b> Tras el primer evento, la sesión guarda el escenario con el stock inicial original "
                   "(sólo se le aplica el evento), mientras el plan nuevo empieza en «ahora». Si el segundo evento usa el mismo instante (valor por defecto: rec.inicio, n = 0) o uno anterior, "
                   "reconfigurar() no actualiza el stock y recalcula con el stock de partida original. La propia nota del código («n ≤ 0: el stock ya corresponde…») no se cumple en ese flujo. "
                   "Reproducido con el motor: tras la baja de la célula 3 a las 10:00, un segundo evento a las 10:00 hace partir la célula 4 de 720 piezas en vez de las 616 del plan vigente.",
             "Recomendación recalculada con un stock desfasado; se evita eligiendo un instante posterior o usando el evento stock_real.", f"{K('app/dashboard.py')} (aplicar evento), {K('rolling.reconfigurar')}"],
            ["H2", "El área de producto terminado está fijada a 800 m² en los gráficos del PDF y del dashboard (no se lee del Excel).", "Si se cambia la hoja Almacen, los gráficos mostrarían una capacidad incorrecta (el modelo sí usa el valor del Excel).", f"{K('informes._grafico_almacen')}, {K('graficos.almacen')}"],
            ["H3", "La explicación elige «mantenimiento» o «baja» según que la célula aparezca en la hoja Mantenimientos (en cualquier fecha), no según la causa real del bloqueo posterior.", "Texto «por qué» posiblemente impreciso si la célula tiene ambas incidencias.", f"{K('motor._explicar')}"],
            ["H4", "Q se promedia de forma ligeramente distinta en el MILP (÷|W|) y en evaluar/validar (÷ horas con algún recurso &gt; 0).", "Sólo difiere en el caso degenerado Mto = Calidad = 0 en una hora laborable; el validador lo marcaría como discrepancia de puntuación.", f"{K('modelo.resolver')}, {K('modelo._fracciones_recursos')}, {K('validador._validar_puntuacion')}"],
            ["H5", f"{K('modelo.evaluar')} con estado por defecto EVALUADO asigna idoneidad 100 % (gap = 0 por defecto) a cualquier activación evaluada.", "Podría confundirse con una garantía de optimalidad si se usa esa función de forma aislada.", f"{K('modelo.evaluar')}"],
            ["H6", "La franja nocturna 22–06 y los turnos están fijados en el código; el Excel sólo parametriza la franja solar.", "Cambiar los turnos exige modificar código en varios módulos.", f"{K('horizonte')}, {K('modelo._kpis_tramo')}, {K('config.TURNOS')}"]]
    E += [tabla(hall, [0.9 * cm, 8.1 * cm, 4.5 * cm, 3.5 * cm], ["C", "L", "L", "L"], fs=6.9, valign="TOP")]
    E += [H2("8.3 Riesgos")]
    E += bullets([
        "<b>Calibración con datos reales.</b> Los resultados dependen de ciclos, OEE, densidades y demanda reales; sin ellos el motor es un prototipo validado sobre un escenario ilustrativo.",
        "<b>Interpretación de los criterios.</b> La orientación de R y Q (menor uso = mejor) condiciona el estilo de planes (pocas células, noches vacías); conviene validarla con KWD.",
        "<b>Rendimiento en el equipo de demo.</b> Los tiempos de resolución no son deterministas; el límite de 30 s por plan garantiza respuesta pero puede ofrecer un gap mayor.",
        "<b>Dependencias.</b> PuLP 4 + highspy 1.15; los cambios de API del solver podrían afectar a la lectura de estado y gap en {resolver}.".replace("{resolver}", K("modelo.resolver")),
    ])
    E += [H2("8.4 Líneas futuras")]
    E += bullets([
        "Validar con KWD los supuestos pendientes (F5 nocturno, F9, I1, calendario de camiones) y sustituir los datos de demo por programas reales.",
        "Modelo de lista de materiales (varias piezas por conjunto y por célula, variantes VE/COMB) y de materia prima y cargas.",
        "Planificación del mantenimiento dentro del optimizador (ventanas, técnicos y criticidad).",
        "Horizonte con anticipación del fin de semana (condición terminal sobre el siguiente turno laborable) y calendario de festivos.",
        "Curva real de fotovoltaica y tarifas horarias en lugar de factores multiplicativos.",
        "Integración con el MES (DOEET) para leer stock, recursos y expediciones reales y cerrar el bucle de horizonte rodante.",
        "Criterio de desempate y robustez: mostrar explícitamente alternativas casi empatadas y optimización robusta frente al absentismo.",
        "Corregir los hallazgos H1–H6 y añadir validación de esquema del Excel con mensajes claros.",
    ])
    return E


# ------------------------------------------------------------------------------------- anexos
def anexo_a(X, D):
    t = D["tc"]
    esc = X["esc"]
    E = H1("Anexo A · Tabla de células")
    E += [P(f"Datos de {K('ttablas.xlsx')} tal y como los carga {K('datos.cargar_entrada')} (hoja {K('Celulas')} de {K('data/entrada_ejemplo.xlsx')}). "
            "Recursos = personas equivalentes (fracciones: una persona repartida entre varios puestos, D5) exigidas mientras la célula está activa; kW = potencia; "
            "cap. = capacidad con OEE 100 % (F8); SS = stock de seguridad por pieza (F10).")]
    f = [["Cél.", "Tipo", "Ciclo (s)", "pz/m²", "Oper.", "Pick.", "Carr.", "Mto", "Cal.", "kW", "ppc", "Cap. (pz/h)", "SS (pz)", "Observación"]]
    obs = {10: "Servicio logístico; sin pieza (F11)", 11: "Pareja con la 12 (regla 3)", 12: "Pareja con la 11 (regla 3)", 13: "Mantenimiento en la demo (T, 02/10)",
           14: "Baja en el escenario de contingencia"}
    for c in sorted(t.index):
        r = t.loc[c]
        f.append([c, r["tipo"], fm(r["ciclo_s"], 0), fm(r["piezas_m2"], 0), fm(r["operarios"], 1), fm(r["picking"], 1), fm(r["carretilleros"], 1), fm(r["mto"], 2),
                  fm(r["calidad"], 2), fm(r["kw"], 2), fm(r["ppc"], 0), "—" if c == 10 else fm(r["cap_h"], 1), "—" if c == 10 else fm(D["ss"][c], 0), obs.get(c, "")])
    E += [tabla(f, [0.9 * cm, 1.2 * cm, 1.3 * cm, 1.2 * cm, 1.1 * cm, 1.1 * cm, 1.1 * cm, 1.1 * cm, 1.1 * cm, 1.4 * cm, 0.9 * cm, 1.4 * cm, 1.1 * cm, 2.1 * cm],
                ["C"] * 13 + ["L"], fs=6.7)]
    E += [Spacer(1, 6), P(f"Totales: Σ kW = {fm(float(t['kw'].sum()), 2)}; recursos de las 16 células activas a la vez: operarios {fm(float(t['operarios'].sum()), 1)}, picking {fm(float(t['picking'].sum()), 1)}, "
                          f"carretilleros {fm(float(t['carretilleros'].sum()), 1)}, Mto {fm(float(t['mto'].sum()), 2)}, calidad {fm(float(t['calidad'].sum()), 2)} "
                          f"(frente a 15/2/4/7/3 disponibles en turno de mañana o tarde: el motor <b>no puede</b> activar todas las células a la vez).", "small")]
    return E


USADO_EN = {
    "absentismo": ("horizonte.construir_horizonte", "F7"), "colchon_ss": ("modelo.preparar (k), baseline, motor._alertas", "F9"),
    "ss_ve": ("datos.ss_por_celula", "F10"), "ss_comb": ("datos.ss_por_celula", "F10"),
    "camiones_dia": ("horizonte._camiones_previstos", "F6"), "intervalo_camion_h": ("horizonte._camiones_previstos", "F6"),
    "primer_camion_h": ("horizonte._camiones_previstos", "F6"), "m2_max_camion": ("horizonte.construir_horizonte", "F6"),
    "solar_ini": ("horizonte (factor), modelo.preparar, motor._explicar", "F5"), "solar_fin": ("horizonte (factor), modelo.preparar, motor._explicar", "F5"),
    "factor_solar": ("horizonte (factor), modelo.preparar (fmax)", "F5"), "factor_noche": ("horizonte (factor), modelo.preparar (fmax)", "F5"),
    "peso_recursos": ("modelo.preparar (w_R), validador", "D1"), "peso_espacio": ("modelo.preparar (w_S), validador", "D1"),
    "peso_calidad_mto": ("modelo.preparar (w_Q), validador", "D1"), "peso_stock": ("modelo.preparar (w_B), validador", "D1"),
    "peso_energia": ("modelo.preparar (w_E), validador", "D1"), "tiempo_limite_s": ("modelo.resolver (timeLimit); rolling.simular_semana", "—"),
    "horas_horizonte": ("horizonte.construir_horizonte (H)", "—"), "cobertura_final_h": ("horizonte.construir_horizonte (t2)", "F18"),
    "gap_relativo": ("modelo.resolver (gapRel); rolling.simular_semana", "—"),
}


def anexo_b(X, D):
    E = H1("Anexo B · Parámetros")
    E += [H2("B.1 Hoja Parametros (claves, valores por defecto y significado)")]
    f = [["Clave", "Valor por defecto", "Significado (descripción del código)", "Usado en", "Ref."]]
    for k, (v, d) in PARAMETROS_DEFECTO.items():
        f.append([K(k), f"{v:g}".replace(".", ","), d, K(USADO_EN.get(k, ("—", ""))[0]), USADO_EN.get(k, ("", "—"))[1]])
    E += [tabla(f, [3.0 * cm, 1.8 * cm, 6.0 * cm, 5.0 * cm, 1.2 * cm], ["L", "R", "L", "L", "C"], fs=7.0, valign="TOP")]
    E += [H2("B.2 Constantes del código (no parametrizables desde el Excel)")]
    const = [["Constante", "Valor", "Módulo", "Significado"],
             [K("PENALIZACION"), f"{modelo.PENALIZACION:g}", "modelo", "Penalización de las holguras de SS y espacio (planes INVIABLE si se usan)"],
             [K("PENALIZACION_FINAL"), f"{modelo.PENALIZACION_FINAL:g}", "modelo", "Penalización de la holgura terminal (F18), sólo aviso"],
             [K("PESO_ARRANQUES"), f"{modelo.PESO_ARRANQUES:g}".replace(".", ","), "modelo", "Penalización por arranque de célula (estabilidad)"],
             [K("TOL_HOLGURA"), f"{modelo.TOL_HOLGURA:g}", "modelo", "Umbral para considerar activa una holgura"],
             [K("TOL"), f"{config.TOL:g}", "config", "Tolerancia general"],
             [K("TOL_STOCK / TOL_ESPACIO"), f"{validador.TOL_STOCK:g} / {validador.TOL_ESPACIO:g}", "validador", "Tolerancias de stock (piezas) y espacio (m²)"],
             [K("TOL_BIN / TOL_PUNT"), f"{validador.TOL_BIN:g} / {validador.TOL_PUNT:g}", "validador", "Tolerancias de binariedad y de puntuación"],
             [K("HORIZONTE_MIRADA"), f"{baseline.HORIZONTE_MIRADA}", "baseline", "Horas de previsión de la regla manual"],
             [K("FACTOR_STOCK_DEMO"), f"{datos.FACTOR_STOCK_DEMO}".replace(".", ","), "datos", "Stock inicial de la demo = factor × SS (F19)"],
             [K("CELULA_LOGISTICA"), f"{config.CELULA_LOGISTICA}", "config", "Célula de servicio logístico (F11)"],
             [K("CELULAS_PAREJA"), f"{config.CELULAS_PAREJA}", "config", "Células que trabajan a la vez (regla 3)"],
             [K("TURNOS"), "M 6–14, T 14–22, N 22–6", "config", "Horario de turnos (F4)"],
             [K("DIAS_LABORABLES"), "0–4 (lun–vie)", "config", "Días laborables (F4)"],
             [K("RECURSOS / RECURSOS_R"), "5 recursos / operarios, picking, carretilleros", "config", "Recursos y subconjunto que puntúa en R"],
             ["±45 min", "45", "horizonte._camiones_previstos", "Tolerancia para casar una expedición real con un camión previsto (F15)"],
             ["22 / 6", "22, 6", "horizonte, modelo._kpis_tramo", "Horas fijas de la franja nocturna (factor_noche y kwh_noche)"],
             ["0,9", "0,9", "motor._alertas", "Umbral de aviso de ocupación de almacén (90 %)"],
             [K("top_k"), "3", "motor.recomendar", "Número de alternativas del Top"],
             [K("simular_semana"), "15 it.; 10 s; gap 0,005", "rolling", "Iteraciones y límites de la simulación semanal"]]
    E += [tabla(const, [3.8 * cm, 3.2 * cm, 3.6 * cm, 6.4 * cm], ["L", "L", "L", "L"], fs=7.0, valign="TOP")]
    return E


def _modulos_api():
    import ast
    rutas = sorted((RAIZ / "src" / "kwd").glob("*.py")) + [RAIZ / "app" / "graficos.py", RAIZ / "app" / "dashboard.py"]
    filas = []
    for ruta in rutas:
        if ruta.name == "__init__.py":
            continue
        tree = ast.parse(ruta.read_text(encoding="utf-8-sig"))
        mod = f"{'kwd.' if ruta.parent.name == 'kwd' else 'app.'}{ruta.stem}"

        def sig(fn):
            try:
                s = ast.unparse(fn.args)
            except Exception:
                s = "…"
            ret = f" → {ast.unparse(fn.returns)}" if fn.returns is not None else ""
            return f"({s}){ret}"

        def doc(nodo):
            d = ast.get_docstring(nodo) or ""
            return d.strip().split("\n")[0][:150]
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                filas.append([K(mod), K(n.name), K(sig(n)).replace("&", "&amp;") if False else K(sig(n)), doc(n)])
            elif isinstance(n, ast.ClassDef):
                campos = [b.target.id for b in n.body if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name)]
                filas.append([K(mod), K("class " + n.name), K("campos: " + ", ".join(campos)) if campos else "", doc(n)])
                for b in n.body:
                    if isinstance(b, ast.FunctionDef):
                        filas.append([K(mod), K(f"{n.name}.{b.name}"), K(sig(b)), doc(b)])
    return filas


def anexo_c(X, D):
    E = H1("Anexo C · Mapa de módulos y funciones (API)")
    E += [P("Inventario extraído automáticamente del código fuente (módulo {ast} de Python): funciones y clases de nivel superior y métodos, con su firma y la primera línea de su "
            "docstring. Los símbolos que empiezan por «_» son internos. El módulo {app.dashboard} sólo contiene funciones auxiliares: su lógica es código de script de Streamlit."
            .replace("{ast}", K("ast")).replace("{app.dashboard}", K("app/dashboard.py")))]
    resumen = [["Módulo", "Rol en el pipeline"],
               [K("kwd.config"), "Constantes, FLAGS, hojas y parámetros por defecto"], [K("kwd.datos"), "Escenario y E/S de Excel; cálculos derivados de datos"],
               [K("kwd.horizonte"), "Construcción del horizonte de 24 slots"], [K("kwd.plan"), "Estructura de datos del plan"],
               [K("kwd.modelo"), "MILP, evaluación, KPIs y puntuación"], [K("kwd.baseline"), "Plan de referencia manual"],
               [K("kwd.motor"), "Top-K, contingencia, explicación y alertas"], [K("kwd.validador"), "Validación independiente"],
               [K("kwd.rolling"), "Eventos, reconfiguración y simulación semanal"], [K("kwd.cli"), "Línea de comandos"], [K("kwd.informes"), "Informe PDF de dirección"],
               [K("app.graficos / app.dashboard"), "Gráficos plotly y dashboard Streamlit"]]
    E += [tabla(resumen, [5.0 * cm, 12.0 * cm], ["L", "L"], fs=7.4), Spacer(1, 6)]
    E += [tabla([["Módulo", "Símbolo", "Firma / campos", "Descripción (docstring)"]] + _modulos_api(), [2.2 * cm, 3.3 * cm, 6.2 * cm, 5.3 * cm], ["L"] * 4, fs=6.3, valign="TOP")]
    return E


DESC_COL = {
    ("Celulas", "celula"): ("int", "Sí", "Nº de célula 1–16."), ("Celulas", "tipo"): ("texto", "Sí", "«VE» o «COMB» (se normaliza a mayúsculas)."),
    ("Celulas", "ciclo_s"): ("float", "Sí", "Tiempo de ciclo en segundos (1 ciclo = 1 pieza, F2)."), ("Celulas", "piezas_m2"): ("float", "Sí", "Piezas almacenables por m² (0 para la célula 10)."),
    ("Celulas", "operarios"): ("float", "Sí", "Operarios equivalentes que exige la célula activa."), ("Celulas", "picking"): ("float", "Sí", "Personal de picking equivalente."),
    ("Celulas", "carretilleros"): ("float", "Sí", "Carretilleros equivalentes."), ("Celulas", "mto"): ("float", "Sí", "Ratio de Mantenimiento (personas equivalentes)."),
    ("Celulas", "calidad"): ("float", "Sí", "Ratio de Calidad (personas equivalentes)."), ("Celulas", "kw"): ("float", "Sí", "Potencia en kW."),
    ("Celulas", "piezas_por_conjunto"): ("float", "No (1)", "Piezas de esta célula por chasis (F1/F2)."),
    ("Turnos", "recurso"): ("texto", "Sí", "operarios, picking, carretilleros, mto o calidad."), ("Turnos", "M"): ("float", "Sí", "Disponibilidad estándar en el turno de mañana."),
    ("Turnos", "T"): ("float", "Sí", "Disponibilidad estándar en el turno de tarde."), ("Turnos", "N"): ("float", "Sí", "Disponibilidad estándar en el turno de noche."),
    ("Almacen", "zona"): ("texto", "Sí", "materia_prima, cargas o producto_terminado (sólo esta última se usa; si falta, 800)."), ("Almacen", "m2"): ("float", "Sí", "Superficie en m²."),
    ("Parametros", "parametro"): ("texto", "No", "Clave (ver Anexo B)."), ("Parametros", "valor"): ("float", "No", "Valor que sustituye al valor por defecto."),
    ("Parametros", "descripcion"): ("texto", "No", "Descripción libre."),
    ("DemandaSemanal", "semana_inicio"): ("fecha", "No", "Lunes de la semana."), ("DemandaSemanal", "chasis_ve"): ("float", "No", "Chasis VE de la semana."),
    ("DemandaSemanal", "chasis_comb"): ("float", "No", "Chasis COMB de la semana."),
    ("CorreccionDiaria", "fecha"): ("fecha", "No", "Día cuya demanda sustituye a semanal ÷ 5."), ("CorreccionDiaria", "chasis_ve"): ("float", "No", "Chasis VE del día."),
    ("CorreccionDiaria", "chasis_comb"): ("float", "No", "Chasis COMB del día."),
    ("StockActual", "celula"): ("int", "No", "Célula productiva."), ("StockActual", "piezas"): ("float", "No", "Piezas en stock al inicio del plan (0 si la célula no figura)."),
    ("Disponibilidad", "celula"): ("int", "No", "Célula afectada."), ("Disponibilidad", "estado"): ("texto", "No", "«BAJA» (única que bloquea)."),
    ("Disponibilidad", "desde"): ("fecha-hora", "No", "Inicio de la baja (vacío = sin límite)."), ("Disponibilidad", "hasta"): ("fecha-hora", "No", "Fin de la baja (vacío = sin límite)."),
    ("Mantenimientos", "fecha"): ("fecha", "No", "Fecha del turno (fecha_turno)."), ("Mantenimientos", "turno"): ("texto", "No", "M, T, N o DIA (todo el día)."),
    ("Mantenimientos", "celula"): ("int", "No", "Célula bloqueada."), ("Mantenimientos", "tecnicos"): ("float", "No", "Técnicos que resta a la disponibilidad de Mto."),
    ("RecursosReales", "fecha"): ("fecha", "No", "Fecha del turno."), ("RecursosReales", "turno"): ("texto", "No", "M, T o N."),
    ("RecursosReales", "operarios"): ("float", "No", "Operarios reales (vacío = estándar con absentismo)."), ("RecursosReales", "picking"): ("float", "No", "Picking real."),
    ("RecursosReales", "carretilleros"): ("float", "No", "Carretilleros reales."), ("RecursosReales", "mto"): ("float", "No", "Mantenimiento real."), ("RecursosReales", "calidad"): ("float", "No", "Calidad real."),
    ("Expediciones", "fecha_hora"): ("fecha-hora", "No", "Hora real de salida del camión (sustituye al previsto a ±45 min)."),
    ("Expediciones", "chasis_ve"): ("float", "No", "Chasis VE cargados."), ("Expediciones", "chasis_comb"): ("float", "No", "Chasis COMB cargados."),
}


def anexo_d(X, D):
    E = H1("Anexo D · Estructura del Excel de entrada")
    E += [P(f"Libro con 11 hojas (cabecera en la fila 1). Sólo {K('Celulas')} es obligatoria; las demás pueden faltar o estar vacías. Se generan con {K('datos.crear_plantilla_ejemplo')} / "
            f"{K('datos.crear_plantilla_contingencia')} y se leen con {K('datos.cargar_entrada')}. Definición de columnas: {K('config.HOJAS_COLUMNAS')}.")]
    for hoja, cols in HOJAS_COLUMNAS.items():
        f = [["Columna", "Tipo", "Oblig.", "Descripción"]]
        for c in cols:
            d = DESC_COL.get((hoja, c), ("", "", ""))
            f.append([K(c), d[0], d[1], d[2]])
        E += [KeepTogether([H2(f"Hoja {hoja}"), tabla(f, [3.2 * cm, 2.0 * cm, 1.6 * cm, 10.2 * cm], ["L", "L", "C", "L"], fs=7.2)])]
    return E


def anexo_e(X, D):
    E = H1("Anexo E · Glosario")
    g = [("Activación a[c,h]", "Variable binaria: la célula c está activa (con sus recursos comprometidos) en el slot h."),
         ("Uso u[c,h]", "Fracción de la hora en que la célula produce (0–1); u ≤ a."),
         ("Slot", "Hora de calendario del horizonte (24 por defecto)."),
         ("Turno / fecha_turno", "M 06–14, T 14–22, N 22–06; fecha_turno es el día al que pertenece el turno (la noche 00–06 es del día anterior)."),
         ("Laborable", "Slot cuya fecha_turno cae de lunes a viernes."),
         ("Célula productiva", "Toda célula salvo la 10 (servicio logístico)."),
         ("Chasis / conjunto", "Unidad de venta que sale en camión; consume una pieza de cada célula de su tipo (F1)."),
         ("ppc", "Piezas por conjunto de una célula (F2, defecto 1)."),
         ("SS", "Stock de seguridad por pieza (400 VE, 200 COMB)."),
         ("Colchón", "SS × (1 + k), con k = 10 %: objetivo blando que puntúa en B."),
         ("Holgura", "Variable auxiliar penalizada que permite incumplir una regla; si es &gt; 0 el plan es INVIABLE."),
         ("Plan INVIABLE / contingencia", "Plan que incumple reglas obligatorias; se muestra sólo como contingencia, nunca como recomendación."),
         ("Plan de referencia (baseline)", "Heurística manual de 8 h usada para cuantificar el impacto."),
         ("Top-K", "K configuraciones del turno actual distintas, obtenidas con cortes no-good."),
         ("Corte no-good", "Restricción que prohíbe exactamente una configuración ya obtenida."),
         ("Gap", "Distancia relativa entre la mejor solución y la cota del solver."),
         ("Idoneidad", "100·(1 − gap): garantía de optimalidad del plan (±gap)."),
         ("Arranque en caliente (warm start)", "Solución inicial sugerida al solver (activación del plan de referencia)."),
         ("MILP", "Programación lineal entera mixta."), ("HiGHS / PuLP", "Solver de código abierto y librería de modelado en Python."),
         ("Flag", "Supuesto marcado en el código con «# FLAG Fx» y listado en config.FLAGS."),
         ("Horizonte rodante", "Replanificar cada turno o incidencia sobre las siguientes 24 h con el stock actualizado."),
         ("OEE", "Eficiencia global del equipo (aquí 100 %)."), ("FV", "Fotovoltaica (franja 11–17 h con factor 0,85)."),
         ("KWD / VE / COMB", "Karosseriewerke Dresden; vehículo eléctrico; vehículo de combustión."),
         ("MQB / MEB21 / DOEET", "Plataformas de VW y sistema MES de KWD España citados en la presentación.")]
    E += [tabla([["Término", "Definición"]] + [[a, b] for a, b in g], [4.4 * cm, 12.6 * cm], ["L", "L"], fs=7.4)]
    return E


def anexo_f(X, D):
    E = H1("Anexo F · Cómo reproducir")
    E += [H2("F.1 Entorno")]
    E += [formulas([
        'cd "<carpeta del proyecto>\\kwd_motor"',
        "python -m venv .venv                                  # sólo la primera vez",
        ".venv\\Scripts\\python.exe -m pip install -r requirements.txt",
        "# opcional (revisión visual del PDF):  .venv\\Scripts\\python.exe -m pip install pypdfium2"])]
    E += [P(f"{K('requirements.txt')}: highspy==1.15.1, pulp==4.0.0, pandas==3.0.6, openpyxl==3.1.5, streamlit==1.64.0, plotly==7.1.0, matplotlib==3.11.2, reportlab==5.0.1, pytest==9.1.1.", "small")]
    E += [H2("F.2 Comandos (PowerShell 5.1; sin &amp;&amp;)")]
    E += [formulas([
        '$env:PYTHONPATH = "src"',
        '$py = ".venv\\Scripts\\python.exe"',
        "# Pruebas (21 en ≈2 min)",
        "& $py -m pytest -q",
        "# Plan por consola y PDF de dirección",
        "& $py -m kwd.cli --entrada data/entrada_ejemplo.xlsx `",
        '      --inicio "2026-10-02 06:00" --pdf salida/informe_ejemplo.pdf',
        "& $py -m kwd.cli --entrada data/escenario_contingencia.xlsx `",
        '      --inicio "2026-10-02 06:00" --pdf salida/prueba_contingencia.pdf',
        "# Dashboard (o doble clic en iniciar_dashboard.bat)",
        "& $py -m streamlit run app/dashboard.py",
        "# Regenerar los PDF de documentación (ejecutan el motor real)",
        "& $py docs\\generar_documento.py        # salida/KWD_Solucion_y_Plan_de_Accion.pdf",
        "& $py docs\\generar_informe_tecnico.py  # salida/KWD_Informe_Tecnico.pdf",
        "# Regenerar las plantillas Excel",
        "$c = 'import kwd.datos as d;'",
        "& $py -c \"$c d.crear_plantilla_ejemplo('data/entrada_ejemplo.xlsx')\"",
        "& $py -c \"$c d.crear_plantilla_contingencia('data/escenario_contingencia.xlsx')\""])]
    E += [H2("F.3 Configuraciones de VS Code (.vscode/launch.json)")]
    try:
        lj = json.loads((RAIZ / ".vscode" / "launch.json").read_text(encoding="utf-8-sig"))
        f = [["Nombre", "Módulo", "Argumentos", "Entorno"]]
        for c in lj["configurations"]:
            f.append([c["name"], K(c.get("module", "")), K(" ".join(c.get("args", []))), K(json.dumps(c.get("env", {})))])
        E += [tabla(f, [3.4 * cm, 2.4 * cm, 4.6 * cm, 6.6 * cm], ["L"] * 4, fs=7.0)]
    except Exception as e:  # noqa: BLE001
        E += [P(f"(No se pudo leer launch.json: {e})", "small")]
    E += [P(f"{K('.vscode/settings.json')}: intérprete {K('.venv\\Scripts\\python.exe')}, pytest activado sobre {K('tests')}, UTF-8. "
            f"{K('.streamlit/config.toml')}: tema claro y color primario #1F2A6B.", "small")]
    E += [H2("F.4 Qué cambia al ejecutar de nuevo")]
    E += bullets([
        "Cifras de resultados, tamaños de modelo y tiempos se recalculan; los <b>tiempos</b> varían con la máquina (HiGHS multihilo); los planes óptimos y sus puntuaciones deben coincidir salvo empates dentro del gap.",
        f"Variable de entorno {K('KWD_INFORME_CACHE')} (opcional): ruta de un .pkl con los resultados del motor, sólo para depurar el maquetado de este informe.",
        "El PDF indica su fecha de edición (v1, 2 de octubre de 2026) y no la de ejecución; el pie del informe de dirección sí lleva la fecha de generación.",
    ])
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
    X["sem"]["idoneidad"] = pd.to_numeric(X["sem"]["idoneidad"], errors="coerce")
    print(f"Motor listo en {X['t_motor']:.0f} s", flush=True)
    D = derivados(X)
    tmp = Path(tempfile.mkdtemp(prefix="kwd_inf_"))
    esc, escc = X["esc"], X["escc"]
    r06, r14, rc = X["rec06"], X["rec14"], X["recc"]
    F = {k: tmp / f"{k}.png" for k in ("pipeline", "topk", "finde", "camiones", "gantt06", "stock06", "almacen06", "energia06", "contrib06",
                                       "gantt14", "stock14", "ganttc", "stockc", "reconf", "semana", "tiempos")}
    print("Gráficos…", flush=True)
    g_pipeline(F["pipeline"])
    g_topk(F["topk"])
    g_finde(F["finde"])
    g_camiones(r06.horizonte, esc, F["camiones"])
    t1 = r06.top[0]
    g_gantt(t1, r06.horizonte, esc, F["gantt06"], "Top 1 a las 06:00 · activación de células en 24 h")
    g_stock(t1, r06.horizonte, esc, F["stock06"], "Stock por pieza frente al stock de seguridad (Top 1, 06:00)")
    g_almacen(t1, r06.baseline, r06.horizonte, esc, F["almacen06"])
    g_energia(t1, r06.horizonte, esc, F["energia06"])
    g_contrib(r06.top + [r06.baseline], [p.nombre for p in r06.top] + ["Referencia manual"], F["contrib06"])
    g_gantt(r14.top[0], r14.horizonte, esc, F["gantt14"], "Top 1 a las 14:00 · activación de células en 24 h")
    g_stock(r14.top[0], r14.horizonte, esc, F["stock14"], "Stock por pieza frente al stock de seguridad (Top 1, 14:00)")
    g_gantt(rc.contingencia, rc.horizonte, escc, F["ganttc"], "Contingencia (célula 14 de baja) · plan INVIABLE, no recomendable")
    g_stock(rc.contingencia, rc.horizonte, escc, F["stockc"], "Stock por pieza en el plan de contingencia (en rojo, las piezas que incumplen el SS)")
    g_reconf(r06, X["rec_rc"], esc, X["evento"], F["reconf"])
    g_semana(X["sem"], F["semana"])
    # el gráfico de tiempos se genera dentro de la sección 5.12

    E = []
    E += seccion_portada(X, D)
    E += seccion_resumen(X, D)
    E += seccion_fuentes(X, D)
    E += seccion_supuestos(X, D)
    E += seccion_trabajo(X, D)
    E += seccion_metodologia_a(X, D, F)
    E += seccion_metodologia_b(X, D, F)
    E += seccion_metodologia_c(X, D, F)
    E += seccion_ejemplo(X, D, F)
    E += seccion_validacion(X, D, F)
    E += seccion_limitaciones(X, D, F)
    E += anexo_a(X, D)
    E += anexo_b(X, D)
    E += anexo_c(X, D)
    E += anexo_d(X, D)
    E += anexo_e(X, D)
    E += anexo_f(X, D)

    faltan = comprobar_glifos()
    if faltan:
        print("AVISO: glifos ausentes en DejaVu:", {k: v for k, v in faltan.items()})
    doc = Doc(SALIDA)
    doc.multiBuild(E)
    kb = SALIDA.stat().st_size / 1024
    print(f"PDF generado: {SALIDA} · {doc.page} páginas · {kb:.0f} KB · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
