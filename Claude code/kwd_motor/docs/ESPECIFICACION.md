# Motor de decisión KWD — Especificación v1

Fuente de verdad para implementar el motor, el dashboard y los informes.
Idioma de la app, informes y nombres visibles: **español**. Código: identificadores en español sin tildes.

## 0. Flags (supuestos a revisar en el futuro)

Todo supuesto se marca en el código con un comentario `# FLAG Fx:` y se lista en `kwd/config.py::FLAGS`
(diccionario id → descripción) para mostrarlo en el dashboard y en el informe.

| Id | Supuesto actual |
|---|---|
| F1 | Cada célula produce un tipo de pieza distinto; el conjunto (chasis) se ensambla después con una pieza de cada célula de su tipo (VE o combustión). |
| F2 | Un ciclo = 1 pieza. Piezas por conjunto = 1 para toda célula (parámetro `piezas_por_conjunto` por célula). |
| F3 | El mantenimiento no se calcula: entra como input (fecha, turno, célula, técnicos). |
| F4 | Turnos: Mañana 06–14, Tarde 14–22, Noche 22–06. Laborables lunes–viernes (el turno de noche del viernes termina el sábado 06:00). Fin de semana: sin producción ni expediciones. |
| F5 | Factor energético horario: franja solar 11–17 → 0,85 (FV cubre 15 % de la potencia); noche 22–06 → 1,20; resto 1,00. |
| F6 | 15 camiones/día laborable, cada 1,5 h desde las 06:00 (06:00, 07:30 … 03:00). Cada camión libera como máximo 15 m². Carga prevista = demanda diaria / 15 si no hay carga real registrada. |
| F7 | Absentismo 5 %: disponible = estándar − round(estándar × 0,05) si no hay recursos reales del turno. |
| F8 | OEE = 100 % (capacidad = 3600 / ciclo piezas/hora). |
| F9 | Colchón objetivo sobre stock de seguridad = 10 % (sólo puntúa, no es obligatorio). |
| F10 | Stock de seguridad por pieza: 400 piezas para cada célula VE, 200 para cada célula de combustión. Validación: Σ SS_c / densidad_c = 151,7 m² ≈ 150 m² que indica KWD → confirma F1/F2. |
| F11 | Célula 10 (servicio logístico) no produce piezas ni ocupa almacén; activa todas las horas laborables. |

## 1. Datos de entrada (plantilla Excel `data/entrada_ejemplo.xlsx`)

Hojas y columnas exactas (cabecera en fila 1):

1. `Celulas`: `celula` (int), `tipo` ("VE"|"COMB"), `ciclo_s`, `piezas_m2`, `operarios`, `picking`, `carretilleros`, `mto`, `calidad`, `kw`, `piezas_por_conjunto` (default 1). Datos de `ttablas.xlsx` (célula 10: carretilleros 0,5).
2. `Turnos`: `recurso` (operarios, picking, carretilleros, mto, calidad), `M`, `T`, `N` → 16/16/14, 2/2/2, 4/4/3, 7/7/4, 3/3/2.
3. `Almacen`: `zona`, `m2` → materia_prima 400, cargas 100, producto_terminado 800.
4. `Parametros`: `parametro`, `valor`, `descripcion`. Claves: `absentismo` 0.05, `colchon_ss` 0.10, `ss_ve` 400, `ss_comb` 200, `camiones_dia` 15, `intervalo_camion_h` 1.5, `primer_camion_h` 6, `m2_max_camion` 15, `solar_ini` 11, `solar_fin` 17, `factor_solar` 0.85, `factor_noche` 1.2, `peso_recursos` 0.5, `peso_espacio` 0.2, `peso_calidad_mto` 0.15, `peso_stock` 0.1, `peso_energia` 0.05, `tiempo_limite_s` 30, `horas_horizonte` 24.
5. `DemandaSemanal`: `semana_inicio` (fecha del lunes), `chasis_ve`, `chasis_comb`.
6. `CorreccionDiaria`: `fecha`, `chasis_ve`, `chasis_comb` (sustituye la demanda de ese día).
7. `StockActual`: `celula`, `piezas` (stock en el instante de inicio del plan; célula 10 se omite).
8. `Disponibilidad`: `celula`, `estado` ("BAJA"), `desde` (datetime), `hasta` (datetime). Fuera de estos intervalos la célula está de alta.
9. `Mantenimientos`: `fecha`, `turno` ("M"|"T"|"N"|"DIA"), `celula`, `tecnicos` → célula bloqueada ese turno y Mto disponible −tecnicos durante esas horas.
10. `RecursosReales`: `fecha`, `turno`, `operarios`, `picking`, `carretilleros`, `mto`, `calidad` (vacío = usar estándar con absentismo F7).
11. `Expediciones`: `fecha_hora` (datetime del camión), `chasis_ve`, `chasis_comb` (cargas reales; sustituyen la previsión de ese camión).

