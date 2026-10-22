param(
    [Parameter(Mandatory = $true)]
    [string]$RunName,
    [string]$ContainerName = "",
    [string]$CoverageRoot = "",
    [string]$JaCoCoDir = "",
    [string]$ReportDirName = "jacoco_timeseries_dinkel_scope",
    [string]$ClassfilesDir = "",
    [switch]$IncludeFinalExec
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param([string]$RelativePath)
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        (Join-Path $PSScriptRoot "..\$RelativePath")
    )
}

function Get-LongPath {
    param([string]$Path)
    if ($env:OS -eq "Windows_NT") {
        if ($Path.StartsWith("\\?\")) {
            return $Path
        }
        return "\\?\$Path"
    }
    return $Path
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Data
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-CoveragePercent {
    param(
        [int64]$Covered,
        [int64]$Total
    )
    if ($Total -eq 0) {
        return 0.0
    }
    return [math]::Round(100.0 * $Covered / $Total, 6)
}

function ConvertTo-CsvField {
    param([object]$Value)
    if ($null -eq $Value) {
        return ""
    }
    $text = [string]$Value
    if ($text -match '[,"\r\n]') {
        return '"' + ($text -replace '"', '""') + '"'
    }
    return $text
}

function Extract-DinkelScopeClassfiles {
    param(
        [string]$ContainerName,
        [string]$RunDir
    )

    $jarDir = Join-Path $RunDir "neo4j_dinkel_scope_jars"
    $flatClassDir = Join-Path $RunDir "neo4j_dinkel_scope_classfiles"
    if (Test-Path -LiteralPath $jarDir) {
        Remove-Item -LiteralPath $jarDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $flatClassDir) {
        Remove-Item -LiteralPath $flatClassDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $jarDir, $flatClassDir | Out-Null

    if ([string]::IsNullOrWhiteSpace($ContainerName)) {
        $candidateJarDirs = @(
            (Join-Path $RunDir "neo4j_classfiles"),
            (Join-Path $RunDir "runtime_home\lib"),
            (Join-Path $RunDir "neo4j_container_copy\lib")
        )
        $sourceJarDir = ""
        foreach ($candidate in $candidateJarDirs) {
            if ((Test-Path -LiteralPath $candidate -PathType Container) -and
                @(Get-ChildItem -LiteralPath $candidate -Filter "*.jar" -File -ErrorAction SilentlyContinue).Count -gt 0) {
                $sourceJarDir = $candidate
                break
            }
        }
        if ([string]::IsNullOrWhiteSpace($sourceJarDir)) {
            throw "ContainerName was not provided and no saved Neo4j runtime jar directory was found under $RunDir"
        }
        Get-ChildItem -LiteralPath $sourceJarDir -Filter "*.jar" -File |
            Copy-Item -Destination $jarDir -Force
    } else {
        docker cp "${ContainerName}:/var/lib/neo4j/lib/." $jarDir | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to copy Neo4j runtime jars from $ContainerName"
        }
    }

    Get-ChildItem -LiteralPath $jarDir -Filter "*.jar" -File |
        Where-Object {
            ($_.Name -notmatch "^(neo4j|cypher-)") -or
            ($_.Name -match "^cypher-shell")
        } |
        Remove-Item -Force

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $copiedClasses = 0
    $skippedDuplicateClasses = 0
    $skippedMultiReleaseClasses = 0
    foreach ($jar in Get-ChildItem -LiteralPath $jarDir -Filter "*.jar" -File | Sort-Object FullName) {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($jar.FullName)
        try {
            foreach ($entry in $zip.Entries) {
                if (-not $entry.FullName.EndsWith(".class", [System.StringComparison]::OrdinalIgnoreCase)) {
                    continue
                }
                if ($entry.FullName.StartsWith("META-INF/versions/", [System.StringComparison]::OrdinalIgnoreCase)) {
                    $skippedMultiReleaseClasses += 1
                    continue
                }
                $relative = $entry.FullName -replace "/", [System.IO.Path]::DirectorySeparatorChar
                $destination = Join-Path $flatClassDir $relative
                $destinationDir = Split-Path -Parent $destination
                [System.IO.Directory]::CreateDirectory((Get-LongPath $destinationDir)) | Out-Null
                $longDestination = Get-LongPath $destination
                if ([System.IO.File]::Exists($longDestination)) {
                    $skippedDuplicateClasses += 1
                    continue
                }
                $inputStream = $entry.Open()
                try {
                    $outputStream = [System.IO.File]::Create($longDestination)
                    try {
                        $inputStream.CopyTo($outputStream)
                    } finally {
                        $outputStream.Dispose()
                    }
                } finally {
                    $inputStream.Dispose()
                }
                $copiedClasses += 1
            }
        } finally {
            $zip.Dispose()
        }
    }

    return [pscustomobject]@{
        classfilesDir = $flatClassDir
        jarsDir = $jarDir
        jarCount = @(Get-ChildItem -LiteralPath $jarDir -Filter "*.jar" -File).Count
        copiedClassFiles = $copiedClasses
        skippedDuplicateClassFiles = $skippedDuplicateClasses
        skippedMultiReleaseClassFiles = $skippedMultiReleaseClasses
    }
}

function Get-SnapshotEntries {
    param([string]$RunDir)

    $entries = @()
    $manifestPath = Join-Path $RunDir "jacoco\snapshot_manifest.jsonl"
    if (Test-Path -LiteralPath $manifestPath) {
        foreach ($line in Get-Content -LiteralPath $manifestPath) {
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $item = $line | ConvertFrom-Json
                if ([string]$item.status -ne "ok") {
                    continue
                }
                $execFile = [string]$item.execFile
                if (Test-Path -LiteralPath $execFile) {
                    $entries += [pscustomobject]@{
                        label = [string]$item.label
                        elapsedSeconds = [int]$item.elapsedSeconds
                        createdAt = [string]$item.createdAt
                        execFile = $execFile
                        kind = "snapshot"
                    }
                }
            } catch {
                continue
            }
        }
    }

    if ($entries.Count -eq 0) {
        $snapshotDir = Join-Path $RunDir "jacoco\snapshots"
        if (Test-Path -LiteralPath $snapshotDir) {
            foreach ($file in Get-ChildItem -LiteralPath $snapshotDir -Filter "*.exec" -File | Sort-Object Name) {
                $elapsed = $null
                if ($file.BaseName -match "t_(\d+)s") {
                    $elapsed = [int]$Matches[1]
                }
                $entries += [pscustomobject]@{
                    label = $file.BaseName
                    elapsedSeconds = $elapsed
                    createdAt = $file.LastWriteTime.ToString("o")
                    execFile = $file.FullName
                    kind = "snapshot"
                }
            }
        }
    }

    return @($entries | Sort-Object elapsedSeconds, label)
}

function Summarize-JaCoCoCsv {
    param(
        [string]$CsvPath,
        [object]$Entry
    )
    $rows = @(Import-Csv -LiteralPath $CsvPath)
    $instructionMissed = [int64]0
    $instructionCovered = [int64]0
    $branchMissed = [int64]0
    $branchCovered = [int64]0
    $lineMissed = [int64]0
    $lineCovered = [int64]0
    $methodMissed = [int64]0
    $methodCovered = [int64]0
    foreach ($row in $rows) {
        $instructionMissed += [int64]$row.INSTRUCTION_MISSED
        $instructionCovered += [int64]$row.INSTRUCTION_COVERED
        $branchMissed += [int64]$row.BRANCH_MISSED
        $branchCovered += [int64]$row.BRANCH_COVERED
        $lineMissed += [int64]$row.LINE_MISSED
        $lineCovered += [int64]$row.LINE_COVERED
        $methodMissed += [int64]$row.METHOD_MISSED
        $methodCovered += [int64]$row.METHOD_COVERED
    }
    $packages = @($rows | Select-Object -ExpandProperty PACKAGE -Unique).Count
    return [ordered]@{
        label = [string]$Entry.label
        kind = [string]$Entry.kind
        elapsedSeconds = $Entry.elapsedSeconds
        createdAt = [string]$Entry.createdAt
        classes = @($rows).Count
        packages = $packages
        instructionCovered = $instructionCovered
        instructionTotal = $instructionCovered + $instructionMissed
        instructionCoveragePct = Get-CoveragePercent -Covered $instructionCovered -Total ($instructionCovered + $instructionMissed)
        branchCovered = $branchCovered
        branchTotal = $branchCovered + $branchMissed
        branchCoveragePct = Get-CoveragePercent -Covered $branchCovered -Total ($branchCovered + $branchMissed)
        lineCovered = $lineCovered
        lineTotal = $lineCovered + $lineMissed
        lineCoveragePct = Get-CoveragePercent -Covered $lineCovered -Total ($lineCovered + $lineMissed)
        methodCovered = $methodCovered
        methodTotal = $methodCovered + $methodMissed
        methodCoveragePct = Get-CoveragePercent -Covered $methodCovered -Total ($methodCovered + $methodMissed)
        execFile = [string]$Entry.execFile
        csv = $CsvPath
    }
}

if ([string]::IsNullOrWhiteSpace($CoverageRoot)) {
    $CoverageRoot = Resolve-RepoPath "coverage_runs"
}
if ([string]::IsNullOrWhiteSpace($JaCoCoDir)) {
    $JaCoCoDir = Resolve-RepoPath "external_tools\jacoco"
}

$coverageRootPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CoverageRoot)
$runDir = Join-Path $coverageRootPath $RunName
if (-not (Test-Path -LiteralPath $runDir)) {
    throw "Coverage run directory not found: $runDir"
}

