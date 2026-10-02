"""Configuración global del motor KWD: flags (supuestos), constantes y valores por defecto."""
from __future__ import annotations

# Supuestos del modelo. Cada uno se marca en el código con un comentario "# FLAG Fx:".
FLAGS: dict[str, str] = {
    "F1": "Cada célula produce un tipo de pieza distinto; el conjunto (chasis) se ensambla después "
          "con una pieza de cada célula de su tipo (VE o combustión).",
    "F2": "Un ciclo = 1 pieza. Piezas por conjunto = 1 para toda célula (parámetro "
          "`piezas_por_conjunto` por célula).",
    "F3": "El mantenimiento no se calcula: entra como input (fecha, turno, célula, técnicos).",
    "F4": "Turnos: Mañana 06-14, Tarde 14-22, Noche 22-06. Laborables lunes-viernes (el turno de "
          "noche del viernes termina el sábado 06:00). Fin de semana: sin producción ni expediciones.",
    "F5": "Factor energético horario: franja solar 11-17 -> 0,85 (FV cubre 15 % de la potencia); "
          "noche 22-06 -> 1,20; resto 1,00.",
    "F6": "15 camiones/día laborable, cada 1,5 h desde las 06:00 (06:00, 07:30 ... 03:00). Cada camión "
          "libera como máximo 15 m². Carga prevista = demanda diaria / 15 si no hay carga real registrada.",
    "F7": "Absentismo 5 %: disponible = estándar - round(estándar x 0,05) si no hay recursos reales del turno.",
    "F8": "OEE = 100 % (capacidad = 3600 / ciclo piezas/hora).",
    "F9": "Colchón objetivo sobre stock de seguridad = 10 % (sólo puntúa, no es obligatorio).",
    "F10": "Stock de seguridad por pieza: 400 piezas para cada célula VE, 200 para cada célula de "
           "combustión. Validación: suma SS_c/densidad_c = 151,7 m² ≈ 150 m² que indica KWD "
           "-> confirma F1/F2.",
    "F11": "Célula 10 (servicio logístico) no produce piezas ni ocupa almacén; activa todas las horas laborables.",
    # Supuestos adicionales introducidos por la implementación (no figuran en la especificación v1).
    "F12": "Si la demanda semanal no cubre una fecha se usa la semana anterior más reciente (o la primera "
           "disponible); sábado y domingo sin corrección diaria tienen demanda 0.",
    "F13": "Una baja o mantenimiento bloquea la célula en todo slot horario que solape con el intervalo.",
    "F14": "Si la célula 10 queda bloqueada o los recursos no alcanzan para ella en un slot, deja de ser "
           "obligatoria en ese slot (se avisa en alertas).",
    "F15": "Una expedición real sustituye al camión previsto más cercano (±45 min) de su día; si no hay "
           "ninguno cercano se añade como camión extra.",
    "F16": "Energía: la célula consume kW x fracción de uso u; `energia_kwh` es energía de red "
           "(kW x u x factor horario F5); `kwh_bruto` es sin factor.",
    "F18": "Condición terminal blanda: el stock al final del horizonte debe cubrir SS + envíos previstos de las siguientes `cobertura_final_h` horas (8 por defecto). Penalización 10 en el objetivo; su incumplimiento NO hace inviable el plan, sólo genera aviso.",
    "F19": "Stock inicial de la demo = 1,8 x SS (la especificación v1 indicaba 1,3 x SS, que hace inviable el escenario).",
    "F17": "Redondeo del absentismo: round() 'comercial' (0,5 hacia arriba), no el redondeo bancario.",
}

FLAGS = dict(sorted(FLAGS.items(), key=lambda kv: int(kv[0][1:])))

RECURSOS = ["operarios", "picking", "carretilleros", "mto", "calidad"]
# Recursos que puntúan en el componente R (recursos, 50 %).
RECURSOS_R = ["operarios", "picking", "carretilleros"]
CELULA_LOGISTICA = 10  # FLAG F11
CELULAS_PAREJA = (11, 12)

