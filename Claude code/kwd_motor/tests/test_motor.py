"""Pruebas del motor de decisión KWD v2 (pytest)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kwd.config import COMPONENTES, DEFINICION_PLAN_MANUAL, FLAGS, PESO_PARAM, RECURSOS  # noqa: E402
from kwd.datos import (cargar_entrada, crear_escenario_ejemplo, crear_plantilla_contingencia,  # noqa: E402
                       crear_plantilla_ejemplo, demanda_desde_coches, demanda_dia, guardar_entrada,
                       ss_por_celula, validar_ss_almacen)
from kwd.horizonte import construir_horizonte  # noqa: E402
from kwd.motor import recomendar  # noqa: E402
from kwd.rolling import (Evento, aplicar_evento, contingencia_celula, estado_en, reconfigurar,  # noqa: E402
                         simular_semana)
from kwd.validador import validar  # noqa: E402

INICIO = pd.Timestamp("2026-10-02 14:00")


def _esc(tl=5):
    esc = crear_escenario_ejemplo()
    esc.parametros["tiempo_limite_s"] = tl
    return esc


@pytest.fixture(scope="module")
def esc06():
    return _esc()


@pytest.fixture(scope="module")
def rec06(esc06):
    return recomendar(esc06, "2026-10-02 06:00", top_k=3)


# --- viabilidad y reglas ----------------------------------------------------------------------
def test_top3_viables_a_las_06(rec06):
    assert len(rec06.top) == 3 and rec06.contingencia is None
    assert all(p.viable and p.estado in ("OPTIMO", "FACTIBLE") for p in rec06.top)
    assert len({frozenset(p.config_turno_actual) for p in rec06.top}) == 3
    assert all(p.idoneidad is not None and 0 <= p.idoneidad <= 100 for p in rec06.top)


def test_demo_14_es_viable():
    rec = recomendar(_esc(), INICIO, top_k=2)
    assert len(rec.top) >= 1 and rec.contingencia is None and all(p.viable for p in rec.top)


def test_reglas_duras_y_celula_10(esc06, rec06):
    hz = rec06.horizonte
    for p in rec06.top:
        assert validar(esc06, hz, p) == []
        assert (p.activacion[10] == 1).all()  # célula 10 siempre activa (24 h laborables) y nunca parada
        assert (p.activacion[11] == p.activacion[12]).all()


def test_franja_solar_11_14(esc06):
    assert esc06.parametros["solar_ini"] == 11 and esc06.parametros["solar_fin"] == 14
    hz = construir_horizonte(esc06, "2026-10-02 06:00")
    f = hz.slots.set_index("hora")["factor_energia"]
    assert f[11] == f[12] == f[13] == 0.85 and f[14] == 1.0 and f[10] == 1.0


def test_celula_10_no_se_puede_parar(esc06):
    e = aplicar_evento(esc06, Evento("parada_celula", {"celula": 10, "desde": INICIO, "hasta": None,
                                                        "tipo": "AVERIA"}), INICIO)
    assert not (e.paradas["celula"] == 10).any() and any("célula 10" in a for a in e.avisos)
    e.paradas = pd.concat([e.paradas, pd.DataFrame({"celula": [10], "desde": [INICIO], "hasta": [pd.NaT],
                                                    "tipo": ["AVERIA"], "tecnicos": [0.0]})])
    hz = construir_horizonte(e, INICIO)
    assert hz.bloqueos[10] == set() and any("célula 10" in a for a in hz.alertas)
    with pytest.raises(ValueError):
        contingencia_celula(esc06, recomendar(_esc(2), INICIO, top_k=1), 10, INICIO)


# --- piezas, camiones, demanda ------------------------------------------------------------------
def test_demanda_por_piezas_y_coches():
    assert demanda_desde_coches(1500) == (500.0, 1000.0)
    esc = _esc()
    assert demanda_dia(esc, "2026-10-01") == (500.0, 1000.0)
    assert demanda_dia(esc, "2026-10-02") == (520.0, 980.0)
    assert demanda_dia(esc, "2026-10-03") == (0.0, 0.0)


def test_camiones_15m2_y_demanda_completa(esc06):
    hz = construir_horizonte(esc06, "2026-10-02 06:00")
    cam = hz.camiones
    assert list(cam.columns) == ["slot", "fecha_hora", "piezas_ve", "piezas_comb", "m2", "n_camiones", "real"]
    assert len(cam) == 16  # 16 ciclos por día laborable
    assert (cam["fecha_hora"].diff().dropna() == pd.Timedelta(hours=1.5)).all()
    assert (cam["m2"] / cam["n_camiones"] <= 15 + 1e-9).all()  # cada camión <= 15 m²
    assert ((cam["n_camiones"] - 1) * 15 < cam["m2"]).all()  # los justos
    assert cam["piezas_ve"].sum() == pytest.approx(520) and cam["piezas_comb"].sum() == pytest.approx(980)
    assert hz.camiones["fecha_hora"].max() == pd.Timestamp("2026-10-03 04:30")


def test_expedicion_real_sustituye_ciclo(esc06):
    e = aplicar_evento(esc06, Evento("expedicion_real", {"fecha_hora": "2026-10-02 15:00", "piezas_ve": 10,
                                                          "piezas_comb": 5}))
    hz = construir_horizonte(e, INICIO)
    f = hz.camiones[hz.camiones["fecha_hora"] == pd.Timestamp("2026-10-02 15:00")].iloc[0]
    assert f["piezas_ve"] == 10 and f["piezas_comb"] == 5 and f["real"]
    assert len(hz.camiones) == 10


def test_ss_consumible_con_reposicion_y_alerta():
    # stock = SS justo: el primer ciclo se lleva el SS (no es inviable) y debe reponerse con aviso
    esc = _esc(4)
    ss = ss_por_celula(esc)
    esc.stock_actual = pd.DataFrame({"celula": list(ss), "piezas": [1.05 * v for v in ss.values()]})
    rec = recomendar(esc, "2026-10-02 14:00", top_k=1)
    p = rec.mejor
    assert p.viable and rec.contingencia is None
    assert (p.stock.to_numpy() >= -1e-6).all()
    assert (p.stock.to_numpy() < np.array([ss[c] for c in p.stock.columns])[None, :] - 1e-6).any()
    assert any("Stock de seguridad de la pieza" in a and "consumido por expedición" in a for a in p.avisos)
    assert any("repuesto a" in a for a in p.avisos)
    assert any("Stock de seguridad de la pieza" in a for a in rec.alertas)


def test_stock_negativo_es_inviable():
    esc = _esc(3)
    esc.stock_actual["piezas"] = 0.0
    esc.paradas = pd.concat([esc.paradas, pd.DataFrame({"celula": [3], "desde": [pd.Timestamp("2026-10-01")],
                                                        "hasta": [pd.Timestamp("2026-10-04")], "tipo": ["AVERIA"],
                                                        "tecnicos": [0.0]})], ignore_index=True)
    rec = recomendar(esc, INICIO, top_k=1)
    assert rec.top == [] and rec.contingencia is not None
    assert rec.contingencia.estado == "INVIABLE" and rec.contingencia.idoneidad is None
    assert any("pedido no servido" in m for m in rec.contingencia.incumplimientos)
    assert any("INVIABLE" in a for a in rec.alertas)


# --- personal entero y trabajadores ---------------------------------------------------------------
def test_personal_entero_y_plantilla(esc06, rec06):
    hz = rec06.horizonte
    for p in rec06.top + [rec06.baseline]:
        N = p.personas[RECURSOS]
        assert (N.to_numpy() == np.round(N.to_numpy())).all()
        for r in RECURSOS:
            assert (p.recursos[f"{r}_usado"] >= p.recursos[f"{r}_req"] - 1e-6).all()  # N >= Σ carga
            assert (p.recursos[f"{r}_usado"] <= p.recursos[f"{r}_disp"] + 1e-6).all()
            assert p.kpis[f"desperdicio_{r}_h"] >= -1e-6
        assert p.kpis["desperdicio_personal_h"] == pytest.approx(
            sum(p.kpis[f"desperdicio_{r}_h"] for r in RECURSOS))
        # P >= N en cada turno, P <= disponibles
        for ft, tn, slots in hz.turnos_trabajo():
            for r in RECURSOS:
                P = p.plantilla[(p.plantilla["fecha_turno"] == ft) & (p.plantilla["turno"] == tn) &
                                (p.plantilla["rol"] == r)]["plantilla"].iloc[0]
                assert P >= N.loc[slots, r].max()
                assert P <= min(hz.slots[f"{r}_disp"].iloc[slots])


def test_trabajadores_cargas_y_ids_estables(rec06):
    p = rec06.top[0]
    pe = p.personal
    assert list(pe.columns[:8]) == ["slot", "hora", "turno", "rol", "trabajador", "celulas", "carga", "estado"]
    assert (pe["carga"] <= 1 + 1e-9).all()
    assert set(pe["estado"]) <= {"ASIGNADO", "LIBRE", "EXCEDENTE", "PARADA"}
    assert pe["trabajador"].str.match(r"^[MTN]-(OP|PK|CA|MT|CL)\d{2}$").all()
    # Σ cargas asignadas = Σ requisitos por hora y rol
    suma = pe.groupby(["slot", "rol"])["carga"].sum()
    for (h, r), v in suma.items():
        assert v == pytest.approx(p.recursos.at[h, f"{r}_req"], abs=1e-6)
    # ids estables: la plantilla de cada turno y rol es la misma todas las horas
    crew = pe[pe["estado"].isin(["ASIGNADO", "LIBRE"])]
    for _, g in crew.groupby(["fecha_turno", "turno", "rol"]):
        assert len({frozenset(x["trabajador"]) for _, x in g.groupby("slot")}) == 1
    # la célula 10 siempre tiene un operario (y su fracción de carretillero)
    c10 = pe[(pe["estado"] == "ASIGNADO") & pe["celulas"].str.contains(r"C10(?!\d)")]
    assert set(c10["rol"]) >= {"operarios", "carretilleros"}
    assert c10.groupby("slot").size().min() >= 2
    # resumen por trabajador
    tr = p.trabajadores
    assert {"trabajador", "turno", "rol", "horas_asignado", "horas_libre", "celulas"} <= set(tr.columns)
    assert (tr["horas_asignado"] + tr["horas_libre"] + tr["horas_excedente"] <= 8).all()


def test_baja_de_trabajador_concreto(esc06):
    ev = Evento("baja_personal", {"trabajador": "T-OP04", "fecha": "2026-10-02"})
    e = aplicar_evento(esc06, ev)
    assert ((e.bajas_trabajadores["trabajador"] == "T-OP04")).any()
    from kwd.datos import trabajadores_disponibles
    assert "T-OP04" not in trabajadores_disponibles(e, "2026-10-02", "T", "operarios")
    e2 = aplicar_evento(esc06, Evento("baja_personal", {"fecha": "2026-10-01", "turno": "M", "rol": "operarios",
                                                         "cantidad": 2}))
    hz = construir_horizonte(e2, "2026-10-01 06:00")
    assert hz.slots["operarios_disp"].iloc[0] == 16 - (1 + 2)  # absentismo 1 + 2 bajas


# --- puntuación y datos -------------------------------------------------------------------------
def test_puntuacion(esc06, rec06):
    for p in rec06.top + [rec06.baseline]:
        pesos = {c: float(esc06.parametros[PESO_PARAM[c]]) for c in COMPONENTES}
        assert p.puntuacion == pytest.approx(100 * (1 - sum(pesos[c] * p.componentes[c] for c in COMPONENTES)),
                                             abs=1e-6)
        assert all(0 <= p.componentes[c] <= 1 + 1e-9 for c in COMPONENTES)


def test_ss_151_7_y_flags_y_definicion():
    assert validar_ss_almacen(crear_escenario_ejemplo()) == pytest.approx(151.7, abs=0.1)
    assert set(FLAGS) >= {f"F{i}" for i in range(1, 12)}
    assert "pieza exclusiva" in FLAGS["F1"] and "solar" in FLAGS["F5"] and "11-14" in FLAGS["F5"]
    assert DEFINICION_PLAN_MANUAL.startswith("Plan de referencia manual")


def test_excel_roundtrip_y_compatibilidad(tmp_path):
    ruta = crear_plantilla_ejemplo(tmp_path / "x.xlsx")
    esc = cargar_entrada(ruta)
    assert demanda_dia(esc, "2026-10-02") == (520.0, 980.0)
    assert len(esc.paradas) == 1 and esc.paradas["tipo"].iloc[0] == "PROGRAMADA" and esc.paradas["tecnicos"].iloc[0] == 2
    assert len(esc.bajas) == 1
    # Excel v1: chasis_*, Disponibilidad, Mantenimientos, RecursosReales
    base = crear_escenario_ejemplo()
    with pd.ExcelWriter(tmp_path / "v1.xlsx") as w:
        base.celulas.assign(piezas_por_conjunto=1).to_excel(w, sheet_name="Celulas", index=False)
        base.turnos.to_excel(w, sheet_name="Turnos", index=False)
        base.almacen.to_excel(w, sheet_name="Almacen", index=False)
        pd.DataFrame({"semana_inicio": ["2026-09-28"], "chasis_ve": [2400], "chasis_comb": [1600]}).to_excel(
            w, sheet_name="DemandaSemanal", index=False)
        base.stock_actual.to_excel(w, sheet_name="StockActual", index=False)
        pd.DataFrame({"celula": [14], "estado": ["BAJA"], "desde": ["2026-10-02 06:00"],
                      "hasta": ["2026-10-03 06:00"]}).to_excel(w, sheet_name="Disponibilidad", index=False)
        pd.DataFrame({"fecha": ["2026-10-02"], "turno": ["T"], "celula": [13], "tecnicos": [2]}).to_excel(
            w, sheet_name="Mantenimientos", index=False)
        pd.DataFrame({"fecha": ["2026-10-02"], "turno": ["T"], "operarios": [15]}).to_excel(
            w, sheet_name="RecursosReales", index=False)
    v1 = cargar_entrada(tmp_path / "v1.xlsx")
    assert demanda_dia(v1, "2026-10-01") == (480.0, 320.0)
    assert set(v1.paradas["tipo"]) == {"AVERIA", "PROGRAMADA"}
    assert v1.bajas["operarios"].iloc[0] == 1


def test_hojas_vacias_roundtrip(tmp_path):
    esc = _esc(3)
    for hoja in ("correccion_diaria", "bajas", "paradas", "expediciones"):
        setattr(esc, hoja, getattr(esc, hoja).iloc[0:0])
    guardar_entrada(esc, tmp_path / "e.xlsx")
    e2 = cargar_entrada(tmp_path / "e.xlsx")
    assert len(e2.paradas) == 0 and demanda_dia(e2, "2026-10-02") == (500.0, 1000.0)
    assert recomendar(e2, "2026-10-02 06:00", top_k=1).mejor is not None


def test_horizonte_cruza_fin_de_semana():
    esc = _esc(3)
    hz = construir_horizonte(esc, "2026-10-02 22:00")
    s = hz.slots
    assert s["laborable"].iloc[:8].all() and not s["laborable"].iloc[8:].any()
    assert hz.envios.iloc[8:].to_numpy().sum() == 0
    rec = recomendar(esc, "2026-10-02 22:00", top_k=1)
    assert rec.mejor is not None and (rec.mejor.activacion.iloc[8:].to_numpy() == 0).all()


# --- tiempo real, eventos encadenados y contingencia -----------------------------------------------
def test_estado_en_16_filas(esc06, rec06):
    df = estado_en(esc06, rec06, "2026-10-02 10:00")
    assert len(df) == 16
    assert list(df.columns) == ["celula", "tipo", "estado", "personas", "stock", "ss", "cobertura_h",
                                "proxima_activacion"]
    assert set(df["estado"]) <= {"PRODUCIENDO", "EN ESPERA", "PARADA PROGRAMADA", "AVERÍA"}
    assert df.loc[df["celula"] == 10, "estado"].iloc[0] == "PRODUCIENDO"
    # a las 15:00 la 13 está en parada programada
    df2 = estado_en(esc06, rec06, "2026-10-02 15:00")
    assert df2.loc[df2["celula"] == 13, "estado"].iloc[0] == "PARADA PROGRAMADA"


def test_eventos_encadenados_conservan_stock():
    esc = _esc(3)
    rec = recomendar(esc, "2026-10-02 06:00", top_k=1)
    ahora = pd.Timestamp("2026-10-02 09:00")
    esc1, rec1 = reconfigurar(esc, rec, Evento("parada_celula", {"celula": 9, "desde": ahora, "hasta": ahora + pd.Timedelta(hours=4),
                                                                 "tipo": "AVERIA"}), ahora)
    assert rec1.inicio == ahora and rec1.horizonte.bloqueos[9] >= set(range(0, 4))
    stock_plan = rec.mejor.stock.iloc[2]  # final de las 08:00 = stock a las 09:00
    for c, v in stock_plan.items():
        assert rec1.horizonte.stock_inicial[c] == pytest.approx(v)
    # H1: segundo evento en la MISMA hora (ahora = inicio del plan): debe partir del stock actualizado, no del original
    esc2, rec2 = reconfigurar(esc1, rec1, Evento("correccion_demanda", {"fecha": "2026-10-02", "piezas_ve": 520,
                                                                        "piezas_comb": 980}), ahora)
    for c, v in stock_plan.items():
        assert rec2.horizonte.stock_inicial[c] == pytest.approx(v)
    # y el escenario devuelto ya lleva ese stock
    from kwd.datos import stock_inicial
    assert stock_inicial(esc2)[14] == pytest.approx(stock_plan[14])
    # `esc` antiguo (stale) usado con rec1 tampoco corrompe el stock
    _, rec3 = reconfigurar(esc, rec1, Evento("stock_real", {5: 777}), ahora)
    assert rec3.horizonte.stock_inicial[5] == 777 and rec3.horizonte.stock_inicial[14] == pytest.approx(stock_plan[14])


def test_fin_parada_reactiva():
    esc = aplicar_evento(_esc(), Evento("parada_celula", {"celula": 4, "desde": "2026-10-02 06:00", "hasta": None,
                                                          "tipo": "AVERIA"}))
    esc = aplicar_evento(esc, Evento("fin_parada", {"celula": 4, "ahora": "2026-10-02 10:00"}))
    p = esc.paradas[esc.paradas["celula"] == 4]
    assert len(p) == 1 and p["hasta"].iloc[0] == pd.Timestamp("2026-10-02 10:00")


@pytest.fixture(scope="module")
def contingencia():
    esc = _esc(4)
    rec = recomendar(esc, "2026-10-02 06:00", top_k=1)
    act = rec.mejor.activacion[14]
    h = next(i for i in range(len(act)) if act[i] > 0.5)  # primera hora con la C14 activa
    desde = rec.horizonte.slots["inicio"].iloc[h]
    return contingencia_celula(esc, rec, 14, desde, pd.Timestamp("2026-10-03 06:00")), desde


def test_contingencia_c14(contingencia):
    d, desde = contingencia
    assert set(d) >= {"rec_antes", "rec_despues", "reubicacion", "maquinas", "agotamiento", "resumen"}
    ru = d["reubicacion"]
    assert list(ru.columns) == ["persona", "rol", "de_celula", "a_celulas", "desde", "hasta"]
    ops = ru[(ru["rol"] == "operarios") & ru["de_celula"].str.contains(r"C14(?!\d)") & (ru["desde"] == desde)]
    assert ops["persona"].nunique() == 3  # los 3 operarios de la C14
    assert (ru["persona"].str.match(r"^[MTN]-(OP|PK|CA|MT|CL)\d{2}$")).all()
    assert len(d["resumen"]) > 0 and any("operarios de la C14" in m for m in d["resumen"])
    assert d["rec_despues"].inicio == desde
    assert (d["rec_despues"].mejor.activacion[14] == 0).all()
    assert {"celula", "horas_antes", "horas_despues", "delta", "franjas_nuevas"} == set(d["maquinas"].columns)
    assert set(d["agotamiento"]) >= {"hora_bajo_ss", "hora_sin_stock"}


# --- plantillas de demo y semana -----------------------------------------------------------------
def test_escenario_contingencia_xlsx(tmp_path):
    esc = cargar_entrada(crear_plantilla_contingencia(tmp_path / "c.xlsx"))
    assert (esc.paradas["tipo"] == "AVERIA").sum() == 1 and (esc.paradas["celula"] == 14).any()
    esc.parametros["tiempo_limite_s"] = 4
    rec = recomendar(esc, "2026-10-02 10:00", top_k=1)
    assert rec.mejor is not None and (rec.mejor.activacion[14] == 0).all()
    assert (validar(esc, rec.horizonte, rec.mejor) == []) or not rec.mejor.viable


def test_simular_semana_devuelve_filas_por_turno():
    esc = _esc()
    df = simular_semana(esc, "2026-09-28", iteraciones=3)  # la semana completa son 15 turnos (~90 s)
    assert len(df) == 3 and list(df["turno"][:3]) == ["M", "T", "N"]
    assert df["tiempo_s"].max() < 15
    assert (df["estado"] != "INVIABLE").all()
