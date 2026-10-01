@echo off
set DST=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
if not exist "%DST%" mkdir "%DST%"
xcopy /E /I /Y "%~dp0..\plugin\baodao_wenyu" "%DST%\baodao_wenyu" >nul
echo Plugin copied to %DST%\baodao_wenyu
echo Enable it in QGIS: Plugins - Manage and Install Plugins - Installed - "baodao_wenyu"
pause
