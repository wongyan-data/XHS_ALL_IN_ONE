@echo off
echo Starting XHS_ALL_IN_ONE...
cd /d "%~dp0"
start "XHS_ALL_IN_ONE_SERVER" cmd /k "python -u main.py --with-frontend --reload"
echo Project started!
echo Frontend: http://127.0.0.1:5173
echo Backend: http://127.0.0.1:8000
echo Do not close the new black window, that is your running server.
pause
