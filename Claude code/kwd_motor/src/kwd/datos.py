"""Datos de entrada: escenario, lectura/escritura del Excel y plantilla de ejemplo."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import (CELULA_LOGISTICA, HOJAS_COLUMNAS, PARAMETROS_DEFECTO, RECURSOS,
                     DIAS_LABORABLES)

# Hojas opcionales y columnas de fecha/hora de cada hoja.
_COLS_FECHA = {
    "DemandaSemanal": ["semana_inicio"],
    "CorreccionDiaria": ["fecha"],
    "Disponibilidad": ["desde", "hasta"],
    "Mantenimientos": ["fecha"],
    "RecursosReales": ["fecha"],
    "Expediciones": ["fecha_hora"],
}

# Tabla de células de ttablas.xlsx (hoja "Células"). FLAG F1/F2: una pieza por ciclo y por conjunto.
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
    (10, "COMB", 60, 0, 1, 0, 0.5, 0, 0, 0),  # FLAG F11: servicio logístico, no produce
    (11, "COMB", 40, 20, 3, 0.3, 0.5, 0.5, 0.35, 51),
    (12, "COMB", 40, 20, 3, 0.3, 0.5, 0.6, 0.35, 51),
    (13, "VE", 60, 63, 1, 0.3, 0.4, 1.2, 0.25, 79.6),
    (14, "VE", 60, 12, 3, 0.3, 0.8, 0.9, 0.65, 77.94),
    (15, "VE", 60, 12, 3, 0.3, 0.8, 0.9, 0.65, 77.95),
    (16, "COMB", 30, 168, 2, 0, 0.4, 0.9, 0.3, 25),
]


FACTOR_STOCK_DEMO = 1.8  # FLAG F19


@dataclass
class Escenario:
    """Todas las hojas del Excel de entrada más los parámetros ya completados con valores por defecto."""
    celulas: pd.DataFrame
    turnos: pd.DataFrame
    almacen: pd.DataFrame
    parametros: dict
    demanda_semanal: pd.DataFrame
    correccion_diaria: pd.DataFrame
    stock_actual: pd.DataFrame
    disponibilidad: pd.DataFrame
    mantenimientos: pd.DataFrame
    recursos_reales: pd.DataFrame
    expediciones: pd.DataFrame
    descripciones: dict = field(default_factory=dict)

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
            df[c] = pd.NA if c != "piezas_por_conjunto" else 1
    df = df[cols].dropna(how="all").reset_index(drop=True)
    for c in _COLS_FECHA.get(hoja, []):
        df[c] = pd.to_datetime(df[c])
        if c in ("fecha", "semana_inicio"):
            df[c] = df[c].dt.normalize()
    return df


def tabla_celulas(esc: Escenario) -> pd.DataFrame:
    """Tabla de células indexada por número con columnas auxiliares (`es_ve`, `cap_h`)."""
    t = esc.celulas.set_index("celula").copy()
    t.index = t.index.astype(int)
    t["es_ve"] = t["tipo"].astype(str).str.upper().eq("VE")
    t["ppc"] = t["piezas_por_conjunto"].fillna(1).astype(float)  # FLAG F2
    t["cap_h"] = 3600.0 / t["ciclo_s"].astype(float)  # FLAG F8: OEE 100 %
    return t


def celulas_productivas(esc: Escenario) -> list[int]:
    """Células que producen piezas (todas menos la logística, F11), ordenadas."""
    return sorted(int(c) for c in esc.celulas["celula"] if int(c) != CELULA_LOGISTICA)


def ss_por_celula(esc: Escenario) -> dict:
    """Stock de seguridad (piezas) por célula productiva. FLAG F10: 400 VE / 200 combustión."""
    t = tabla_celulas(esc)
    ss_ve = float(esc.parametros["ss_ve"])
    ss_comb = float(esc.parametros["ss_comb"])
    return {c: (ss_ve if t.loc[c, "es_ve"] else ss_comb) for c in celulas_productivas(esc)}


def stock_inicial(esc: Escenario) -> dict:
    """Stock inicial por célula productiva (0 si no figura en la hoja StockActual)."""
    base = {c: 0.0 for c in celulas_productivas(esc)}
    for _, f in esc.stock_actual.iterrows():
        if pd.notna(f["celula"]) and int(f["celula"]) in base and pd.notna(f["piezas"]):
            base[int(f["celula"])] = float(f["piezas"])
    return base


def demanda_dia(esc: Escenario, fecha) -> tuple[float, float]:
    """Demanda diaria (chasis VE, chasis COMB) del día `fecha`.

    Prioridad: corrección diaria; si no, demanda semanal / 5 en día laborable (F4).
    """
    fecha = pd.Timestamp(fecha).normalize()
    corr = esc.correccion_diaria
    if len(corr):
        m = corr[corr["fecha"] == fecha]
        if len(m):
            f = m.iloc[-1]
            return float(f["chasis_ve"] or 0), float(f["chasis_comb"] or 0)
    if fecha.weekday() not in DIAS_LABORABLES:
        return 0.0, 0.0
    dem = esc.demanda_semanal
    if not len(dem):
        return 0.0, 0.0
    lunes = fecha - pd.Timedelta(days=fecha.weekday())
    m = dem[dem["semana_inicio"] == lunes]
    if not len(m):
        # FLAG F12: semana sin dato -> última semana anterior (o la primera disponible)
        previas = dem[dem["semana_inicio"] <= lunes].sort_values("semana_inicio")
        m = previas.tail(1) if len(previas) else dem.sort_values("semana_inicio").head(1)
    f = m.iloc[-1]
    return float(f["chasis_ve"]) / 5.0, float(f["chasis_comb"]) / 5.0


# --- lectura / escritura ----------------------------------------------------------------------
def cargar_entrada(path) -> Escenario:
    """Lee el Excel de entrada. Las hojas opcionales ausentes o vacías se tratan como tablas vacías."""
    path = Path(path)
    hojas = pd.read_excel(path, sheet_name=None)
    hojas = {k.strip(): v for k, v in hojas.items()}

    def hoja(nombre):
        df = hojas.get(nombre)
        if df is None:
            return _vacia(nombre)
        return _normalizar_hoja(nombre, df)

    celulas = hoja("Celulas")
    if celulas.empty:
        raise ValueError("La hoja 'Celulas' es obligatoria y está vacía o no existe.")
    celulas["piezas_por_conjunto"] = celulas["piezas_por_conjunto"].fillna(1)
    for c in HOJAS_COLUMNAS["Celulas"][2:]:
        celulas[c] = pd.to_numeric(celulas[c]).astype(float)
    celulas["celula"] = celulas["celula"].astype(int)
    celulas["tipo"] = celulas["tipo"].astype(str).str.strip().str.upper()

    turnos = hoja("Turnos")
    almacen = hoja("Almacen")
    params = {k: v for k, (v, _) in PARAMETROS_DEFECTO.items()}
    descr = {k: d for k, (_, d) in PARAMETROS_DEFECTO.items()}
    pdf = hoja("Parametros")
    for _, f in pdf.iterrows():
        if pd.notna(f["parametro"]) and pd.notna(f["valor"]):
            params[str(f["parametro"]).strip()] = float(f["valor"])
            if pd.notna(f["descripcion"]):
                descr[str(f["parametro"]).strip()] = str(f["descripcion"])

    stock = hoja("StockActual")
    for c in ("celula", "piezas"):
        stock[c] = pd.to_numeric(stock[c])
    disp = hoja("Disponibilidad")
    mant = hoja("Mantenimientos")
    mant["turno"] = mant["turno"].astype(str).str.strip().str.upper()
    rr = hoja("RecursosReales")
    rr["turno"] = rr["turno"].astype(str).str.strip().str.upper()

    return Escenario(
        celulas=celulas, turnos=turnos, almacen=almacen, parametros=params,
        demanda_semanal=hoja("DemandaSemanal"), correccion_diaria=hoja("CorreccionDiaria"),
        stock_actual=stock, disponibilidad=disp, mantenimientos=mant, recursos_reales=rr,
        expediciones=hoja("Expediciones"), descripciones=descr,
    )


def guardar_entrada(esc: Escenario, path) -> None:
    """Escribe el escenario en un Excel con la misma estructura que `cargar_entrada` espera."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = pd.DataFrame({
        "parametro": list(esc.parametros.keys()),
        "valor": list(esc.parametros.values()),
        "descripcion": [esc.descripciones.get(k, "") for k in esc.parametros],
    })
    hojas = {
        "Celulas": esc.celulas, "Turnos": esc.turnos, "Almacen": esc.almacen, "Parametros": pdf,
        "DemandaSemanal": esc.demanda_semanal, "CorreccionDiaria": esc.correccion_diaria,
        "StockActual": esc.stock_actual, "Disponibilidad": esc.disponibilidad,
        "Mantenimientos": esc.mantenimientos, "RecursosReales": esc.recursos_reales,
        "Expediciones": esc.expediciones,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)


