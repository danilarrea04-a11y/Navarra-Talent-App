# Motor de decisión KWD — Cambios v2 (vinculante)

Complementa `ESPECIFICACION.md`. Donde haya conflicto, manda este documento. Decisiones del equipo (2 oct 2026, tarde).

## A. Cambios de modelo (motor, `src/kwd/`)

### A1. Sin ensamblaje: pedidos de piezas individuales (punto 7)
- KWD no ensambla. Cada célula fabrica una **pieza exclusiva** (ninguna otra célula puede fabricarla). Se eliminan los conceptos "conjunto/chasis" y `piezas_por_conjunto` (si la columna existe en un Excel antiguo, se ignora).
- La demanda se introduce **por tipo**: `piezas_ve` y `piezas_comb` = cantidad que se pide de **cada** pieza de ese tipo.
- Hojas: `DemandaSemanal(semana_inicio, piezas_ve, piezas_comb)`, `CorreccionDiaria(fecha, piezas_ve, piezas_comb)`. Compatibilidad: si aparecen columnas `chasis_ve/chasis_comb`, leerlas como `piezas_*`.
- Demanda del día = corrección si existe; si no, semanal / 5 (laborables). Repartida por igual entre los ciclos de expedición del día (es decir, entre los 3 turnos).
- Textos de la app/informes: "piezas" en vez de "chasis"; actualizar FLAGS F1/F2 en `config.FLAGS` (F1 = "Cada célula fabrica una pieza exclusiva; la demanda llega por tipo VE/COMB y aplica a cada pieza de ese tipo". F2 = "Un ciclo = 1 pieza").

### A2. Expediciones (respuesta del equipo)
- Ciclos de expedición cada **1,5 h** desde las 06:00 → **16 ciclos por día laborable** (06:00, 07:30 … 04:30 del día siguiente). Parámetros: `intervalo_camion_h`=1.5, `primer_camion_h`=6, `ciclos_dia`=16, `m2_max_camion`=15.
- En cada ciclo salen **los camiones que hagan falta**, cada uno con **15 m² de capacidad máxima** (no tiene por qué ir lleno). Nº camiones del ciclo = ceil(m² de la carga / 15). Ya no se escala ni se recorta la carga. KPI: camiones/día y por ciclo.
- Carga prevista de un ciclo = demanda del día / 16 de cada pieza. `Expediciones(fecha_hora, piezas_ve, piezas_comb)` = carga real de ese ciclo (sustituye a la previsión).
- `Horizonte.camiones`: DataFrame con `slot, fecha_hora, piezas_ve, piezas_comb, m2, n_camiones, real`.

### A3. Stock de seguridad y prioridad de reposición
- Un camión **puede llevarse producto del stock de seguridad**. Por tanto `stock ≥ SS` deja de descartar el plan: la holgura `s_{c,h}` se penaliza con un peso de **prioridad máxima** (por encima de todos los criterios KWD; p. ej. 50 por unidad normalizada `s/SS` y hora), de modo que el optimizador **repone el SS lo antes posible**. Sigue contando en el criterio B (10 %).
- **INVIABLE** (descarta) sólo si: stock < 0 (pedido no servido), almacén > 800 m², o se viola una regla dura de recursos/células.
- Alerta: "Stock de seguridad de la pieza X consumido por expedición de HH:MM a HH:MM; repuesto a HH:MM" (o "no se repone en el horizonte").
- Actualizar `validador` (SS por debajo = aviso, no incumplimiento; stock < 0 = incumplimiento).

### A4. Franja solar 11–14 (punto 3)
- `solar_ini`=11, `solar_fin`=14 por defecto (FLAG F5 actualizado).

### A5. Célula 10 no puede averiarse ni pararse (punto 4)
- `datos`/`rolling` ignoran (con aviso) cualquier parada/baja de la célula 10. La UI no la ofrece.

