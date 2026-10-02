"""Gráficos plotly del dashboard KWD (VE = verde, COMB = azul)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

NAVY = "#1F2A6B"
VERDE = "#2E9E5B"
AZUL = "#2F6FD0"
ROJO = "#C0392B"
AMBAR = "#E0A100"
GRIS = "#6B7280"
COLOR_TIPO = {"VE": VERDE, "COMB": AZUL, "LOG": "#7A869A"}
COLOR_ESTADO = {"OPTIMO": VERDE, "FACTIBLE": AMBAR, "INVIABLE": ROJO}
NOMBRE_REC = {"operarios": "Operarios", "picking": "Picking", "carretilleros": "Carretilleros",
              "mto": "Mantenimiento", "calidad": "Calidad"}
NOMBRE_CRIT = {"R": "Recursos (50 %)", "S": "Espacio (20 %)", "Q": "Calidad + Mto (15 %)",
               "B": "Stock seguridad (10 %)", "E": "Energía (5 %)"}
COLOR_CRIT = {"R": NAVY, "S": "#5B6BC0", "Q": "#26A69A", "B": "#F4A300", "E": "#8D6E63"}


def tipos(esc) -> dict:
    return {int(r["celula"]): str(r["tipo"]) for _, r in esc.celulas.iterrows()}


def horas(rec) -> list:
    return [pd.Timestamp(x) for x in rec.horizonte.slots.reset_index(drop=True)["inicio"]]


def _decor(fig, rec, row=None, col=None, solar=True):
    """Marca cambios de turno y sombrea la franja solar."""
    s = rec.horizonte.slots.reset_index(drop=True)
    hs = horas(rec)
    kw = {} if row is None else {"row": row, "col": col}
    if solar and "factor_energia" in s:
        for h, f in zip(hs, s["factor_energia"]):
            if f < 1.0:
                fig.add_vrect(x0=h, x1=h + pd.Timedelta(hours=1), fillcolor="#FFD54F", opacity=0.18,
                              line_width=0, layer="below", **kw)
    prev = None
    for h, t in zip(hs, s["turno"]):
        if prev is not None and t != prev:
            fig.add_vline(x=h, line_dash="dash", line_color=GRIS, line_width=1, **kw)
        prev = t


def gantt(rec, esc, plan):
    hs = horas(rec)
    tp = tipos(esc)
    filas = []
    for c in plan.activacion.columns:
        for i, h in enumerate(hs):
            if plan.activacion.iloc[i][c] > 0.5:
                u = 1.0
                try:
                    u = float(plan.uso.iloc[i][c])
                except Exception:
                    pass
                filas.append(dict(Celula=f"Célula {int(c)}", Tipo="LOG" if int(c) == 10 else ("VE" if tp.get(int(c)) == "VE" else "COMB"),
                                  ini=h, fin=h + pd.Timedelta(hours=1), uso=u))
    fig = go.Figure()
    for t, nombre in (("VE", "VE"), ("COMB", "Combustión"), ("LOG", "Logística")):
        sub = [f for f in filas if f["Tipo"] == t]
        if not sub:
            continue
        fig.add_trace(go.Bar(
            base=[f["ini"] for f in sub], x=[3600 * 1000] * len(sub), y=[f["Celula"] for f in sub],
            orientation="h", marker=dict(color=COLOR_TIPO[t], line=dict(color="white", width=1)),
            opacity=0.95, name=nombre,
            customdata=[[f["ini"].strftime("%H:%M"), f["fin"].strftime("%H:%M"), round(f["uso"] * 100)] for f in sub],
            hovertemplate="%{y}<br>%{customdata[0]}–%{customdata[1]}<br>Producción: %{customdata[2]} % de la hora<extra></extra>"))
    orden = [f"Célula {int(c)}" for c in plan.activacion.columns]
    fig.update_yaxes(categoryorder="array", categoryarray=orden[::-1])
    fig.update_xaxes(type="date", range=[hs[0], hs[-1] + pd.Timedelta(hours=1)], tickformat="%H:%M", dtick=2 * 3600 * 1000)
    _decor(fig, rec)
    fig.update_layout(barmode="overlay", height=max(320, 28 * len(orden) + 120), margin=dict(l=10, r=10, t=40, b=10),
                      title="Activación de células (franja amarilla = solar · línea discontinua = cambio de turno)",
                      legend=dict(orientation="h", y=1.08, x=1, xanchor="right"))
    return fig


def contribuciones(plan):
    fig = go.Figure()
    cont = plan.contribuciones or {}
    for k in ["R", "S", "Q", "B", "E"]:
        v = cont.get(k, cont.get(NOMBRE_CRIT[k], 0)) or 0
        fig.add_trace(go.Bar(y=["Puntuación"], x=[v], orientation="h", name=NOMBRE_CRIT[k],
                             marker_color=COLOR_CRIT[k], text=[f"{v:.1f}"], textposition="inside",
                             hovertemplate=f"{NOMBRE_CRIT[k]}: %{{x:.2f}} pts<extra></extra>"))
    fig.update_layout(barmode="stack", height=170, margin=dict(l=10, r=10, t=30, b=10),
                      xaxis=dict(range=[0, 100], title="Puntos (máx. 100)"),
                      legend=dict(orientation="h", y=-0.55), title="Contribución por criterio")
    return fig


def stock(rec, esc, plan, ss: dict, k: float):
    cols = list(plan.stock.columns)
    hs = horas(rec)
    tp = tipos(esc)
    nc = 3
    nf = -(-len(cols) // nc)
    fig = make_subplots(rows=nf, cols=nc, subplot_titles=[f"Célula {int(c)} ({tp.get(int(c), '?')})" for c in cols],
                        vertical_spacing=0.09)
    for i, c in enumerate(cols):
        r, cc = i // nc + 1, i % nc + 1
        fig.add_trace(go.Scatter(x=hs, y=plan.stock[c].values, mode="lines", showlegend=False,
                                 line=dict(color=COLOR_TIPO.get(tp.get(int(c)), AZUL), width=2),
                                 hovertemplate="%{x|%H:%M}: %{y:.0f} piezas<extra></extra>"), row=r, col=cc)
        fig.add_hline(y=ss[int(c)], line_dash="dash", line_color=ROJO, row=r, col=cc)
        fig.add_hline(y=ss[int(c)] * (1 + k), line_dash="dot", line_color=AMBAR, row=r, col=cc)
    fig.update_xaxes(tickformat="%H")
    fig.update_layout(height=230 * nf, margin=dict(l=10, r=10, t=40, b=10),
                      title="Stock por pieza (rojo = stock de seguridad · ámbar = colchón)")
    return fig


def almacen(rec, plan, A=800.0):
    hs = horas(rec)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hs, y=plan.espacio.values, fill="tozeroy", name="m² ocupados",
                             line=dict(color=NAVY, width=2), fillcolor="rgba(31,42,107,0.2)"))
    fig.add_hline(y=A, line_dash="dash", line_color=ROJO, annotation_text="Capacidad 800 m²")
    fig.add_hline(y=0.9 * A, line_dash="dot", line_color=AMBAR, annotation_text="Alerta 90 %")
    _decor(fig, rec)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10), title="Ocupación almacén producto terminado (m²)",
                      yaxis=dict(range=[0, max(A * 1.1, float(plan.espacio.max()) * 1.05)]))
    return fig


def recursos(rec, plan):
    hs = horas(rec)
    recs = ["operarios", "picking", "carretilleros", "mto", "calidad"]
    fig = make_subplots(rows=1, cols=5, subplot_titles=[NOMBRE_REC[r] for r in recs], shared_xaxes=True)
    for i, r in enumerate(recs):
        fig.add_trace(go.Bar(x=hs, y=plan.recursos[f"{r}_usado"].values, marker_color=NAVY, name="Usado",
                             showlegend=(i == 0)), row=1, col=i + 1)
        fig.add_trace(go.Scatter(x=hs, y=plan.recursos[f"{r}_disp"].values, mode="lines", line_shape="hv",
                                 line=dict(color=ROJO, dash="dash"), name="Disponible", showlegend=(i == 0)),
                      row=1, col=i + 1)
    fig.update_xaxes(tickformat="%H")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=50, b=10), title="Recursos usados vs disponibles por hora",
                      legend=dict(orientation="h", y=1.15, x=1, xanchor="right"))
    return fig


def energia(rec, plan):
    hs = horas(rec)
    s = rec.horizonte.slots.reset_index(drop=True)
    cols = [VERDE if f < 1 else (ROJO if f > 1 else NAVY) for f in s["factor_energia"]]
    fig = go.Figure(go.Bar(x=[h + pd.Timedelta(minutes=30) for h in hs], y=plan.energia_kwh.values, marker_color=cols,
                           hovertemplate="%{x|%H:%M}: %{y:.0f} kWh<extra></extra>"))
    _decor(fig, rec)
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10),
                      title="Energía horaria, kWh (verde = franja solar · rojo = nocturna)")
    return fig


def comparar_barras(nombres, valores, titulo, colores=None):
    fig = go.Figure(go.Bar(x=nombres, y=valores, marker_color=colores or NAVY,
                           text=[f"{v:.1f}" for v in valores], textposition="outside"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10), title=titulo)
    return fig

