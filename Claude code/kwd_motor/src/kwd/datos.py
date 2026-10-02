"""Datos de entrada: escenario, estado persistente (JSON) y escenario de demostración (v3)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import (CELULA_LOGISTICA, CODIGO_ROL, DIAS_LABORABLES, HOJAS_COLUMNAS, PARAMETROS_DEFECTO,
                     RECURSOS, TURNOS, TURNOS_POR_DIA)

# Columnas de fecha/hora de cada hoja.
_COLS_FECHA = {
    "DemandaSemanal": ["semana_inicio"],
    "CorreccionDiaria": ["fecha"],
    "Bajas": ["fecha"],
    "BajasTrabajadores": ["fecha"],
    "Paradas": ["desde", "hasta"],
    "Expediciones": ["fecha_hora"],
}

# Tabla de células: una pieza exclusiva por célula, un ciclo = 1 pieza.
# columnas: celula, tipo, ciclo_s, piezas_m2, operarios, picking, carretilleros, mto, calidad, kw
_CELULAS_BASE = [
    (1, "COMB", 50, 36, 1, 0.3, 0.4, 0.35, 0.15, 22),
    (2, "COMB", 50, 36, 1, 0.3, 0.4, 0.35, 0.15, 22),
    (3, "VE", 60, 31, 4, 0.5, 0.8, 1.1, 0.8, 115.42),
    (4, "VE", 60, 64, 1, 0.5, 0.7, 1.4, 0.2, 45.1),
    (5, "COMB", 10, 333, 1, 0, 0.1, 0.9, 0.1, 10),
    (6, "COMB", 40, 38, 1, 0.3, 0.4, 0.05, 0.15, 15),
    (7, "COMB", 40, 10, 1, 0.3, 0.5, 0.1, 0.2, 16),
    (8, "VE", 60, 417, 0.5, 0, 0.2, 0.2, 0.25, 13.79),
    (9, "VE", 30, 1167, 0.5, 0, 0.1, 0.7, 0.15, 10.46),
    (10, "COMB", 60, 0, 1, 0, 0.5, 0, 0, 0),  # servicio logístico, no produce
    (11, "COMB", 40, 20, 3, 0.3, 0.5, 0.5, 0.35, 51),
    (12, "COMB", 40, 20, 3, 0.3, 0.5, 0.6, 0.35, 51),
    (13, "VE", 60, 63, 1, 0.3, 0.4, 1.2, 0.25, 79.6),
    (14, "VE", 60, 12, 3, 0.3, 0.8, 0.9, 0.65, 77.94),
    (15, "VE", 60, 12, 3, 0.3, 0.8, 0.9, 0.65, 77.95),
    (16, "COMB", 30, 168, 2, 0, 0.4, 0.9, 0.3, 25),
]

FACTOR_STOCK_DEMO = 2.0  # stock inicial de la demo = 2 x SS


@dataclass
class Escenario:
    """Constantes del sistema más las entradas persistentes (demanda, bajas, paradas, stock, expediciones)."""
    celulas: pd.DataFrame
    turnos: pd.DataFrame
    almacen: pd.DataFrame
    parametros: dict
    demanda_semanal: pd.DataFrame
    correccion_diaria: pd.DataFrame
    stock_actual: pd.DataFrame
    bajas: pd.DataFrame
    paradas: pd.DataFrame
    expediciones: pd.DataFrame
    descripciones: dict = field(default_factory=dict)
    bajas_trabajadores: pd.DataFrame = None  # (fecha, trabajador): trabajadores concretos de baja (A7-bis)
    avisos: list = field(default_factory=list)  # avisos de lectura/eventos (p. ej. parada de la célula 10 ignorada)

    def __post_init__(self):
        if self.bajas_trabajadores is None:
            self.bajas_trabajadores = _vacia("BajasTrabajadores")

    def copiar(self) -> "Escenario":
        """Copia profunda del escenario (para aplicar eventos sin tocar el original)."""
        return copy.deepcopy(self)

    # --- accesos de conveniencia -------------------------------------------------------------
    def area_producto_terminado(self) -> float:
        """m² de la zona de producto terminado (incluye stock de seguridad)."""
        fila = self.almacen[self.almacen["zona"] == "producto_terminado"]
        return float(fila["m2"].iloc[0]) if len(fila) else 800.0

    def disp_estandar(self, recurso: str, turno: str) -> float:
        """Disponibilidad estándar de un recurso en un turno (M/T/N)."""
        fila = self.turnos[self.turnos["recurso"] == recurso]
        return float(fila[turno].iloc[0]) if len(fila) else 0.0


# --- utilidades de tablas ---------------------------------------------------------------------
def _vacia(hoja: str) -> pd.DataFrame:
    return pd.DataFrame(columns=HOJAS_COLUMNAS[hoja])


def _normalizar_hoja(hoja: str, df: pd.DataFrame) -> pd.DataFrame:
    """Asegura columnas, orden y tipos de fecha de una hoja."""
    cols = HOJAS_COLUMNAS[hoja]
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[cols].dropna(how="all").reset_index(drop=True)
    for c in _COLS_FECHA.get(hoja, []):
        df[c] = pd.to_datetime(df[c])
        if c in ("fecha", "semana_inicio"):
            df[c] = df[c].dt.normalize()
    return df


def tabla_celulas(esc: Escenario) -> pd.DataFrame:
    """Tabla de células indexada por número con columnas auxiliares (`es_ve`, `cap_h`, `ppc`=1)."""
    t = esc.celulas.set_index("celula").copy()
    t.index = t.index.astype(int)
    t["es_ve"] = t["tipo"].astype(str).str.upper().eq("VE")
    t["ppc"] = 1.0  # un ciclo = 1 pieza; sin ensamblaje (se mantiene la columna por compatibilidad)
    t["cap_h"] = 3600.0 / t["ciclo_s"].astype(float)  # OEE 100 %
    return t


def celulas_productivas(esc: Escenario) -> list[int]:
    """Células que producen piezas (todas menos la logística), ordenadas."""
    return sorted(int(c) for c in esc.celulas["celula"] if int(c) != CELULA_LOGISTICA)


def ss_por_celula(esc: Escenario) -> dict:
    """Stock de seguridad (piezas) por célula productiva. 400 VE / 200 combustión."""
    t = tabla_celulas(esc)
    ss_ve = float(esc.parametros["ss_ve"])
    ss_comb = float(esc.parametros["ss_comb"])
    return {c: (ss_ve if t.loc[c, "es_ve"] else ss_comb) for c in celulas_productivas(esc)}


def stock_optimo(esc: Escenario, fecha) -> dict:
    """Stock óptimo por célula productiva al cierre de un turno del día `fecha`.

    Óptimo = SS + demanda de un turno de la pieza = SS + demanda_dia(tipo de la pieza) / 3.
    """
    t = tabla_celulas(esc)
    ve, comb = demanda_dia(esc, fecha)
    ss = ss_por_celula(esc)
    return {c: ss[c] + (ve if t.loc[c, "es_ve"] else comb) / TURNOS_POR_DIA for c in ss}


def stock_inicial(esc: Escenario) -> dict:
    """Stock inicial por célula productiva (0 si no figura en la hoja StockActual)."""
    base = {c: 0.0 for c in celulas_productivas(esc)}
    for _, f in esc.stock_actual.iterrows():
        if pd.notna(f["celula"]) and int(f["celula"]) in base and pd.notna(f["piezas"]):
            base[int(f["celula"])] = float(f["piezas"])
    return base


def demanda_desde_coches(coches_dia: float, ratio_comb_ve: float = 2.0) -> tuple[float, float]:
    """Piezas por referencia y día (VE, COMB) a partir de coches/día y la proporción COMB:VE.

    Ej.: 1.500 coches/día con doble de combustión -> (500 VE, 1.000 COMB) de cada pieza.
    """
    ve = float(coches_dia) / (1.0 + float(ratio_comb_ve))
    return ve, float(coches_dia) - ve


def demanda_dia(esc: Escenario, fecha) -> tuple[float, float]:
    """Demanda diaria (piezas de cada referencia VE, piezas de cada referencia COMB) del día `fecha`.

    Prioridad: corrección diaria; si no, demanda semanal / 5 en día laborable.
    """
    fecha = pd.Timestamp(fecha).normalize()
    corr = esc.correccion_diaria
    if len(corr):
        m = corr[corr["fecha"] == fecha]
        if len(m):
            f = m.iloc[-1]
            return _num(f["piezas_ve"]), _num(f["piezas_comb"])
    if fecha.weekday() not in DIAS_LABORABLES:
        return 0.0, 0.0
    dem = esc.demanda_semanal
    if not len(dem):
        return 0.0, 0.0
    lunes = fecha - pd.Timedelta(days=fecha.weekday())
    m = dem[dem["semana_inicio"] == lunes]
    if not len(m):
        # semana sin dato -> última semana anterior (o la primera disponible)
        previas = dem[dem["semana_inicio"] <= lunes].sort_values("semana_inicio")
        m = previas.tail(1) if len(previas) else dem.sort_values("semana_inicio").head(1)
    f = m.iloc[-1]
    return _num(f["piezas_ve"]) / 5.0, _num(f["piezas_comb"]) / 5.0


def _num(v) -> float:
    return 0.0 if pd.isna(v) else float(v)


# --- bajas y trabajadores ---------------------------------------------------------------------
def n_bajas(esc: Escenario, fecha_turno, turno: str, rol: str):
    """Bajas registradas del rol en ese turno (None si no hay fila o el valor está vacío)."""
    b = esc.bajas
    if not len(b):
        return None
    m = b[(b["fecha"] == pd.Timestamp(fecha_turno).normalize()) & (b["turno"] == turno)]
    if not len(m):
        return None
    v = m.iloc[-1][rol]
    return None if pd.isna(v) else float(v)


def redondeo_comercial(x: float) -> int:
    """Redondeo 'half up', no el bancario de Python."""
    import math
    return int(math.floor(x + 0.5))


def bajas_efectivas(esc: Escenario, fecha_turno, turno: str, rol: str) -> float:
    """Nº de personas de baja del rol en el turno: valor de la hoja Bajas o, si falta, round(estándar x absentismo)."""
    v = n_bajas(esc, fecha_turno, turno, rol)
    if v is not None:
        return v
    base = esc.disp_estandar(rol, turno)
    return float(redondeo_comercial(base * float(esc.parametros["absentismo"])))


def disp_base(esc: Escenario, fecha_turno, turno: str, rol: str) -> float:
    """Personas disponibles del rol en el turno (estándar - bajas), antes de restar técnicos en paradas."""
    return max(0.0, esc.disp_estandar(rol, turno) - bajas_efectivas(esc, fecha_turno, turno, rol))


def codigo_trabajador(turno: str, rol: str, i: int) -> str:
    """Identificador estable de trabajador: turno-rol-número, p. ej. 'M-OP01', 'T-CA02'."""
    return f"{turno}-{CODIGO_ROL[rol]}{i:02d}"


def trabajadores_disponibles(esc: Escenario, fecha_turno, turno: str, rol: str) -> list[str]:
    """Ids de los trabajadores disponibles del rol en el turno (en orden).

    Parte de 1..estándar, quita los dados de baja por id (hoja BajasTrabajadores) y, si hay más bajas
    que las nominales, quita los de numeración más alta.
    """
    std = int(round(esc.disp_estandar(rol, turno)))
    n = int(round(disp_base(esc, fecha_turno, turno, rol)))
    ids = [codigo_trabajador(turno, rol, i) for i in range(1, std + 1)]
    bt = esc.bajas_trabajadores
    if len(bt):
        fuera = set(bt.loc[bt["fecha"] == pd.Timestamp(fecha_turno).normalize(), "trabajador"].astype(str))
        ids = [i for i in ids if i not in fuera]
    return ids[:n]


# --- paradas ----------------------------------------------------------------------------------
def _limites_turno(fecha, turno: str):
    fecha = pd.Timestamp(fecha).normalize()
    if turno == "DIA":
        return fecha + pd.Timedelta(hours=6), fecha + pd.Timedelta(hours=30)
    ini, fin = TURNOS[turno]
    desde = fecha + pd.Timedelta(hours=ini)
    hasta = fecha + pd.Timedelta(hours=fin if fin > ini else fin + 24)
    return desde, hasta


def _normalizar_paradas(df: pd.DataFrame, avisos: list) -> pd.DataFrame:
    df = _normalizar_hoja("Paradas", df)
    if not len(df):
        return df
    df["celula"] = pd.to_numeric(df["celula"]).astype(int)
    df["tipo"] = (df["tipo"].astype(str).str.strip().str.upper().str.replace("Í", "I", regex=False)
                  .replace({"NAN": "AVERIA", "<NA>": "AVERIA", "": "AVERIA", "BAJA": "AVERIA",
                            "MANTENIMIENTO": "PROGRAMADA"}))
    df["tecnicos"] = pd.to_numeric(df["tecnicos"], errors="coerce").fillna(0.0)
    es10 = df["celula"] == CELULA_LOGISTICA
    if es10.any():
        avisos.append("Se ignora la parada/baja de la célula 10: el servicio logístico no puede pararse.")
        df = df[~es10].reset_index(drop=True)
    return df


# --- estado persistente (data/estado.json) -----------------------------------------------------
ESTADO_DEFECTO = Path(__file__).resolve().parents[2] / "data" / "estado.json"
_CLAVES_ESTADO = {
    "demanda_semanal": "DemandaSemanal", "demanda_diaria": "CorreccionDiaria", "bajas": "Bajas",
    "bajas_trabajadores": "BajasTrabajadores", "paradas": "Paradas", "expediciones_reales": "Expediciones",
}


def _constantes() -> Escenario:
    """Escenario con las constantes fijas del sistema (células, turnos, almacén, parámetros) y entradas vacías."""
    celulas = pd.DataFrame(_CELULAS_BASE, columns=HOJAS_COLUMNAS["Celulas"])
    celulas["celula"] = celulas["celula"].astype(int)
    for c in HOJAS_COLUMNAS["Celulas"][2:]:
        celulas[c] = celulas[c].astype(float)
    turnos = pd.DataFrame({"recurso": RECURSOS, "M": [16, 2, 4, 7, 3], "T": [16, 2, 4, 7, 3], "N": [14, 2, 3, 4, 2]})
    almacen = pd.DataFrame({"zona": ["materia_prima", "cargas", "producto_terminado"], "m2": [400, 100, 800]})
    return Escenario(
        celulas=celulas, turnos=turnos, almacen=almacen,
        parametros={k: v for k, (v, _) in PARAMETROS_DEFECTO.items()},
        demanda_semanal=_vacia("DemandaSemanal"), correccion_diaria=_vacia("CorreccionDiaria"),
        stock_actual=_vacia("StockActual"), bajas=_vacia("Bajas"), paradas=_vacia("Paradas"),
        expediciones=_vacia("Expediciones"), descripciones={k: d for k, (_, d) in PARAMETROS_DEFECTO.items()},
    )


def _registros(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicts serializable en JSON (fechas ISO, NaN/NaT -> null)."""
    out = []
    for _, f in df.iterrows():
        fila = {}
        for k, v in f.items():
            if v is None or (not isinstance(v, (str, bytes)) and pd.isna(v)):
                fila[k] = None
            elif isinstance(v, pd.Timestamp):
                fila[k] = v.isoformat(sep=" ") if (v.hour or v.minute) else f"{v:%Y-%m-%d}"
            elif hasattr(v, "item"):
                fila[k] = v.item()
            else:
                fila[k] = v
        out.append(fila)
    return out


