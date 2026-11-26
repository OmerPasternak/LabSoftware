Option Explicit

Dim shell, files, script, powershell, port, pattern, quote
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
quote = Chr(34)
script = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "check_ni_max_comparison.ps1")
If Not files.FileExists(script) Then
    MsgBox "The native VISA comparison script is missing beside this file.", vbCritical, "TC motor"
    WScript.Quit 1
End If

port = InputBox("Close NI MAX, TC motor and any other COM program. Enter the COM number for the read-only test.", "TC motor native VISA comparison", "21")
If port = "" Then WScript.Quit 0
Set pattern = New RegExp
pattern.Pattern = "^[1-9][0-9]{0,4}$"
If Not pattern.Test(port) Then
    MsgBox "Enter the COM number only, for example 21.", vbExclamation, "TC motor"
    WScript.Quit 1
End If
If CLng(port) > 65535 Then WScript.Quit 1

powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
shell.Run quote & powershell & quote & " -NoLogo -NoProfile -NoExit -ExecutionPolicy RemoteSigned -File " & _
    quote & script & quote & " -Physical -Port " & port, 1, False
