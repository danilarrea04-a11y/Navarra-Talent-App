"""Datos de entrada: escenario, lectura/escritura del Excel y plantilla de ejemplo (v2)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import (CELULA_LOGISTICA, CODIGO_ROL, DIAS_LABORABLES, HOJAS_COLUMNAS, PARAMETROS_DEFECTO,
                     RECURSOS, TURNOS)

# Columnas de fecha/hora de cada hoja.
_COLS_FECHA = {
    "DemandaSemanal": ["semana_inicio"],
    "CorreccionDiaria": ["fecha"],
    "Bajas": ["fecha"],
    "BajasTrabajadores": ["fecha"],
    "Paradas": ["desde", "hasta"],
    "Expediciones": ["fecha_hora"],
}

# Tabla de células de ttablas.xlsx (hoja "Células"). FLAG F1/F2: una pieza exclusiva por célula, un ciclo = 1 pieza.
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

FACTOR_STOCK_DEMO = 2.0 # FLAG F19


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
    t["ppc"] = 1.0  # FLAG F2: un ciclo = 1 pieza; sin ensamblaje (se mantiene la columna por compatibilidad)
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


def demanda_desde_coches(coches_dia: float, ratio_comb_ve: float = 2.0) -> tuple[float, float]:
    """Piezas por referencia y día (VE, COMB) a partir de coches/día y la proporción COMB:VE.

    Ej.: 1.500 coches/día con doble de combustión -> (500 VE, 1.000 COMB) de cada pieza.
    """
    ve = float(coches_dia) / (1.0 + float(ratio_comb_ve))
    return ve, float(coches_dia) - ve


def demanda_dia(esc: Escenario, fecha) -> tuple[float, float]:
    """Demanda diaria (piezas de cada referencia VE, piezas de cada referencia COMB) del día `fecha`.

    Prioridad: corrección diaria; si no, demanda semanal / 5 en día laborable (F4).
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
        # FLAG F12: semana sin dato -> última semana anterior (o la primera disponible)
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
    """Redondeo 'half up' (FLAG F17), no el bancario de Python."""
    import math
    return int(math.floor(x + 0.5))


def bajas_efectivas(esc: Escenario, fecha_turno, turno: str, rol: str) -> float:
    """Nº de personas de baja del rol en el turno: valor de la hoja Bajas o, si falta, round(estándar x absentismo)."""
    v = n_bajas(esc, fecha_turno, turno, rol)
    if v is not None:
        return v
    base = esc.disp_estandar(rol, turno)
    return float(redondeo_comercial(base * float(esc.parametros["absentismo"])))  # FLAG F7


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
    if es10.any():  # FLAG F22
        avisos.append("Se ignora la parada/baja de la célula 10: el servicio logístico no puede pararse.")
        df = df[~es10].reset_index(drop=True)
    return df


