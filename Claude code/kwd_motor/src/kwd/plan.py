"""Estructura de datos de un plan de producción."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Plan:
    """Plan de activación de células para el horizonte.

    Todos los DataFrame/Series usan como índice el slot horario (0..H-1).
    - `estado`: OPTIMO | FACTIBLE | CRITICO | INVIABLE | EVALUADO. CRITICO = plan ejecutable pero con pedidos sin servir
      (stock < 0): lleva `aviso_direccion`; INVIABLE sólo por imposibilidades duras (almacén, reglas de células).
    - `gap`: gap relativo del solver (0 = óptimo demostrado); `idoneidad` = 100 * (1 - gap).
    - `activacion`: 0/1 por célula (todas las células, incluida la 10); `uso`: fracción de la hora
      produciendo; `produccion`: piezas; `stock`: piezas al final de cada hora (células productivas).
    - `recursos`: columnas `<recurso>_usado` y `<recurso>_disp`.
    - `energia_kwh`: energía de red por hora (kW x u x factor horario).
    - `recursos`: `<recurso>_usado` = personas enteras N ocupadas; `<recurso>_req` = suma de cargas; `<recurso>_disp` = presentes.
    """
    estado: str
    viable: bool
    gap: float | None
    idoneidad: float | None
    puntuacion: float
    componentes: dict
    contribuciones: dict
    activacion: pd.DataFrame
    uso: pd.DataFrame
    produccion: pd.DataFrame
    stock: pd.DataFrame
    espacio: pd.Series
    recursos: pd.DataFrame
    energia_kwh: pd.Series
    config_turno_actual: list
    incumplimientos: list = field(default_factory=list)
    kpis: dict = field(default_factory=dict)
    resumen_turnos: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Campos adicionales (no contractuales)
    avisos: list = field(default_factory=list)  # no son violaciones (p. ej. stock de seguridad consumido)
    nombre: str = ""
    tiempo_s: float = 0.0
    objetivo: float = float("nan")
    holguras: dict = field(default_factory=dict)
    # Stock óptimo y desabastecimiento (v3)
    stock_vs_optimo: pd.DataFrame = field(default_factory=pd.DataFrame)  # cierre, slot, celula, stock, optimo, desviacion, desviacion_pct
    desabastecimiento: pd.DataFrame = field(default_factory=pd.DataFrame)  # pieza, ciclo, piezas_no_servidas
    agotamiento: pd.DataFrame = field(default_factory=pd.DataFrame)  # pieza, hora_bajo_ss, hora_sin_stock, hora_repone_ss, stock_min
    aviso_direccion: str | None = None
    # Personal: N entero por slot y rol; asignación nominal por slot; resumen por trabajador; presentes por turno
    personas: pd.DataFrame = field(default_factory=pd.DataFrame)      # index slot, columnas = roles (N)
    personal: pd.DataFrame = field(default_factory=pd.DataFrame)      # slot, hora, turno, rol, trabajador, celulas, carga, estado (ASIGNADO/LIBRE/PARADA)
    trabajadores: pd.DataFrame = field(default_factory=pd.DataFrame)  # trabajador, turno, rol, horas_asignado, horas_libre, celulas
    plantilla: pd.DataFrame = field(default_factory=pd.DataFrame)     # fecha_turno, turno, rol, plantilla (= presentes), disponibles, excedente (=0), horas_libres

    def resumen(self) -> str:
        """Línea de texto con lo esencial del plan."""
        return (f"{self.nombre or 'Plan'} [{self.estado}] puntuación {self.puntuacion:.1f}, "
                f"idoneidad {self.idoneidad_txt()}, config turno actual {self.config_turno_actual}")

    def idoneidad_txt(self) -> str:
        """Idoneidad formateada; '—' si no aplica (plan inviable)."""
        return "—" if self.idoneidad is None else f"{self.idoneidad:.1f} %"
