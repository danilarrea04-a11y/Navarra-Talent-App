"""Genera salida/KWD_Logica_MILP_Resumen.pdf (resumen de la lógica del MILP, sin ejecutar el motor).

Uso: $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe docs\\generar_resumen_milp.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "salida" / "KWD_Logica_MILP_Resumen.pdf"
NAVY, NAVY_HEX = colors.HexColor("#1F2A6B"), "#1F2A6B"
ZEBRA, GRIS = colors.HexColor("#EEF0F8"), colors.HexColor("#5A6070")

_ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
for n, f in [("DV", "DejaVuSans.ttf"), ("DV-B", "DejaVuSans-Bold.ttf"), ("DV-I", "DejaVuSans-Oblique.ttf"),
             ("DV-BI", "DejaVuSans-BoldOblique.ttf"), ("DVM", "DejaVuSansMono.ttf")]:
    pdfmetrics.registerFont(TTFont(n, str(_ttf / f)))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-BI")
plt.rcParams.update({"font.family": "DejaVu Sans"})


def _st(name, **kw):
    b = dict(fontName="DV", fontSize=8.8, leading=12.2, textColor=colors.HexColor("#222222"), spaceAfter=3)
    b.update(kw)
    return ParagraphStyle(name, **b)


S = {"body": _st("b"), "bul": _st("u", leftIndent=13, bulletIndent=2, spaceAfter=1.8),
     "h1": _st("h1", fontName="DV-B", fontSize=16, leading=20, textColor=NAVY, spaceAfter=2),
     "sub": _st("s", fontSize=9, textColor=GRIS, spaceAfter=6),
     "h2": _st("h2", fontName="DV-B", fontSize=10.5, leading=13.5, textColor=NAVY, spaceBefore=7, spaceAfter=2.5,
               keepWithNext=1),
     "f": _st("f", fontName="DVM", fontSize=8.2, leading=11.5, alignment=1, textColor=NAVY, spaceBefore=2,
              spaceAfter=3),
     "cel": _st("c", fontSize=8.8, spaceAfter=0), "celh": _st("ch", fontName="DV-B", fontSize=8.8, spaceAfter=0,
                                                              textColor=colors.white)}


def P(t, s="body"):
    return Paragraph(t, S[s])


def B(items):
    return [Paragraph(t, S["bul"], bulletText="•") for t in items]


def diagrama(ruta):
    cajas = ["1 Entradas", "2 Horizonte\n24 franjas", "3 Variables\nde decisión", "4 Reglas\nobligatorias",
             "5 Objetivo\n(pesos KWD)", "6 Resolución\nHiGHS", "7 Top 1/2/3\npor cortes", "8 Validación\ny explicación"]
    fig, ax = plt.subplots(figsize=(7.4, 2.15))
    ax.set_xlim(0, 4.3)
    ax.set_ylim(0, 2.3)
    ax.axis("off")
    pos = [(0.05 + (i % 4) * 1.08, 1.45 if i < 4 else 0.45) for i in range(8)]
    for i, ((x, y), t) in enumerate(zip(pos, cajas)):
        fc = NAVY_HEX if i in (3, 4, 5) else "#EEF0F8"
        tc = "white" if i in (3, 4, 5) else NAVY_HEX
        ax.add_patch(FancyBboxPatch((x, y), 0.9, 0.62, boxstyle="round,pad=0.02,rounding_size=0.06", fc=fc,
                                    ec=NAVY_HEX, lw=1.1))
        ax.text(x + 0.45, y + 0.31, t, ha="center", va="center", fontsize=7.6, color=tc, fontweight="bold")
    kw = dict(arrowstyle="-|>", mutation_scale=10, color=NAVY_HEX, lw=1.2)
    for i in range(3):
        ax.add_patch(FancyArrowPatch((pos[i][0] + 0.92, 1.76), (pos[i + 1][0] - 0.02, 1.76), **kw))
        ax.add_patch(FancyArrowPatch((pos[i + 4][0] + 0.92, 0.76), (pos[i + 5][0] - 0.02, 0.76), **kw))
    ax.add_patch(FancyArrowPatch((pos[3][0] + 0.45, 1.43), (pos[4][0] + 0.45, 1.09), connectionstyle="arc3,rad=0",
                                 **kw))
    ax.text(2.15, 0.12, "9 Contingencia (a petición): se dan de baja células y personas y se recalcula "
            "desde el stock actual", ha="center", fontsize=7.4, color=NAVY_HEX, style="italic")
    fig.savefig(ruta, dpi=200, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def cabecera_pie(c, doc):
    w, h = A4
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, h - 1.0 * cm, w, 1.0 * cm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("DV-B", 8.5)
    c.drawString(1.8 * cm, h - 0.65 * cm, "KWD · Lógica del motor MILP")
    c.setFont("DV", 8)
    c.drawRightString(w - 1.8 * cm, h - 0.65 * cm, "Resumen en lenguaje llano")
    c.setFillColor(GRIS)
    c.setFont("DV", 7.8)
    c.drawString(1.8 * cm, 0.9 * cm, "Equipo KWD · Navarra Talent Challenge 2026")
    c.drawRightString(w - 1.8 * cm, 0.9 * cm, f"Página {doc.page}")
    c.restoreState()


def caja(filas):
    t = Table([[P(a, "celh"), P(b, "cel")] for a, b in filas], colWidths=[3.6 * cm, 13.8 * cm])
    est = [("BACKGROUND", (0, 0), (0, -1), NAVY), ("ROWBACKGROUNDS", (1, 0), (1, -1), [ZEBRA, colors.white]),
           ("BOX", (0, 0), (-1, -1), 0.8, NAVY), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9CDE0")),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    t.setStyle(TableStyle(est))
    return t


def main():
    tmp = Path(tempfile.mkdtemp()) / "flujo.png"
    diagrama(tmp)
    el = [P("¿Cómo decide el motor qué células activar?", "h1"),
          P("Lógica del modelo MILP (optimización lineal entera mixta), paso a paso y sin jerga.", "sub"),
          Image(str(tmp), width=17.2 * cm, height=17.2 * cm * 0.30)]
    el[-1].drawHeight = 17.2 * cm * Image(str(tmp)).imageHeight / Image(str(tmp)).imageWidth

    el += [P("1. Entradas (se introducen en la app, sin Excel)", "h2")] + B([
        "<b>Demanda semanal</b> por tipo de pieza y <b>demanda diaria corregida</b>; si un día no tiene corrección "
        "se usa la semanal / 5.",
        "<b>Bajas de personal</b> por turno y rol, <b>paradas programadas</b> (p. ej. técnicos de una célula) y "
        "<b>stock actual</b> de cada pieza.",
        "Las constantes de la planta (células, turnos estándar, almacén) están fijas en el programa."])

    el += [P("2. Horizonte de 24 franjas horarias", "h2"),
           P("Se mira el futuro inmediato hora a hora. A cada hora se le asigna su <b>turno</b>, el <b>personal "
             "presente</b> por rol (estándar menos bajas), las <b>células bloqueadas</b> por parada, los "
             "<b>camiones</b> (cada 1,5 h, tantos de 15 m² como hagan falta) y un <b>factor energético</b>: más "
             "barato en la franja solar (11-14 h) y más caro de noche.")]

    el += [P("3. Qué decide el modelo", "h2"),
           P("Para <b>cada célula y cada hora</b>: si está <b>activa</b> (sí/no) y <b>qué fracción de la hora "
             "produce</b> (entre 0 y 1). Además, para cada rol (operarios, picking, carretilleros, mantenimiento, "
             "calidad) decide el <b>número entero de personas</b> asignadas. Después los trabajadores concretos se "
             "enumeran y se asignan <b>puesto a puesto</b>: cada persona presente queda asignada o libre.")]

    el += [P("4. Reglas obligatorias", "h2")] + B([
        "<b>Célula 10</b> siempre activa; <b>células 11 y 12</b> siempre juntas.",
        "<b>Personal asignado ≤ personal presente</b> en cada rol y hora; células en parada, apagadas.",
        "<b>Almacén de producto terminado ≤ 800 m²</b>.",
        "<b>Balance de stock</b> con los camiones cada 1,5 h:"]) + [
        P("stock<sub>h</sub> = stock<sub>h-1</sub> + producción<sub>h</sub> − salida de camiones<sub>h</sub>", "f"),
        P("Jerarquía de penalizaciones: <b>pedido no servido</b> (el plan se marca CRÍTICO y se avisa a "
          "dirección) <b>≫ stock por debajo del de seguridad (SS)</b> (se repone primero) <b>≫ criterios</b>. "
          "Solo el almacén > 800 m² o las reglas de células hacen inviable un plan.")]

    el += [P("5. Qué busca (criterios con pesos KWD)", "h2"),
           P("Entre los planes posibles minimiza una suma ponderada de criterios normalizados (0 = ideal):")] + [
        caja_pesos()] + [
        P("Puntuación = 100 × (1 − Σ peso<sub>i</sub> × criterio<sub>i</sub>)", "f"),
        P("El <b>tiempo muerto real</b> es el personal presente menos el trabajo productivo, y el trabajo "
          "productivo cuenta solo la fracción de la hora en que la célula produce. El <b>stock óptimo</b> es el "
          "SS más la demanda de un turno, y se mide al cierre de cada turno (06, 14 y 22 h).")]

    el += [P("6. Resolución", "h2"),
           P("El solver <b>HiGHS</b> dispone de <b>8 s por plan</b>. La <b>idoneidad</b> = 100 · (1 − gap) indica "
             "lo cerca que está el plan del mejor posible, y se informa también la <b>puntuación máxima "
             "alcanzable</b>. Si no existe un plan que cumpla las reglas duras, no se inventa nada: se explica qué "
             "regla falla.")]

    el += [P("7. Top 1/2/3", "h2"),
           P("Tras el mejor plan se <b>prohíbe (corte) la configuración de células</b> que acaba de ganar y se "
             "vuelve a resolver, obteniendo el Top 2 y el Top 3, <b>ordenados por puntuación</b>. Las alternativas "
             "de baja calidad (<b>idoneidad < 50 %</b>) se descartan.")]

    el += [P("8. Comprobación y explicación", "h2"),
           P("Un <b>validador independiente</b> recalcula todas las reglas sobre el plan final. Después se explica "
             "en lenguaje llano <b>qué activar</b>, <b>por qué</b> y <b>con qué impacto</b>, comparando con las "
             "alternativas Top 2/3.")]

    el += [P("9. Contingencia (a petición)", "h2"),
           P("Solo cuando se pulsa el botón, se indican las <b>células</b> y las <b>personas</b> dados de baja y se "
             "recalcula el plan desde el stock actual. El plan <b>consume el stock de seguridad</b> para aguantar; "
             "si el stock llega a agotarse, se genera un <b>aviso para dirección</b> (pieza, hora de agotamiento, "
             "piezas no servidas). Resuelta la incidencia, <b>repone primero el SS y después recupera el stock "
             "óptimo</b>. Se muestra la reubicación de personas, las máquinas a activar y los indicadores antes y "
             "después.")]

    el += [Spacer(1, 8), KeepTogether([P("En una frase", "h2"), caja([
        ("Qué activar", "Las células y personas de cada hora que cumplen todas las reglas y ocupan al máximo al personal presente."),
        ("Por qué", "Para servir todos los camiones sin bajar del stock de seguridad y acercarse al stock óptimo."),
        ("Con qué impacto", "Menos tiempo muerto, menos m² de almacén y menos energía que las alternativas Top 2/3."),
        ("Qué tan buena es", "Puntuación sobre 100 con su máximo alcanzable e idoneidad = 100 · (1 − gap).")])])]

    doc = BaseDocTemplate(str(SALIDA), pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.7 * cm,
                          bottomMargin=1.6 * cm, title="KWD - Lógica del MILP", author="Equipo KWD")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width,
                                                             doc.height, id="f")], onPage=cabecera_pie)])
    SALIDA.parent.mkdir(exist_ok=True)
    doc.build(el)
    print(SALIDA)


def caja_pesos():
    datos = [("50 %", "<b>Tiempo muerto real</b> de todo el personal presente (objetivo principal)"),
             ("20 %", "Menos m² de almacén ocupados"),
             ("15 %", "Más margen de calidad y mantenimiento"),
             ("10 %", "Menor desviación respecto al stock óptimo (SS + un turno de demanda) al cierre de turno"),
             ("5 %", "Menor coste energético (franja solar 11-14 h; la noche es más cara)")]
    t = Table([[P(f"<b>{a}</b>", "cel"), P(b, "cel")] for a, b in datos], colWidths=[1.8 * cm, 15.6 * cm])
    t.setStyle(TableStyle([("ROWBACKGROUNDS", (0, 0), (-1, -1), [ZEBRA, colors.white]),
                           ("BOX", (0, 0), (-1, -1), 0.6, NAVY), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


if __name__ == "__main__":
    main()
