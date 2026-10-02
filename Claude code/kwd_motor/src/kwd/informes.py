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
COLOR_ESTADO = {"OPTIMO": VERDE, "FACTIBLE": AMBAR, "CRITICO": ROJO, "INVIABLE": ROJO}
NOMBRE_REC = {"operarios": "Operarios", "picking": "Picking", "carretilleros": "Carretilleros",
              "mto": "Mantenimiento", "calidad": "Calidad"}
NOMBRE_CRIT = {"R": "Recursos", "S": "Espacio", "Q": "Calidad + Mto", "B": "Stock óptimo",
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
    ax.set_xlabel("Hora del día (zona amarilla = franja solar 11–14, factor energético reducido; línea discontinua = cambio de turno)", fontsize=7)
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
    opt = stock_optimo(esc, pd.Timestamp(horas[0]).normalize())
    cierres = [f for _, f in cierres_turno(rec)]
    for i, c in enumerate(cols):
        ax = axs[i // nc][i % nc]
        _shade_solar(ax, horas, slots)
        ax.plot(horas, plan.stock[c].values, color=COLOR_TIPO.get(tipos.get(int(c), "COMB"), AZUL), lw=1.6)
        ax.axhline(ss[int(c)], color=ROJO, ls="--", lw=1)
        ax.axhline(opt[int(c)], color=AMBAR, ls=":", lw=1)
        for f in cierres:
            ax.axvline(f, color=GRIS, ls=":", lw=0.5)
        ax.set_title(f"Célula {int(c)} ({tipos.get(int(c), '?')})", fontsize=7, loc="left")
        _fmt_x(ax)
        ax.tick_params(labelsize=6)
    for j in range(n, nf * nc):
        axs[j // nc][j % nc].axis("off")
    fig.suptitle("Stock por pieza (piezas) · rojo = stock de seguridad · ámbar = stock óptimo (SS + demanda de un turno)",
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


ROLES = ("operarios", "picking", "carretilleros", "mto", "calidad")


def horas_libres(plan) -> dict:
    """Horas libres (horas-persona de personal presente sin tarea) por rol y total.

    Devuelve {"total": h, "roles": {rol: {"libres": h, "disponibles": h, "ocupacion_pct": %}}}.
    Usa los KPI del motor si existen y, si no, los calcula como Σ(disp − N) de `plan.recursos`.
    """
    k = plan.kpis or {}
    rec_ = plan.recursos
    roles = {}
    for r in ROLES:
        disp = usado = None
        if isinstance(rec_, pd.DataFrame) and f"{r}_disp" in rec_.columns and f"{r}_usado" in rec_.columns:
            disp = float(rec_[f"{r}_disp"].sum())
            usado = float(rec_[f"{r}_usado"].clip(upper=rec_[f"{r}_disp"]).sum())
        libres = k.get(f"horas_libres_{r}")
        if libres is None and disp is not None:
            libres = disp - usado
        if libres is None:
            continue
        ocup = k.get(f"ocupacion_{r}_pct")
        if ocup is None and disp:
            ocup = 100.0 * (disp - float(libres)) / disp
        roles[r] = {"libres": float(libres), "disponibles": disp, "ocupacion_pct": None if ocup is None else float(ocup)}
    total = k.get("horas_libres_total")
    if total is None:
        total = sum(v["libres"] for v in roles.values())
    return {"total": float(total), "roles": roles}


def stock_optimo(esc, fecha) -> dict:
    """Stock óptimo por pieza = SS + demanda de un turno (demanda diaria del tipo / 3) del día `fecha`."""
    from kwd import datos
    ss = datos.ss_por_celula(esc)
    tp = _tipos(esc)
    dve, dcomb = datos.demanda_dia(esc, pd.Timestamp(fecha).normalize())
    return {c: ss[c] + (dve if tp.get(c) == "VE" else dcomb) / 3.0 for c in ss}


def cierres_turno(rec) -> list:
    """[(índice de la última hora del turno, instante del cierre)] de los cierres 06:00/14:00/22:00 del horizonte."""
    s = _slots(rec)
    out = []
    for i, f in enumerate(s["fin"]):
        f = pd.Timestamp(f)
        if f.hour in (6, 14, 22) and f.minute == 0:
            out.append((i, f))
    return out


def stock_vs_optimo(rec, esc, plan) -> pd.DataFrame:
    """Stock de cada pieza en cada cierre de turno frente al óptimo (SS + demanda de un turno)."""
    from kwd import datos
    ss = datos.ss_por_celula(esc)
    tp = _tipos(esc)
    filas = []
    for i, f in cierres_turno(rec):
        opt = stock_optimo(esc, f.normalize() - pd.Timedelta(days=1) if f.hour == 6 else f.normalize())
        for c in plan.stock.columns:
            c = int(c)
            st_ = float(plan.stock.iloc[i][c if c in plan.stock.columns else str(c)])
            filas.append({"cierre": f, "celula": c, "tipo": tp.get(c, ""), "stock": st_, "ss": ss.get(c, np.nan),
                          "optimo": opt[c], "desviacion": st_ - opt[c],
                          "desviacion_pct": 100.0 * abs(st_ - opt[c]) / opt[c] if opt[c] else np.nan})
    return pd.DataFrame(filas, columns=["cierre", "celula", "tipo", "stock", "ss", "optimo", "desviacion", "desviacion_pct"])


def resumen_stock_optimo(df: pd.DataFrame) -> dict:
    """Desviación media y máxima (%) respecto al óptimo y nº de cierres con alguna pieza bajo SS."""
    if not isinstance(df, pd.DataFrame) or not len(df):
        return {}
    return {"media_pct": float(df["desviacion_pct"].mean()), "max_pct": float(df["desviacion_pct"].max()),
            "bajo_ss": int((df["stock"] < df["ss"] - 1e-9).sum())}


def aviso_direccion(rec, contingencia=None):
    """Texto del aviso para dirección (desabastecimiento) o None. Prioriza el de la contingencia."""
    if contingencia is not None and contingencia.get("aviso_direccion"):
        return str(contingencia["aviso_direccion"])
    a = getattr(rec, "aviso_direccion", None)
    if a:
        return str(a)
    ex = [str(x) for x in (rec.alertas or []) if "DIRECCI" in str(x).upper()]
    return " | ".join(ex) if ex else None


def tabla_agotamiento(ag) -> pd.DataFrame:
    """Normaliza el agotamiento por pieza (DataFrame, dict {pieza: {...}} o lista de dicts) a DataFrame."""
    if ag is None:
        return pd.DataFrame()
    if isinstance(ag, pd.DataFrame):
        return ag.copy()
    if isinstance(ag, dict):
        if "celula" in ag or "pieza" in ag:
            return pd.DataFrame([ag])
        filas = []
        for k, v in ag.items():
            d = dict(v) if isinstance(v, dict) else {"valor": v}
            d.setdefault("celula", k)
            filas.append(d)
        return pd.DataFrame(filas)
    return pd.DataFrame(list(ag))


def resumen_camiones(rec):
    """Resumen de camiones del horizonte a partir de `Horizonte.camiones` (None si no hay datos)."""
    cam = getattr(rec.horizonte, "camiones", None)
    if not isinstance(cam, pd.DataFrame) or not len(cam) or "n_camiones" not in cam.columns:
        return None
    c = cam.copy()
    n = pd.to_numeric(c["n_camiones"], errors="coerce").fillna(0)
    total = float(n.sum())
    horizonte_h = float(len(rec.horizonte.slots))
    return {"total": total, "media": float(n.mean()), "max": float(n.max()), "ciclos": int(len(c)),
            "por_dia": total * 24.0 / horizonte_h if horizonte_h else total, "tabla": c}


def _slot_idx(rec, serie):
    """Convierte la columna `slot` (índice 0..H-1 o marca temporal) en índices enteros."""
    hs = [pd.Timestamp(x) for x in rec.horizonte.slots.reset_index(drop=True)["inicio"]]
    if pd.api.types.is_datetime64_any_dtype(serie):
        mp = {h: i for i, h in enumerate(hs)}
        return pd.to_datetime(serie).map(lambda t: mp.get(pd.Timestamp(t).floor("h"), -1)).astype(int)
    return pd.to_numeric(serie).astype(int)


def personal_en_slot(rec, plan, i) -> pd.DataFrame:
    """Filas de `Plan.personal` del slot `i` con columnas normalizadas
    (trabajador, rol, turno, celulas, carga, estado)."""
    cols = ["trabajador", "rol", "turno", "celulas", "carga", "estado"]
    pers = getattr(plan, "personal", None)
    if not isinstance(pers, pd.DataFrame) or not len(pers):
        return pd.DataFrame(columns=cols)
    d = pers.copy()
    if "trabajador" not in d.columns and "persona" in d.columns:
        d["trabajador"] = d["persona"]
    for c, v in (("estado", "ASIGNADO"), ("turno", ""), ("carga", np.nan), ("celulas", "")):
        if c not in d.columns:
            d[c] = v
    d = d[_slot_idx(rec, d["slot"]) == int(i)].copy()
    d["celulas"] = d["celulas"].fillna("").astype(str)
    d["estado"] = d["estado"].astype(str).str.upper()
    return d[cols].reset_index(drop=True)


def roster_turno(rec, plan):
    """Tabla persona × hora del turno actual con las células de cada persona (None si no hay datos)."""
    pers = getattr(plan, "personal", None)
    if not isinstance(pers, pd.DataFrame) or not len(pers):
        return None
    idx_turno = list(rec.horizonte.slots_turno_actual())
    hs = [pd.Timestamp(x) for x in rec.horizonte.slots.reset_index(drop=True)["inicio"]]
    d = pers.copy()
    if "trabajador" not in d.columns and "persona" in d.columns:
        d["trabajador"] = d["persona"]
    d["_i"] = _slot_idx(rec, d["slot"])
    d = d[d["_i"].isin(idx_turno)]
    if not len(d):
        return None
    d["_h"] = d["_i"].map(lambda i: hs[i].strftime("%H:%M"))
    cel = d["celulas"].fillna("").astype(str)
    if "estado" in d.columns:
        est = d["estado"].astype(str).str.upper()
        cel = cel.where(~(est == "PARADA"), "parada")
        cel = cel.where(~((est == "LIBRE") & (cel == "")), "libre")
    d["celulas"] = cel.replace("", "libre")
    piv = d.pivot_table(index=["rol", "trabajador"], columns="_h", values="celulas", aggfunc="first", sort=False)
    piv = piv[[hs[i].strftime("%H:%M") for i in idx_turno if hs[i].strftime("%H:%M") in piv.columns]]
    piv = piv.fillna("—").reset_index()
    piv["rol"] = piv["rol"].map(lambda r: NOMBRE_REC.get(str(r), str(r)))
    return piv.rename(columns={"rol": "Rol", "trabajador": "Trabajador"})


def roster_compacto(rec, plan):
    """Filas [persona, 'HH:MM–HH:MM C8+C9; ...'] resumiendo tramos contiguos del turno actual."""
    t = roster_turno(rec, plan)
    if t is None:
        return None
    horas = [c for c in t.columns if c not in ("Rol", "Trabajador")]
    filas = []
    for _, r in t.iterrows():
        tramos, ini = [], 0
        for j in range(1, len(horas) + 1):
            if j == len(horas) or r[horas[j]] != r[horas[ini]]:
                fin = (pd.Timestamp(f"2000-01-01 {horas[j - 1]}") + pd.Timedelta(hours=1)).strftime("%H:%M")
                tramos.append(f"{horas[ini]}–{fin}: {r[horas[ini]]}")
                ini = j
        filas.append([str(r["Trabajador"]), "; ".join(tramos)])
    return filas


def kpi_lista(plan) -> list:
    """Lista curada de KPIs (etiqueta, valor formateado, grupo)."""
    k = plan.kpis or {}
    g = lambda n, d=1, suf="": (_f(k[n], d) + suf) if k.get(n) is not None else "—"  # noqa: E731
    def g2(*nombres, d=1, suf=""):  # primer KPI disponible entre varios nombres (compatibilidad v1/v2)
        for n in nombres:
            if k.get(n) is not None:
                return _f(k[n], d) + suf
        return "—"

    out = [
        ("Demanda cubierta", g2("demanda_cubierta_pct", d=1, suf=" %"), "Demanda"),
        ("Piezas VE a expedir / cubiertas",
         f"{g2('piezas_ve_a_expedir', 'chasis_ve_a_expedir', d=0)} / "
         f"{g2('piezas_ve_cubiertas', 'piezas_ve_cubiertos', 'chasis_ve_cubiertos', d=0)}", "Demanda"),
        ("Piezas COMB a expedir / cubiertas",
         f"{g2('piezas_comb_a_expedir', 'chasis_comb_a_expedir', d=0)} / "
         f"{g2('piezas_comb_cubiertas', 'piezas_comb_cubiertos', 'chasis_comb_cubiertos', d=0)}", "Demanda"),
    ]
    # KPIs de camiones y de plantilla con etiquetas legibles (sin duplicar las tarjetas propias del dashboard)
    out += [
        ("Camiones en el horizonte", g("camiones", 0), "Expediciones"),
        ("Camiones por ciclo (media / máx.)",
         f"{g('camiones_por_ciclo_medio', 1)} / {g('camiones_por_ciclo_max', 0)}", "Expediciones"),
    ]
    hl = horas_libres(plan)
    out.append(("Horas libres (total, KPI principal)", _f(hl["total"], 0) + " h", "Personal"))
    for r, v in hl["roles"].items():
        out.append((f"Horas libres ({NOMBRE_REC[r].lower()})", _f(v["libres"], 0) + " h", "Personal"))
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


def _paradas_activas(rec, esc) -> pd.DataFrame:
    """Paradas (programadas y averías) que solapan con el horizonte del plan."""
    for nombre in ("paradas",):
        df = getattr(esc, nombre, None)
        if isinstance(df, pd.DataFrame) and len(df):
            t0 = pd.Timestamp(rec.inicio)
            t1 = t0 + pd.Timedelta(hours=len(rec.horizonte.slots))
            d = df.copy()
            d["desde"] = pd.to_datetime(d["desde"])
            d["hasta"] = pd.to_datetime(d["hasta"])
            return d[(d["desde"] < t1) & (d["hasta"] > t0)].reset_index(drop=True)
    return pd.DataFrame()


def generar_informe_pdf(rec, esc, ruta, contingencia=None) -> str:
    """Genera el informe PDF. `contingencia` = dict de `rolling.contingencia` si el plan viene de una incidencia."""
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
    aviso = aviso_direccion(rec, contingencia)
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
               ["Estado", f'<font color="{col_est}"><b>{est}</b></font>', "Índice KWD", f"<b>{_f(plan.puntuacion, 1)}</b>"],
               ["Idoneidad (sobre el máximo alcanzable)",
                "<b>—</b> (No aplica: plan de contingencia)" if plan.idoneidad is None
                else f"<b>{_f(plan.idoneidad, 1)} %</b>", "Horizonte", f"{len(horas)} h"]]
    techo = (plan.kpis or {}).get("puntuacion_max_teorica")
    hl_ = horas_libres(plan)
    portada.append(["Máximo alcanzable", "—" if techo is None else f"≈ {_f(techo, 1)} / 100",
                    "Horas libres (KPI principal)", f"<b>{_f(hl_['total'], 0)} h</b>"])
    story.append(_tabla(portada, [3 * cm, 5.5 * cm, 3.5 * cm, ancho - 12 * cm], ss, cabecera=False, zebra=False))
    if es_contingencia:
        story += [Spacer(1, 6), Paragraph(
            '<font color="%s"><b>Plan de contingencia — incumple:</b> %s</font>' % (ROJO, "; ".join(map(str, plan.incumplimientos)) or "ver alertas"),
            ss["Cuerpo"])]

    if aviso:
        caja = Table([[Paragraph("<b>AVISO PARA LA DIRECCIÓN — riesgo de desabastecimiento</b><br/>" +
                                 " ".join(str(aviso).splitlines()),
                                 ParagraphStyle("av", parent=ss["Cuerpo"], textColor=colors.HexColor(ROJO)))]],
                     colWidths=[ancho])
        caja.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor(ROJO)),
                                  ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDECEA")),
                                  ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [Spacer(1, 6), caja]

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
    dv = impacto.get("delta_vs_alternativas") or []
    if dv:
        story.append(_tabla([["Comparación", "Diferencia de puntuación"]] +
                            [[f"Frente a {d['nombre']}", _f(d["puntuacion"], 2) + " puntos"] for d in dv],
                            [ancho * 0.6, ancho * 0.4], ss))
    else:
        story.append(Paragraph("Sin alternativas viables con las que comparar.", ss["Cuerpo"]))
    if contingencia is not None and contingencia.get("rec_antes") is not None:
        pa, _ = _plan_principal(contingencia["rec_antes"])
        story.append(Paragraph(
            f"Frente al plan previo a la incidencia: puntuación {_f(plan.puntuacion - pa.puntuacion, 1)} puntos de "
            f"diferencia; horas libres {_f(hl_['total'] - horas_libres(pa)['total'], 0)} h.", ss["Cuerpo"]))
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

    # ---- personal nominal del turno
    story.append(Paragraph(f"Asignación nominal de personal ({_turno_nombre(turno_act)})", ss["H2k"]))
    try:
        ro = roster_compacto(rec, plan)
    except Exception:  # noqa: BLE001
        ro = None
    if ro:
        story.append(_tabla([["Trabajador", "Células por tramo horario"]] + ro, [2.6 * cm, ancho - 2.6 * cm], ss))
        story.append(Paragraph("Cada trabajador está numerado (turno-rol-nº, p. ej. M-OP07) y puede cubrir varias células "
                               "en la misma hora si su carga suma ≤ 1 (p. ej. C8+C9). La asignación se mantiene estable "
                               "entre horas mientras la célula siga activa; «libre» = presente sin tarea.", ss["Peq"]))
    else:
        story.append(Paragraph("El plan no incluye reparto nominal de personal.", ss["Cuerpo"]))

    # ---- expediciones
    cam = resumen_camiones(rec)
    story.append(Paragraph("Expediciones y camiones", ss["H2k"]))
    if cam:
        story.append(Paragraph(
            f"Ciclos de expedición cada 1,5 h (16 por día laborable). En cada ciclo salen los camiones necesarios "
            f"(m² de la carga / 15 m² por camión, sin obligación de ir llenos). En el horizonte: "
            f"<b>{_f(cam['total'], 0)} camiones</b> en {cam['ciclos']} ciclos "
            f"(media {_f(cam['media'], 1)}, máximo {_f(cam['max'], 0)} por ciclo; ≈ {_f(cam['por_dia'], 0)} camiones/día).",
            ss["Cuerpo"]))
        t_ = cam["tabla"].head(24)
        filas_c = [["Ciclo", "Salida", "Piezas VE", "Piezas COMB", "m²", "Camiones"]]
        for _, r_ in t_.iterrows():
            filas_c.append([str(r_.get("slot", "")), pd.Timestamp(r_["fecha_hora"]).strftime("%d/%m %H:%M")
                            if "fecha_hora" in t_.columns else "—",
                            _f(r_.get("piezas_ve", 0), 0), _f(r_.get("piezas_comb", 0), 0),
                            _f(r_.get("m2", 0), 1), _f(r_.get("n_camiones", 0), 0)])
        story.append(_tabla(filas_c, [1.6 * cm, 3 * cm, 3 * cm, 3 * cm, 2.6 * cm, ancho - 13.2 * cm], ss))
    else:
        story.append(Paragraph("Sin información de camiones en el horizonte.", ss["Cuerpo"]))

    # ---- contingencia (incidencia)
    par_act = _paradas_activas(rec, esc)
    aver = par_act[par_act["tipo"].astype(str).str.upper().str.startswith("AVER")] if len(par_act) else par_act
    if contingencia is not None or len(aver):
        story.append(Paragraph("Contingencia", ss["H1k"]))
        if len(aver):
            story.append(Paragraph("Averías vigentes en el horizonte: " + "; ".join(
                f"célula {int(r_['celula'])} desde {pd.Timestamp(r_['desde']):%d/%m %H:%M} hasta "
                f"{pd.Timestamp(r_['hasta']):%d/%m %H:%M}" for _, r_ in aver.iterrows()) + ".", ss["Cuerpo"]))
        if contingencia is not None:
            for t_ in contingencia.get("resumen") or []:
                story.append(Paragraph("• " + str(t_), ss["Cuerpo"]))
            ag = tabla_agotamiento(contingencia.get("agotamiento"))
            if len(ag):
                ag = ag.copy()
                for c_ in ag.columns:
                    if "hora" in str(c_):
                        ag[c_] = ag[c_].map(lambda x: pd.Timestamp(x).strftime("%d/%m %H:%M") if pd.notna(x) else "—")
                ag.columns = [{"celula": "Pieza", "pieza": "Pieza", "hora_bajo_ss": "Baja del SS",
                               "hora_sin_stock": "Sin stock"}.get(c, str(c).replace("_", " ")) for c in ag.columns]
                story.append(Paragraph("Consumo de stock de seguridad y agotamiento por pieza", ss["H2k"]))
                story.append(_df_a_tabla(ag, ss, max_filas=30))
            ds = contingencia.get("desabastecimiento")
            if isinstance(ds, pd.DataFrame) and len(ds):
                story.append(Paragraph("Piezas no servidas por ciclo de expedición", ss["H2k"]))
                story.append(_df_a_tabla(ds, ss, max_filas=40))
            ru = contingencia.get("reubicacion")
            if isinstance(ru, pd.DataFrame) and len(ru):
                d_ = ru.copy()
                for c_ in ("desde", "hasta"):
                    if c_ in d_:
                        d_[c_] = d_[c_].map(lambda x: pd.Timestamp(x).strftime("%d/%m %H:%M") if pd.notna(x) else "—")
                d_.columns = [{"persona": "Trabajador", "trabajador": "Trabajador", "rol": "Rol", "de_celula": "De célula", "a_celulas": "A células",
                               "desde": "Desde", "hasta": "Hasta"}.get(c, c) for c in d_.columns]
                story.append(Paragraph("Reubicación de personas", ss["H2k"]))
                story.append(_df_a_tabla(d_, ss, max_filas=30))
            mq = contingencia.get("maquinas")
            if isinstance(mq, pd.DataFrame) and len(mq):
                mq2 = mq.copy()
                mq2.columns = [{"celula": "Célula", "horas_antes": "Horas antes", "horas_despues": "Horas después",
                                "delta": "Δ horas", "franjas_nuevas": "Franjas nuevas"}.get(c, c) for c in mq2.columns]
                story.append(Paragraph("Máquinas a activar o ampliar", ss["H2k"]))
                story.append(_df_a_tabla(mq2, ss, max_filas=30))

    # ---- horas libres
    story.append(Paragraph("Horas libres del personal (KPI principal)", ss["H1k"]))
    story.append(Paragraph(
        f"Horas libres = horas-persona del personal presente sin tarea asignada. Total en el horizonte: "
        f"<b>{_f(hl_['total'], 0)} h</b>. Cuanto menor, mejor aprovechado está el personal.", ss["Cuerpo"]))
    filas = [["Rol", "Horas libres", "Horas disponibles", "Ocupación"]]
    for r_, v_ in hl_["roles"].items():
        filas.append([NOMBRE_REC[r_], _f(v_["libres"], 0) + " h",
                      "—" if v_["disponibles"] is None else _f(v_["disponibles"], 0) + " h",
                      "—" if v_["ocupacion_pct"] is None else _f(v_["ocupacion_pct"], 0) + " %"])
    filas.append(["<b>Total</b>", f"<b>{_f(hl_['total'], 0)} h</b>", "", ""])
    story.append(_tabla(filas, [ancho * 0.34, ancho * 0.22, ancho * 0.24, ancho * 0.20], ss))

    # ---- stock vs óptimo
    try:
        svo = stock_vs_optimo(rec, esc, plan)
    except Exception:  # noqa: BLE001
        svo = pd.DataFrame()
    if len(svo):
        rs = resumen_stock_optimo(svo)
        story.append(Paragraph("Stock frente al óptimo en los cierres de turno", ss["H1k"]))
        story.append(Paragraph(
            "Stock óptimo = stock de seguridad + demanda de un turno de la pieza. Desviación media "
            f"<b>{_f(rs['media_pct'], 1)} %</b>, máxima <b>{_f(rs['max_pct'], 1)} %</b>; piezas por debajo del SS en "
            f"{rs['bajo_ss']} cierre(s)-pieza.", ss["Cuerpo"]))
        cierres = list(dict.fromkeys(svo["cierre"]))
        filas = [["Pieza", "SS", "Óptimo"] + [pd.Timestamp(c).strftime("%d/%m %H:%M") for c in cierres]]
        for c_, g_ in svo.groupby("celula"):
            filas.append([f"C{c_} ({g_['tipo'].iloc[0]})", _f(g_["ss"].iloc[0], 0), _f(g_["optimo"].iloc[0], 0)] +
                         [_f(v, 0) for v in g_["stock"]])
        story.append(_tabla(filas, [2.4 * cm, 1.5 * cm, 1.8 * cm] + [(ancho - 5.7 * cm) / max(len(cierres), 1)] * len(cierres), ss))

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
    filas.append(["<b>Índice KWD</b>", "100 %", f"<b>{_f(plan.puntuacion, 2)}</b>"])
    story.append(_tabla(filas, [ancho * 0.5, ancho * 0.2, ancho * 0.3], ss))

    # ---- alternativas
    story.append(Paragraph("Alternativas (Top 1/2/3)", ss["H1k"]))
    filas = [["Plan", "Estado", "Índice KWD", "Idoneidad %", "Células activas (turno actual)", "m² medios", "kWh"]]
    opciones = [(f"Top {i + 1}", p_) for i, p_ in enumerate(rec.top)]
    if es_contingencia:
        opciones.append(("Contingencia", rec.contingencia))
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
    story.append(Paragraph("Anexo · Parámetros de lectura", ss["H1k"]))
    sol_i = int(float(esc.parametros.get("solar_ini", 11)))
    sol_f = int(float(esc.parametros.get("solar_fin", 14)))
    story.append(Paragraph(f"<b>Franja solar:</b> {sol_i:02d}:00–{sol_f:02d}:00 (zona amarilla de los gráficos): "
                           f"energía de red con factor reducido.", ss["Cuerpo"]))
    story.append(Paragraph("<b>Stock óptimo:</b> stock de seguridad + demanda de un turno de la pieza (demanda diaria / 3).",
                           ss["Cuerpo"]))

    doc = SimpleDocTemplate(ruta, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.5 * cm,
                            title="KWD · Plan de producción", author="KWD Motor de decisión")
    doc._etiqueta = f"Plan {inicio:%d/%m/%Y %H:%M} · turno {turno_act}"
    doc.build(story, onFirstPage=_pie_cabecera, onLaterPages=_pie_cabecera)
    return ruta



