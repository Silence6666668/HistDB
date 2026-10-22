param(
    [string]$RunName = "",
    [string]$ContainerName = "",
    [string]$Image = $(if ($env:NEO4J_IMAGE) { $env:NEO4J_IMAGE } else { "neo4j:2026.03.1" }),
    [int]$HttpPort = 7490,
    [int]$BoltPort = 7710,
    [int]$CoveragePort = 36310,
    [int]$BudgetSeconds = 86400,
    [int]$SnapshotIntervalSeconds = 1800,
    [ValidateSet("0", "1", "2", "none", "equivalence_transformation", "predicate_partitioning")]
    [string]$Strategy = "2",
    [string]$Auth = "none",
    [bool]$EnableApoc = $true,
    [bool]$StartDockerDesktop = $true,
    [switch]$RemoveExistingContainer,
    [switch]$StopContainerAtEnd,
    [switch]$RemoveContainerAtEnd
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:IsWindowsPlatform = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT

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
    $Data | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Test-DockerEngine {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker info *> $null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $oldPreference
    return $ok
}

function Ensure-DockerEngine {
    param([bool]$MayStartDockerDesktop)
    if (Test-DockerEngine) {
        return
    }
    if (-not $MayStartDockerDesktop) {
        throw "Docker engine is not reachable."
    }

    $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path -LiteralPath $desktop) {
        Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    }

    $deadline = (Get-Date).AddMinutes(8)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) {
            return
        }
        Start-Sleep -Seconds 10
    }
    throw "Docker engine did not become ready after starting Docker Desktop."
}

function Invoke-JaCoCoDump {
    param(
        [string]$CliPath,
        [int]$Port,
        [string]$DestFile,
        [switch]$Reset
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestFile) | Out-Null
    $args = @("dump", "--address", "localhost", "--port", "$Port", "--destfile", $DestFile)
    if ($Reset) {
        $args += "--reset"
    }
    & java -jar $CliPath @args
    if ($LASTEXITCODE -ne 0) {
        throw "JaCoCo dump failed: $DestFile"
    }
}

function Get-StrategyName {
    param([string]$Value)
    switch ($Value) {
        "0" { return "none" }
        "none" { return "none" }
        "1" { return "equivalence_transformation" }
        "equivalence_transformation" { return "equivalence_transformation" }
        default { return "predicate_partitioning" }
    }
}

$repoRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath((Join-Path $PSScriptRoot ".."))
$dinkelDir = Resolve-RepoPath "external_tools\dinkel"
$coverageRoot = Resolve-RepoPath "coverage_runs"
$jacocoDir = Resolve-RepoPath "external_tools\jacoco"
$paperDir = Resolve-RepoPath "paper"

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "cov_dinkel_neo4j_2026_04_24h_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
if ([string]::IsNullOrWhiteSpace($ContainerName)) {
    $safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
    $ContainerName = "neo4j_cov_$safeRunName"
}
if (-not $StopContainerAtEnd -and -not $RemoveContainerAtEnd) {
    $StopContainerAtEnd = $true
}

$dinkelExe = Join-Path $dinkelDir $(if ($script:IsWindowsPlatform) { "dinkel.exe" } else { "dinkel" })
$jacocoCli = Join-Path $jacocoDir "jacococli.jar"
if (-not (Test-Path -LiteralPath $dinkelExe)) {
    throw "Dinkel binary not found: $dinkelExe"
}
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

$strategyName = Get-StrategyName $Strategy
$strategyNumber = switch ($strategyName) {
    "none" { "0" }
    "equivalence_transformation" { "1" }
    default { "2" }
}

$baselineDir = Join-Path (Join-Path $dinkelDir "baseline_runs") $RunName
$bugreportsDir = Join-Path $baselineDir "bugreports"
$coverageDir = Join-Path $coverageRoot $RunName
$snapshotDir = Join-Path $coverageDir "jacoco\snapshots"
$statusPath = Join-Path $baselineDir "status.json"
$summaryPath = Join-Path $baselineDir "summary.json"
$dinkelLogPath = Join-Path $baselineDir "dinkel.log"
$wrapperLogPath = Join-Path $baselineDir "wrapper_events.jsonl"

New-Item -ItemType Directory -Force -Path $bugreportsDir, $coverageDir, $snapshotDir | Out-Null

