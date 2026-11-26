param(
    [ValidateRange(1, 65535)][int]$Port = 21,
    [switch]$Physical,
    [switch]$LockAfterOpen
)

$ErrorActionPreference = 'Stop'

function New-NiVisaApi {
    # The same synchronous VISA ABI used by the NI MAX Basic I/O panel.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class TcMotorNativeVisa {
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viOpenDefaultRM(out uint session);
    [DllImport("visa32.dll", ExactSpelling = true, CharSet = CharSet.Ansi)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viOpen(uint manager, string resource, uint access, uint timeout, out uint session);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viLock(uint session, uint lockType, uint timeout, IntPtr requestedKey, IntPtr accessKey);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viSetAttribute(uint session, uint attribute, UIntPtr value);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viGetAttribute(uint session, uint attribute, out uint value);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viWrite(uint session, byte[] buffer, uint count, out uint written);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viRead(uint session, [Out] byte[] buffer, uint count, out uint received);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    public static extern int viClose(uint session);
}
'@
    return @{
        Manager = {
            [uint32]$handle = 0
            $code = [TcMotorNativeVisa]::viOpenDefaultRM([ref]$handle)
            @{ code = $code; handle = $handle }
        }
        Open = {
            param($manager, $resource, [uint32]$accessMode, [uint32]$timeoutMs)
            [uint32]$handle = 0
            $code = [TcMotorNativeVisa]::viOpen($manager, $resource, $accessMode, $timeoutMs, [ref]$handle)
            @{ code = $code; handle = $handle }
        }
        Lock = {
            param($handle)
            [TcMotorNativeVisa]::viLock($handle, 1, 300, [IntPtr]::Zero, [IntPtr]::Zero)
        }
        Set = {
            param($handle, $attribute, $value)
            [TcMotorNativeVisa]::viSetAttribute($handle, $attribute, [UIntPtr]::new([uint32]$value))
        }
        Available = {
            param($handle)
            [uint32]$count = 0
            $code = [TcMotorNativeVisa]::viGetAttribute($handle, 0x3FFF00AC, [ref]$count)
            Assert-VisaSuccess $code 'Bytes available'
            $count
        }
        Write = {
            param($handle, [byte[]]$buffer)
            [uint32]$count = 0
            $code = [TcMotorNativeVisa]::viWrite($handle, $buffer, $buffer.Length, [ref]$count)
            @{ code = $code; count = $count }
        }
        Read = {
            param($handle, [int]$requested)
            $buffer = [byte[]]::new($requested)
            [uint32]$count = 0
            $code = [TcMotorNativeVisa]::viRead($handle, $buffer, $requested, [ref]$count)
            $bytes = [byte[]]::new([Math]::Min([int64]$requested, [int64]$count))
            [Array]::Copy($buffer, $bytes, $bytes.Length)
            @{ code = $code; count = $count; bytes = $bytes }
        }
        Close = { param($handle) [TcMotorNativeVisa]::viClose($handle) }
    }
}

function Assert-VisaSuccess([int]$Code, [string]$Operation) {
    if ($Code -lt 0) { throw ('{0} failed (VISA {1:X8})' -f $Operation, $Code) }
}

function Set-SessionAttributes($Api, $Handle, [bool]$Counted) {
    $attrs = [ordered]@{
        0x3FFF001A = 300   # I/O timeout ms
        0x3FFF0021 = 57600 # Baud
        0x3FFF0022 = 8     # Data bits
        0x3FFF0025 = 1     # XON/XOFF
        0x3FFF0023 = 0     # No parity
        0x3FFF0024 = 10    # One stop bit
        0x3FFF0018 = $(if ($Counted) { 13 } else { 10 })
        0x3FFF0038 = [int](-not $Counted)
        0x3FFF0036 = 0     # Recognize END on reads
        0x3FFF00B3 = 2     # Stop reads at term char
        0x3FFF00B4 = 0     # Append nothing to writes
    }
    foreach ($attr in $attrs.GetEnumerator()) {
        $code = & $Api.Set $Handle $attr.Key $attr.Value
        Assert-VisaSuccess $code ('Set session attribute 0x{0:X8}' -f $attr.Key)
    }
}

