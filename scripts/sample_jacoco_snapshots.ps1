param(
    [Parameter(Mandatory = $true)]
    [string]$RunName,
    [int]$CoveragePort = 36300,
    [int]$DurationSeconds = 86400,
    [int]$IntervalSeconds = 300,
    [string]$CoverageRoot = "",
    [string]$JaCoCoDir = "",
    [string]$ManifestPath = "",
    [string]$StatusPath = ""
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

function Add-JsonLine {
    param(
        [string]$Path,
        [object]$Data
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    ([pscustomobject]$Data | ConvertTo-Json -Compress -Depth 10) |
        Add-Content -LiteralPath $Path -Encoding UTF8
}

if ($IntervalSeconds -le 0) {
    throw "IntervalSeconds must be positive"
}
if ($DurationSeconds -le 0) {
    throw "DurationSeconds must be positive"
}
if ([string]::IsNullOrWhiteSpace($CoverageRoot)) {
    $CoverageRoot = Resolve-RepoPath "coverage_runs"
}
if ([string]::IsNullOrWhiteSpace($JaCoCoDir)) {
    $JaCoCoDir = Resolve-RepoPath "external_tools\jacoco"
}

$coverageRootPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CoverageRoot)
$runDir = Join-Path $coverageRootPath $RunName
$snapshotDir = Join-Path $runDir "jacoco\snapshots"
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $runDir "jacoco\snapshot_manifest.jsonl"
}
if ([string]::IsNullOrWhiteSpace($StatusPath)) {
    $StatusPath = Join-Path $runDir "jacoco\snapshot_sampler_status.json"
}

$jacocoCli = Join-Path (Resolve-Path -LiteralPath $JaCoCoDir).Path "jacococli.jar"
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null

$startedAt = Get-Date
$deadline = $startedAt.AddSeconds($DurationSeconds)
$nextSnapshot = $startedAt.AddSeconds($IntervalSeconds)
$snapshotCount = 0
$failureCount = 0

Write-JsonFile -Path $StatusPath -Data ([ordered]@{
    runName = $RunName
    status = "running"
    startedAt = $startedAt.ToString("o")
    coveragePort = $CoveragePort
    durationSeconds = $DurationSeconds
    intervalSeconds = $IntervalSeconds
    snapshotCount = 0
    failureCount = 0
})

try {
    while ((Get-Date) -lt $deadline) {
        $now = Get-Date
        if ($now -lt $nextSnapshot) {
            $sleepSeconds = [int][Math]::Max(1, [Math]::Min(15, ($nextSnapshot - $now).TotalSeconds))
            Start-Sleep -Seconds $sleepSeconds
            continue
        }

        $elapsed = [int]($now - $startedAt).TotalSeconds
        $label = "t_{0:D6}s" -f $elapsed
        $snapshotExec = Join-Path $snapshotDir "$label.exec"
        $entry = [ordered]@{
            event = "snapshot"
            label = $label
            elapsedSeconds = $elapsed
            execFile = $snapshotExec
            createdAt = (Get-Date).ToString("o")
            coveragePort = $CoveragePort
        }

        & java -jar $jacocoCli dump --address localhost --port $CoveragePort --destfile $snapshotExec
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $snapshotExec)) {
            $snapshotCount += 1
            $entry.status = "ok"
        } else {
            $failureCount += 1
            $entry.status = "failed"
            $entry.exitCode = $LASTEXITCODE
        }
        Add-JsonLine -Path $ManifestPath -Data $entry

        Write-JsonFile -Path $StatusPath -Data ([ordered]@{
            runName = $RunName
            status = "running"
            startedAt = $startedAt.ToString("o")
            updatedAt = (Get-Date).ToString("o")
            elapsedSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
            coveragePort = $CoveragePort
            durationSeconds = $DurationSeconds
            intervalSeconds = $IntervalSeconds
            snapshotCount = $snapshotCount
            failureCount = $failureCount
        })

        do {
            $nextSnapshot = $nextSnapshot.AddSeconds($IntervalSeconds)
        } while ($nextSnapshot -le (Get-Date))
    }

    Write-JsonFile -Path $StatusPath -Data ([ordered]@{
        runName = $RunName
        status = "completed"
        startedAt = $startedAt.ToString("o")
        endedAt = (Get-Date).ToString("o")
        coveragePort = $CoveragePort
        durationSeconds = $DurationSeconds
        intervalSeconds = $IntervalSeconds
        snapshotCount = $snapshotCount
        failureCount = $failureCount
        manifestPath = $ManifestPath
    })
} catch {
    Write-JsonFile -Path $StatusPath -Data ([ordered]@{
        runName = $RunName
        status = "failed"
        startedAt = $startedAt.ToString("o")
        failedAt = (Get-Date).ToString("o")
        coveragePort = $CoveragePort
        snapshotCount = $snapshotCount
        failureCount = $failureCount
        error = $_.Exception.Message
        manifestPath = $ManifestPath
    })
    throw
}
