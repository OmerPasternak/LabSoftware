Option Explicit

Dim shell, files, folder, launcher, comspec, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
launcher = files.BuildPath(folder, "run_TC_motor.bat")
If Not files.FileExists(launcher) Then
    MsgBox "The TC motor setup launcher is missing beside this file.", vbCritical, "TC motor setup"
    WScript.Quit 1
End If

comspec = shell.ExpandEnvironmentStrings("%ComSpec%")
command = Chr(34) & comspec & Chr(34) & " /d /c " & Chr(34) & Chr(34) & launcher & Chr(34) & " setup" & Chr(34)
' Keep setup visible so package-install and VISA-runtime errors can be read.
shell.Run command, 1, False
