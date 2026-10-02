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
             "5 Objetivo\n(pesos KWD)", "6 Resolución\nHiGHS", "7 Top 1/2/3\n+ referencia", "8 Validación\ny explicación"]
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
    ax.text(2.15, 0.12, "9 Reconfiguración: ante una incidencia se vuelve al paso 1 con el stock actual y se "
            "recalculan las 24 h siguientes", ha="center", fontsize=7.4, color=NAVY_HEX, style="italic")
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

    el += [P("1. Entradas", "h2"),
           P("El motor parte de los datos reales de la planta:")] + B([
        "<b>Demanda</b> semanal por pieza, con corrección diaria si cambia el pedido.",
        "<b>Stock</b> actual de cada pieza y <b>recursos reales</b> disponibles por turno.",
        "Células de <b>alta o baja</b>, <b>mantenimientos</b> programados y <b>cargas de camiones</b> (qué sale y cuándo)."])

    el += [P("2. Horizonte de 24 franjas horarias", "h2"),
           P("Se mira el futuro inmediato hora a hora. A cada una de las 24 horas se le asigna: su <b>turno</b>; "
             "los <b>recursos disponibles</b> (estándar del turno menos un 5 % de absentismo, o los reales si se "
             "han informado); las <b>células bloqueadas</b> (baja o mantenimiento); los <b>camiones que salen</b> y "
             "su carga; y un <b>factor energético</b>: 0,85 en la franja solar (11-17 h), 1,2 de noche (22-06 h) y "
             "1 el resto.")]

    el += [P("3. Qué decide el modelo", "h2"),
           P("Para <b>cada célula y cada hora</b> el modelo elige dos cosas: si está <b>activa</b> (sí/no, una "
             "variable binaria) y <b>qué fracción de la hora produce</b> (un número entre 0 y 1). Una célula sólo "
             "puede producir si está activa. El modelo de demostración tiene unas 2.200 variables (384 binarias) "
             "y unas 2.000 restricciones, y se resuelve en segundos.")]

    el += [P("4. Reglas obligatorias", "h2"),
           P("Son las condiciones del problema. Si una sola se incumple, el plan se descarta (en el modelo, "
             "romperlas tiene una penalización enorme que lo marca como inviable):")] + B([
        "<b>Célula 10</b> (logística) siempre activa en horas laborables; <b>células 11 y 12</b> siempre juntas.",
        "Células de <b>baja o mantenimiento</b> apagadas.",
        "<b>Operarios, picking, carretilleros, mantenimiento y calidad</b> nunca por encima de lo disponible en esa hora.",
        "<b>Stock de cada pieza ≥ stock de seguridad</b> (400 piezas VE / 200 piezas COMB) en todas las horas.",
        "<b>Almacén de producto terminado ≤ 800 m²</b>."]) + [
        P("Y la contabilidad del stock, que enlaza una hora con la siguiente:"),
        P("stock<sub>h</sub> = stock<sub>h-1</sub> + producción<sub>h</sub> − salidas de camiones<sub>h</sub>", "f")]

    el += [P("5. Qué busca (función objetivo)", "h2"),
           P("Entre todos los planes que cumplen las reglas, busca el que <b>minimiza</b> una suma ponderada con "
             "los pesos del cliente (KWD):")] + [caja_pesos()] + [
        P("Cada criterio se normaliza entre 0 y 1 (0 = ideal), y la puntuación final es:"),
        P("Puntuación = 100 × (1 − Σ peso<sub>i</sub> × criterio<sub>i</sub>)", "f"),
        P("Se añaden dos términos auxiliares muy pequeños: una penalización <b>blanda</b> si el stock final no "
          "alcanza para las <b>8 h siguientes</b> (evita \"dejar la casa vacía\" al cierre del horizonte) y una "
          "mínima penalización por cada <b>arranque/parada</b> innecesario (planes más estables).")]

    el += [P("6. Resolución", "h2"),
           P("El solver <b>HiGHS</b> explora las combinaciones y devuelve la mejor demostrando que ninguna otra "
             "puede ser mejor salvo un margen del <b>0,1 %</b>; esa garantía es la <b>idoneidad</b> de la solución. "
             "Si <b>no existe ninguna solución que cumpla todo</b>, no se inventa nada: se devuelve un "
             "<b>plan de contingencia</b> indicando exactamente qué regla se incumple y dónde.")]

    el += [P("7. Top 1/2/3 y comparación con el plan manual", "h2"),
           P("Para ofrecer alternativas reales, tras obtener el mejor plan se <b>prohíbe la configuración de "
             "células del turno actual</b> que acaba de ganar y se <b>vuelve a resolver</b>; así se obtienen el "
             "Top 2 y el Top 3, ordenados por puntuación. Además, se calcula un <b>plan manual de referencia</b> "
             "(regla sencilla de planificador, que mira 8 h adelante) y se mide el impacto frente a él en "
             "<b>horas-operario, m² de almacén y kWh</b>.")]

    el += [P("8. Comprobación y explicación", "h2"),
           P("Un <b>validador independiente</b> recalcula desde cero todas las reglas sobre el plan final (no se "
             "fía del solver). Después el programa explica en lenguaje llano <b>qué activar</b>, <b>por qué</b> "
             "(por ejemplo, cuándo caería el stock bajo el mínimo si no se hiciera) y <b>con qué impacto</b>.")]

    el += [P("9. Reconfiguración (rolling horizon)", "h2"),
           P("Ante una incidencia (avería, falta de personal, pedido urgente) se <b>fija lo ya ejecutado</b>, se "
             "toma el <b>stock actual</b> como punto de partida y se <b>recalculan las 24 h siguientes</b> en "
             "segundos, repitiendo los pasos 1 a 8.")]

    el += [Spacer(1, 8), KeepTogether([P("En una frase", "h2"), caja([
        ("Qué activar", "El conjunto de células que cada hora cumple todas las reglas con la menor carga de recursos."),
        ("Por qué", "Porque así el stock de cada pieza no cae bajo seguridad y se cubren los camiones previstos."),
        ("Con qué impacto", "Menos horas-operario, menos m² de almacén y menos kWh que el plan manual de referencia."),
        ("Qué tan buena es", "Puntuación sobre 100, con idoneidad demostrada: ningún plan es mejor salvo un 0,1 %.")])])]

    doc = BaseDocTemplate(str(SALIDA), pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.7 * cm,
                          bottomMargin=1.6 * cm, title="KWD - Lógica del MILP", author="Equipo KWD")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width,
                                                             doc.height, id="f")], onPage=cabecera_pie)])
    SALIDA.parent.mkdir(exist_ok=True)
    doc.build(el)
    print(SALIDA)


def caja_pesos():
    datos = [("50 %", "Menor ocupación de operarios, picking y carretilleros"),
             ("20 %", "Menos m² de almacén ocupados"),
             ("15 %", "Más margen de calidad y mantenimiento"),
             ("10 %", "Mantener un colchón por encima del stock de seguridad"),
             ("5 %", "Menor consumo ponderado (aprovechar la franja solar)")]
    t = Table([[P(f"<b>{a}</b>", "cel"), P(b, "cel")] for a, b in datos], colWidths=[1.8 * cm, 15.6 * cm])
    t.setStyle(TableStyle([("ROWBACKGROUNDS", (0, 0), (-1, -1), [ZEBRA, colors.white]),
                           ("BOX", (0, 0), (-1, -1), 0.6, NAVY), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


if __name__ == "__main__":
    main()
