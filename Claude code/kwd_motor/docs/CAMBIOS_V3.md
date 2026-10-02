# Motor de decisión KWD — Cambios v3 (vinculante)

Complementa `CAMBIOS_V2.md` y `ESPECIFICACION.md`; donde haya conflicto manda este documento. Decisiones del equipo (2 oct 2026, noche). El límite de tiempo por plan se mantiene en **8 s** (no subirlo).

## A. Motor (`src/kwd/`)

### A1. Horas libres: objetivo principal (punto 1)
- "Horas libres" = horas-persona de **todo el personal presente** sin tarea: `L = Σ_{k,h∈W} (Disp_{k,h} − N_{k,h})` para k ∈ {operarios, picking, carretilleros, mto, calidad}.
- El criterio R (50 %, el de más peso) se **redefine** como fracción libre: `R = Σ (Disp − N) / Σ Disp` (antes premiaba usar poca gente; ahora premia ocupar a todo el personal presente sin sobrepasar recursos). Se elimina la plantilla `P_{k,s}`, el término auxiliar de horas libres de plantilla y el concepto EXCEDENTE: cada persona presente está ASIGNADA o LIBRE (o PARADA si es técnico en una parada).
- KPIs: `horas_libres_total`, `horas_libres_<rol>`, `ocupacion_<rol>_pct`. El roster (`Plan.personal`, `Plan.trabajadores`) se mantiene con todos los disponibles enumerados.

### A2. Sin plan manual (punto 2)
- Eliminar `baseline.py` del flujo: `Recomendacion` ya no lleva `baseline` (o `None`), sin `DEFINICION_PLAN_MANUAL`, sin warm start desde baseline (usar arranque propio: p. ej. fase 1 continua), sin "impacto vs referencia". El impacto se expresa contra Top 2/3 y, en contingencia, contra el plan previo a la incidencia.

### A3. Stock óptimo (punto 3)
- `stock_optimo_c = SS_c + demanda de un turno de la pieza c` = SS_c + demanda_dia(tipo de c)/3 (con la demanda del día del cierre de turno).
- En **cada cierre de turno** dentro del horizonte (06:00, 14:00, 22:00): variables `dev⁺, dev⁻ ≥ 0` con `I_{c,cierre} − opt_c = dev⁺ − dev⁻`; penalización `|I − opt| = dev⁺ + dev⁻`.
- El criterio B (10 %) pasa a ser `B = media_{c,cierres} (dev⁺ + dev⁻)/opt_c` (sustituye al colchón del 10 % y a la condición de stock final F18, que se eliminan).
- KPIs: desviación media y máxima respecto al óptimo por cierre de turno; tabla stock vs óptimo por pieza en cada cierre.

### A4. Sin flags (punto 4)
- Eliminar `config.FLAGS`, comentarios `# FLAG Fx` y cualquier referencia en app/informe de la app. Los supuestos quedan documentados en `docs/` (no en la app).

### A5. Contingencia y desabastecimiento (punto 5)
- Jerarquía de penalizaciones: **pedido no servido** (stock < 0) ≫ **stock por debajo de SS** ≫ criterios (R, S, Q, B, E). Con esto, ante una avería grave el plan consume SS para aguantar y, resuelta la incidencia, repone primero el SS y luego vuelve al **stock óptimo**.
- Stock < 0 ya **no** descarta el plan: se calcula igualmente y el plan se marca `CRITICO`, con **aviso para dirección**: pieza, hora en que se agota el SS, hora en que se agota el stock, piezas no servidas por camión/ciclo (hora) y total. `INVIABLE` queda sólo para imposibilidades duras (almacén > 800 m², reglas de células).
- `rolling.contingencia(esc, rec, ahora, bajas_celulas: list[{celula, desde, hasta}], bajas_personas: list[trabajador], bajas_rol: dict[rol→cantidad]=None) -> dict` con `rec_antes, rec_despues, reubicacion, maquinas, agotamiento (por pieza), desabastecimiento (DataFrame pieza, ciclo, piezas_no_servidas), aviso_direccion (str|None), resumen (list[str])`. Mantener `contingencia_celula` como atajo.

### A6. Datos internos y demanda (punto 6)
- **Sin Excel**. Constantes (células, turnos estándar, almacén, parámetros) fijas en `config.py`/`datos.py`.
- Estado de entrada persistente en `data/estado.json`: `demanda_semanal` (lista de {semana_inicio (lunes), piezas_ve, piezas_comb} para 5 días), `demanda_diaria` (lista de {fecha, piezas_ve, piezas_comb} — demanda corregida confirmada del día), `bajas`, `paradas`, `stock_actual`, `expediciones_reales`. `datos.cargar_estado(path)`, `datos.guardar_estado(esc, path)`, `datos.estado_ejemplo()` (escenario demo: semana 2026-09-28, 2.500 VE / 5.000 COMB por semana, C13 parada programada 02/10 T con 2 técnicos, 1 operario de baja 02/10 T, stock inicial actual del demo).
- Demanda de un día = corregida si existe; si no, **demanda semanal / 5** (se va sustituyendo conforme se confirman las corregidas).
- Eliminar `cargar_entrada/guardar_entrada` de Excel (o dejarlas sólo como import/export opcional NO usado por la app). `data/*.xlsx` se eliminan.

## B. App (`app/`, `src/kwd/informes.py`)

1. **Datos** (pestaña de entrada): demanda semanal (5 días, por tipo, con ayuda coches/día 2:1) y **demanda corregida del día** (alta por fecha); bajas por turno (todos los roles); paradas programadas; stock actual; expediciones reales. Guardar automáticamente en `data/estado.json`. Constantes en un expander de sólo lectura. Botón "Restaurar ejemplo".
2. **Planta en tiempo real**: **sólo visualización** (selector de hora, rejilla de 16 células con estado, trabajadores asignados, stock vs SS/óptimo; panel de personal). Sin botones de avería/baja ni recálculo automático.
3. **Contingencia** (única pestaña de incidencias): seleccionar células a dar de baja (desde/hasta) y personas concretas (por ID, filtro por rol) o nº por rol; botón **"Calcular plan de contingencia"** (sólo calcula al pulsar). Muestra: resumen en lenguaje de planta, reubicación de trabajadores, máquinas a activar, consumo de SS y agotamiento por pieza, **aviso para dirección destacado** si hay desabastecimiento, KPIs antes/después, botón "Aplicar como incidencia real".
4. Quitar todo lo del plan manual y los FLAGS (pie/expander, tablas de impacto vs referencia, informe PDF de la app).
5. KPIs: horas libres por rol y total (KPI principal, destacado), ocupación, stock vs óptimo al cierre de turno, puntuación y máximo alcanzable, idoneidad.
6. Informe PDF de la app (`informes.py`): adaptar a lo anterior (sin plan manual ni flags; horas libres; stock óptimo; aviso a dirección si lo hay).
