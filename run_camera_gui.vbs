Option Explicit

Dim shell, files, root, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

root = files.GetParentFolderName(WScript.ScriptFullName)
pythonw = files.BuildPath(root, ".venv\Scripts\pythonw.exe")

If Not files.FileExists(pythonw) Then
    MsgBox "This checkout needs its Python 3.12 virtual environment. Create .venv and install requirements.txt first.", vbCritical, "HHG Camera GUI"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
shell.Environment("PROCESS")("PYTHONPATH") = files.BuildPath(root, "src")
shell.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
command = Chr(34) & pythonw & Chr(34) & " -m hhg_control.ui.camera_gui"
shell.Run command, 0, False
