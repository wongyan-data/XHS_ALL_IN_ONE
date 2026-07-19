@echo off
echo Stopping XHS_ALL_IN_ONE...
taskkill /F /FI "WINDOWTITLE eq XHS_ALL_IN_ONE_SERVER*" /T >nul 2>&1
echo Stopped.
pause
