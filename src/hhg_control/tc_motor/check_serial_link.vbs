Option Explicit

Dim shell, files, folder, launcher, port, pattern, command, comspec
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
launcher = files.BuildPath(folder, "run_TC_motor.bat")
If Not files.FileExists(launcher) Then
    MsgBox "The TC motor BAT launcher is missing beside this file.", vbCritical, "TC motor link check"
    WScript.Quit 1
End If

If WScript.Arguments.Count = 0 Then
    port = InputBox("Close LabVIEW and TC motor first. Enter the COM number for a 30-second query-only check (no motion).", "TC motor link check", "21")
Else
    port = WScript.Arguments(0)
End If
If port = "" Then WScript.Quit 0
Set pattern = New RegExp
pattern.Pattern = "^[1-9][0-9]{0,4}$"
If Not pattern.Test(port) Then
    MsgBox "Enter a positive COM number only, for example 21.", vbExclamation, "TC motor link check"
    WScript.Quit 1
End If

comspec = shell.ExpandEnvironmentStrings("%ComSpec%")
command = Chr(34) & comspec & Chr(34) & " /d /c " & Chr(34) & Chr(34) & launcher & Chr(34) & " check-link " & port & Chr(34)
' Keep the bounded diagnostic and its report path visible; do not start the GUI.
shell.Run command, 1, False
