@echo off
echo Stopping Tor Checker Backend...
taskkill /F /IM pythonw.exe
taskkill /F /IM tor.exe
echo Done!
pause
