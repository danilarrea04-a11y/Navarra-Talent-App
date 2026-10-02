"""Pruebas del motor de decisión KWD (pytest)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kwd.config import COMPONENTES, PESO_PARAM, FLAGS  # noqa: E402
from kwd.datos import (cargar_entrada, crear_escenario_ejemplo, crear_plantilla_ejemplo, demanda_dia,  # noqa: E402
                       guardar_entrada, ss_por_celula, validar_ss_almacen)
from kwd.horizonte import construir_horizonte  # noqa: E402
from kwd.motor import recomendar  # noqa: E402
from kwd.rolling import Evento, aplicar_evento, reconfigurar, simular_semana  # noqa: E402
from kwd.validador import validar  # noqa: E402

INICIO = pd.Timestamp("2026-10-02 14:00")


def _escenario_holgado(mult=2.0):
    """Escenario de ejemplo con stock inicial = mult x SS (permite varias alternativas viables)."""
    esc = crear_escenario_ejemplo()
    ss = ss_por_celula(esc)
    esc.stock_actual = pd.DataFrame({"celula": list(ss), "piezas": [mult * v for v in ss.values()]})
    return esc


@pytest.fixture(scope="module")
def esc_holgado():
    return _escenario_holgado()


@pytest.fixture(scope="module")
def rec_top3(esc_holgado):
    return recomendar(esc_holgado, INICIO, top_k=3)


def test_hay_top3_viables(rec_top3):
    assert len(rec_top3.top) == 3
    assert rec_top3.contingencia is None
    assert all(p.estado in ("OPTIMO", "FACTIBLE") for p in rec_top3.top)
    assert all(0 <= p.idoneidad <= 100 for p in rec_top3.top)


def test_reglas_duras_nunca_violadas(esc_holgado, rec_top3):
    hz = rec_top3.horizonte
    for p in rec_top3.top:
        assert validar(esc_holgado, hz, p) == []
        assert p.viable and p.incumplimientos == []


def test_celula_10_siempre_activa_en_horas_laborables(rec_top3):
    hz = rec_top3.horizonte
    lab = hz.slots.index[hz.slots["laborable"]]
    assert len(lab) > 0
    for p in rec_top3.top:
        assert (p.activacion.loc[lab, 10] == 1).all()
        assert (p.activacion.loc[~hz.slots["laborable"], :].to_numpy() == 0).all()


def test_celulas_11_y_12_misma_activacion(rec_top3):
    for p in rec_top3.top + [rec_top3.baseline]:
        assert (p.activacion[11] == p.activacion[12]).all()
        assert ((p.uso[11] - p.uso[12]).abs() < 1e-9).all()


def test_celula_de_baja_no_aparece_en_ninguna_config():
    esc = _escenario_holgado()
    esc = aplicar_evento(esc, Evento("baja_celula", {"celula": 8, "desde": INICIO, "hasta": INICIO + pd.Timedelta(hours=30)}))
    rec = recomendar(esc, INICIO, top_k=3)
    assert len(rec.top) >= 1
    for p in rec.top:
        assert 8 not in p.config_turno_actual
        assert p.activacion[8].sum() == 0
    assert rec.baseline.activacion[8].sum() == 0


def test_top_configs_distintas(rec_top3):
    configs = [frozenset(p.config_turno_actual) for p in rec_top3.top]
    assert len(set(configs)) == len(configs)


def test_puntuacion_es_100_por_uno_menos_componentes_ponderados(esc_holgado, rec_top3):
    for p in rec_top3.top + [rec_top3.baseline]:
        pesos = {c: float(esc_holgado.parametros[PESO_PARAM[c]]) for c in COMPONENTES}
        esperado = 100 * (1 - sum(pesos[c] * p.componentes[c] for c in COMPONENTES))
        assert p.puntuacion == pytest.approx(esperado, abs=1e-6)
        assert sum(p.contribuciones.values()) == pytest.approx(p.puntuacion, abs=1e-6)
        assert all(0 <= p.componentes[c] <= 1 + 1e-9 for c in COMPONENTES)


def test_validacion_stock_seguridad_151_7_m2():
    esc = crear_escenario_ejemplo()
    assert validar_ss_almacen(esc) == pytest.approx(151.7, abs=0.1)


def test_escenario_imposible_es_inviable_como_contingencia():
    esc = _escenario_holgado()
    # célula 3 de baja todo el horizonte con stock inicial muy por debajo del SS
    esc.stock_actual.loc[esc.stock_actual["celula"] == 3, "piezas"] = 50
    esc = aplicar_evento(esc, Evento("baja_celula", {"celula": 3, "desde": INICIO - pd.Timedelta(days=1),
                                                    "hasta": INICIO + pd.Timedelta(days=3)}))
    rec = recomendar(esc, INICIO, top_k=3)
    assert rec.top == []
    assert rec.contingencia is not None
    assert rec.contingencia.estado == "INVIABLE"
    assert not rec.contingencia.viable
    assert len(rec.contingencia.incumplimientos) > 0
    assert any("INVIABLE" in a for a in rec.alertas)


def test_reconfigurar_baja_celula_retira_la_celula(esc_holgado, rec_top3):
    ahora = INICIO + pd.Timedelta(hours=2)
    ev = Evento("baja_celula", {"celula": 9, "desde": ahora, "hasta": ahora + pd.Timedelta(hours=6)})
    rec2 = reconfigurar(esc_holgado, rec_top3, ev, ahora)
    assert rec2.inicio == ahora
    plan = rec2.mejor
    assert plan is not None
    assert plan.activacion.loc[0:5, 9].sum() == 0
    # el stock de partida procede del plan vigente (no del escenario inicial)
    assert rec2.horizonte.bloqueos[9] == set(range(0, 6))


def test_simular_semana_devuelve_15_filas():
    df = simular_semana(crear_escenario_ejemplo(), "2026-09-28")
    assert len(df) == 15
    assert list(df["turno"][:3]) == ["M", "T", "N"]
    assert df["tiempo_s"].max() < 15
    # con la condición terminal la semana de la demo se mantiene viable y sin romper el stock de seguridad
    assert (df["estado"] != "INVIABLE").all()
    assert (df["stock_min_ratio_ss"] >= 1 - 1e-6).all()


def test_horizonte_cruza_fin_de_semana():
    esc = crear_escenario_ejemplo()
    hz = construir_horizonte(esc, "2026-10-02 22:00")  # viernes noche
    s = hz.slots
    # la noche del viernes (hasta el sábado 06:00) es laborable; el resto no
    assert s["laborable"].iloc[:8].all()
    assert not s["laborable"].iloc[8:].any()
    assert hz.envios.iloc[8:].to_numpy().sum() == 0  # sin expediciones el fin de semana
    # camiones del viernes hasta las 03:00 del sábado
    assert hz.camiones["hora"].max() <= pd.Timestamp("2026-10-03 03:00")
    rec = recomendar(_escenario_holgado(), "2026-10-02 22:00", top_k=1)
    assert rec.mejor is not None
    assert (rec.mejor.activacion.iloc[8:].to_numpy() == 0).all()


def test_recurso_con_disponibilidad_cero_no_rompe():
    esc = _escenario_holgado()
    esc.recursos_reales = pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")], "turno": ["T"], "operarios": [15],
                                        "picking": [0], "carretilleros": [pd.NA], "mto": [pd.NA],
                                        "calidad": [pd.NA]})
    rec = recomendar(esc, INICIO, top_k=1)
    p = rec.mejor
    assert p is not None
    # picking = 0 en el turno de tarde: sólo pueden activarse células sin picking
    tarde = rec.horizonte.slots.index[rec.horizonte.slots["turno"] == "T"]
    assert (p.recursos.loc[tarde, "picking_usado"] == 0).all()
    assert validar(esc, rec.horizonte, p) == []


def test_hojas_opcionales_vacias_y_roundtrip(tmp_path):
    esc = crear_escenario_ejemplo()
    for hoja in ("correccion_diaria", "disponibilidad", "mantenimientos", "recursos_reales", "expediciones"):
        setattr(esc, hoja, getattr(esc, hoja).iloc[0:0])
    ruta = tmp_path / "e.xlsx"
    guardar_entrada(esc, ruta)
    esc2 = cargar_entrada(ruta)
    assert len(esc2.mantenimientos) == 0 and len(esc2.expediciones) == 0
    assert demanda_dia(esc2, "2026-10-02") == (480.0, 320.0)
    rec = recomendar(esc2, "2026-10-02 06:00", top_k=1)
    assert rec.mejor is not None


def test_plantilla_ejemplo_y_demanda(tmp_path):
    ruta = crear_plantilla_ejemplo(tmp_path / "x.xlsx")
    esc = cargar_entrada(ruta)
    assert demanda_dia(esc, "2026-10-02") == (520.0, 300.0)  # corrección diaria
    assert demanda_dia(esc, "2026-10-01") == (480.0, 320.0)  # semanal / 5
    assert demanda_dia(esc, "2026-10-03") == (0.0, 0.0)  # sábado
    assert set(FLAGS) >= {f"F{i}" for i in range(1, 12)}


def test_expedicion_real_sustituye_prevision():
    esc = _escenario_holgado()
    esc = aplicar_evento(esc, Evento("expedicion_real", {"fecha_hora": "2026-10-02 15:00", "ve": 10, "comb": 5}))
    hz = construir_horizonte(esc, INICIO)
    fila = hz.camiones[hz.camiones["hora"] == pd.Timestamp("2026-10-02 15:00")].iloc[0]
    assert fila["ve"] == 10 and fila["comb"] == 5 and fila["real"]
    assert len(hz.camiones) == 9




@pytest.fixture(scope="module")
def rec_demo_06():
    return recomendar(crear_escenario_ejemplo(), "2026-10-02 06:00", top_k=3)


def test_demo_06_tiene_top3_viables_distintos(rec_demo_06):
    assert len(rec_demo_06.top) == 3 and rec_demo_06.contingencia is None
    assert all(p.estado in ("OPTIMO", "FACTIBLE") for p in rec_demo_06.top)
    assert len({frozenset(p.config_turno_actual) for p in rec_demo_06.top}) == 3
    assert all(p.idoneidad is not None and p.idoneidad > 99 for p in rec_demo_06.top)


def test_demo_adelanta_produccion_de_celula_13(rec_demo_06):
    assert 13 in rec_demo_06.top[0].config_turno_actual
    assert any("Se adelanta producción de la célula 13 por mantenimiento en turno T" in m
               for m in rec_demo_06.explicacion["porque"])


def test_condicion_terminal_de_stock_en_la_demo():
    # jueves 14:00: el horizonte acaba el viernes 14:00 y quedan camiones previstos después
    esc = crear_escenario_ejemplo()
    esc.parametros["tiempo_limite_s"] = 10
    rec = recomendar(esc, "2026-10-01 14:00", top_k=1)
    hz = rec.horizonte
    ss = ss_por_celula(crear_escenario_ejemplo())
    assert hz.envio_final is not None and hz.envio_final.sum() > 0
    assert rec.top
    for p in rec.top:
        final = p.stock.iloc[-1]
        for c in p.stock.columns:
            assert final[c] >= ss[c] + hz.envio_final[c] - 1e-3
        assert p.avisos == []


def test_demo_14_es_viable():
    rec = recomendar(crear_escenario_ejemplo(), INICIO, top_k=3)
    assert len(rec.top) >= 1 and rec.contingencia is None
    assert all(p.viable for p in rec.top)


def test_escenario_contingencia_es_inviable(tmp_path):
    from kwd.datos import crear_plantilla_contingencia
    esc = cargar_entrada(crear_plantilla_contingencia(tmp_path / "c.xlsx"))
    rec = recomendar(esc, "2026-10-02 06:00", top_k=3)
    assert rec.top == [] and rec.contingencia is not None
    assert rec.contingencia.estado == "INVIABLE"
    assert rec.contingencia.idoneidad is None and rec.contingencia.gap is None
    assert any("célula 14" in m for m in rec.contingencia.incumplimientos)
    assert rec.contingencia.idoneidad_txt() == "—"


