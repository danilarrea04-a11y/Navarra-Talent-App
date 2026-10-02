"""Configuración global del motor KWD: flags (supuestos), constantes y valores por defecto (v2)."""
from __future__ import annotations

# Supuestos del modelo. Cada uno se marca en el código con un comentario "# FLAG Fx:".
FLAGS: dict[str, str] = {
    "F1": "Cada célula fabrica una pieza exclusiva; la demanda llega por tipo VE/COMB y aplica a cada pieza de ese tipo.",
    "F2": "Un ciclo = 1 pieza.",
    "F3": "El mantenimiento no se calcula: entra como input (hoja Paradas: célula, desde, hasta, tipo, técnicos).",
    "F4": "Turnos: Mañana 06-14, Tarde 14-22, Noche 22-06. Laborables lunes-viernes (el turno de "
          "noche del viernes termina el sábado 06:00). Fin de semana: sin producción ni expediciones.",
    "F5": "Factor energético horario: franja solar 11-14 -> 0,85 (FV cubre 15 % de la potencia); "
          "noche 22-06 -> 1,20; resto 1,00.",
    "F6": "16 ciclos de expedición por día laborable, cada 1,5 h desde las 06:00 (06:00, 07:30 ... 04:30 del día "
          "siguiente). En cada ciclo salen los camiones que hagan falta (nº = techo de m²/15), cada uno con "
          "15 m² de capacidad máxima. Carga prevista de un ciclo = demanda diaria / 16 de cada pieza.",
    "F7": "Absentismo 5 %: disponible = estándar - round(estándar x 0,05) si no hay fila de bajas del turno; "
          "si existe fila en la hoja Bajas, disponible = estándar - bajas.",
    "F8": "OEE = 100 % (capacidad = 3600 / ciclo piezas/hora).",
    "F9": "Colchón objetivo sobre stock de seguridad = 10 % (sólo puntúa, no es obligatorio).",
    "F10": "Stock de seguridad por pieza: 400 piezas para cada célula VE, 200 para cada célula de "
           "combustión. Validación: suma SS_c/densidad_c = 151,7 m² ≈ 150 m² que indica KWD "
           "-> confirma F1/F2.",
    "F11": "Célula 10 (servicio logístico) no produce piezas ni ocupa almacén; activa todas las horas laborables.",
    # Supuestos adicionales introducidos por la implementación.
    "F12": "Si la demanda semanal no cubre una fecha se usa la semana anterior más reciente (o la primera "
           "disponible); sábado y domingo sin corrección diaria tienen demanda 0.",
    "F13": "Una parada (programada o avería) bloquea la célula en todo slot horario que solape con el intervalo.",
    "F14": "Si la célula 10 queda bloqueada o los recursos no alcanzan para ella en un slot, deja de ser "
           "obligatoria en ese slot (se avisa en alertas).",
    "F15": "Una expedición real sustituye al ciclo previsto más cercano (±45 min) de su día; si no hay "
           "ninguno cercano se añade como ciclo extra.",
    "F16": "Energía: la célula consume kW x fracción de uso u; `energia_kwh` es energía de red "
           "(kW x u x factor horario F5); `kwh_bruto` es sin factor.",
    "F17": "Redondeo del absentismo: round() 'comercial' (0,5 hacia arriba), no el redondeo bancario.",
    "F18": "Condición terminal blanda: el stock al final del horizonte debe cubrir SS + envíos previstos de las "
           "siguientes `cobertura_final_h` horas (8 por defecto). Penalización 10 en el objetivo; su "
           "incumplimiento NO hace inviable el plan, sólo genera aviso.",
    "F19": "Stock inicial de la demo = 2,0 x SS (la especificación v1 indicaba 1,3 x SS).",
    "F20": "Un camión puede llevarse producto del stock de seguridad. Bajar del SS no descarta el plan, pero se "
           "penaliza con prioridad máxima (por encima de todos los criterios KWD) para reponerlo cuanto antes; "
           "sólo es INVIABLE si el stock < 0, el almacén > 800 m² o se viola una regla dura de recursos/células.",
    "F21": "Personal entero: personas por hora y rol N >= suma de cargas; plantilla por turno P >= N (pico). "
           "R = 0,7 x media(P/disp) + 0,3 x media(N/disp). Cada trabajador tiene un id estable por turno y rol "
           "(M-OP01, T-CA02 ...) y una persona puede cubrir varias células si la suma de cargas <= 1.",
    "F22": "La célula 10 (servicio logístico) no puede averiarse ni pararse: cualquier parada de la 10 se ignora (con aviso).",
}

