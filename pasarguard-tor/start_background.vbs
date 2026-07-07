Set WshShell = CreateObject("WScript.Shell")
' Run api.py using pythonw to avoid opening a console window. 
' The 0 means hidden window, False means do not wait for it to finish.
WshShell.Run "pythonw.exe api.py", 0, False
