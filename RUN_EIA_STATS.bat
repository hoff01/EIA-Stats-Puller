@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_eia_stats_task.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" echo EIA Stats failed. Review the error above and old_stats\logs.
if not defined EIA_NO_PAUSE if "%~1"=="" pause
exit /b %EXIT_CODE%