Escenario de ejemplo (para la demo): semana del lunes 2026-09-28, demanda 2400 VE / 1600 COMB chasis/semana; corrección 2026-10-02 → 520 VE / 300 COMB; stock inicial = round(1,3 × SS) por pieza; mantenimiento 2026-10-02 turno T célula 13, 2 técnicos; sin bajas; recursos reales 2026-10-02 T: operarios 15 (resto estándar); sin expediciones reales.

Demanda diaria del día D (chasis por tipo): `CorreccionDiaria[D]` si existe, si no `DemandaSemanal / 5` (sólo laborables).

## 2. Horizonte temporal

Slots horarios h = 0..H−1 (H = 24) desde `inicio` (datetime redondeado a la hora). Para cada slot:
`inicio`, `fin`, `turno` (M/T/N), `fecha_turno` (fecha a la que pertenece el turno; para N de 00–06 es el día anterior), `laborable` (bool, F4), disponibles por recurso (RecursosReales o F7; Mto − técnicos en mantenimiento), `factor_energia` (F5), `celulas_bloqueadas` (bajas + mantenimientos), camiones que salen en el slot (F6) con su carga VE/COMB en chasis (real o prevista).

**Carga prevista de un camión**: `d_ve = demanda_dia_ve/15`, `d_comb = demanda_dia_comb/15` (fraccional permitido). m² del camión = d_ve·Σ_{c∈VE} ppc_c/dens_c + d_comb·Σ_{c∈COMB} ppc_c/dens_c. Si supera 15 m² se escala proporcionalmente a 15 m² y se genera alerta "Capacidad de expedición insuficiente: X chasis no expedibles". Expediciones del día D: camiones del día laborable D (06:00 D … 03:00 D+1).

Turno actual = turno del slot 0. "Configuración del turno actual" = conjunto de células activas al menos una hora en los slots del turno actual dentro del horizonte.

## 3. Modelo MILP (PuLP + HiGHS)

Conjuntos: C = células, P = C \ {10} (células productivas), H slots, W ⊆ H laborables.
Parámetros: cap_c = 3600/ciclo_c (F8); req_{c,k} para k ∈ {op, pick, carr, mto, cal}; Disp_{k,h}; dens_c; ppc_c; SS_c (F10); kW_c; f_h; I0_c; env_{c,h} = piezas que salen en el slot h = (chasis del tipo de c en camiones del slot) × ppc_c; A = 800 m².

Variables:
- a_{c,h} ∈ {0,1}: célula c activa (recursos comprometidos) en la hora h.
- u_{c,h} ∈ [0,1]: fracción de la hora produciendo (permite activar sólo unas horas/parcial).
- I_{c,h} libre: stock al final de h (negativo = backlog, sólo posible con holgura).
- s_{c,h} ≥ 0: holgura stock de seguridad (penalización enorme → solución INVIABLE si > 0).
- sa_h ≥ 0: holgura de espacio (idem).
- short_{c,h} ≥ 0: déficit respecto al colchón (1+k)·SS_c.
- st_{c,h} ≥ 0: arranques (para estabilidad).

Restricciones obligatorias (descartan):
1. u_{c,h} ≤ a_{c,h}.
2. a_{10,h} = 1 ∀h ∈ W; a_{c,h} = 0 ∀h ∉ W.
3. a_{11,h} = a_{12,h}, u_{11,h} = u_{12,h}.
4. a_{c,h} = 0 si c ∈ celulas_bloqueadas(h).
5. Σ_c req_{c,k}·a_{c,h} ≤ Disp_{k,h} ∀k ∈ {op, pick, carr, mto, cal}, h ∈ W (calidad y mantenimiento obligatorios).
6. I_{c,h} = I_{c,h−1} + cap_c·u_{c,h} − env_{c,h} (c ∈ P; I_{c,−1} = I0_c).
7. I_{c,h} ≥ SS_c − s_{c,h} (stock de seguridad).
8. Σ_{c∈P} I_{c,h}/dens_c ≤ A + sa_h (espacio producto terminado).
9. short_{c,h} ≥ (1+k)·SS_c − I_{c,h}.
10. st_{c,h} ≥ a_{c,h} − a_{c,h−1} (a_{c,−1}=0).

