param(
    [string]$RunName = "",
    [string]$Image = $(if ($env:NEO4J_IMAGE) { $env:NEO4J_IMAGE } else { "neo4j:2026.03.1" }),
    [int]$HttpPort = 7492,
    [int]$BoltPort = 7720,
    [int]$CoveragePort = 36350,
    [int]$BudgetSeconds = 86400,
    [int]$SnapshotIntervalSeconds = 300,
    [string]$Oracle = "RANDOM_CRASH",
    [int]$NumThreads = 1,
    [int]$NumQueries = 100000000,
    [switch]$RemoveExistingContainer,
    [switch]$StopContainerAtEnd,
    [switch]$RemoveContainerAtEnd
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
    param(
        [string]$Path,
        [object]$Data
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Data | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-JaCoCoDump {
    param(
        [string]$CliPath,
        [int]$Port,
        [string]$DestFile,
        [switch]$Reset,
        [switch]$IgnoreFailure
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestFile) | Out-Null
    $args = @("dump", "--address", "localhost", "--port", "$Port", "--destfile", $DestFile)
    if ($Reset) {
        $args += "--reset"
    }
    & java -jar $CliPath @args
    if ($LASTEXITCODE -ne 0) {
        if ($IgnoreFailure) {
            return $false
        }
        throw "JaCoCo dump failed: $DestFile"
    }
    return $true
}

$repoRoot = Resolve-RepoPath "."
$coverageRoot = Resolve-RepoPath "coverage_runs"
$gdsmithDir = Resolve-RepoPath "external_tools\GDsmith"
$jacocoCli = Join-Path (Resolve-RepoPath "external_tools\jacoco") "jacococli.jar"

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "cov_gdsmith_neo4j_2026_03_1_wholejvm_24h_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

$safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
$containerName = "neo4j_cov_$safeRunName"
$runDir = Join-Path $coverageRoot $RunName
$snapshotDir = Join-Path $runDir "jacoco\snapshots"
$logsDir = Join-Path $runDir "logs"
$statusPath = Join-Path $runDir "gdsmith_neo4j_jacoco_status.json"
$summaryPath = Join-Path $runDir "gdsmith_neo4j_jacoco_summary.json"
$snapshotManifestPath = Join-Path $runDir "jacoco\snapshot_manifest.jsonl"
New-Item -ItemType Directory -Force -Path $runDir, $snapshotDir, $logsDir | Out-Null

$startedAt = Get-Date
Write-JsonFile -Path (Join-Path $runDir "run_meta.json") -Data ([ordered]@{
    runName = $RunName
    tool = "GDsmith"
    mode = "external Neo4j Docker whole-JVM JaCoCo"
    image = $Image
    containerName = $containerName
    startedAt = $startedAt.ToString("o")
    budgetSeconds = $BudgetSeconds
    snapshotIntervalSeconds = $SnapshotIntervalSeconds
    httpPort = $HttpPort
    boltPort = $BoltPort
    coveragePort = $CoveragePort
    oracle = $Oracle
    gdsmithDir = $gdsmithDir
})

$process = $null
try {
    $startArgs = @{
        RunName = $RunName
        ContainerName = $containerName
        Image = $Image
        HttpPort = $HttpPort
        BoltPort = $BoltPort
        CoveragePort = $CoveragePort
        Auth = "neo4j/password"
        EnableApoc = $false
    }
    if ($RemoveExistingContainer) {
        $startArgs.RemoveExisting = $true
    }
    & (Join-Path $PSScriptRoot "start_neo4j_jacoco_container.ps1") @startArgs

    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile (Join-Path $runDir "jacoco\warmup_reset.exec") -Reset | Out-Null

    $gdsmithRunName = "${RunName}_gdsmith"
    $gdsmithOutputRoot = Join-Path $gdsmithDir "baseline_runs"
    $stdoutPath = Join-Path $logsDir "gdsmith_wrapper_stdout.log"
    $stderrPath = Join-Path $logsDir "gdsmith_wrapper_stderr.log"
    $childArgs = @(
        "-NoLogo", "-NoProfile",
        "-File", (Join-Path $PSScriptRoot "run_gdsmith_with_restarts.ps1"),
        "-DurationSeconds", "$BudgetSeconds",
        "-GdsmithRoot", $gdsmithDir,
        "-OutputRoot", $gdsmithOutputRoot,
        "-RunName", $gdsmithRunName,
        "-Dbms", "neo4j",
        "-HostName", "localhost",
        "-Port", "$BoltPort",
        "-Username", "neo4j",
        "-Password", "password",
        "-Oracle", $Oracle,
        "-NumThreads", "$NumThreads",
        "-NumQueries", "$NumQueries"
    )

    $process = Start-Process -FilePath "pwsh" `
        -ArgumentList $childArgs `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $deadline = $startedAt.AddSeconds($BudgetSeconds)
    $nextSnapshot = (Get-Date).AddSeconds($SnapshotIntervalSeconds)
    $snapshots = @()
    $stopReason = "budget_elapsed"

    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            $stopReason = "gdsmith_wrapper_exited"
            break
        }
        if ($SnapshotIntervalSeconds -gt 0 -and (Get-Date) -ge $nextSnapshot) {
            $elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
            $label = "t_{0:D6}s" -f $elapsed
            $snapshotExec = Join-Path $snapshotDir "$label.exec"
            $ok = Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $snapshotExec -IgnoreFailure
            if ($ok) {
                $snapshot = [ordered]@{
                    label = $label
                    elapsedSeconds = $elapsed
                    execFile = $snapshotExec
                    createdAt = (Get-Date).ToString("o")
                }
                $snapshots += $snapshot
                ([pscustomobject]$snapshot | ConvertTo-Json -Compress) | Add-Content -LiteralPath $snapshotManifestPath -Encoding UTF8
            }
            $nextSnapshot = (Get-Date).AddSeconds($SnapshotIntervalSeconds)
        }
        Write-JsonFile -Path $statusPath -Data ([ordered]@{
            runName = $RunName
            status = "running"
            gdsmithWrapperPid = $process.Id
            elapsedSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
            budgetSeconds = $BudgetSeconds
            snapshotCount = $snapshots.Count
            updatedAt = (Get-Date).ToString("o")
            coveragePort = $CoveragePort
            boltPort = $BoltPort
        })
        Start-Sleep -Seconds 15
    }

    $finalExec = Join-Path $runDir "jacoco\neo4j.exec"
    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $finalExec -IgnoreFailure | Out-Null
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }

    $collectArgs = @{
        RunName = $RunName
        ContainerName = $containerName
        CoveragePort = $CoveragePort
    }
    if ($RemoveContainerAtEnd) {
        $collectArgs.RemoveContainer = $true
    } elseif ($StopContainerAtEnd -or (-not $RemoveContainerAtEnd)) {
        $collectArgs.StopContainer = $true
    }
    & (Join-Path $PSScriptRoot "collect_neo4j_jacoco_report.ps1") @collectArgs

    $endedAt = Get-Date
    $summary = [ordered]@{
        runName = $RunName
        status = "completed"
        stopReason = $stopReason
        startedAt = $startedAt.ToString("o")
        endedAt = $endedAt.ToString("o")
        durationSeconds = [math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
        gdsmithWrapperExitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        gdsmithRunName = $gdsmithRunName
        gdsmithOutputRoot = $gdsmithOutputRoot
        coverageCsv = Join-Path $runDir "jacoco_report\jacoco.csv"
        snapshots = $snapshots
    }
    Write-JsonFile -Path $summaryPath -Data $summary
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        status = "completed"
        summaryPath = $summaryPath
        endedAt = $endedAt.ToString("o")
    })
} catch {
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        status = "failed"
        failedAt = (Get-Date).ToString("o")
        error = $_.Exception.Message
    })
    throw
}
