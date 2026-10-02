# Plan de acción del equipo (5 personas) — NTC 2026 · Reto KWD

Punto de partida: v1 funcional de la app en local (motor MILP + dashboard + informes PDF).
Hito final: **sábado 3 de octubre, 08:30 — presentación del reto** (selección de finalistas 09:00, presentaciones 10:00).

## Roles

| # | Rol | Responsabilidad principal | Entregable |
|---|---|---|---|
| P1 | **Modelado y optimización** (líder técnico) | Validar la formulación MILP, pesos y normalizaciones; revisar flags F1–F11 con KWD; ajustar restricciones (stock, espacio, energía) y rendimiento del solver. | Modelo validado + justificación matemática (1 diapositiva) |
| P2 | **Datos y validación con KWD** | Contrastar con el personal de KWD los supuestos (piezas por conjunto, horarios de turno, franja solar, cargas de camión); preparar escenarios realistas (demanda semanal + correcciones, stock, bajas). | Excel de escenarios + lista de flags confirmados/cambiados |
| P3 | **Dashboard / UX** | Pulir el dashboard para que un encargado lo use en minutos; flujo de demo (cargar → validar → excluir → decidir → incidencia → reconfigurar). | Dashboard final + guion de la demo en vivo |
| P4 | **Informes y presentación** | Diapositivas del pitch (problema → solución → demo → impacto → idoneidad → próximos pasos); revisar el informe PDF que genera la app. | Presentación (≤10 min) + informe PDF de ejemplo |
| P5 | **Pruebas y calidad** | Batería de escenarios límite (célula crítica de baja, noche con poco personal, almacén lleno, demanda imposible); comprobar que ninguna recomendación viola reglas; medir tiempos. | Informe de pruebas + 3 escenarios de demo preparados |

## Cronograma

| Franja | Actividad | Quién |
|---|---|---|
| Vie 14:00–15:00 | Comida. Cada uno instala el proyecto (VS Code + `iniciar_dashboard.bat`) y lo arranca. | Todos |
| 15:00–16:00 | Lectura de la especificación y de los flags; reparto de tareas; lista de preguntas para KWD. | Todos (P1 coordina) |
| 16:00–17:00 | Espacio RRHH / contacto con la empresa: resolver flags con KWD. | P2 (+ P1) |
| 16:00–19:00 | Ajustes del modelo según respuestas; escenarios de prueba; mejoras de UX; estructura del pitch. | P1, P5, P3, P4 |
| 19:00–21:00 | Integración: congelar versión (v1.1). Ensayo de demo completo con el escenario oficial. | Todos |
| 21:00–22:00 | Cena. | — |
| 22:00–00:30 | Pulido: textos, gráficos, informe PDF final, diapositiva de idoneidad y de impacto (vs. plan manual). Segundo ensayo cronometrado. | P3, P4 (P1/P5 soporte) |
| Sáb 07:30–08:30 | Desayuno, comprobación final del portátil (app arrancada, escenario cargado, PDF generado, plan B en vídeo/capturas). | Todos |
| 08:30 | Presentación. | P4 + P1 (demo: P3) |

## Reglas de trabajo

- Una única fuente de verdad: `docs/ESPECIFICACION.md`. Cualquier cambio de supuesto → actualizar flag + test.
- Antes de congelar: `pytest` en verde y validador sin incumplimientos en los 3 escenarios de demo.
- Plan B de la demo: capturas y PDF generados de antemano por si falla el portátil.

## Mensajes clave del pitch

1. **Qué activar, por qué y con qué impacto**: una decisión clara y explicable, no solo una configuración posible.
2. **Idoneidad demostrada**: el solver certifica que el Top 1 es óptimo con un margen máximo del 0,1 % (gap MIP) y un validador independiente comprueba todas las reglas.
3. **Planta dinámica**: rolling horizon de 24 h, reconfiguración en segundos ante averías, bajas o cambios de demanda.
4. **Impacto cuantificado** frente al plan manual: horas-operario, m² de almacén y kWh ahorrados.
5. **Preparado para evolucionar**: supuestos aislados como flags configurables (piezas por conjunto, mantenimiento externo, turnos).
