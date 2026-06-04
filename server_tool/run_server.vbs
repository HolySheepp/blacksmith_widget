Set o = CreateObject("WScript.Shell")
d = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
o.Run "cmd /c python """ & d & "server.py""", 0, False
Set o = Nothing
