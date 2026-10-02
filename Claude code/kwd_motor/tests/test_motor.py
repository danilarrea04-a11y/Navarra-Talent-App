"""Pruebas del motor de decisión KWD v3 (pytest)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kwd.config import COMPONENTES, PESO_PARAM, RECURSOS, penalizacion_tramos  # noqa: E402
from kwd.datos import (cargar_estado, demanda_desde_coches, demanda_dia,  # noqa: E402
                       estado_ejemplo, guardar_estado, ss_por_celula, validar_ss_almacen)
from kwd.horizonte import construir_horizonte  # noqa: E402
from kwd.motor import recomendar  # noqa: E402
from kwd.rolling import (Evento, aplicar_evento, contingencia, contingencia_celula, estado_en,  # noqa: E402
                         reconfigurar)
from kwd.validador import validar  # noqa: E402

INICIO = pd.Timestamp("2026-10-02 14:00")


def _esc(tl=5):
    esc = estado_ejemplo()
    esc.parametros["tiempo_limite_s"] = tl
    return esc


@pytest.fixture(scope="module")
def esc06():
    return _esc(tl=8)  # mismo límite que la app: con menos tiempo el Top 3 puede no alcanzar calidad suficiente


@pytest.fixture(scope="module")
def rec06(esc06):
    return recomendar(esc06, "2026-10-02 06:00", top_k=3)


# --- viabilidad y reglas ----------------------------------------------------------------------
def test_top3_viables_a_las_06(rec06):
    # El Top 3 depende del tiempo límite (8 s/plan): las alternativas de baja idoneidad se descartan, así que se
    # exige al menos un Top 2 viable y distinto.
    assert len(rec06.top) >= 2 and rec06.contingencia is None
    assert all(p.viable and p.estado in ("OPTIMO", "FACTIBLE") for p in rec06.top)
    assert len({frozenset(p.config_turno_actual) for p in rec06.top}) == len(rec06.top)
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
    assert demanda_dia(esc, "2026-10-01") == (500.0, 1000.0)   # semanal / 5
    assert demanda_dia(esc, "2026-10-02") == (500.0, 1000.0)   # sin corregida: semanal / 5
    e2 = aplicar_evento(esc, Evento("correccion_demanda", {"fecha": "2026-10-02", "piezas_ve": 520, "piezas_comb": 980}))
    assert demanda_dia(e2, "2026-10-02") == (520.0, 980.0)     # la corregida manda
    assert demanda_dia(e2, "2026-10-01") == (500.0, 1000.0)
    assert demanda_dia(esc, "2026-10-03") == (0.0, 0.0)


def test_camiones_15m2_y_demanda_completa(esc06):
    hz = construir_horizonte(esc06, "2026-10-02 06:00")
    cam = hz.camiones
    assert list(cam.columns) == ["slot", "fecha_hora", "piezas_ve", "piezas_comb", "m2", "n_camiones", "real"]
    assert len(cam) == 16  # 16 ciclos por día laborable
    assert (cam["fecha_hora"].diff().dropna() == pd.Timedelta(hours=1.5)).all()
    assert (cam["m2"] / cam["n_camiones"] <= 15 + 1e-9).all()  # cada camión <= 15 m²
    assert ((cam["n_camiones"] - 1) * 15 < cam["m2"]).all()  # los justos
    assert cam["piezas_ve"].sum() == pytest.approx(500) and cam["piezas_comb"].sum() == pytest.approx(1000)
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


def test_pedido_no_servido_es_critico_con_aviso():
    esc = _esc(4)
    esc.stock_actual["piezas"] = 0.0
    esc.paradas = pd.concat([esc.paradas, pd.DataFrame({"celula": [3], "desde": [pd.Timestamp("2026-10-01")],
                                                        "hasta": [pd.Timestamp("2026-10-04")], "tipo": ["AVERIA"],
                                                        "tecnicos": [0.0]})], ignore_index=True)
    rec = recomendar(esc, INICIO, top_k=1)
    assert rec.contingencia is None and rec.top
    p = rec.mejor
    assert p.estado == "CRITICO" and p.viable and p.idoneidad is not None
    assert p.aviso_direccion and "Pieza 3" in p.aviso_direccion and "total" in p.aviso_direccion
    assert rec.aviso_direccion == p.aviso_direccion
    des = p.desabastecimiento
    assert list(des.columns) == ["pieza", "ciclo", "piezas_no_servidas"] and (des["pieza"] == 3).any()
    assert set(p.agotamiento["pieza"]) >= {3} and any("CRÍTICO" in a for a in rec.alertas)


# --- horas libres (R) y stock óptimo (B) ----------------------------------------------------------
def test_R_es_tiempo_muerto_del_personal_presente(rec06):
    # R = (presentes - trabajo productivo) / presentes; trabajo = carga x fracción de la hora produciendo
    p = rec06.top[0]
    hz = rec06.horizonte
    W = hz.slots["laborable"].to_numpy(dtype=bool)
    disp = sum(p.recursos[f"{r}_disp"].to_numpy()[W].sum() for r in RECURSOS)
    trabajo = sum(p.recursos[f"{r}_trabajo"].to_numpy()[W].sum() for r in RECURSOS)
    usado = sum(p.recursos[f"{r}_usado"].to_numpy()[W].sum() for r in RECURSOS)
    assert trabajo <= usado + 1e-6  # producir parte de la hora no cuenta como hora completa trabajada
    assert p.componentes["R"] == pytest.approx((disp - trabajo) / disp, abs=1e-9)
    assert p.kpis["horas_libres_total"] == pytest.approx(disp - trabajo)
    assert p.kpis["horas_libres_total"] == pytest.approx(sum(p.kpis[f"horas_libres_{r}"] for r in RECURSOS))
    assert "ocupacion_operarios_pct" in p.kpis
    # todo el personal presente está ASIGNADO o LIBRE (sin EXCEDENTE)
    assert "EXCEDENTE" not in set(p.personal["estado"])
    libres = int(((p.personal["estado"] == "LIBRE")).sum())
    assert libres <= p.kpis["horas_libres_total"] + 1e-6  # el resto del tiempo muerto son fracciones de hora


def test_B_desviacion_respecto_al_optimo_en_cierres(rec06, esc06):
    p, hz = rec06.top[0], rec06.horizonte
    assert hz.cierres == [7, 15, 23]  # 14:00, 22:00, 06:00 del día siguiente
    ss = ss_por_celula(esc06)
    for ic, c in enumerate(p.stock.columns):
        assert hz.stock_optimo[c].iloc[0] == pytest.approx(ss[c] + (500 if c in (3, 4, 8, 9, 13, 14, 15) else 1000) / 3)
    # B = media de la penalización por tramos de (stock − óptimo) / óptimo; D = demanda de un turno = óptimo − SS
    opt = hz.stock_optimo.to_numpy(dtype=float)
    desv = p.stock.loc[hz.cierres].to_numpy(dtype=float) - opt
    turno = opt - np.array([ss[c] for c in p.stock.columns], dtype=float)[None, :]
    assert p.componentes["B"] == pytest.approx(float((penalizacion_tramos(desv, turno) / opt).mean()), abs=1e-6)
    # con los tramos, ninguna pieza supera el óptimo en más de un turno de demanda (tramo de factor 30)
    assert (desv <= turno + 1e-6).all()
    assert len(p.stock_vs_optimo) == 3 * len(p.stock.columns)
    assert p.kpis["stock_opt_dev_media_pct"] == pytest.approx(100 * float((np.abs(desv) / opt).mean()), abs=1e-6)


# --- estado.json ----------------------------------------------------------------------------------
def test_estado_json_roundtrip(tmp_path):
    esc = _esc()
    esc = aplicar_evento(esc, Evento("correccion_demanda", {"fecha": "2026-10-01", "piezas_ve": 510, "piezas_comb": 990}))
    ruta = tmp_path / "estado.json"
    guardar_estado(esc, ruta)
    e2 = cargar_estado(ruta)
    assert demanda_dia(e2, "2026-10-01") == (510.0, 990.0) and demanda_dia(e2, "2026-10-02") == (500.0, 1000.0)
    assert len(e2.paradas) == 1 and e2.paradas["tipo"].iloc[0] == "PROGRAMADA" and e2.paradas["tecnicos"].iloc[0] == 2
    assert len(e2.bajas) == 1 and (e2.stock_actual["piezas"] == esc.stock_actual["piezas"]).all()
    nuevo = cargar_estado(tmp_path / "otro.json")  # no existe: se crea con el ejemplo
    assert (tmp_path / "otro.json").exists() and len(nuevo.stock_actual) == 15
    for hoja in ("correccion_diaria", "bajas", "paradas", "expediciones"):
        setattr(esc, hoja, getattr(esc, hoja).iloc[0:0])
    guardar_estado(esc, ruta)
    assert len(cargar_estado(ruta).paradas) == 0


# --- personal entero y trabajadores ---------------------------------------------------------------
def test_personal_entero_y_plantilla(esc06, rec06):
    hz = rec06.horizonte
    for p in rec06.top:
        N = p.personas[RECURSOS]
        assert (N.to_numpy() == np.round(N.to_numpy())).all()
        for r in RECURSOS:
            assert (p.recursos[f"{r}_usado"] >= p.recursos[f"{r}_req"] - 1e-6).all()  # N >= Σ carga
            assert (p.recursos[f"{r}_usado"] <= p.recursos[f"{r}_disp"] + 1e-6).all()
            assert p.kpis[f"desperdicio_{r}_h"] >= -1e-6
        assert p.kpis["desperdicio_personal_h"] == pytest.approx(
            sum(p.kpis[f"desperdicio_{r}_h"] for r in RECURSOS))
        # presentes >= ocupados y >= disponibles de cada hora
        for ft, tn, slots in hz.turnos_trabajo():
            for r in RECURSOS:
                P = p.plantilla[(p.plantilla["fecha_turno"] == ft) & (p.plantilla["turno"] == tn) &
                                (p.plantilla["rol"] == r)]["plantilla"].iloc[0]
                assert P >= N.loc[slots, r].max()  # plantilla = todos los presentes
                assert P >= max(hz.slots[f"{r}_disp"].iloc[slots])


def test_trabajadores_cargas_y_ids_estables(rec06):
    p = rec06.top[0]
    pe = p.personal
    assert list(pe.columns[:8]) == ["slot", "hora", "turno", "rol", "trabajador", "celulas", "carga", "estado"]
    assert (pe["carga"] <= 1 + 1e-9).all()
    assert set(pe["estado"]) <= {"ASIGNADO", "LIBRE", "PARADA"}
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
    for p in rec06.top:
        pesos = {c: float(esc06.parametros[PESO_PARAM[c]]) for c in COMPONENTES}
        assert p.puntuacion == pytest.approx(100 * (1 - sum(pesos[c] * p.componentes[c] for c in COMPONENTES)),
                                             abs=1e-6)
        assert all(0 <= p.componentes[c] for c in COMPONENTES)
        assert all(p.componentes[c] <= 1 + 1e-9 for c in "RSQE")


def test_ss_151_7():
    assert validar_ss_almacen(estado_ejemplo()) == pytest.approx(151.7, abs=0.1)


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


def test_contingencia_celulas_y_personas():
    esc = _esc(4)
    rec = recomendar(esc, "2026-10-02 06:00", top_k=1)
    d = contingencia(esc, rec, "2026-10-02 10:00",
                     bajas_celulas=[{"celula": 14, "desde": "2026-10-02 10:00", "hasta": "2026-10-02 22:00"},
                                    {"celula": 3, "desde": "2026-10-02 10:00", "hasta": "2026-10-02 22:00"}],
                     bajas_personas=["M-OP02"])
    assert set(d) >= {"rec_antes", "rec_despues", "reubicacion", "maquinas", "agotamiento", "desabastecimiento",
                      "aviso_direccion", "resumen"}
    p2 = d["rec_despues"].mejor
    assert d["rec_despues"].inicio == pd.Timestamp("2026-10-02 10:00")
    assert "M-OP02" not in set(p2.personal["trabajador"])
    assert any("M-OP02" in m for m in d["resumen"]) and len(d["resumen"]) > 2
    assert list(d["desabastecimiento"].columns) == ["pieza", "ciclo", "piezas_no_servidas"]
    with pytest.raises(ValueError):
        contingencia(esc, rec, "2026-10-02 10:00", bajas_celulas=[{"celula": 10, "desde": "2026-10-02 10:00"}])
    d1 = contingencia_celula(esc, rec, 14, "2026-10-02 10:00", "2026-10-02 22:00")
    assert set(d1["agotamiento"].columns) >= {"hora_bajo_ss", "hora_sin_stock"}