### A6. Datos manuales vs constantes (punto 2)
Datos que se introducen manualmente (cambian): **bajas por turno**, **paradas de máquinas** y **demanda semanal y diaria**. Además: stock actual y expediciones reales (operativos). El resto de hojas (Celulas, Turnos, Almacen, Parametros) son constantes.
- Nueva hoja `Bajas(fecha, turno, operarios, picking, carretilleros, mto, calidad)` = nº de personas de baja de cada rol en ese turno. Disponible = estándar − bajas. Si no hay fila para un turno → estándar − round(estándar × absentismo 5 %) (FLAG F7). Compatibilidad: si existe `RecursosReales`, convertirla.
- Nueva hoja `Paradas(celula, desde, hasta, tipo, tecnicos)` con `tipo` ∈ {"PROGRAMADA", "AVERIA"}; `tecnicos` = técnicos de mantenimiento ocupados durante la parada (programadas; averías opcional, por defecto 0). Sustituye a `Disponibilidad` y `Mantenimientos` (compatibilidad: convertirlas al leer).

### A7. Personal entero (punto 9)
- Para cada hora laborable y rol k ∈ {operarios, picking, carretilleros, mto, calidad}: variable **entera** `N_{k,h}` = personas asignadas; `N_{k,h} ≥ Σ_c req_{c,k}·a_{c,h}`, `N_{k,h} ≤ Disp_{k,h}`.
- El criterio R (50 %) se calcula con `N` (personas realmente ocupadas) y Q con `N` de mto/calidad → una fracción suelta (p. ej. célula 8 sola, 0,5 operarios) cuesta una persona entera, así que el optimizador tiende a **emparejar** células fraccionarias (8 y 9 a la vez) o a evitar fracciones sueltas.
- KPI `desperdicio_<rol>_h` = Σ_h (N − Σ req·a) y alerta si > 0.
- Post-proceso `asignacion_personal`: por hora y rol, reparto nominal de personas (Operario 1, 2, …; Picking 1…; Carretillero 1…; Técnico 1…; Calidad 1…) a células por "first-fit decreasing", estable entre horas (misma persona sigue en la misma célula mientras siga activa). Una persona puede cubrir varias células cuya suma ≤ 1 (p. ej. "Operario 7: C8 (0,5) + C9 (0,5)").
- `Plan.personal`: DataFrame `slot, rol, persona, celulas (str "C8+C9"), carga, desperdicio`. `Plan.personas` DataFrame slot × rol con N.

### A8. Contingencia por avería de una célula (punto 1)
`rolling.contingencia_celula(esc, rec, celula, desde, hasta=None) -> dict`:
- Aplica parada AVERIA a `celula` desde `desde` (hasta fin de horizonte si `hasta` es None), toma el stock en `desde` del plan vigente y recalcula.
- Devuelve: `rec_antes`, `rec_despues`, `reubicacion` (DataFrame `persona, rol, de_celula, a_celulas, desde, hasta` — a dónde van las personas que trabajaban en la célula averiada y quién cambia de puesto), `maquinas` (DataFrame `celula, horas_antes, horas_despues, delta, franjas_nuevas` — qué máquinas se activan o amplían), `agotamiento` (`hora_bajo_ss`, `hora_sin_stock` de la pieza de la célula averiada, o None), `resumen` (list[str] en lenguaje de planta: "Los 3 operarios de la C14 pasan a: …", "Activar C15 de 14:00 a 18:00", "La pieza 14 cubre pedidos hasta las HH:MM; reparar antes de …").
- Genérico para cualquier célula ≠ 10.

### A9. Tiempo real (puntos 6 y 8)
- `rolling.estado_en(esc, rec, ahora) -> DataFrame` por célula: `celula, tipo, estado` ("PRODUCIENDO", "EN ESPERA", "PARADA PROGRAMADA", "AVERÍA"), `personas` (str), `stock`, `ss`, `cobertura_h`, `proxima_activacion`.
- Eventos de `rolling.Evento`: `parada_celula{celula, desde, hasta, tipo, tecnicos}`, `fin_parada{celula, ahora}`, `baja_personal{fecha, turno, rol, cantidad}` (suma bajas al turno), `alta_personal{fecha, turno, rol, cantidad}`, `correccion_demanda{fecha, piezas_ve, piezas_comb}`, `expedicion_real{fecha_hora, piezas_ve, piezas_comb}`, `stock_real{celula: piezas}`.
- **Corregir H1**: `reconfigurar` siempre toma el stock en `ahora` del plan vigente (también si `ahora` = inicio del plan) y devuelve una recomendación cuyo `inicio` = `ahora`; el escenario devuelto/actualizado debe llevar ya ese stock para que eventos encadenados partan del estado correcto. Exponer `rolling.reconfigurar(esc, rec, evento, ahora) -> (Escenario, Recomendacion)`.