# --- lectura / escritura ----------------------------------------------------------------------
def cargar_entrada(path) -> Escenario:
    """Lee el Excel de entrada. Las hojas opcionales ausentes o vacías se tratan como tablas vacías.

    Compatibilidad con v1: `chasis_*` -> `piezas_*`; `Disponibilidad`/`Mantenimientos` -> `Paradas`;
    `RecursosReales` -> `Bajas`; `piezas_por_conjunto` se ignora.
    """
    path = Path(path)
    hojas = pd.read_excel(path, sheet_name=None)
    hojas = {k.strip(): v for k, v in hojas.items()}
    avisos: list[str] = []

    def hoja(nombre, df=None):
        df = hojas.get(nombre) if df is None else df
        if df is None:
            return _vacia(nombre)
        if nombre in ("DemandaSemanal", "CorreccionDiaria", "Expediciones"):
            df = df.rename(columns={"chasis_ve": "piezas_ve", "chasis_comb": "piezas_comb"})
        return _normalizar_hoja(nombre, df)

    celulas = hoja("Celulas")
    if celulas.empty:
        raise ValueError("La hoja 'Celulas' es obligatoria y está vacía o no existe.")
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
    params.pop("camiones_dia", None)  # obsoleto (v1): ahora son 16 ciclos con los camiones que hagan falta

    stock = hoja("StockActual")
    for c in ("celula", "piezas"):
        stock[c] = pd.to_numeric(stock[c])

    # Bajas (con compatibilidad RecursosReales)
    bajas = hoja("Bajas")
    if bajas.empty and "RecursosReales" in hojas:
        rr = _normalizar_hoja("Bajas", hojas["RecursosReales"].rename(columns={}))
        rows = []
        for _, f in rr.iterrows():
            t = str(f["turno"]).strip().upper()
            fila = {"fecha": f["fecha"], "turno": t}
            for r in RECURSOS:
                v = f[r]
                fila[r] = pd.NA if pd.isna(v) else max(0.0, esc_std(turnos, r, t) - float(v))
            rows.append(fila)
        bajas = pd.DataFrame(rows, columns=HOJAS_COLUMNAS["Bajas"])
    bajas["turno"] = bajas["turno"].astype(str).str.strip().str.upper()
    for r in RECURSOS:
        bajas[r] = pd.to_numeric(bajas[r], errors="coerce")

    # Paradas (con compatibilidad Disponibilidad y Mantenimientos)
    paradas = hoja("Paradas")
    if paradas.empty:
        filas = []
        if "Disponibilidad" in hojas:
            for _, f in hojas["Disponibilidad"].dropna(how="all").iterrows():
                if pd.notna(f.get("celula")):
                    filas.append({"celula": int(f["celula"]), "desde": pd.to_datetime(f.get("desde")),
                                  "hasta": pd.to_datetime(f.get("hasta")), "tipo": "AVERIA", "tecnicos": 0})
        if "Mantenimientos" in hojas:
            for _, f in hojas["Mantenimientos"].dropna(how="all").iterrows():
                if pd.notna(f.get("celula")) and pd.notna(f.get("fecha")):
                    d, h = _limites_turno(f["fecha"], str(f["turno"]).strip().upper())
                    filas.append({"celula": int(f["celula"]), "desde": d, "hasta": h, "tipo": "PROGRAMADA",
                                  "tecnicos": float(f["tecnicos"]) if pd.notna(f.get("tecnicos")) else 0.0})
        paradas = pd.DataFrame(filas, columns=HOJAS_COLUMNAS["Paradas"])
    paradas = _normalizar_paradas(paradas, avisos)

    bt = hoja("BajasTrabajadores")
    esc = Escenario(
        celulas=celulas, turnos=turnos, almacen=almacen, parametros=params,
        demanda_semanal=hoja("DemandaSemanal"), correccion_diaria=hoja("CorreccionDiaria"),
        stock_actual=stock, bajas=bajas, paradas=paradas, expediciones=hoja("Expediciones"),
        descripciones=descr, bajas_trabajadores=bt, avisos=avisos,
    )
    for hh in ("demanda_semanal", "correccion_diaria", "expediciones"):
        df = getattr(esc, hh)
        for c in ("piezas_ve", "piezas_comb"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return esc


def esc_std(turnos: pd.DataFrame, rol: str, turno: str) -> float:
    fila = turnos[turnos["recurso"] == rol]
    return float(fila[turno].iloc[0]) if len(fila) and turno in fila.columns else 0.0


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
        "StockActual": esc.stock_actual, "Bajas": esc.bajas, "BajasTrabajadores": esc.bajas_trabajadores,
        "Paradas": esc.paradas, "Expediciones": esc.expediciones,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)


def crear_escenario_ejemplo() -> Escenario:
    """Escenario de demostración (A11): semana del 28/09/2026, 1.500 coches/día (2 COMB : 1 VE).

    Demanda por pieza: 500 VE / 1.000 COMB al día (semanal 2.500 / 5.000); corrección el 02/10: 520 / 980.
    Parada programada de la célula 13 el 02/10 turno T (2 técnicos) y una baja de operario ese turno.
    """
    celulas = pd.DataFrame(_CELULAS_BASE, columns=HOJAS_COLUMNAS["Celulas"])
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
    ve, comb = demanda_desde_coches(1500, 2.0)
    esc = Escenario(
        celulas=celulas, turnos=turnos, almacen=almacen, parametros=params,
        demanda_semanal=pd.DataFrame({"semana_inicio": [pd.Timestamp("2026-09-28")],
                                      "piezas_ve": [ve * 5], "piezas_comb": [comb * 5]}),
        correccion_diaria=pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")],
                                        "piezas_ve": [520.0], "piezas_comb": [980.0]}),
        stock_actual=_vacia("StockActual"),
        bajas=pd.DataFrame({"fecha": [pd.Timestamp("2026-10-02")], "turno": ["T"], "operarios": [1.0],
                            "picking": [0.0], "carretilleros": [0.0], "mto": [0.0], "calidad": [0.0]}),
        paradas=pd.DataFrame({"celula": [13], "desde": [pd.Timestamp("2026-10-02 14:00")],
                              "hasta": [pd.Timestamp("2026-10-02 22:00")], "tipo": ["PROGRAMADA"],
                              "tecnicos": [2.0]}),
        expediciones=_vacia("Expediciones"),
        descripciones=descr,
    )
    ss = ss_por_celula(esc)
    # FLAG F19: stock inicial de la demo = round(FACTOR x SS) por pieza (rango admitido 1,3 x - 2 x SS).
    esc.stock_actual = pd.DataFrame({"celula": list(ss.keys()),
                                     "piezas": [float(round(FACTOR_STOCK_DEMO * v)) for v in ss.values()]})
    return esc


def crear_escenario_contingencia() -> Escenario:
    """Demo + avería de la célula 14 desde el 02/10 10:00 hasta el 03/10 06:00."""
    esc = crear_escenario_ejemplo()
    nueva = pd.DataFrame({"celula": [14], "desde": [pd.Timestamp("2026-10-02 10:00")],
                          "hasta": [pd.Timestamp("2026-10-03 06:00")], "tipo": ["AVERIA"], "tecnicos": [0.0]})
    esc.paradas = pd.concat([esc.paradas, nueva], ignore_index=True)
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
