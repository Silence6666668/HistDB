param(
    [string]$RunName = "",
    [string]$Image = $(if ($env:NEO4J_IMAGE) { $env:NEO4J_IMAGE } else { "neo4j:2026.03.1" }),
    [int]$HttpPort = 7502,
    [int]$BoltPort = 7732,
    [int]$CoveragePort = 36372,
    [int]$BudgetSeconds = 86400,
    [int]$SnapshotIntervalSeconds = 300,
    [string]$Oracle = "partition",
    [int]$RestartDelaySeconds = 2,
    [int]$WrapperExitGraceSeconds = 60,
    [switch]$NoVerboseQueries,
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

function Copy-Neo4jRuntimeHome {
    param(
        [string]$ContainerName,
        [string]$Destination
    )
    $runtimeHome = Join-Path $Destination "runtime_home"
    $libDir = Join-Path $runtimeHome "lib"
    $pluginsDir = Join-Path $runtimeHome "plugins"
    New-Item -ItemType Directory -Force -Path $libDir, $pluginsDir | Out-Null

    docker cp "${ContainerName}:/var/lib/neo4j/lib/." $libDir | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy Neo4j runtime lib jars from $ContainerName"
    }

    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker cp "${ContainerName}:/var/lib/neo4j/plugins/." $pluginsDir *> $null
    $ErrorActionPreference = $oldPreference
    return $runtimeHome
}

$repoRoot = Resolve-RepoPath "."
$coverageRoot = Resolve-RepoPath "coverage_runs"
$gdbmeterDir = Resolve-RepoPath "external_tools\gdbmeter"
$jacocoCli = Join-Path (Resolve-RepoPath "external_tools\jacoco") "jacococli.jar"
$wrapperScript = Join-Path $PSScriptRoot "run_gdbmeter_with_restarts.ps1"

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "cov_gdbmeter_external_neo4j_2026_03_1_wholejvm_24h_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}
if (-not (Test-Path -LiteralPath $wrapperScript)) {
    throw "GDBMeter restart wrapper not found: $wrapperScript"
}
if (-not $StopContainerAtEnd -and -not $RemoveContainerAtEnd) {
    $StopContainerAtEnd = $true
}

$safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
$containerName = "neo4j_cov_$safeRunName"
$runDir = Join-Path $coverageRoot $RunName
$snapshotDir = Join-Path $runDir "jacoco\snapshots"
$logsDir = Join-Path $runDir "logs"
$statusPath = Join-Path $runDir "gdbmeter_external_neo4j_jacoco_status.json"
$summaryPath = Join-Path $runDir "gdbmeter_external_neo4j_jacoco_summary.json"
$snapshotManifestPath = Join-Path $runDir "jacoco\snapshot_manifest.jsonl"
New-Item -ItemType Directory -Force -Path $runDir, $snapshotDir, $logsDir | Out-Null

$startedAt = Get-Date
$gdbmeterRunName = "${RunName}_gdbmeter"
$gdbmeterOutputRoot = Join-Path $gdbmeterDir "baseline_runs"
$stdoutPath = Join-Path $logsDir "gdbmeter_wrapper_stdout.log"
$stderrPath = Join-Path $logsDir "gdbmeter_wrapper_stderr.log"
$externalUri = "bolt://localhost:$BoltPort"

Write-JsonFile -Path (Join-Path $runDir "run_meta.json") -Data ([ordered]@{
    runName = $RunName
    tool = "GDBMeter"
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
    gdbmeterDir = $gdbmeterDir
    gdbmeterExternalUri = $externalUri
})

$process = $null
$oldGdbmeterUri = $env:GDBMETER_NEO4J_URI
$oldGdbmeterUser = $env:GDBMETER_NEO4J_USER
$oldGdbmeterPassword = $env:GDBMETER_NEO4J_PASSWORD

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

    $env:GDBMETER_NEO4J_URI = $externalUri
    $env:GDBMETER_NEO4J_USER = "neo4j"
    $env:GDBMETER_NEO4J_PASSWORD = "password"

    $childArgs = @(
        "-NoLogo", "-NoProfile",
        "-File", $wrapperScript,
        "-DurationSeconds", "$BudgetSeconds",
        "-GdbmeterRoot", $gdbmeterDir,
        "-OutputRoot", $gdbmeterOutputRoot,
        "-RunName", $gdbmeterRunName,
        "-Database", "neo4j",
        "-Oracle", $Oracle,
        "-RestartDelaySeconds", "$RestartDelaySeconds"
    )
    if ($NoVerboseQueries) {
        $childArgs += "-NoVerboseQueries"
    }

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
            $stopReason = "gdbmeter_wrapper_exited"
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
            gdbmeterWrapperPid = $process.Id
            elapsedSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
            budgetSeconds = $BudgetSeconds
            snapshotCount = $snapshots.Count
            updatedAt = (Get-Date).ToString("o")
            coveragePort = $CoveragePort
            boltPort = $BoltPort
            gdbmeterExternalUri = $externalUri
        })
        Start-Sleep -Seconds 15
    }

    $finalExec = Join-Path $runDir "jacoco\neo4j.exec"
    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $finalExec -IgnoreFailure | Out-Null
    if (-not $process.HasExited) {
        $graceMs = [int][Math]::Max(1000, $WrapperExitGraceSeconds * 1000)
        if (-not $process.WaitForExit($graceMs)) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
    }

    $runtimeHome = Copy-Neo4jRuntimeHome -ContainerName $containerName -Destination $runDir

    if ($RemoveContainerAtEnd) {
        docker stop $containerName | Out-Null
        docker rm $containerName | Out-Null
    } elseif ($StopContainerAtEnd) {
        docker stop $containerName | Out-Null
    }

    & (Join-Path $PSScriptRoot "collect_neo4j_whole_jvm_jacoco_report.ps1") `
        -RunName $RunName `
        -RuntimeHome $runtimeHome `
        -ReportDirName "jacoco_whole_jvm_report_nohtml"

    $wrapperSummaryPath = Join-Path $gdbmeterOutputRoot (Join-Path $gdbmeterRunName "summary.json")
    $endedAt = Get-Date
    $summary = [ordered]@{
        runName = $RunName
        status = "completed"
        stopReason = $stopReason
        startedAt = $startedAt.ToString("o")
        endedAt = $endedAt.ToString("o")
        durationSeconds = [math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
        gdbmeterWrapperExitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        gdbmeterRunName = $gdbmeterRunName
        gdbmeterOutputRoot = $gdbmeterOutputRoot
        gdbmeterWrapperSummary = $wrapperSummaryPath
        wholeJvmSummary = Join-Path $runDir "jacoco_whole_jvm_report_nohtml\summary.json"
        finalExec = $finalExec
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
} finally {
    $env:GDBMETER_NEO4J_URI = $oldGdbmeterUri
    $env:GDBMETER_NEO4J_USER = $oldGdbmeterUser
    $env:GDBMETER_NEO4J_PASSWORD = $oldGdbmeterPassword
}
