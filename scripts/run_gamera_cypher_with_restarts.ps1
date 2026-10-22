param(
    [int]$DurationSeconds = 86400,
    [string]$RunName = "",
    [ValidateSet("neo4j", "memgraph", "redis")]
    [string]$Database = "neo4j",
    [ValidateSet("empty_result", "non_empty_result", "partition", "path", "node", "edge")]
    [string]$Oracle = "path",
    [string]$GameraRoot = "",
    [string]$OutputRoot = "",
    [int]$RestartDelaySeconds = 2,
    [switch]$VerboseQueries
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param([string]$RelativePath)
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        (Join-Path $PSScriptRoot "..\$RelativePath")
    )
}

function Write-JsonFile {
    param([string]$Path, [object]$Data)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Count-MatchingLines {
    param([string]$Path, [string]$Pattern)
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }
    return [int](@(Select-String -LiteralPath $Path -Pattern $Pattern -SimpleMatch -ErrorAction SilentlyContinue).Count)
}

if ([string]::IsNullOrWhiteSpace($GameraRoot)) {
    $GameraRoot = Resolve-RepoPath "external_tools\Gamera\CypherChecker"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $GameraRoot "baseline_runs"
}
if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "gamera_${Database}_${Oracle}_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}

$gameraRootPath = (Resolve-Path -LiteralPath $GameraRoot).Path
$jarPath = Join-Path $gameraRootPath "target\cypherchecker-1.0-SNAPSHOT.jar"
if (-not (Test-Path -LiteralPath $jarPath)) {
    throw "Gamera CypherChecker jar not found: $jarPath"
}

$runDir = Join-Path $OutputRoot $RunName
$summaryPath = Join-Path $runDir "summary.json"
$statusPath = Join-Path $runDir "status.json"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$startedAt = Get-Date
$deadline = $startedAt.AddSeconds($DurationSeconds)
$attempts = @()
$attemptIndex = 0
$stopReason = "budget_elapsed"

while ((Get-Date) -lt $deadline) {
    $attemptIndex += 1
    $attemptDir = Join-Path $runDir ("attempt_{0:D4}" -f $attemptIndex)
    New-Item -ItemType Directory -Force -Path $attemptDir | Out-Null
    $stdoutPath = Join-Path $attemptDir "stdout.log"
    $stderrPath = Join-Path $attemptDir "stderr.log"
    $attemptStart = Get-Date

    $args = @("-jar", $jarPath, $Database, "--oracle", $Oracle)
    if ($VerboseQueries) {
        $args += "--verbose"
    }

    $processParams = @{
        FilePath = "java"
        ArgumentList = $args
        WorkingDirectory = $gameraRootPath
        RedirectStandardOutput = $stdoutPath
        RedirectStandardError = $stderrPath
        PassThru = $true
    }
    if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
        $processParams.WindowStyle = "Hidden"
    }
    $process = Start-Process @processParams

    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Write-JsonFile -Path $statusPath -Data ([ordered]@{
            runName = $RunName
            tool = "Gamera"
            database = $Database
            oracle = $Oracle
            status = "running"
            attempt = $attemptIndex
            gameraPid = $process.Id
            elapsedSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
            budgetSeconds = $DurationSeconds
            updatedAt = (Get-Date).ToString("o")
        })
        Start-Sleep -Seconds 10
        $process.Refresh()
    }

    $timedOut = $false
    if (-not $process.HasExited) {
        $timedOut = $true
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    $attemptEnd = Get-Date
    $attempt = [ordered]@{
        attempt = $attemptIndex
        startTime = $attemptStart.ToString("o")
        endTime = $attemptEnd.ToString("o")
        durationSeconds = [math]::Round(($attemptEnd - $attemptStart).TotalSeconds, 3)
        timedOut = $timedOut
        exitCode = $process.ExitCode
        stdout = $stdoutPath
        stderr = $stderrPath
        queryLines = Count-MatchingLines -Path $stdoutPath -Pattern "INFO - "
        oracleChecks = Count-MatchingLines -Path $stdoutPath -Pattern "Check oracle "
        assertionLines = Count-MatchingLines -Path $stdoutPath -Pattern "java.lang.AssertionError"
        exceptionLines = Count-MatchingLines -Path $stdoutPath -Pattern "Exception"
    }
    $attempts += [pscustomobject]$attempt
    Write-JsonFile -Path (Join-Path $attemptDir "attempt_summary.json") -Data $attempt

    if ($timedOut) {
        break
    }
    if ($RestartDelaySeconds -gt 0 -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}

$endedAt = Get-Date
$totals = [ordered]@{
    attemptCount = $attempts.Count
    crashedAttempts = @($attempts | Where-Object { -not $_.timedOut -and $_.exitCode -ne 0 }).Count
    budgetStoppedAttempts = @($attempts | Where-Object { $_.timedOut }).Count
    queryLines = [int64](($attempts | Measure-Object -Property queryLines -Sum).Sum)
    oracleChecks = [int64](($attempts | Measure-Object -Property oracleChecks -Sum).Sum)
    assertionLines = [int64](($attempts | Measure-Object -Property assertionLines -Sum).Sum)
    exceptionLines = [int64](($attempts | Measure-Object -Property exceptionLines -Sum).Sum)
}
$summary = [ordered]@{
    tool = "Gamera"
    database = $Database
    oracle = $Oracle
    runName = $RunName
    command = "java -jar $jarPath $Database --oracle $Oracle"
    workingDirectory = $gameraRootPath
    runDirectory = $runDir
    requestedDurationSeconds = $DurationSeconds
    actualDurationSeconds = [math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
    startTime = $startedAt.ToString("o")
    endTime = $endedAt.ToString("o")
    stopReason = $stopReason
    attempts = $attempts
    totals = $totals
}
Write-JsonFile -Path $summaryPath -Data $summary
Write-JsonFile -Path $statusPath -Data ([ordered]@{
    runName = $RunName
    tool = "Gamera"
    database = $Database
    oracle = $Oracle
    status = "completed"
    endedAt = $endedAt.ToString("o")
    summaryPath = $summaryPath
    totals = $totals
})

Write-Host "Gamera restart run complete."
Write-Host "Summary: $summaryPath"
Write-Host "Attempts: $($totals.attemptCount)"
Write-Host "Query lines: $($totals.queryLines)"