$startedAt = Get-Date
$meta = [ordered]@{
    runName = $RunName
    startedAt = $startedAt.ToString("o")
    repoRoot = $repoRoot
    dinkelDir = $dinkelDir
    baselineDir = $baselineDir
    coverageDir = $coverageDir
    image = $Image
    containerName = $ContainerName
    httpPort = $HttpPort
    boltPort = $BoltPort
    coveragePort = $CoveragePort
    budgetSeconds = $BudgetSeconds
    snapshotIntervalSeconds = $SnapshotIntervalSeconds
    strategy = $strategyName
    strategyNumber = $strategyNumber
    auth = $Auth
    enableApoc = $EnableApoc
}
Write-JsonFile -Path (Join-Path $baselineDir "run_meta.json") -Data $meta

try {
    Ensure-DockerEngine -MayStartDockerDesktop $StartDockerDesktop

    $startArgs = @{
        RunName = $RunName
        ContainerName = $ContainerName
        Image = $Image
        HttpPort = $HttpPort
        BoltPort = $BoltPort
        CoveragePort = $CoveragePort
        Auth = $Auth
        EnableApoc = $EnableApoc
    }
    if ($RemoveExistingContainer) {
        $startArgs.RemoveExisting = $true
    }
    & (Join-Path $PSScriptRoot "start_neo4j_jacoco_container.ps1") @startArgs

    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile (Join-Path $coverageDir "jacoco\warmup_reset.exec") -Reset

    $relativeBugreports = "baseline_runs/$RunName/bugreports"
    $relativeLog = "baseline_runs/$RunName/dinkel.log"
    $dinkelArgs = @(
        "--db-host", "localhost",
        "--port", "$BoltPort",
        "--bugreports", $relativeBugreports,
        "--log-file", $relativeLog,
        "--silence-tty",
        "--timeout", "10",
        "fuzz", "neo4j", $strategyNumber,
        "--disable-keybinds"
    )

    function Start-DinkelAttempt {
        param([int]$Attempt)
        $attemptDir = Join-Path $baselineDir ("attempt_{0:D4}" -f $Attempt)
        New-Item -ItemType Directory -Force -Path $attemptDir | Out-Null
        $stdoutPath = Join-Path $attemptDir "stdout.log"
        $stderrPath = Join-Path $attemptDir "stderr.log"
        $startProcessParams = @{
            FilePath = $dinkelExe
            ArgumentList = $dinkelArgs
            WorkingDirectory = $dinkelDir
            RedirectStandardOutput = $stdoutPath
            RedirectStandardError = $stderrPath
            PassThru = $true
        }
        if ($script:IsWindowsPlatform) {
            $startProcessParams.WindowStyle = "Hidden"
        }
        $p = Start-Process @startProcessParams
        return [pscustomobject]@{
            Attempt = $Attempt
            Process = $p
            Stdout = $stdoutPath
            Stderr = $stderrPath
            StartedAt = (Get-Date)
        }
    }

    $deadline = $startedAt.AddSeconds($BudgetSeconds)
    $nextSnapshot = (Get-Date).AddSeconds($SnapshotIntervalSeconds)
    $snapshots = @()
    $stopReason = "budget_elapsed"
    $attempts = @()
    $attemptIndex = 1
    $restartCount = 0
    $lastDinkelExitCode = $null
    $processInfo = Start-DinkelAttempt -Attempt $attemptIndex
    $process = $processInfo.Process

    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            $process.Refresh()
            $lastDinkelExitCode = $process.ExitCode
            $attempt = [ordered]@{
                attempt = $attemptIndex
                startedAt = $processInfo.StartedAt.ToString("o")
                endedAt = (Get-Date).ToString("o")
                exitCode = $lastDinkelExitCode
                stdout = $processInfo.Stdout
                stderr = $processInfo.Stderr
            }
            $attempts += [pscustomobject]$attempt
            ([pscustomobject]([ordered]@{
                event = "dinkel_exited"
                at = (Get-Date).ToString("o")
                attempt = $attemptIndex
                exitCode = $lastDinkelExitCode
                restartCount = $restartCount
                stdout = $processInfo.Stdout
                stderr = $processInfo.Stderr
            }) | ConvertTo-Json -Compress) | Add-Content -LiteralPath $wrapperLogPath -Encoding UTF8

            if ((Get-Date) -ge $deadline) {
                break
            }
            $restartCount += 1
            Start-Sleep -Seconds 5
            $attemptIndex += 1
            $processInfo = Start-DinkelAttempt -Attempt $attemptIndex
            $process = $processInfo.Process
            continue
        }

        if ($SnapshotIntervalSeconds -gt 0 -and (Get-Date) -ge $nextSnapshot) {
            $elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
            $label = "t_{0:D6}s" -f $elapsed
            $snapshotExec = Join-Path $snapshotDir "$label.exec"
            try {
                Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $snapshotExec
                $snapshots += [ordered]@{
                    label = $label
                    elapsedSeconds = $elapsed
                    execFile = $snapshotExec
                    createdAt = (Get-Date).ToString("o")
                }
                ([pscustomobject]$snapshots[-1] | ConvertTo-Json -Compress) | Add-Content -LiteralPath $wrapperLogPath -Encoding UTF8
            } catch {
                ([pscustomobject]@{
                    event = "snapshot_failed"
                    at = (Get-Date).ToString("o")
                    error = $_.Exception.Message
                } | ConvertTo-Json -Compress) | Add-Content -LiteralPath $wrapperLogPath -Encoding UTF8
            }
            $nextSnapshot = (Get-Date).AddSeconds($SnapshotIntervalSeconds)
        }

        $status = [ordered]@{
            runName = $RunName
            status = "running"
            dinkelPid = $process.Id
            attempt = $attemptIndex
            restartCount = $restartCount
            lastDinkelExitCode = $lastDinkelExitCode
            elapsedSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
            budgetSeconds = $BudgetSeconds
            snapshotCount = $snapshots.Count
            updatedAt = (Get-Date).ToString("o")
        }
        Write-JsonFile -Path $statusPath -Data $status
        Start-Sleep -Seconds 15
    }

    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
        $attempts += [pscustomobject]([ordered]@{
            attempt = $attemptIndex
            startedAt = $processInfo.StartedAt.ToString("o")
            endedAt = (Get-Date).ToString("o")
            exitCode = $process.ExitCode
            stoppedByWrapper = $true
            stdout = $processInfo.Stdout
            stderr = $processInfo.Stderr
        })
    }

    $finalExec = Join-Path $coverageDir "jacoco\neo4j.exec"
    Invoke-JaCoCoDump -CliPath $jacocoCli -Port $CoveragePort -DestFile $finalExec

    $collectArgs = @{
        RunName = $RunName
        ContainerName = $ContainerName
        CoveragePort = $CoveragePort
    }
    if ($RemoveContainerAtEnd) {
        $collectArgs.RemoveContainer = $true
    } elseif ($StopContainerAtEnd) {
        $collectArgs.StopContainer = $true
    }
    & (Join-Path $PSScriptRoot "collect_neo4j_jacoco_report.ps1") @collectArgs

    $csvPath = Join-Path $coverageDir "jacoco_report\jacoco.csv"
    $paperCsv = Join-Path $paperDir ("dinkel_neo4j_24h_coverage_summary_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".csv")
    $paperMd = [System.IO.Path]::ChangeExtension($paperCsv, ".md")
    & python (Join-Path $PSScriptRoot "summarize_jacoco_csv.py") `
        --input "Dinkel-24h=$csvPath" `
        --output-csv $paperCsv `
        --output-md $paperMd

    $endedAt = Get-Date
    $ymlReports = @(Get-ChildItem -LiteralPath $bugreportsDir -Filter "*.yml" -File -ErrorAction SilentlyContinue).Count
    $mdReports = @(Get-ChildItem -LiteralPath $bugreportsDir -Filter "*.md" -File -ErrorAction SilentlyContinue).Count
    $summary = [ordered]@{
        runName = $RunName
        status = "completed"
        stopReason = $stopReason
        startedAt = $startedAt.ToString("o")
        endedAt = $endedAt.ToString("o")
        durationSeconds = [math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
        dinkelExitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        lastDinkelExitCode = $lastDinkelExitCode
        restartCount = $restartCount
        attempts = $attempts
        ymlBugreports = $ymlReports
        mdBugreports = $mdReports
        coverageCsv = $csvPath
        paperSummaryCsv = $paperCsv
        paperSummaryMd = $paperMd
        snapshots = $snapshots
    }
    Write-JsonFile -Path $summaryPath -Data $summary
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        status = "completed"
        stopReason = $stopReason
        endedAt = $endedAt.ToString("o")
        summaryPath = $summaryPath
        paperSummaryCsv = $paperCsv
        paperSummaryMd = $paperMd
    })
} catch {
    $failedAt = Get-Date
    Write-JsonFile -Path $statusPath -Data ([ordered]@{
        runName = $RunName
        status = "failed"
        failedAt = $failedAt.ToString("o")
        error = $_.Exception.Message
    })
    throw
}
