"""Configuración global del motor KWD: constantes y valores por defecto (v3).

Los supuestos del modelo están documentados en `docs/` (no en la app).
"""
from __future__ import annotations

RECURSOS = ["operarios", "picking", "carretilleros", "mto", "calidad"]
# Roles que se mostraban en el criterio R de la v2 (se conserva por compatibilidad con la app).
RECURSOS_R = ["operarios", "picking", "carretilleros"]
# Códigos de trabajador y etiquetas visibles por rol.
CODIGO_ROL = {"operarios": "OP", "picking": "PK", "carretilleros": "CA", "mto": "MT", "calidad": "CL"}
ROL_DE_CODIGO = {v: k for k, v in CODIGO_ROL.items()}
ETIQUETA_ROL = {"operarios": "Operario", "picking": "Picking", "carretilleros": "Carretillero",
                "mto": "Técnico", "calidad": "Calidad"}
CELULA_LOGISTICA = 10  # servicio logístico: no produce, no ocupa almacén y no puede pararse
CELULAS_PAREJA = (11, 12)
TURNOS_POR_DIA = 3     # stock óptimo = SS + demanda diaria / TURNOS_POR_DIA (la demanda de un turno)

# Turnos: código -> (hora inicio, hora fin)
TURNOS = {"M": (6, 14), "T": (14, 22), "N": (22, 6)}
NOMBRES_TURNO = {"M": "Mañana", "T": "Tarde", "N": "Noche"}
CIERRES_TURNO = (6, 14, 22)        # horas del reloj a las que cierra un turno
DIAS_LABORABLES = (0, 1, 2, 3, 4)  # lunes-viernes

# Tablas de entrada y sus columnas (el estado persistente vive en data/estado.json).
HOJAS_COLUMNAS: dict[str, list[str]] = {
    "Celulas": ["celula", "tipo", "ciclo_s", "piezas_m2", "operarios", "picking", "carretilleros",
                "mto", "calidad", "kw"],
    "Turnos": ["recurso", "M", "T", "N"],
    "Almacen": ["zona", "m2"],
    "Parametros": ["parametro", "valor", "descripcion"],
    "DemandaSemanal": ["semana_inicio", "piezas_ve", "piezas_comb"],
    "CorreccionDiaria": ["fecha", "piezas_ve", "piezas_comb"],
    "StockActual": ["celula", "piezas"],
    "Bajas": ["fecha", "turno", "operarios", "picking", "carretilleros", "mto", "calidad"],
    "BajasTrabajadores": ["fecha", "trabajador"],
    "Paradas": ["celula", "desde", "hasta", "tipo", "tecnicos"],
    "Expediciones": ["fecha_hora", "piezas_ve", "piezas_comb"],
}

# Parámetros constantes: clave -> (valor, descripción).
PARAMETROS_DEFECTO: dict[str, tuple[float, str]] = {
    "absentismo": (0.05, "Fracción de absentismo sobre el estándar de turno cuando no hay fila de bajas"),
    "ss_ve": (400, "Stock de seguridad por célula VE, piezas"),
    "ss_comb": (200, "Stock de seguridad por célula de combustión, piezas"),
    "ciclos_dia": (16, "Ciclos de expedición por día laborable"),
    "intervalo_camion_h": (1.5, "Intervalo entre ciclos de expedición, horas"),
    "primer_camion_h": (6, "Hora del primer ciclo del día"),
    "m2_max_camion": (15, "m² máximos por camión"),
    "solar_ini": (11, "Inicio de la franja solar, hora"),
    "solar_fin": (14, "Fin de la franja solar, hora"),
    "factor_solar": (0.85, "Factor energético en franja solar"),
    "factor_noche": (1.2, "Factor energético nocturno 22-06"),
    "peso_recursos": (0.5, "Peso del criterio R (horas libres del personal presente)"),
    "peso_espacio": (0.2, "Peso del criterio espacio de almacén"),
    "peso_calidad_mto": (0.15, "Peso del criterio calidad y mantenimiento"),
    "peso_stock": (0.1, "Peso del criterio stock óptimo (|stock - óptimo| al cierre de turno)"),
    "peso_energia": (0.05, "Peso del criterio energía"),
    "tiempo_limite_s": (8, "Tiempo límite total del solver por plan (2 fases), segundos"),
    "horas_horizonte": (24, "Horas del horizonte de planificación"),
    "gap_relativo": (0.001, "Gap relativo MIP aceptado por el solver (la spec pide 0; relajado por velocidad)"),
    "penalizacion_ss": (50, "Penalización por unidad normalizada (s/SS) y hora de stock de seguridad consumido "
                            "(por encima de los criterios, por debajo del pedido no servido)"),
    "penalizacion_pedido": (1000, "Penalización por unidad normalizada (stock<0 / SS) y hora de pedido no "
                                  "servido (máxima prioridad)"),
}

# Orden y nombres visibles de los componentes de la puntuación.
COMPONENTES = ["R", "S", "Q", "B", "E"]
NOMBRES_COMPONENTE = {
    "R": "Horas libres del personal", "S": "Espacio almacén", "Q": "Calidad y mantenimiento",
    "B": "Stock óptimo", "E": "Energía",
}
PESO_PARAM = {
    "R": "peso_recursos", "S": "peso_espacio", "Q": "peso_calidad_mto",
    "B": "peso_stock", "E": "peso_energia",
}

TOL = 1e-6
