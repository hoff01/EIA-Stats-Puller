@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_eia_stats_task.ps1" -SetupOnly
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" echo EIA Stats setup failed. Review the error above.
if not defined EIA_NO_PAUSE pause
exit /b %EXIT_CODE%