FLAGS = dict(sorted(FLAGS.items(), key=lambda kv: int(kv[0][1:])))

DEFINICION_PLAN_MANUAL = (
    "Plan de referencia manual: simulación de cómo planificaría un encargado sin optimizador. Cada hora activa "
    "a plena marcha las células cuya pieza quedaría por debajo del colchón de seguridad en las próximas 8 h, "
    "por orden de número de célula y mientras haya personal; no adelanta producción a la franja solar, no "
    "empareja células que comparten operario ni busca minimizar personal, espacio o energía. Sirve de línea "
    "base para medir el impacto del optimizador."
)

RECURSOS = ["operarios", "picking", "carretilleros", "mto", "calidad"]
# Recursos que puntúan en el componente R (recursos, 50 %).
RECURSOS_R = ["operarios", "picking", "carretilleros"]
# Códigos de trabajador y etiquetas visibles por rol (A7-bis).
CODIGO_ROL = {"operarios": "OP", "picking": "PK", "carretilleros": "CA", "mto": "MT", "calidad": "CL"}
ROL_DE_CODIGO = {v: k for k, v in CODIGO_ROL.items()}
ETIQUETA_ROL = {"operarios": "Operario", "picking": "Picking", "carretilleros": "Carretillero",
                "mto": "Técnico", "calidad": "Calidad"}
CELULA_LOGISTICA = 10  # FLAG F11/F22
CELULAS_PAREJA = (11, 12)
PESO_PLANTILLA = 0.7   # R = 0,7 media(P/disp) + 0,3 media(N/disp)
PESO_IDLE = 0.02       # término auxiliar: horas libres dentro de la plantilla

# Turnos (FLAG F4): código -> (hora inicio, hora fin)
TURNOS = {"M": (6, 14), "T": (14, 22), "N": (22, 6)}
NOMBRES_TURNO = {"M": "Mañana", "T": "Tarde", "N": "Noche"}
DIAS_LABORABLES = (0, 1, 2, 3, 4)  # lunes-viernes (FLAG F4)

# Hojas del Excel de entrada y sus columnas.
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

# Parámetros por defecto: clave -> (valor, descripción).
PARAMETROS_DEFECTO: dict[str, tuple[float, str]] = {
    "absentismo": (0.05, "Fracción de absentismo sobre el estándar de turno (F7)"),
    "colchon_ss": (0.10, "Colchón objetivo sobre el stock de seguridad (F9)"),
    "ss_ve": (400, "Stock de seguridad por célula VE, piezas (F10)"),
    "ss_comb": (200, "Stock de seguridad por célula de combustión, piezas (F10)"),
    "ciclos_dia": (16, "Ciclos de expedición por día laborable (F6)"),
    "intervalo_camion_h": (1.5, "Intervalo entre ciclos de expedición, horas (F6)"),
    "primer_camion_h": (6, "Hora del primer ciclo del día (F6)"),
    "m2_max_camion": (15, "m² máximos por camión (F6)"),
    "solar_ini": (11, "Inicio de la franja solar, hora (F5)"),
    "solar_fin": (14, "Fin de la franja solar, hora (F5)"),
    "factor_solar": (0.85, "Factor energético en franja solar (F5)"),
    "factor_noche": (1.2, "Factor energético nocturno 22-06 (F5)"),
    "peso_recursos": (0.5, "Peso del criterio recursos en la puntuación"),
    "peso_espacio": (0.2, "Peso del criterio espacio de almacén"),
    "peso_calidad_mto": (0.15, "Peso del criterio calidad y mantenimiento"),
    "peso_stock": (0.1, "Peso del criterio stock de seguridad"),
    "peso_energia": (0.05, "Peso del criterio energía"),
    "tiempo_limite_s": (8, "Tiempo límite total del solver por plan (2 fases), segundos"),
    "horas_horizonte": (24, "Horas del horizonte de planificación"),
    "cobertura_final_h": (8, "Horas de envíos posteriores al horizonte que debe cubrir el stock final sobre el SS (F18)"),
    "gap_relativo": (0.001, "Gap relativo MIP aceptado por el solver (la spec pide 0; relajado por velocidad)"),
    "penalizacion_ss": (50, "Penalización de prioridad máxima por unidad normalizada (s/SS) y hora de stock "
                            "de seguridad consumido (F20)"),
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