Componentes normalizados en [0,1] (menor = mejor):
- R (recursos, 50 %) = media sobre h ∈ W y k ∈ {op, pick, carr} de usado/Disp (si Disp=0, se omite el término).
- S (espacio, 20 %) = media sobre h ∈ H de espacio_h / A.
- Q (calidad+mto, 15 %) = media sobre h ∈ W de ½(mto_usado/Disp_mto + cal_usado/Disp_cal) — más margen libre = mejor.
- B (stock seguridad, 10 %) = media sobre c ∈ P, h ∈ H de short_{c,h}/(k·SS_c).
- E (energía, 5 %) = Σ_{h,c} f_h·kW_c·u_{c,h} / Σ_{h∈W,c} max(f)·kW_c.

Objetivo: min 0,5R + 0,2S + 0,15Q + 0,1B + 0,05E + 1000·(Σ s_{c,h}/SS_c + Σ sa_h/A) + 1e−4·Σ st_{c,h}.
Puntuación global = 100·(1 − (0,5R + 0,2S + 0,15Q + 0,1B + 0,05E)) (pesos leídos de Parametros).
Contribución de cada criterio = 100·peso·(1 − componente) → suman la puntuación.

Solver: HiGHS vía PuLP, `mip_rel_gap = 0`, límite de tiempo `tiempo_limite_s`. Estado del plan:
- `OPTIMO` si el solver demuestra optimalidad; `FACTIBLE` si para por tiempo (reportar gap); `INVIABLE` si alguna holgura s o sa > 1e−6 (se muestra como plan de contingencia, nunca como recomendación viable).
- **Idoneidad (%)** = 100 · (1 − gap relativo). Además `validador.py` recalcula de forma independiente todas las restricciones obligatorias y la puntuación a partir del plan (certificado de factibilidad).

## 4. Top 1/2/3

Variable y_c (c ∈ C) binaria: y_c ≥ a_{c,h} ∀h del turno actual; y_c ≤ Σ_{h∈turno actual} a_{c,h}. Tras obtener la configuración S_j, añadir corte Σ_{c∈S_j}(1−y_c) + Σ_{c∉S_j} y_c ≥ 1 y resolver de nuevo. K = 3. Sólo los planes no INVIABLES se listan como Top; si ninguno es viable, se devuelve el mejor como "contingencia" con sus incumplimientos.

## 5. Plan de referencia (baseline "manual")

Heurística sin optimizar para cuantificar el impacto: en cada hora laborable, para cada célula productiva disponible, si el stock previsto de su pieza al final de las próximas 8 h queda por debajo de (1+k)·SS, activarla la hora completa (u=1) mientras los recursos (todas las restricciones 5) lo permitan, en orden de célula; respetar reglas 2–4. Se evalúa con la misma función de puntuación. Impacto = diferencias Top 1 vs baseline (horas-operario, m² medios, kWh, puntuación).

## 6. Explicación ("qué activar, por qué, con qué impacto")

Para el turno actual del Top 1:
- **Qué**: por célula activa: horas activas, franja (p. ej. 06:00–11:00), piezas producidas, recursos asignados.
- **Por qué**: por célula activa, la hora en que su pieza caería por debajo del SS sin producir (simulando I sin producción) o "repone colchón"; por célula inactiva: "BAJA/MANTENIMIENTO" o "stock suficiente hasta HH:MM". Indicar si la célula se ha colocado en franja solar.
- **Impacto**: KPIs (ver §7), comparación con Top 2/3 y con baseline.
- **Alertas**: stock < colchón, ocupación de almacén > 90 %, recursos al 100 %, expedición insuficiente, planes INVIABLES.

## 7. KPIs

