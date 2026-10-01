@echo off
cd /d "%~dp0.."
netstat -ano | findstr :8090 >nul || start "baodao-tiles" /min python server\serve_tiles.py
netstat -ano | findstr :8600 >nul || start "baodao-rag" /min python server\rag_server.py
timeout /t 3 >nul
curl -s -m 3 http://127.0.0.1:8090/street/7/106/55.png -o nul && echo [OK] tiles :8090
curl -s -m 5 http://127.0.0.1:8600/api/health && echo  [OK] rag :8600