function Read-Reply($Api, $Handle, [bool]$Counted, $Row) {
    $bytes = [Collections.Generic.List[byte]]::new()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    if ($Counted) { Start-Sleep -Milliseconds 100 }
    while ($timer.ElapsedMilliseconds -lt 300) {
        $wanted = if ($Counted) { & $Api.Available $Handle } else { 64 }
        if ($wanted -eq 0) {
            Start-Sleep -Milliseconds 10
            continue
        }
        if ($wanted -gt (64 - $bytes.Count)) { throw 'Reply exceeds 64 bytes.' }
        $read = & $Api.Read $Handle $wanted
        $bytes.AddRange([byte[]]$read.bytes)
        $Row.reads.Add(@{ requested = $wanted; returned = $read.count
                          visa_status = ('0x{0:X8}' -f [int]$read.code)
                          hex = [BitConverter]::ToString([byte[]]$read.bytes).Replace('-', ' ') })
        $Row.rx_hex = [BitConverter]::ToString($bytes.ToArray()).Replace('-', ' ')
        Assert-VisaSuccess $read.code 'Read'
        if ($read.count -ne $read.bytes.Length -or $read.count -gt $wanted) {
            throw 'Invalid VISA byte count.'
        }
        if ($Counted -and $read.count -ne $wanted -and
                ($read.count -ne ($wanted - 1) -or $read.count -eq 0 -or $read.bytes[-1] -ne 13)) {
            throw 'Unexpected short counted read.'
        }
        if (-not $Counted -or ($bytes.Count -gt 0 -and $bytes[$bytes.Count - 1] -eq 10)) {
            if ($Counted -and $timer.ElapsedMilliseconds -gt 300) { throw 'Counted reply arrived after deadline.' }
            if ($Counted -and (& $Api.Available $Handle) -ne 0) { throw 'Unexpected trailing input.' }
            return [Text.Encoding]::ASCII.GetString($bytes.ToArray())
        }
        Start-Sleep -Milliseconds 10
    }
    throw 'No complete reply within the receive deadline.'
}

function Invoke-NiMaxQuery($Api, $Handle, [ValidateSet('1TS', '1TP')][string]$Query, [bool]$Counted, $Row) {
    if ($Counted -and (& $Api.Available $Handle) -ne 0) { throw 'Unexpected input before request.' }
    $buffer = [Text.Encoding]::ASCII.GetBytes($Query + "`r`n")
    $write = & $Api.Write $Handle $buffer
    $Row.write_status = ('0x{0:X8}' -f [int]$write.code)
    $Row.written = $write.count
    Assert-VisaSuccess $write.code 'Write'
    if ($write.count -ne $buffer.Length) { throw 'Short VISA write.' }
    $reply = Read-Reply $Api $Handle $Counted $Row
    $pattern = if ($Query -eq '1TS') { '\A1TS[0-9A-Fa-f]{6}\r\n\z' } else {
        '\A1TP[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?\r\n\z'
    }
    if (-not [regex]::IsMatch($reply, $pattern)) { throw 'Malformed reply. See raw bytes in the report.' }
    if ($Query -eq '1TP') {
        $position = [double]::Parse($reply.Substring(3), [Globalization.CultureInfo]::InvariantCulture)
        if ([double]::IsInfinity($position) -or [double]::IsNaN($position)) { throw 'Invalid position.' }
    }
    $Row.reply = $reply.TrimEnd([char[]]"`r`n")
}