Por plan y por turno: demanda (chasis a expedir vs cubiertos), operarios-hora / picking-hora / carretilleros-hora usados y % ocupación media y pico, Mto y Calidad % ocupación y pico, m² ocupados (medio, pico, % sobre 800), stock mínimo por pieza vs SS (cobertura en horas), kWh total, kWh en franja solar (%), kWh nocturnos, puntuación y contribuciones, idoneidad.

## 8. Rolling horizon y reconfiguración

- `reconfigurar(esc, rec_actual, evento, ahora)`: el stock en `ahora` se toma del plan vigente (I al final de la hora anterior) salvo que el evento sea `stock_real`; se aplica el evento al escenario (copia) y se vuelve a llamar a `recomendar` desde `ahora` (24 h). Tipos de evento: `baja_celula` {celula, desde, hasta}, `alta_celula` {celula}, `recursos_reales` {fecha, turno, valores}, `correccion_demanda` {fecha, ve, comb}, `expedicion_real` {fecha_hora, ve, comb}, `mantenimiento` {fecha, turno, celula, tecnicos}, `stock_real` {celula: piezas}.
- `simular_semana(esc, lunes)`: desde lunes 06:00, 15 iteraciones: resolver 24 h, consolidar las 8 h del turno actual (stock evoluciona), avanzar al siguiente turno. Devuelve tabla por turno con KPIs y configuración.

## 9. API Python (contrato entre módulos)

Paquete `src/kwd/`:
- `config.py`: `FLAGS: dict[str,str]`, `RECURSOS = ["operarios","picking","carretilleros","mto","calidad"]`, `CELULA_LOGISTICA = 10`, `CELULAS_PAREJA = (11, 12)`, horario de turnos.
- `datos.py`: `@dataclass Escenario` (DataFrames/valores de cada hoja + `parametros: dict`); `cargar_entrada(path) -> Escenario`; `guardar_entrada(esc, path)`; `crear_plantilla_ejemplo(path)`; `demanda_dia(esc, fecha) -> (ve, comb)`; `ss_por_celula(esc) -> dict`.
- `horizonte.py`: `@dataclass Horizonte` (`slots: DataFrame`, `envios: DataFrame` celula×slot piezas, `bloqueos: dict[int,set]`, `alertas: list[str]`); `construir_horizonte(esc, inicio, horas=None) -> Horizonte`.
- `modelo.py`: `resolver(esc, hz, cortes: list[frozenset]=[], tiempo_limite=None) -> Plan`; `evaluar(esc, hz, activacion, uso) -> Plan` (calcula KPIs/puntuación para una activación dada; usado por baseline).
- `plan.py`: `@dataclass Plan`: `estado`, `viable: bool`, `gap: float`, `idoneidad: float`, `puntuacion: float`, `componentes: dict` (R,S,Q,B,E), `contribuciones: dict`, `activacion` (DataFrame index=slot, columns=celula, 0/1), `uso` (fracción u), `produccion` (piezas), `stock` (piezas, columns=células productivas), `espacio` (Series m²), `recursos` (DataFrame: por slot `<recurso>_usado`, `<recurso>_disp`), `energia_kwh` (Series), `config_turno_actual: list[int]`, `incumplimientos: list[str]`, `kpis: dict`, `resumen_turnos: DataFrame` (fila por turno del horizonte).
- `baseline.py`: `plan_referencia(esc, hz) -> Plan`.
- `motor.py`: `@dataclass Recomendacion` (`inicio`, `horizonte`, `top: list[Plan]`, `contingencia: Plan|None`, `baseline: Plan`, `explicacion: dict` {que: DataFrame, porque: list[str], impacto: dict}, `alertas: list[str]`, `flags: dict`); `recomendar(esc, inicio, horas=None, top_k=3) -> Recomendacion`.
- `validador.py`: `validar(esc, hz, plan) -> list[str]` (lista vacía = cumple todas las reglas obligatorias).
- `rolling.py`: `@dataclass Evento(tipo: str, datos: dict)`; `aplicar_evento(esc, evento) -> Escenario`; `reconfigurar(esc, rec, evento, ahora) -> Recomendacion`; `simular_semana(esc, lunes) -> DataFrame`.
- `informes.py`: `generar_informe_pdf(rec, esc, ruta) -> ruta` (ReportLab + gráficos matplotlib: Gantt de 24 h, stock vs SS, ocupación de almacén, recursos por hora, energía con franja solar).
- `cli.py`: `python -m kwd.cli --entrada data/entrada_ejemplo.xlsx [--inicio "2026-10-02 14:00"] [--pdf salida/informe.pdf]` imprime el Top 3 y KPIs.