$jacocoCli = Join-Path (Resolve-Path -LiteralPath $JaCoCoDir).Path "jacococli.jar"
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

$classScope = $null
if ([string]::IsNullOrWhiteSpace($ClassfilesDir)) {
    $classScope = Extract-DinkelScopeClassfiles -ContainerName $ContainerName -RunDir $runDir
    $ClassfilesDir = $classScope.classfilesDir
} else {
    $ClassfilesDir = (Resolve-Path -LiteralPath $ClassfilesDir).Path
    $classScope = [pscustomobject]@{
        classfilesDir = $ClassfilesDir
        source = "provided"
    }
}
if (-not (Test-Path -LiteralPath $ClassfilesDir -PathType Container)) {
    throw "ClassfilesDir not found: $ClassfilesDir"
}

$reportDir = Join-Path $runDir $ReportDirName
$csvDir = Join-Path $reportDir "csv"
New-Item -ItemType Directory -Force -Path $csvDir | Out-Null

$entries = @(Get-SnapshotEntries -RunDir $runDir)
$finalExec = Join-Path $runDir "jacoco\neo4j.exec"
if ($IncludeFinalExec -or (Test-Path -LiteralPath $finalExec)) {
    if (Test-Path -LiteralPath $finalExec) {
        $budgetSeconds = $null
        $runMetaPath = Join-Path $runDir "run_meta.json"
        if (Test-Path -LiteralPath $runMetaPath) {
            try {
                $runMeta = Get-Content -LiteralPath $runMetaPath -Raw | ConvertFrom-Json
                if ($null -ne $runMeta.budgetSeconds) {
                    $budgetSeconds = [int]$runMeta.budgetSeconds
                }
            } catch {
                $budgetSeconds = $null
            }
        }
        if ($null -eq $budgetSeconds -and $entries.Count -gt 0) {
            $budgetSeconds = [int](@($entries | Sort-Object elapsedSeconds | Select-Object -Last 1).elapsedSeconds)
        }
        $entries += [pscustomobject]@{
            label = "t_final"
            elapsedSeconds = $budgetSeconds
            createdAt = (Get-Item -LiteralPath $finalExec).LastWriteTime.ToString("o")
            execFile = $finalExec
            kind = "final"
        }
    }
}

