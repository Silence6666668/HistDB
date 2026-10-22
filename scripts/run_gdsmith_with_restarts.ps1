param(
    [int]$DurationSeconds = 600,
    [string]$GdsmithRoot = "",
    [string]$OutputRoot = "",
    [string]$RunName = "",
    [string]$Dbms = "neo4j",
    [string]$HostName = "localhost",
    [int]$Port = 7687,
    [string]$Username = "neo4j",
    [string]$PasswordEnv = "",
    [string]$Password = "",
    [string]$Oracle = "RANDOM_CRASH",
    [int]$RandomSeed = -1,
    [int]$NumThreads = 1,
    [int]$NumQueries = 1000,
    [int]$RestartDelaySeconds = 2
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

function Start-ProcessWithRedirectedLogs {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    function ConvertTo-WindowsArgument {
        param([string]$Argument)
        if ([string]::IsNullOrEmpty($Argument)) {
            return '""'
        }
        if ($Argument -notmatch '[\s"]') {
            return $Argument
        }
        return '"' + ($Argument -replace '\\', '\\' -replace '"', '\"') + '"'
    }

    $argumentString = (($Arguments | ForEach-Object { ConvertTo-WindowsArgument -Argument $_ }) -join " ")
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $argumentString `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru

    return [pscustomobject]@{
        Process = $process
    }
}

function Close-RedirectedProcess {
    param($Handle)
    # Start-Process writes directly to files; this hook is kept for symmetry
    # with the process lifecycle and future capture implementations.
}

if ([string]::IsNullOrWhiteSpace($GdsmithRoot)) {
    $GdsmithRoot = Join-Path $PSScriptRoot "..\external_tools\GDsmith"
}

$root = (Resolve-Path -LiteralPath $GdsmithRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "target\classes"))) {
    throw "GDsmith classes not found. Build GDsmith before running: $root"
}

if ([string]::IsNullOrWhiteSpace($Password) -and -not [string]::IsNullOrWhiteSpace($PasswordEnv)) {
    $Password = [Environment]::GetEnvironmentVariable($PasswordEnv)
}
if ($null -eq $Password) {
    $Password = ""
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $root "baseline_runs"
}
if ([string]::IsNullOrWhiteSpace($RunName)) {
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $RunName = "${stamp}_gdsmith_${Dbms}_${HostName}_${Port}_${DurationSeconds}s_restart"
}

$runDir = Join-Path $OutputRoot $RunName
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$classpathSeparator = [System.IO.Path]::PathSeparator
$classpath = "target/classes${classpathSeparator}target/dependency/*"
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

    $arguments = @(
        "-cp", $classpath,
        "org.example.gdsmith.Main",
        "--num-threads", "$NumThreads",
        "--num-queries", "$NumQueries",
        "--timeout-seconds", "-1",
        "--print-progress-information", "false"
    )
    if ($RandomSeed -ne -1) {
        $arguments += @("--random-seed", "$RandomSeed")
    }
    $arguments += @(
        $Dbms,
        "--host", $HostName,
        "--port", "$Port",
        "--username", $Username,
        "--password", $Password,
        "--oracle", $Oracle
    )

    $attemptStart = Get-Date
    $remainingMs = [int][Math]::Max(1000, [Math]::Ceiling(($deadline - $attemptStart).TotalMilliseconds))

    $handle = Start-ProcessWithRedirectedLogs `
        -FilePath "java" `
        -Arguments $arguments `
        -WorkingDirectory $root `
        -StdoutPath $stdoutPath `
        -StderrPath $stderrPath

    $process = $handle.Process
    $timedOut = -not $process.WaitForExit($remainingMs)
    if ($timedOut) {
        try {
            $process.Kill()
        } catch {
            Write-Warning "Failed to kill process $($process.Id): $_"
        }
        $process.WaitForExit()
    }
    Close-RedirectedProcess -Handle $handle

    $attemptEnd = Get-Date
    $stdoutLines = Get-LineCount -Path $stdoutPath
    $stderrLines = Get-LineCount -Path $stderrPath
    $queryLines = 0
    $createLines = 0
    $matchLines = 0
    $errorLines = 0
    $databaseRuns = 0

    foreach ($path in @($stdoutPath, $stderrPath)) {
        $queryLines += Get-MatchCount -Path $path -Pattern "^\s*(CREATE|MATCH|MERGE|WITH|RETURN|CALL|DROP|UNWIND|OPTIONAL)\b"
        $createLines += Get-MatchCount -Path $path -Pattern "^\s*CREATE\b"
        $matchLines += Get-MatchCount -Path $path -Pattern "^\s*MATCH\b"
        $errorLines += Get-MatchCount -Path $path -Pattern "(?i)(AssertionError|Exception|Error|BUG|failed|failure|RuntimeException)"
        $databaseRuns += Get-MatchCount -Path $path -Pattern "(?i)new global state|Database:"
    }

    $attempt = [ordered]@{
        attempt = $attemptIndex
        startTime = $attemptStart.ToString("o")
        endTime = $attemptEnd.ToString("o")
        durationSeconds = [Math]::Round(($attemptEnd - $attemptStart).TotalSeconds, 3)
        timedOut = $timedOut
        exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        stdout = $stdoutPath
        stderr = $stderrPath
        stdoutLines = $stdoutLines
        stderrLines = $stderrLines
        queryLines = $queryLines
        createLines = $createLines
        matchLines = $matchLines
        databaseRunMarkers = $databaseRuns
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
$totalQueryLines = ($attempts | Measure-Object -Property queryLines -Sum).Sum
$totalCreateLines = ($attempts | Measure-Object -Property createLines -Sum).Sum
$totalMatchLines = ($attempts | Measure-Object -Property matchLines -Sum).Sum
$totalDatabaseRuns = ($attempts | Measure-Object -Property databaseRunMarkers -Sum).Sum
$totalErrorLines = ($attempts | Measure-Object -Property errorLines -Sum).Sum
$crashedAttempts = @($attempts | Where-Object { -not $_.timedOut -and $_.exitCode -ne 0 }).Count
$budgetStoppedAttempts = @($attempts | Where-Object { $_.timedOut }).Count

$redactedCommand = @(
    "java", "-cp", $classpath, "org.example.gdsmith.Main",
    "--num-threads", "$NumThreads",
    "--num-queries", "$NumQueries",
    "--timeout-seconds", "-1",
    "--print-progress-information", "false"
)
if ($RandomSeed -ne -1) {
    $redactedCommand += @("--random-seed", "$RandomSeed")
}
$redactedCommand += @(
    $Dbms, "--host", $HostName, "--port", "$Port",
    "--username", $Username, "--password", "<redacted>", "--oracle", $Oracle
)

$summary = [ordered]@{
    tool = "GDsmith"
    dbms = $Dbms
    host = $HostName
    port = $Port
    username = $Username
    oracle = $Oracle
    command = ($redactedCommand -join " ")
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
        queryLines = $totalQueryLines
        createLines = $totalCreateLines
        matchLines = $totalMatchLines
        databaseRunMarkers = $totalDatabaseRuns
        errorLines = $totalErrorLines
    }
}

$summaryPath = Join-Path $runDir "summary.json"
[pscustomobject]$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "GDsmith restart run complete."
Write-Host "Run directory: $runDir"
Write-Host "Summary: $summaryPath"
Write-Host "Attempts: $($attempts.Count)"
Write-Host "Crashed attempts: $crashedAttempts"
Write-Host "Budget-stopped attempts: $budgetStoppedAttempts"
Write-Host "Query lines: $totalQueryLines"
Write-Host "Error lines: $totalErrorLines"