`app/dashboard.py` (Streamlit, español, layout ancho): ver §10.

## 10. Dashboard

Barra lateral: cargar Excel (o usar ejemplo), fecha/hora de inicio, botón "Calcular plan".
Pestañas:
1. **Recomendación**: tarjeta Top 1 (puntuación, idoneidad, estado, "Qué activar / Por qué / Impacto"), tabla del turno actual, contribuciones por criterio (barra apilada), alertas.
2. **KPIs**: tarjetas (demanda cubierta, ocupación operarios/picking/carretilleros, Mto/Calidad, almacén m² y %, stock mínimo vs SS, kWh y % solar) + gráficos horarios.
3. **Overview 24 h**: Gantt de células (color VE/COMB), resumen por turno, stock por pieza vs SS, ocupación almacén.
4. **Alternativas**: Top 1/2/3 + baseline lado a lado (KPIs y diferencias).
5. **Incidencias / Reconfigurar**: formularios para cada tipo de evento → recalcula y muestra antes/después.
6. **Datos de entrada**: editores (`st.data_editor`) de demanda, stock, disponibilidad, mantenimientos, recursos reales, expediciones; guardar escenario.
7. **Semana**: simulación rolling horizon de la semana.
8. **Informe**: botón generar PDF y descargar.
Pie: supuestos (FLAGS) en un expander.


## 11. Desviaciones y cambios de la implementación (v1.1)

- **Gap del solver**: `mip_rel_gap` no es 0 sino el parámetro `gap_relativo` (defecto 0,001), porque demostrar gap 0 supera los 30 s por degeneración y simetrías. La idoneidad se etiqueta "Idoneidad (óptimo garantizado ±gap)". Para planes INVIABLE (contingencia) y para la referencia manual, `idoneidad` y `gap` son `None` (se muestra "—", "No aplica: plan de contingencia"). `simular_semana` usa límite 10 s y gap 0,5 %.
- **Arranque en caliente**: la activación del plan de referencia se pasa a HiGHS como solución inicial en el primer plan (`resolver(..., inicial=)`).
- **Condición terminal blanda (F18)**: `I[c,H-1] >= SS_c + envíos previstos de las siguientes cobertura_final_h horas` (parámetro `cobertura_final_h`, defecto 8) con holgura penalizada con peso 10 (por debajo del 1000 de las reglas duras). Su incumplimiento NO vuelve inviable el plan: se emite el aviso "Stock final por debajo del objetivo de cobertura (célula X)" (`Plan.avisos`, alertas, `validador.avisos_plan`). La referencia manual también la tiene en cuenta en su mirada de 8 h.
- **Flags nuevos**: F12 (demanda sin semana → semana anterior; fin de semana 0), F13 (bloqueo por solape de slot), F14 (célula 10 no obligatoria si está bloqueada o faltan recursos), F15 (expedición real sustituye al camión más cercano ±45 min), F16 (energía de red = kW·u·factor; `kwh_bruto` sin factor), F17 (redondeo comercial), F18 (condición terminal), F19 (stock inicial de la demo 1,8 x SS en vez de 1,3 x SS, que hacía inviable el escenario).
- **Escenario de demo**: stock inicial = round(1,8 x SS). Se añade `data/escenario_contingencia.xlsx` (demo + célula 14 de baja del 02/10 06:00 al 03/10 06:00), que produce un plan INVIABLE de contingencia.
- **`Horizonte.envios`**: índice = slot, columnas = células productivas (transpuesta de "celula×slot"). Campos extra de `Horizonte`: `camiones`, `logistica_exigida`, `inicio`, `envio_final`.
- **Campos extra**: `Plan.avisos`, `.nombre`, `.tiempo_s`, `.objetivo`, `.holguras`, `Plan.idoneidad_txt()`; `Recomendacion.tiempo_total_s`, `.mejor`.
- **PuLP 4**: las variables se crean con `prob.add_variable` (helper `_var`, con retrocompatibilidad).
- **Hoja Parametros**: claves nuevas `gap_relativo` y `cobertura_final_h`.