# Turnos (FLAG F4): código -> (hora inicio, hora fin)
TURNOS = {"M": (6, 14), "T": (14, 22), "N": (22, 6)}
NOMBRES_TURNO = {"M": "Mañana", "T": "Tarde", "N": "Noche"}
DIAS_LABORABLES = (0, 1, 2, 3, 4)  # lunes-viernes (FLAG F4)

# Hojas del Excel de entrada y sus columnas.
HOJAS_COLUMNAS: dict[str, list[str]] = {
    "Celulas": ["celula", "tipo", "ciclo_s", "piezas_m2", "operarios", "picking", "carretilleros",
                "mto", "calidad", "kw", "piezas_por_conjunto"],
    "Turnos": ["recurso", "M", "T", "N"],
    "Almacen": ["zona", "m2"],
    "Parametros": ["parametro", "valor", "descripcion"],
    "DemandaSemanal": ["semana_inicio", "chasis_ve", "chasis_comb"],
    "CorreccionDiaria": ["fecha", "chasis_ve", "chasis_comb"],
    "StockActual": ["celula", "piezas"],
    "Disponibilidad": ["celula", "estado", "desde", "hasta"],
    "Mantenimientos": ["fecha", "turno", "celula", "tecnicos"],
    "RecursosReales": ["fecha", "turno", "operarios", "picking", "carretilleros", "mto", "calidad"],
    "Expediciones": ["fecha_hora", "chasis_ve", "chasis_comb"],
}

# Parámetros por defecto: clave -> (valor, descripción).
PARAMETROS_DEFECTO: dict[str, tuple[float, str]] = {
    "absentismo": (0.05, "Fracción de absentismo sobre el estándar de turno (F7)"),
    "colchon_ss": (0.10, "Colchón objetivo sobre el stock de seguridad (F9)"),
    "ss_ve": (400, "Stock de seguridad por célula VE, piezas (F10)"),
    "ss_comb": (200, "Stock de seguridad por célula de combustión, piezas (F10)"),
    "camiones_dia": (15, "Camiones por día laborable (F6)"),
    "intervalo_camion_h": (1.5, "Intervalo entre camiones, horas (F6)"),
    "primer_camion_h": (6, "Hora del primer camión del día (F6)"),
    "m2_max_camion": (15, "m² máximos liberados por camión (F6)"),
    "solar_ini": (11, "Inicio de la franja solar, hora (F5)"),
    "solar_fin": (17, "Fin de la franja solar, hora (F5)"),
    "factor_solar": (0.85, "Factor energético en franja solar (F5)"),
    "factor_noche": (1.2, "Factor energético nocturno 22-06 (F5)"),
    "peso_recursos": (0.5, "Peso del criterio recursos en la puntuación"),
    "peso_espacio": (0.2, "Peso del criterio espacio de almacén"),
    "peso_calidad_mto": (0.15, "Peso del criterio calidad y mantenimiento"),
    "peso_stock": (0.1, "Peso del criterio stock de seguridad"),
    "peso_energia": (0.05, "Peso del criterio energía"),
    "tiempo_limite_s": (30, "Tiempo límite del solver por plan, segundos"),
    "horas_horizonte": (24, "Horas del horizonte de planificación"),
    "cobertura_final_h": (8, "Horas de envíos posteriores al horizonte que debe cubrir el stock final sobre el SS (F18)"),
    "gap_relativo": (0.001, "Gap relativo MIP aceptado por el solver (la spec pide 0; relajado por velocidad)"),
}

# Orden y nombres visibles de los componentes de la puntuación.
COMPONENTES = ["R", "S", "Q", "B", "E"]
NOMBRES_COMPONENTE = {
    "R": "Recursos", "S": "Espacio almacén", "Q": "Calidad y mantenimiento",
    "B": "Stock de seguridad", "E": "Energía",
}
PESO_PARAM = {
    "R": "peso_recursos", "S": "peso_espacio", "Q": "peso_calidad_mto",
    "B": "peso_stock", "E": "peso_energia",
}

TOL = 1e-6