def _tabla(hoja: str, filas) -> pd.DataFrame:
    df = pd.DataFrame(list(filas or []), columns=None)
    if df.empty:
        return _vacia(hoja)
    df = _normalizar_hoja(hoja, df)
    num = [c for c in df.columns if c not in _COLS_FECHA.get(hoja, []) + ["turno", "tipo", "trabajador"]]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("turno", "tipo"):
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.upper()
    return df


def estado_a_dict(esc: Escenario) -> dict:
    """Entradas persistentes del escenario como dict serializable (formato de `data/estado.json`)."""
    return {
        "demanda_semanal": _registros(esc.demanda_semanal),
        "demanda_diaria": _registros(esc.correccion_diaria),
        "bajas": _registros(esc.bajas),
        "bajas_trabajadores": _registros(esc.bajas_trabajadores),
        "paradas": _registros(esc.paradas),
        "stock_actual": _registros(esc.stock_actual),
        "expediciones_reales": _registros(esc.expediciones),
    }


def estado_desde_dict(d: dict) -> Escenario:
    """Construye un escenario (constantes del sistema + entradas del dict)."""
    esc = _constantes()
    esc.demanda_semanal = _tabla("DemandaSemanal", d.get("demanda_semanal"))
    esc.correccion_diaria = _tabla("CorreccionDiaria", d.get("demanda_diaria"))
    b = _tabla("Bajas", d.get("bajas"))
    for r in RECURSOS:
        b[r] = pd.to_numeric(b[r], errors="coerce")
    esc.bajas = b
    esc.bajas_trabajadores = _tabla("BajasTrabajadores", d.get("bajas_trabajadores"))
    esc.expediciones = _tabla("Expediciones", d.get("expediciones_reales"))
    st = d.get("stock_actual")
    if isinstance(st, dict):
        st = [{"celula": int(k), "piezas": v} for k, v in st.items()]
    esc.stock_actual = _tabla("StockActual", st)
    esc.paradas = _normalizar_paradas(_tabla("Paradas", d.get("paradas")), esc.avisos)
    return esc