def crear_escenario_ejemplo() -> Escenario:
    """Escenario de demostración (spec §1): semana del 28/09/2026, corrección el 02/10."""
    celulas = pd.DataFrame(_CELULAS_BASE, columns=HOJAS_COLUMNAS["Celulas"][:-1])
    celulas["piezas_por_conjunto"] = 1  # FLAG F2
    celulas["celula"] = celulas["celula"].astype(int)
    for c in HOJAS_COLUMNAS["Celulas"][2:]:
        celulas[c] = celulas[c].astype(float)
    turnos = pd.DataFrame({
        "recurso": RECURSOS, "M": [16, 2, 4, 7, 3], "T": [16, 2, 4, 7, 3], "N": [14, 2, 3, 4, 2],
    })
    almacen = pd.DataFrame({"zona": ["materia_prima", "cargas", "producto_terminado"],
                            "m2": [400, 100, 800]})
    params = {k: v for k, (v, _) in PARAMETROS_DEFECTO.items()}
    descr = {k: d for k, (_, d) in PARAMETROS_DEFECTO.items()}
    esc = Escenario(
        celulas=celulas, turnos=turnos, almacen=almacen, parametros=params,
        demanda_semanal=pd.DataFrame({"semana_inicio": [pd.Timestamp("2026-09-28")],
                                      "chasis_ve": [2400], "chasis_comb": [1600]}),
        correccion_diaria=pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")],
                                        "chasis_ve": [520], "chasis_comb": [300]}),
        stock_actual=_vacia("StockActual"),
        disponibilidad=_vacia("Disponibilidad"),
        mantenimientos=pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")], "turno": ["T"],
                                     "celula": [13], "tecnicos": [2]}),
        recursos_reales=pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")], "turno": ["T"],
                                      "operarios": [15], "picking": [pd.NA], "carretilleros": [pd.NA],
                                      "mto": [pd.NA], "calidad": [pd.NA]}),
        expediciones=_vacia("Expediciones"),
        descripciones=descr,
    )
    ss = ss_por_celula(esc)
    # Stock inicial de la demo = round(FACTOR x SS) por pieza. FLAG F19: la especificación v1 decía 1,3 x SS,
    # pero con 1,3 el escenario es inviable (0,3 x SS no cubre un turno de envíos); se usa 1,8 x SS.
    esc.stock_actual = pd.DataFrame({"celula": list(ss.keys()),
                                     "piezas": [round(FACTOR_STOCK_DEMO * v) for v in ss.values()]})
    return esc