### A10. Plan manual (punto 5)
`config.DEFINICION_PLAN_MANUAL` (texto para UI e informes): "Plan de referencia manual: simulación de cómo planificaría un encargado sin optimizador. Cada hora activa a plena marcha las células cuya pieza quedaría por debajo del colchón de seguridad en las próximas 8 h, por orden de número de célula y mientras haya personal; no adelanta producción a la franja solar, no empareja células que comparten operario ni busca minimizar personal, espacio o energía. Sirve de línea base para medir el impacto del optimizador." El baseline debe usar también personal entero (ceil) en su evaluación.

### A11. Escenario de simulación (punto 10)
- Demo: semana del lunes 2026-09-28, **1.500 coches/día, doble de combustión que eléctricos** → 1.000 piezas COMB y 500 piezas VE por día de cada referencia (semanal 5.000 / 2.500). Sin corrección diaria por defecto (o una pequeña de ejemplo el 02/10: 520 VE / 980 COMB).
- Stock inicial ≈ SS + algo de colchón (ajustar entre 1,3× y 2× SS para que el plan sea viable y realista; informar).
- Paradas de ejemplo: C13 PROGRAMADA el 02/10 turno T (14:00–22:00), 2 técnicos. Bajas de ejemplo: 02/10 T, 1 operario.
- `data/escenario_contingencia.xlsx` = demo + C14 AVERIA desde 02/10 10:00 hasta 03/10 06:00.
- Ayuda `datos.demanda_desde_coches(coches_dia, ratio_comb_ve=2.0) -> (piezas_ve, piezas_comb)`.

### A12. Rendimiento
Top-3 de 24 h en < 30 s; simulación semanal acotada (mantener límites por iteración). Si el personal entero ralentiza, ajustar `gap_relativo`/límites e informar.

## B. Dashboard e informes (`app/`, `src/kwd/informes.py`)

1. **Planta en tiempo real** (nueva primera pestaña): "Hora actual" (selector; por defecto el inicio del plan; botón "+1 h"), rejilla de las 16 células (color VE verde / COMB azul / C10 gris; estado; personas asignadas ahora; stock vs SS y cobertura; próxima activación). Botones por célula: **Avería** / **Parada programada** (desde ahora, hasta opcional) y **Reactivar**; C10 sin botones de parada. Panel de **personal**: por rol, disponibles / asignados ahora y lista de personas con su(s) célula(s); botón **Dar de baja** por persona (o nº por rol) → evento `baja_personal`. Cada acción **recalcula automáticamente** desde la hora actual (sin pulsar "Calcular") y muestra un resumen antes/después.
2. **Entrada manual** (sustituye a "Datos de entrada" como pestaña principal de datos): editores de **Bajas por turno (todos los roles)**, **Paradas (programada/avería)** y **Demanda semanal y diaria por tipo** (con ayuda "Rellenar desde coches/día", 2:1 COMB:VE). Al cambiar cualquiera → recálculo automático (casilla "Recalcular automáticamente", activada por defecto). Constantes (Celulas, Turnos, Almacen, Parametros) en un expander de sólo lectura "Parámetros constantes". Stock actual y expediciones reales en un expander "Datos operativos".
3. **Contingencia** (pestaña): selector de célula (por defecto 14), desde (hora actual), hasta opcional → `contingencia_celula`: tabla de reubicación de personas, tabla de máquinas a activar/ampliar, agotamiento de la pieza, KPIs antes/después, resumen en lenguaje de planta, botón "Aplicar como incidencia real".
4. **Recomendación**: añadir asignación nominal de personal del turno actual (tabla persona → células) y KPIs de desperdicio de personal y camiones/día. "Plan manual" con tooltip/expander que muestre `DEFINICION_PLAN_MANUAL`.
5. Sustituir "chasis" por "piezas" en toda la UI; quitar lo que quede de ensamblaje.
6. `informes.py`: textos en piezas, camiones por ciclo, asignación de personal, sección de contingencia si la recomendación viene de una avería, definición del plan manual, FLAGS actualizados, franja solar 11–14.
