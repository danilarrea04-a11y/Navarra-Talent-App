# Navarra Talent App · Motor de decisión KWD

Motor de decisión de producción para KWD España (Navarra Talent Challenge 2026): recomienda qué células activar en cada turno, por qué y con qué impacto (MILP con HiGHS, horizonte de 24 h, reconfiguración ante incidencias, dashboard e informes PDF).

## Descargar e instalar (Windows)

1. Instala **Python 3.14** desde https://www.python.org/downloads/ marcando **"Add python.exe to PATH"**.
2. Descarga este repositorio: botón verde **Code → Download ZIP**, y descomprímelo.
3. Entra en `Claude code/kwd_motor` y haz doble clic en **`instalar.bat`** (solo la primera vez; descarga las librerías).
4. Haz doble clic en **`iniciar_dashboard.bat`**: la app se abre en http://localhost:8501.

También se puede abrir la carpeta `kwd_motor` en VS Code y ejecutar la configuración **"Dashboard (Streamlit)"** desde *Ejecutar y depurar*.

## Contenido

| Ruta | Qué es |
|---|---|
| `Claude code/kwd_motor/src/kwd/` | Motor: datos, horizonte, modelo MILP, Top 3, validador, reconfiguración, informes |
| `Claude code/kwd_motor/app/` | Dashboard (Streamlit) |
| `Claude code/kwd_motor/data/` | Excel de entrada de ejemplo y escenario de contingencia |
| `Claude code/kwd_motor/docs/` | Especificación, plan del equipo y generadores de documentos |
| `Claude code/kwd_motor/salida/` | Informes PDF generados (solución y plan de acción, informe técnico, resumen del MILP) |
| `Claude code/kwd_motor/tests/` | Pruebas automáticas (`pytest`) |
