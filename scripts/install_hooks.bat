@echo off
REM Install the redline pre-commit hook into .git/hooks (hooks themselves are not versioned).
setlocal
set "REPO=%~dp0.."
if not exist "%REPO%\.git\hooks" (
  echo [ERROR] .git\hooks not found under %REPO%
  exit /b 1
)
copy /Y "%~dp0hooks\pre-commit" "%REPO%\.git\hooks\pre-commit" >nul
if errorlevel 1 (
  echo [ERROR] copy failed
  exit /b 1
)
echo installed: %REPO%\.git\hooks\pre-commit
echo The hook blocks commits that include a dirty demo_project.qgz (username / temp filter).
endlocal