function Save-NiMaxReport($Report, [string]$OutputPath) {
    $temp = "$OutputPath.tmp"
    [IO.File]::WriteAllText($temp, ($Report | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    if ([IO.File]::Exists($OutputPath)) {
        [IO.File]::Replace($temp, $OutputPath, [NullString]::Value)
    } else {
        [IO.File]::Move($temp, $OutputPath)
    }
}

function Invoke-NiMaxComparison($Api, [int]$PortNumber, [string]$OutputPath,
                                [int]$StatusReads = 20, [int]$Windows = 20, [int]$GapMs = 1000,
                                [switch]$LockAfterOpen) {
    # The only instrument writes are the fixed TS and TP queries below.
    $resource = "ASRL${PortNumber}::INSTR"
    $accessMode = if ($LockAfterOpen) { 0 } else { 1 }
    $openTimeoutMs = if ($LockAfterOpen) { 0 } else { 300 }
    $report = [ordered]@{
        report_type = 'tc_motor_native_visa_comparison'
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString('o')
        resource = $resource
        axis = 1
        process_bits = [IntPtr]::Size * 8
        query_gap_ms = $GapMs
        visa_open = @{ access_mode = $accessMode; timeout_ms = $openTimeoutMs
                       lock_after_open = [bool]$LockAfterOpen; exclusive_during_io = $true }
        settings = @{ baud = 57600; data_bits = 8; parity = 'none'; stop_bits = 1
                      flow_control = 'XON/XOFF'; timeout_ms = 300 }
        runs = [Collections.Generic.List[object]]::new()
        follow_up = 'pending'
        port_released = $true
        ok = $false
    }
    $phases = @(
        @{ name = 'status_only_lf'; commands = @('1TS') * $StatusReads; windows = 1; counted = $false }
        @{ name = 'mixed_lf'; commands = @('1TP') + (@('1TS') * 8) + @('1TP'); windows = $Windows; counted = $false }
        @{ name = 'mixed_counted_cr'; commands = @('1TP') + (@('1TS') * 8) + @('1TP'); windows = $Windows; counted = $true }
    )
    $manager = 0
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $lastSend = -$GapMs
    Save-NiMaxReport $report $OutputPath
    try {
        if ($null -eq $Api) { $Api = New-NiVisaApi }
        $opened = & $Api.Manager
        Assert-VisaSuccess $opened.code 'Open VISA manager'
        $manager = $opened.handle
        try {
            $dll = [Diagnostics.Process]::GetCurrentProcess().Modules |
                Where-Object ModuleName -eq 'visa32.dll' | Select-Object -First 1
            if ($dll) {
                $report.visa_dll = @{ path = $dll.FileName; version = $dll.FileVersionInfo.FileVersion
                                     product = $dll.FileVersionInfo.ProductName }
            }
        } catch {
            $report.visa_dll_error = $_.Exception.Message
        }
        foreach ($phase in $phases) {
            if ($phase.counted) { $report.follow_up = 'started' }
            $run = [ordered]@{ name = $phase.name; ok = $false; valid_replies = 0; completed_windows = 0
                               exchanges = [Collections.Generic.List[object]]::new() }
            $report.runs.Add($run)
            Write-Host "Running $($phase.name)..."
            for ($window = 1; $window -le $phase.windows; $window++) {
                $handle = 0
                $row = $null
                $stage = 'open'
                try {
                    $opened = & $Api.Open $manager $resource $accessMode $openTimeoutMs
                    Assert-VisaSuccess $opened.code 'Open COM session'
                    $handle = $opened.handle
                    if ($LockAfterOpen) {
                        # Keep all configuration and I/O behind the same exclusive lock as the baseline.
                        $stage = 'lock'
                        Assert-VisaSuccess (& $Api.Lock $handle) 'Acquire exclusive VISA lock'
                    }
                    $stage = 'configure'
                    Set-SessionAttributes $Api $handle $phase.counted
                    $stage = 'query'
                    foreach ($command in $phase.commands) {
                        $delay = $GapMs - ($clock.ElapsedMilliseconds - $lastSend)
                        if ($delay -gt 0) { Start-Sleep -Milliseconds $delay }
                        $lastSend = $clock.ElapsedMilliseconds
                        $row = [ordered]@{ command = $command; window = $window; send_offset_ms = $lastSend
                                           reads = [Collections.Generic.List[object]]::new() }
                        $run.exchanges.Add($row)
                        try {
                            Invoke-NiMaxQuery $Api $handle $command $phase.counted $row
                        } finally {
                            $row.elapsed_ms = $clock.ElapsedMilliseconds - $lastSend
                        }
                        $run.valid_replies++
                    }
                    $run.completed_windows++
                } catch {
                    $run.failure_stage = $stage
                    $run.error = $_.Exception.Message
                    if ($row) { $row.error = $run.error }
                } finally {
                    if ($handle) {
                        try {
                            Assert-VisaSuccess (& $Api.Close $handle) 'Close COM session'
                        } catch {
                            $run.cleanup_error = $_.Exception.Message
                            $report.port_released = $false
                        }
                    }
                }
                if ($run.error -or -not $report.port_released) { break }
                if ($phase.windows -gt 1 -and $window % 5 -eq 0) {
                    Write-Host "  $window/$($phase.windows) windows complete."
                    Save-NiMaxReport $report $OutputPath
                }
            }
            $run.ok = $report.port_released -and $run.completed_windows -eq $phase.windows -and -not $run.error
            if ($phase.counted) { $report.follow_up = if ($run.ok) { 'passed' } else { 'failed' } }
            Write-Host "  $($run.valid_replies) valid replies. Pass: $($run.ok)."
            if ($run.error) { Write-Host "  $($run.error)" }
            if ($run.cleanup_error) { Write-Host "  $($run.cleanup_error)" }
            Save-NiMaxReport $report $OutputPath
            if (-not $run.ok) { break }
        }
    } catch {
        $report.error = $_.Exception.Message
    } finally {
        if ($manager) {
            try {
                Assert-VisaSuccess (& $Api.Close $manager) 'Close VISA manager'
            } catch {
                $report.manager_cleanup_error = $_.Exception.Message
            }
        }
        if ($report.follow_up -eq 'pending') { $report.follow_up = 'skipped because the LF comparison did not pass' }
        $report.ok = $report.port_released -and $report.follow_up -eq 'passed' -and
                     -not $report.manager_cleanup_error -and -not $report.error
        Save-NiMaxReport $report $OutputPath
    }
    return $report
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $Physical) { throw 'Explicit -Physical authorization required for the query-only test.' }
    $folder = Join-Path $PSScriptRoot 'reports'
    [IO.Directory]::CreateDirectory($folder) | Out-Null
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffffff'Z'")
    $output = Join-Path $folder "TC_motor_native_visa_${stamp}.json"
    Write-Host "Query-only comparison on COM$Port, axis 1. Allow up to about seven minutes."
    try {
        $result = Invoke-NiMaxComparison $null $Port $output -LockAfterOpen:$LockAfterOpen
        if ($result.error) { Write-Host $result.error }
        if ($result.manager_cleanup_error) { Write-Host $result.manager_cleanup_error }
        Write-Host "Report saved: $output"
    } catch {
        Write-Host "Diagnostic error: $($_.Exception.Message)"
    }
}