if ($entries.Count -eq 0) {
    throw "No JaCoCo snapshot exec files found for $RunName"
}

$points = @()
foreach ($entry in @($entries | Sort-Object elapsedSeconds, label)) {
    $safeLabel = ([string]$entry.label) -replace "[^A-Za-z0-9_.-]", "_"
    if ([string]::IsNullOrWhiteSpace($safeLabel)) {
        $safeLabel = "point_$($points.Count)"
    }
    $pointCsv = Join-Path $csvDir "$safeLabel.csv"
    & java -jar $jacocoCli report ([string]$entry.execFile) --classfiles $ClassfilesDir --csv $pointCsv
    if ($LASTEXITCODE -ne 0) {
        throw "JaCoCo report generation failed for $($entry.execFile)"
    }
    $points += [pscustomobject](Summarize-JaCoCoCsv -CsvPath $pointCsv -Entry $entry)
}

$timeseriesCsv = Join-Path $reportDir "coverage_timeseries.csv"
$headers = @(
    "label", "kind", "elapsed_seconds", "created_at", "classes", "packages",
    "instruction_covered", "instruction_total", "instruction_pct",
    "branch_covered", "branch_total", "branch_pct",
    "line_covered", "line_total", "line_pct",
    "method_covered", "method_total", "method_pct",
    "exec_file", "csv"
)
$lines = @()
$lines += ($headers -join ",")
foreach ($point in $points) {
    $values = @(
        $point.label,
        $point.kind,
        $point.elapsedSeconds,
        $point.createdAt,
        $point.classes,
        $point.packages,
        $point.instructionCovered,
        $point.instructionTotal,
        $point.instructionCoveragePct,
        $point.branchCovered,
        $point.branchTotal,
        $point.branchCoveragePct,
        $point.lineCovered,
        $point.lineTotal,
        $point.lineCoveragePct,
        $point.methodCovered,
        $point.methodTotal,
        $point.methodCoveragePct,
        $point.execFile,
        $point.csv
    ) | ForEach-Object { ConvertTo-CsvField $_ }
    $lines += ($values -join ",")
}
$lines | Set-Content -LiteralPath $timeseriesCsv -Encoding UTF8

$summary = [ordered]@{
    runName = $RunName
    reportDir = $reportDir
    timeseriesCsv = $timeseriesCsv
    pointCount = $points.Count
    classScope = $classScope
    scope = "Dinkel Neo4j jar scope: lib jars starting with neo4j or cypher-, excluding cypher-shell; no third-party dependencies/plugins"
    generatedAt = (Get-Date).ToString("o")
    finalPoint = $points[-1]
}
Write-JsonFile -Path (Join-Path $reportDir "summary.json") -Data $summary

Write-Host "Neo4j JaCoCo coverage timeseries generated."
Write-Host "CSV: $timeseriesCsv"
Write-Host ("Points: {0}" -f $points.Count)
