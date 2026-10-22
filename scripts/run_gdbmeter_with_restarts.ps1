param(
    [int]$DurationSeconds = 600,
    [string]$GdbmeterRoot = "",
    [string]$OutputRoot = "",
    [string]$RunName = "",
    [string]$Database = "neo4j",
    [string]$Oracle = "partition",
    [int]$RestartDelaySeconds = 2,
    [switch]$NoVerboseQueries
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-LineCount {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }
    return (Get-Content -LiteralPath $Path -ReadCount 1000 | Measure-Object -Line).Lines
}

function Get-MatchCount {
    param(
        [string]$Path,
        [string]$Pattern
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }
    return @(Select-String -LiteralPath $Path -Pattern $Pattern -AllMatches).Count
}

function Get-NewLogFiles {
    param(
        [string]$LogDir,
        [hashtable]$Before
    )
    if (-not (Test-Path -LiteralPath $LogDir)) {
        return @()
    }
    $files = Get-ChildItem -LiteralPath $LogDir -File
    return @($files | Where-Object { -not $Before.ContainsKey($_.FullName) })
}

if ([string]::IsNullOrWhiteSpace($GdbmeterRoot)) {
    $GdbmeterRoot = Join-Path $PSScriptRoot "..\external_tools\gdbmeter"
}

$root = (Resolve-Path -LiteralPath $GdbmeterRoot).Path
$jarArgument = Join-Path (Join-Path "build" "libs") "gdbmeter-1.0.0-SNAPSHOT-all.jar"
$jarPath = Join-Path $root $jarArgument
if (-not (Test-Path -LiteralPath $jarPath)) {
    throw "GDBMeter jar not found: $jarPath"
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $root "baseline_runs"
}
if ([string]::IsNullOrWhiteSpace($RunName)) {
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $RunName = "${stamp}_gdbmeter_${Database}_${Oracle}_${DurationSeconds}s_restart"
}

$runDir = Join-Path $OutputRoot $RunName
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$logDir = Join-Path $root "logs"
$commandArgs = @("-jar", $jarPath, "-db", $Database, "-o", $Oracle)
if (-not $NoVerboseQueries) {
    $commandArgs += "-v"
}

$overallStart = Get-Date
$deadline = $overallStart.AddSeconds($DurationSeconds)
$attempts = @()
$attemptIndex = 0

