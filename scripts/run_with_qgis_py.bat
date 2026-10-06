@echo off
rem Run a pipeline script with QGIS Python + PROJ/GDAL environment.
rem Usage: scripts\run_with_qgis_py.bat pipeline\make_tiles_terrain.py [args...]
rem Fix for: "PROJ: proj_create: no database context specified" when calling
rem QGIS python.exe directly (PROJ_LIB / GDAL_DATA not set).
setlocal
set "QGIS_ROOT=C:\Program Files\QGIS 3.44.15"
if not exist "%QGIS_ROOT%\apps\Python312\python.exe" (
  echo [ERROR] QGIS not found at "%QGIS_ROOT%" - edit QGIS_ROOT in this script.
  exit /b 1
)
set "PROJ_LIB=%QGIS_ROOT%\share\proj"
set "PROJ_DATA=%QGIS_ROOT%\share\proj"
set "GDAL_DATA=%QGIS_ROOT%\apps\gdal\share\gdal"
set "PATH=%QGIS_ROOT%\bin;%QGIS_ROOT%\apps\gdal\bin;%QGIS_ROOT%\apps\Python312;%PATH%"
set "PYTHONPATH=%QGIS_ROOT%\apps\Python312\Lib\site-packages;%QGIS_ROOT%\apps\qgis-ltr\python"
rem cd to project root (parent of scripts\) so relative paths in pipeline scripts work
cd /d "%~dp0.."
"%QGIS_ROOT%\apps\Python312\python.exe" %*
endlocal
