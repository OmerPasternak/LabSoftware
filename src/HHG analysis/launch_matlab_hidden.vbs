Option Explicit
Dim engine, files, command, logPath, output, failure, count, quiet, stream
Set files = CreateObject("Scripting.FileSystemObject")
command = WScript.Arguments(0)
logPath = WScript.Arguments(1)
quiet = (WScript.Arguments.Count > 2)
On Error Resume Next
' Single creates a dedicated server; never hide an existing MATLAB session.
Set engine = CreateObject("Matlab.Application.Single")
CheckError
engine.Visible = 0
CheckError
WriteLog "BackendVisible=" & CStr(engine.Visible)
output = engine.Execute("hhg_launch_error='';try," & command & ";catch hhg_exception,hhg_launch_error=getReport(hhg_exception,'extended','hyperlinks','off');end")
CheckError
WriteLog CStr(output)
failure = engine.GetVariable("hhg_launch_error", "base")
CheckError
If Len(CStr(failure)) > 0 Then Fail CStr(failure)
engine.Visible = 0
CheckError
WriteLog "AnalyzerReady; BackendVisible=" & CStr(engine.Visible)
Do
    WScript.Sleep 500
    Err.Clear
    output = engine.Execute("hhg_open_count=numel(findall(groot,'Type','figure'));")
    If Err.Number = 0 Then
        count = engine.GetVariable("hhg_open_count", "base")
        CheckError
        If count = 0 And engine.Visible = 0 Then Exit Do
    ElseIf Err.Number <> -2147418111 And Err.Number <> -2147417846 Then
        CheckError
    End If
Loop
engine.Quit
Set engine = Nothing
WScript.Quit 0

Sub WriteLog(message)
    Set stream = files.OpenTextFile(logPath, 8, True, -1)
    stream.WriteLine message
    stream.Close
End Sub

Sub CheckError
    If Err.Number <> 0 Then
        Dim message
        message = Err.Description
        Err.Clear
        Fail message
    End If
End Sub

Sub Fail(message)
    WriteLog message
    If Not quiet Then MsgBox "Analysis could not start: " & message & vbCrLf & "Details: " & logPath, vbCritical, "Analysis startup"
    If IsObject(engine) Then engine.Quit
    WScript.Quit 1
End Sub
