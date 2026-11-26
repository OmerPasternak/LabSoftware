param(
    [ValidateRange(1, 65535)][int]$Port = 21,
    [switch]$Physical,
    [switch]$LockAfterOpen,
    [switch]$FullComparison
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

    private const uint IoCompletion = 0x3FFF2009;
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int viEnableEvent(uint session, uint eventType, ushort mechanism, uint context);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int viReadAsync(uint session, IntPtr buffer, uint count, out uint job);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int viWriteAsync(uint session, IntPtr buffer, uint count, out uint job);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int viWaitOnEvent(uint session, uint eventType, uint timeout, out uint type, out uint context);
    [DllImport("visa32.dll", ExactSpelling = true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int viTerminate(uint session, ushort degree, uint job);

    public static int EnableCompletionQueue(uint session) {
        return viEnableEvent(session, IoCompletion, 1, 0);
    }

    public static int TransferAsync(uint session, byte[] bytes, bool write, out uint count, out int submit) {
        const uint jobAttribute = 0x3FFF4006;
        const uint statusAttribute = 0x3FFF4025;
        const uint count32Attribute = 0x3FFF4026;
        count = 0;
        uint size = (uint)bytes.Length;
        IntPtr buffer = Marshal.AllocHGlobal(bytes.Length);
        bool pending = false;
        try {
            if (write) Marshal.Copy(bytes, 0, buffer, bytes.Length);
            uint job;
            submit = write ? viWriteAsync(session, buffer, size, out job) : viReadAsync(session, buffer, size, out job);
            if (submit < 0) return submit;
            pending = true;
            uint type, context;
            int status = viWaitOnEvent(session, IoCompletion, 300, out type, out context);
            int waitError = status;
            if (status < 0) {
                viTerminate(session, 0, job);
                status = viWaitOnEvent(session, IoCompletion, 100, out type, out context);
                if (status < 0) return waitError;
            }
            try {
                uint completedJob, transferStatus;
                status = viGetAttribute(context, jobAttribute, out completedJob);
                if (status < 0) return status;
                if (type != IoCompletion || completedJob != job) {
                    viTerminate(session, 0, job);
                    throw new InvalidOperationException("Unexpected VISA I/O completion event.");
                }
                pending = false;
                status = viGetAttribute(context, statusAttribute, out transferStatus);
                if (status < 0) return status;
                status = viGetAttribute(context, count32Attribute, out count);
                if (status < 0) return status;
                if (count > size) throw new InvalidOperationException("Invalid VISA async byte count.");
                if (!write) Marshal.Copy(buffer, bytes, 0, (int)count);
                return waitError < 0 ? waitError : unchecked((int)transferStatus);
            } finally {
                int close = viClose(context);
                if (close < 0) throw new InvalidOperationException("VISA event close failed (0x" + close.ToString("X8") + ").");
            }
        } finally {
            // The last phase stops on any failure. Keep this small buffer alive until
            // process exit if VISA could still reference it after a failed abort/wait.
            if (!pending) Marshal.FreeHGlobal(buffer);
        }
    }
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
        Get = {
            param($handle, $attribute)
            [uint32]$value = 0
            $code = [TcMotorNativeVisa]::viGetAttribute($handle, $attribute, [ref]$value)
            Assert-VisaSuccess $code ('Get session attribute 0x{0:X8}' -f $attribute)
            $value
        }
        EnableAsync = {
            param($handle)
            [TcMotorNativeVisa]::EnableCompletionQueue($handle)
        }
        Write = {
            param($handle, [byte[]]$buffer, [bool]$async)
            [uint32]$count = 0
            if ($async) {
                [int]$submit = 0
                $code = [TcMotorNativeVisa]::TransferAsync($handle, $buffer, $true, [ref]$count, [ref]$submit)
                return @{ code = $code; count = $count; submit = $submit }
            }
            $code = [TcMotorNativeVisa]::viWrite($handle, $buffer, $buffer.Length, [ref]$count)
            @{ code = $code; count = $count }
        }
        Read = {
            param($handle, [int]$requested, [bool]$async)
            $buffer = [byte[]]::new($requested)
            [uint32]$count = 0
            $result = @{}
            if ($async) {
                [int]$submit = 0
                $code = [TcMotorNativeVisa]::TransferAsync($handle, $buffer, $false, [ref]$count, [ref]$submit)
                $result.submit = $submit
            } else {
                $code = [TcMotorNativeVisa]::viRead($handle, $buffer, $requested, [ref]$count)
            }
            $bytes = [byte[]]::new([Math]::Min([int64]$requested, [int64]$count))
            [Array]::Copy($buffer, $bytes, $bytes.Length)
            $result.code = $code
            $result.count = $count
            $result.bytes = $bytes
            $result
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

function Read-Reply($Api, $Handle, $Phase, $Row) {
    $counted = $Phase.counted
    $bytes = [Collections.Generic.List[byte]]::new()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    if ($Phase.trace_attributes) {
        $Row.timeout_ms = & $Api.Get $Handle 0x3FFF001A
        $Row.term_char = & $Api.Get $Handle 0x3FFF0018
        if ($Row.timeout_ms -ne 300 -or $Row.term_char -ne 13) { throw 'Unexpected VISA receive attributes.' }
    }
    if ($Counted) { Start-Sleep -Milliseconds 100 }
    while ($timer.ElapsedMilliseconds -lt 300) {
        $wanted = if ($Counted) { & $Api.Get $Handle 0x3FFF00AC } else { 64 }
        if ($wanted -eq 0) {
            Start-Sleep -Milliseconds 10
            continue
        }
        if ($wanted -gt (64 - $bytes.Count)) { throw 'Reply exceeds 64 bytes.' }
        $read = & $Api.Read $Handle $wanted $Phase.async
        $bytes.AddRange([byte[]]$read.bytes)
        $chunk = @{ requested = $wanted; returned = $read.count
                    visa_status = ('0x{0:X8}' -f [int]$read.code)
                    hex = [BitConverter]::ToString([byte[]]$read.bytes).Replace('-', ' ') }
        if ($Phase.async) { $chunk.submit_status = '0x{0:X8}' -f [int]$read.submit }
        $Row.reads.Add($chunk)
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
            if ($Counted -and (& $Api.Get $Handle 0x3FFF00AC) -ne 0) { throw 'Unexpected trailing input.' }
            return [Text.Encoding]::ASCII.GetString($bytes.ToArray())
        }
        Start-Sleep -Milliseconds 10
    }
    throw 'No complete reply within the receive deadline.'
}

function Invoke-NiMaxQuery($Api, $Handle, [ValidateSet('1TS', '1TP')][string]$Query, $Phase, $Row) {
    if ($Phase.counted -and (& $Api.Get $Handle 0x3FFF00AC) -ne 0) { throw 'Unexpected input before request.' }
    $buffer = [Text.Encoding]::ASCII.GetBytes($Query + "`r`n")
    $write = & $Api.Write $Handle $buffer $Phase.async
    $Row.write_status = ('0x{0:X8}' -f [int]$write.code)
    if ($Phase.async) { $Row.write_submit_status = '0x{0:X8}' -f [int]$write.submit }
    $Row.written = $write.count
    Assert-VisaSuccess $write.code 'Write'
    if ($write.count -ne $buffer.Length) { throw 'Short VISA write.' }
    $reply = Read-Reply $Api $Handle $Phase $Row
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
                                [switch]$LockAfterOpen, [switch]$FullComparison) {
    # The only instrument writes are the fixed TS and TP queries below.
    $resource = "ASRL${PortNumber}::INSTR"
    $report = [ordered]@{
        report_type = $(if ($FullComparison) { 'tc_motor_visa_software_comparison' } else { 'tc_motor_native_visa_comparison' })
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString('o')
        resource = $resource
        axis = 1
        process_bits = [IntPtr]::Size * 8
        query_gap_ms = $GapMs
        settings = @{ baud = 57600; data_bits = 8; parity = 'none'; stop_bits = 1
                      flow_control = 'XON/XOFF'; timeout_ms = 300 }
        runs = [Collections.Generic.List[object]]::new()
        port_released = $true
        ok = $false
    }
    $phases = @(@{ name = 'status_only_lf'; commands = @('1TS') * $StatusReads; windows = 1; counted = $false;
                   deferred = [bool]$LockAfterOpen; async = $false })
    if ($FullComparison) {
        $phases += @(
            @{ name = 'mixed_lf_lock_at_open'; counted = $false; deferred = $false; async = $false }
            @{ name = 'mixed_lf_lock_after_open'; counted = $false; deferred = $true; async = $false }
            @{ name = 'mixed_trace_cr_sync'; counted = $true; deferred = $true; trace_attributes = $true; async = $false }
            @{ name = 'mixed_trace_cr_async'; counted = $true; deferred = $true; trace_attributes = $true; async = $true }
        )
    } else {
        $report.follow_up = 'pending'
        $phases += @(
            @{ name = 'mixed_lf'; counted = $false; deferred = [bool]$LockAfterOpen; async = $false }
            @{ name = 'mixed_counted_cr'; counted = $true; deferred = [bool]$LockAfterOpen; async = $false }
        )
    }
    $mixedCommands = @('1TP') + (@('1TS') * 8) + @('1TP')
    foreach ($phase in $phases | Select-Object -Skip 1) {
        $phase.commands = $mixedCommands
        $phase.windows = $Windows
    }
    $report.planned_runs = @($phases | ForEach-Object { $_.name })
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
            $accessMode = if ($phase.deferred) { 0 } else { 1 }
            $openTimeoutMs = if ($phase.deferred) { 0 } else { 300 }
            $visaOpen = @{ access_mode = $accessMode; timeout_ms = $openTimeoutMs
                           lock_after_open = $phase.deferred; exclusive_during_io = $true }
            if (-not $FullComparison) {
                $report.visa_open = $visaOpen
                if ($phase.counted) { $report.follow_up = 'started' }
            }
            $run = [ordered]@{ name = $phase.name; ok = $false; valid_replies = 0; completed_windows = 0
                               visa_open = $visaOpen; async_api = $phase.async
                               trace_attributes = [bool]$phase.trace_attributes
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
                    if ($phase.deferred) {
                        # Keep all configuration and I/O behind the same exclusive lock as the baseline.
                        $stage = 'lock'
                        Assert-VisaSuccess (& $Api.Lock $handle) 'Acquire exclusive VISA lock'
                    }
                    $stage = 'configure'
                    Set-SessionAttributes $Api $handle $phase.counted
                    if ($phase.async) {
                        $stage = 'enable_async'
                        Assert-VisaSuccess (& $Api.EnableAsync $handle) 'Enable VISA I/O completion queue'
                    }
                    $stage = 'query'
                    foreach ($command in $phase.commands) {
                        $delay = $GapMs - ($clock.ElapsedMilliseconds - $lastSend)
                        if ($delay -gt 0) { Start-Sleep -Milliseconds $delay }
                        $lastSend = $clock.ElapsedMilliseconds
                        $row = [ordered]@{ command = $command; window = $window; send_offset_ms = $lastSend
                                           reads = [Collections.Generic.List[object]]::new() }
                        $run.exchanges.Add($row)
                        try {
                            Invoke-NiMaxQuery $Api $handle $command $phase $row
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
            if ($phase.counted -and -not $FullComparison) {
                $report.follow_up = if ($run.ok) { 'passed' } else { 'failed' }
            }
            Write-Host "  $($run.valid_replies) valid replies. Pass: $($run.ok)."
            if ($run.error) { Write-Host "  $($run.error)" }
            if ($run.cleanup_error) { Write-Host "  $($run.cleanup_error)" }
            Save-NiMaxReport $report $OutputPath
            # A query failure is data for the comparison. Always release its handle
            # before moving to the next fixed phase. Setup or cleanup failure aborts.
            if (-not $run.ok -and (-not $FullComparison -or -not $report.port_released -or
                                    $run.failure_stage -ne 'query')) { break }
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
        $report.ok = $report.port_released -and -not $report.manager_cleanup_error -and -not $report.error -and
                     $report.runs.Count -eq $phases.Count -and -not ($report.runs | Where-Object { -not $_.ok })
        Save-NiMaxReport $report $OutputPath
    }
    return $report
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $Physical) { throw 'Explicit -Physical authorization required for the query-only test.' }
    if ($FullComparison -and $LockAfterOpen) { throw 'The full comparison sets its own open sequence.' }
    $folder = Join-Path $PSScriptRoot 'reports'
    [IO.Directory]::CreateDirectory($folder) | Out-Null
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffffff'Z'")
    $prefix = if ($FullComparison) { 'TC_motor_visa_software' } else { 'TC_motor_native_visa' }
    $output = Join-Path $folder "${prefix}_${stamp}.json"
    $minutes = if ($FullComparison) { 15 } else { 7 }
    Write-Host "Query-only comparison on COM$Port, axis 1. Allow up to about $minutes minutes."
    try {
        $result = Invoke-NiMaxComparison $null $Port $output -LockAfterOpen:$LockAfterOpen -FullComparison:$FullComparison
        if ($result.error) { Write-Host $result.error }
        if ($result.manager_cleanup_error) { Write-Host $result.manager_cleanup_error }
        Write-Host "Report saved: $output"
        if ($FullComparison -and -not $result.ok) { exit 1 }
    } catch {
        Write-Host "Diagnostic error: $($_.Exception.Message)"
        if ($FullComparison) { exit 1 }
    }
}
