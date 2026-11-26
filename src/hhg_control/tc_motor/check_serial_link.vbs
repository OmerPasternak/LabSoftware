Option Explicit

Dim shell, files, folder, launcher, port, pattern, command, comspec, mode, choice
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
launcher = files.BuildPath(folder, "run_TC_motor.bat")
If Not files.FileExists(launcher) Then
    MsgBox "The TC motor BAT launcher is missing beside this file.", vbCritical, "TC motor link check"
    WScript.Quit 1
End If

If WScript.Arguments.Count = 0 Then
    port = InputBox("Close LabVIEW and TC motor first. Enter the COM number, then choose the diagnostic to run.", "TC motor link check", "21")
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

mode = "check-link"
If WScript.Arguments.Count = 0 Then
    choice = InputBox("1 - Short VISA versus Direct serial comparison" & vbCrLf & _
        "2 - 10-minute Direct serial check using GUI inspection startup" & vbCrLf & _
        "3 - 2-minute LabVIEW reference check over VISA" & vbCrLf & _
        "All checks only read controller information.", "Choose TC motor link check", "3")
    Select Case choice
        Case "": WScript.Quit 0
        Case "1": mode = "check-link"
        Case "2": mode = "check-link-extended"
        Case "3": mode = "check-link-reference"
        Case Else
            MsgBox "Enter 1, 2, or 3.", vbExclamation, "TC motor link check"
            WScript.Quit 1
    End Select
ElseIf WScript.Arguments.Count = 2 Then
    Select Case WScript.Arguments(1)
        Case "extended": mode = "check-link-extended"
        Case "reference": mode = "check-link-reference"
        Case Else: WScript.Quit 1
    End Select
End If

comspec = shell.ExpandEnvironmentStrings("%ComSpec%")
command = Chr(34) & comspec & Chr(34) & " /d /c " & Chr(34) & Chr(34) & launcher & Chr(34) & " " & mode & " " & port & Chr(34)
' Keep the bounded diagnostic and its report path visible; do not start the GUI.
shell.Run command, 1, False