def crear_escenario_contingencia() -> Escenario:
    """Demo + célula 14 de baja del 02/10 06:00 al 03/10 06:00 (produce un plan INVIABLE de contingencia)."""
    esc = crear_escenario_ejemplo()
    esc.disponibilidad = pd.DataFrame({"celula": [14], "estado": ["BAJA"],
                                       "desde": [pd.Timestamp("2026-10-02 06:00")],
                                       "hasta": [pd.Timestamp("2026-10-03 06:00")]})
    return esc


def crear_plantilla_contingencia(path) -> Path:
    """Genera `escenario_contingencia.xlsx` y devuelve la ruta."""
    guardar_entrada(crear_escenario_contingencia(), path)
    return Path(path)


def crear_plantilla_ejemplo(path) -> Path:
    """Genera `entrada_ejemplo.xlsx` con el escenario de demostración y devuelve la ruta."""
    esc = crear_escenario_ejemplo()
    guardar_entrada(esc, path)
    return Path(path)


def validar_ss_almacen(esc: Escenario) -> float:
    """Suma SS_c / densidad_c en m² (FLAG F10: debe rondar los 150 m²)."""
    t = tabla_celulas(esc)
    ss = ss_por_celula(esc)
    return float(sum(v / t.loc[c, "piezas_m2"] for c, v in ss.items() if t.loc[c, "piezas_m2"] > 0))

