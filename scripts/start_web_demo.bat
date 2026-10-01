@echo off
rem start_web_demo.bat - launch web viewer (tile/web server :8090 + RAG :8600) and open the browser
rem offline mode: comment out the rag line - the page then falls back to bundled GeoJSON snapshots
cd /d "%~dp0.."
netstat -ano | findstr :8090 >nul || start "baodao-tiles" /min python server\serve_tiles.py
netstat -ano | findstr :8600 >nul || start "baodao-rag" /min python server\rag_server.py
timeout /t 6 /nobreak >nul
start "" http://127.0.0.1:8090/
echo.
echo  web viewer : http://127.0.0.1:8090/
echo  live mode needs :8600 (PostGIS + Ollama) ; otherwise the page runs on bundled snapshots
echo.
timeout /t 8 >nul
