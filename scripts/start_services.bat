@echo off
cd /d "%~dp0.."
rem --- pick a python that has project deps (fastapi/psycopg2) ---
set PY=python
python -c "import psycopg2, fastapi" >nul 2>&1
if errorlevel 1 set PY=C:\bdaopy\python.exe
echo [i] using python: %PY%
netstat -ano | findstr :8090 >nul || start "baodao-tiles" /min "%PY%" server\serve_tiles.py
netstat -ano | findstr :8600 >nul || start "baodao-rag" /min "%PY%" server\rag_server.py
ping -n 7 127.0.0.1 >nul
curl -s --noproxy "*" -m 3 http://127.0.0.1:8090/street/7/106/55.png -o nul && echo [OK] tiles :8090
curl -s --noproxy "*" -m 8 http://127.0.0.1:8600/api/health && echo  [OK] rag :8600