while ((Get-Date) -lt $deadline) {
    $attemptIndex += 1
    $attemptDir = Join-Path $runDir ("attempt_{0:D3}" -f $attemptIndex)
    New-Item -ItemType Directory -Force -Path $attemptDir | Out-Null

    $stdoutPath = Join-Path $attemptDir "stdout.log"
    $stderrPath = Join-Path $attemptDir "stderr.log"

    $logsBefore = @{}
    if (Test-Path -LiteralPath $logDir) {
        foreach ($file in Get-ChildItem -LiteralPath $logDir -File) {
            $logsBefore[$file.FullName] = $true
        }
    }

    $attemptStart = Get-Date
    $remainingMs = [int][Math]::Max(1000, [Math]::Ceiling(($deadline - $attemptStart).TotalMilliseconds))

    $process = Start-Process `
        -FilePath "java" `
        -ArgumentList $commandArgs `
        -WorkingDirectory $root `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $timedOut = -not $process.WaitForExit($remainingMs)
    if ($timedOut) {
        try {
            $process.Kill()
        } catch {
            Write-Warning "Failed to kill process $($process.Id): $_"
        }
        $process.WaitForExit()
    }

    $attemptEnd = Get-Date
    $newLogs = Get-NewLogFiles -LogDir $logDir -Before $logsBefore
    $logPaths = @($newLogs | ForEach-Object { $_.FullName })

    $stdoutLines = Get-LineCount -Path $stdoutPath
    $stderrLines = Get-LineCount -Path $stderrPath
    $toolLogLines = 0
    $createLines = 0
    $matchLines = 0
    $queryLines = 0
    $finishedIterations = 0
    $errorLines = 0

    $createLines += Get-MatchCount -Path $stdoutPath -Pattern "GlobalState - CREATE\b"
    $matchLines += Get-MatchCount -Path $stdoutPath -Pattern "GlobalState - MATCH\b"
    $queryLines += Get-MatchCount -Path $stdoutPath -Pattern "GlobalState - (CREATE|MATCH|SHOW|DROP|CALL|MERGE|UNWIND|WITH|RETURN|OPTIONAL)\b"
    $finishedIterations += Get-MatchCount -Path $stdoutPath -Pattern "Finished iteration"
    $errorLines += Get-MatchCount -Path $stdoutPath -Pattern "(?i)(AssertionError|Exception|Error|BUG|failed|failure)"

    foreach ($logPath in $logPaths) {
        $toolLogLines += Get-LineCount -Path $logPath
        $createLines += Get-MatchCount -Path $logPath -Pattern "^(CREATE|CREATE\s+RANGE|CREATE\s+TEXT)\b"
        $matchLines += Get-MatchCount -Path $logPath -Pattern "^MATCH\b"
        $queryLines += Get-MatchCount -Path $logPath -Pattern "^(CREATE|MATCH|SHOW|DROP|CALL|MERGE|UNWIND|WITH|RETURN|OPTIONAL)\b"
        $finishedIterations += Get-MatchCount -Path $logPath -Pattern "Finished iteration"
        $errorLines += Get-MatchCount -Path $logPath -Pattern "(?i)(AssertionError|Exception|Error|BUG|failed|failure)"
    }
    $errorLines += Get-MatchCount -Path $stderrPath -Pattern "(?i)(AssertionError|Exception|Error|BUG|failed|failure)"

    $attempt = [ordered]@{
        attempt = $attemptIndex
        startTime = $attemptStart.ToString("o")
        endTime = $attemptEnd.ToString("o")
        durationSeconds = [Math]::Round(($attemptEnd - $attemptStart).TotalSeconds, 3)
        timedOut = $timedOut
        exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        stdout = $stdoutPath
        stderr = $stderrPath
        gdbmeterLogs = $logPaths
        stdoutLines = $stdoutLines
        stderrLines = $stderrLines
        toolLogLines = $toolLogLines
        queryLines = $queryLines
        createLines = $createLines
        matchLines = $matchLines
        finishedIterations = $finishedIterations
        errorLines = $errorLines
    }
    $attempts += [pscustomobject]$attempt

    $attemptJson = Join-Path $attemptDir "attempt_summary.json"
    [pscustomobject]$attempt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $attemptJson -Encoding UTF8

    if (-not $timedOut -and ((Get-Date) -lt $deadline) -and $RestartDelaySeconds -gt 0) {
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}

$overallEnd = Get-Date
$totalStdoutLines = ($attempts | Measure-Object -Property stdoutLines -Sum).Sum
$totalStderrLines = ($attempts | Measure-Object -Property stderrLines -Sum).Sum
$totalToolLogLines = ($attempts | Measure-Object -Property toolLogLines -Sum).Sum
$totalQueryLines = ($attempts | Measure-Object -Property queryLines -Sum).Sum
$totalCreateLines = ($attempts | Measure-Object -Property createLines -Sum).Sum
$totalMatchLines = ($attempts | Measure-Object -Property matchLines -Sum).Sum
$totalFinishedIterations = ($attempts | Measure-Object -Property finishedIterations -Sum).Sum
$totalErrorLines = ($attempts | Measure-Object -Property errorLines -Sum).Sum
$crashedAttempts = @($attempts | Where-Object { -not $_.timedOut -and $_.exitCode -ne 0 }).Count
$budgetStoppedAttempts = @($attempts | Where-Object { $_.timedOut }).Count

$summary = [ordered]@{
    tool = "GDBMeter"
    database = $Database
    oracle = $Oracle
    command = "java $($commandArgs -join ' ')"
    workingDirectory = $root
    runDirectory = $runDir
    requestedDurationSeconds = $DurationSeconds
    actualDurationSeconds = [Math]::Round(($overallEnd - $overallStart).TotalSeconds, 3)
    startTime = $overallStart.ToString("o")
    endTime = $overallEnd.ToString("o")
    attempts = $attempts
    totals = [ordered]@{
        attemptCount = $attempts.Count
        crashedAttempts = $crashedAttempts
        budgetStoppedAttempts = $budgetStoppedAttempts
        stdoutLines = $totalStdoutLines
        stderrLines = $totalStderrLines
        toolLogLines = $totalToolLogLines
        queryLines = $totalQueryLines
        createLines = $totalCreateLines
        matchLines = $totalMatchLines
        finishedIterations = $totalFinishedIterations
        errorLines = $totalErrorLines
    }
}

$summaryPath = Join-Path $runDir "summary.json"
[pscustomobject]$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "GDBMeter restart run complete."
Write-Host "Run directory: $runDir"
Write-Host "Summary: $summaryPath"
Write-Host "Attempts: $($attempts.Count)"
Write-Host "Crashed attempts: $crashedAttempts"
Write-Host "Budget-stopped attempts: $budgetStoppedAttempts"
Write-Host "Query lines: $totalQueryLines"
Write-Host "Error lines: $totalErrorLines"
