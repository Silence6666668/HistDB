param(
    [string]$RunName = "",
    [string]$Image = $(if ($env:NEO4J_IMAGE) { $env:NEO4J_IMAGE } else { "neo4j:2026.03.1" }),
    [int]$HttpPort = 7512,
    [int]$BoltPort = 7742,
    [int]$CoveragePort = 36382,
    [int]$BudgetSeconds = 86400,
    [int]$SnapshotIntervalSeconds = 300,
    [ValidateSet("empty_result", "non_empty_result", "partition", "path", "node", "edge")]
    [string]$Oracle = "path",
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
    param([string]$Path, [object]$Data)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Data | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-PowerShellExe {
    $pwsh = Get-Command "pwsh" -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) {
        return $pwsh.Source
    }
    $powershell = Get-Command "powershell" -ErrorAction SilentlyContinue
    if ($null -ne $powershell) {
        return $powershell.Source
    }
    throw "Neither pwsh nor powershell was found"
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
$gameraRoot = Resolve-RepoPath "external_tools\Gamera\CypherChecker"
$jacocoCli = Join-Path (Resolve-RepoPath "external_tools\jacoco") "jacococli.jar"

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "cov_gamera_neo4j_2026_03_1_dinkel_scope_24h_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

$safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
$containerName = "neo4j_cov_$safeRunName"
$runDir = Join-Path $coverageRoot $RunName
$snapshotDir = Join-Path $runDir "jacoco\snapshots"
$logsDir = Join-Path $runDir "logs"
$snapshotManifestPath = Join-Path $runDir "jacoco\snapshot_manifest.jsonl"
$statusPath = Join-Path $runDir "gamera_neo4j_jacoco_status.json"
$summaryPath = Join-Path $runDir "gamera_neo4j_jacoco_summary.json"
New-Item -ItemType Directory -Force -Path $runDir, $snapshotDir, $logsDir | Out-Null

$oldUri = $env:GAMERA_NEO4J_URI
$oldUser = $env:GAMERA_NEO4J_USER
$oldPassword = $env:GAMERA_NEO4J_PASSWORD
$startedAt = Get-Date
$samplerProcess = $null
$samplerExitCode = $null
$timeseriesCsv = ""
$timeseriesError = ""

Write-JsonFile -Path (Join-Path $runDir "run_meta.json") -Data ([ordered]@{
    runName = $RunName
    tool = "Gamera"
    mode = "external Neo4j Docker Dinkel-scope JaCoCo"
    image = $Image
    containerName = $containerName
    oracle = $Oracle
    startedAt = $startedAt.ToString("o")
    budgetSeconds = $BudgetSeconds
    snapshotIntervalSeconds = $SnapshotIntervalSeconds
    httpPort = $HttpPort
    boltPort = $BoltPort
    coveragePort = $CoveragePort
})

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

    if ($SnapshotIntervalSeconds -gt 0) {
        $samplerStdout = Join-Path $logsDir "snapshot_sampler_stdout.log"
        $samplerStderr = Join-Path $logsDir "snapshot_sampler_stderr.log"
        $samplerArgs = @(
            "-NoLogo", "-NoProfile",
            "-File", (Join-Path $PSScriptRoot "sample_jacoco_snapshots.ps1"),
            "-RunName", $RunName,
            "-CoveragePort", "$CoveragePort",
            "-DurationSeconds", "$BudgetSeconds",
            "-IntervalSeconds", "$SnapshotIntervalSeconds",
            "-ManifestPath", $snapshotManifestPath,
            "-StatusPath", (Join-Path $runDir "jacoco\snapshot_sampler_status.json")
        )
        $startProcessParams = @{
            FilePath = (Get-PowerShellExe)
            ArgumentList = $samplerArgs
            WorkingDirectory = $repoRoot
            RedirectStandardOutput = $samplerStdout
            RedirectStandardError = $samplerStderr
            PassThru = $true
        }
        if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
            $startProcessParams.WindowStyle = "Hidden"
        }
        $samplerProcess = Start-Process @startProcessParams
    }

    $env:GAMERA_NEO4J_URI = "bolt://localhost:$BoltPort"
    $env:GAMERA_NEO4J_USER = "neo4j"
    $env:GAMERA_NEO4J_PASSWORD = "password"

    $gameraRunName = "${RunName}_gamera"
    $gameraOutputRoot = Join-Path $gameraRoot "baseline_runs"
    & (Join-Path $PSScriptRoot "run_gamera_cypher_with_restarts.ps1") `
        -DurationSeconds $BudgetSeconds `
        -RunName $gameraRunName `
        -Database neo4j `
        -Oracle $Oracle `
        -GameraRoot $gameraRoot `
        -OutputRoot $gameraOutputRoot `
        -VerboseQueries

    if ($null -ne $samplerProcess) {
        $samplerProcess.Refresh()
        if (-not $samplerProcess.HasExited) {
            Stop-Process -Id $samplerProcess.Id -Force
            $samplerProcess.WaitForExit()
        }
        $samplerExitCode = $samplerProcess.ExitCode
    }

    $finalExec = Join-Path $runDir "jacoco\neo4j.exec"
    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $finalExec -IgnoreFailure | Out-Null

    try {
        & (Join-Path $PSScriptRoot "collect_neo4j_jacoco_timeseries.ps1") `
            -RunName $RunName `
            -ContainerName $containerName `
            -ReportDirName "jacoco_timeseries_dinkel_scope" `
            -IncludeFinalExec
        $timeseriesCsv = Join-Path $runDir "jacoco_timeseries_dinkel_scope\coverage_timeseries.csv"
    } catch {
        $timeseriesError = $_.Exception.Message
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
        tool = "Gamera"
        database = "neo4j"
        oracle = $Oracle
        status = "completed"
        startedAt = $startedAt.ToString("o")
        endedAt = $endedAt.ToString("o")
        durationSeconds = [math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
        gameraRunName = $gameraRunName
        gameraSummary = Join-Path $gameraOutputRoot (Join-Path $gameraRunName "summary.json")
        coverageCsv = Join-Path $runDir "jacoco_report\jacoco.csv"
        snapshotManifest = $snapshotManifestPath
        snapshotIntervalSeconds = $SnapshotIntervalSeconds
        samplerExitCode = $samplerExitCode
        timeseriesCsv = $timeseriesCsv
        timeseriesError = $timeseriesError
    }
    Write-JsonFile -Path $summaryPath -Data $summary
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        tool = "Gamera"
        status = "completed"
        endedAt = $endedAt.ToString("o")
        summaryPath = $summaryPath
    })
} catch {
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        tool = "Gamera"
        status = "failed"
        failedAt = (Get-Date).ToString("o")
        error = $_.Exception.Message
    })
    throw
} finally {
    if ($null -ne $samplerProcess) {
        $samplerProcess.Refresh()
        if (-not $samplerProcess.HasExited) {
            Stop-Process -Id $samplerProcess.Id -Force
            $samplerProcess.WaitForExit()
        }
    }
    $env:GAMERA_NEO4J_URI = $oldUri
    $env:GAMERA_NEO4J_USER = $oldUser
    $env:GAMERA_NEO4J_PASSWORD = $oldPassword
}