def guardar_estado(esc: Escenario, path=None) -> Path:
    """Guarda las entradas del escenario en JSON (por defecto `data/estado.json`)."""
    path = Path(path) if path is not None else ESTADO_DEFECTO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(estado_a_dict(esc), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cargar_estado(path=None) -> Escenario:
    """Lee `data/estado.json`. Si el fichero no existe se crea con el escenario de demostración."""
    path = Path(path) if path is not None else ESTADO_DEFECTO
    if not path.exists():
        esc = estado_ejemplo()
        guardar_estado(esc, path)
        return esc
    return estado_desde_dict(json.loads(path.read_text(encoding="utf-8")))


def estado_ejemplo() -> Escenario:
    """Escenario de demostración: semana del 28/09/2026, 1.500 coches/día (2 COMB : 1 VE).

    Demanda semanal por pieza 2.500 VE / 5.000 COMB (500 / 1.000 al día), sin demanda corregida. Parada
    programada de la célula 13 el 02/10 turno T (2 técnicos) y una baja de operario ese turno. Stock inicial 2 x SS.
    """
    esc = _constantes()
    ve, comb = demanda_desde_coches(1500, 2.0)
    esc.demanda_semanal = pd.DataFrame({"semana_inicio": [pd.Timestamp("2026-09-28")],
                                        "piezas_ve": [ve * 5], "piezas_comb": [comb * 5]})
    esc.bajas = pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")], "turno": ["T"], "operarios": [1.0],
                              "picking": [0.0], "carretilleros": [0.0], "mto": [0.0], "calidad": [0.0]})
    esc.paradas = pd.DataFrame({"celula": [13], "desde": [pd.Timestamp("2026-10-02 14:00")],
                                "hasta": [pd.Timestamp("2026-10-02 22:00")], "tipo": ["PROGRAMADA"],
                                "tecnicos": [2.0]})
    ss = ss_por_celula(esc)
    esc.stock_actual = pd.DataFrame({"celula": list(ss.keys()),
                                     "piezas": [float(round(FACTOR_STOCK_DEMO * v)) for v in ss.values()]})
    return esc


crear_escenario_ejemplo = estado_ejemplo  # alias de la v2


def crear_escenario_contingencia() -> Escenario:
    """Demo + avería de la célula 14 desde el 02/10 10:00 hasta el 03/10 06:00."""
    esc = estado_ejemplo()
    nueva = pd.DataFrame({"celula": [14], "desde": [pd.Timestamp("2026-10-02 10:00")],
                          "hasta": [pd.Timestamp("2026-10-03 06:00")], "tipo": ["AVERIA"], "tecnicos": [0.0]})
    esc.paradas = pd.concat([esc.paradas, nueva], ignore_index=True)
    return esc


def validar_ss_almacen(esc: Escenario) -> float:
    """Suma SS_c / densidad_c en m² (debe rondar los 150 m²)."""
    t = tabla_celulas(esc)
    ss = ss_por_celula(esc)
    return float(sum(v / t.loc[c, "piezas_m2"] for c, v in ss.items() if t.loc[c, "piezas_m2"] > 0))
