@echo off
cd /d %~dp0
set PYTHONPATH=%~dp0src
.venv\Scripts\python.exe -m streamlit run app\dashboard.py
pause
