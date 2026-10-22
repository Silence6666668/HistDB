param(
    [Parameter(Mandatory = $true)]
    [string]$RunName,
    [string]$ExecFile = "",
    [string]$RuntimeHome = "",
    [string]$ReportDirName = "jacoco_whole_jvm_report",
    [switch]$Html
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

function Get-CoveragePercent {
    param(
        [int64]$Covered,
        [int64]$Missed
    )
    $total = $Covered + $Missed
    if ($total -eq 0) {
        return 0.0
    }
    return [math]::Round(100.0 * $Covered / $total, 3)
}

function Summarize-JaCoCoRows {
    param(
        [array]$Rows,
        [string]$Label
    )
    $instructionMissed = [int64]0
    $instructionCovered = [int64]0
    $branchMissed = [int64]0
    $branchCovered = [int64]0
    $lineMissed = [int64]0
    $lineCovered = [int64]0
    $methodMissed = [int64]0
    $methodCovered = [int64]0
    foreach ($row in $Rows) {
        $instructionMissed += [int64]$row.INSTRUCTION_MISSED
        $instructionCovered += [int64]$row.INSTRUCTION_COVERED
        $branchMissed += [int64]$row.BRANCH_MISSED
        $branchCovered += [int64]$row.BRANCH_COVERED
        $lineMissed += [int64]$row.LINE_MISSED
        $lineCovered += [int64]$row.LINE_COVERED
        $methodMissed += [int64]$row.METHOD_MISSED
        $methodCovered += [int64]$row.METHOD_COVERED
    }
    return [ordered]@{
        label = $Label
        classesInReport = @($Rows).Count
        instructionCovered = $instructionCovered
        instructionTotal = $instructionCovered + $instructionMissed
        instructionCoveragePct = Get-CoveragePercent -Covered $instructionCovered -Missed $instructionMissed
        branchCovered = $branchCovered
        branchTotal = $branchCovered + $branchMissed
        branchCoveragePct = Get-CoveragePercent -Covered $branchCovered -Missed $branchMissed
        lineCovered = $lineCovered
        lineTotal = $lineCovered + $lineMissed
        lineCoveragePct = Get-CoveragePercent -Covered $lineCovered -Missed $lineMissed
        methodCovered = $methodCovered
        methodTotal = $methodCovered + $methodMissed
        methodCoveragePct = Get-CoveragePercent -Covered $methodCovered -Missed $methodMissed
    }
}

$repoRoot = Resolve-RepoPath "."
$coverageRoot = Resolve-RepoPath "coverage_runs"
$runDir = Join-Path $coverageRoot $RunName
if (-not (Test-Path -LiteralPath $runDir)) {
    throw "Coverage run directory not found: $runDir"
}

$manifestPath = Join-Path $runDir "source_coverage_manifest.json"
$manifest = $null
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
}

if ([string]::IsNullOrWhiteSpace($RuntimeHome)) {
    if ($null -eq $manifest -or [string]::IsNullOrWhiteSpace([string]$manifest.neo4jHome)) {
        throw "RuntimeHome was not provided and source_coverage_manifest.json has no neo4jHome"
    }
    $RuntimeHome = [string]$manifest.neo4jHome
}
$runtimeHomePath = (Resolve-Path -LiteralPath $RuntimeHome).Path

if ([string]::IsNullOrWhiteSpace($ExecFile)) {
    $ExecFile = Join-Path $runDir "jacoco\neo4j.exec"
}
$execFilePath = (Resolve-Path -LiteralPath $ExecFile).Path

$jacocoCli = Join-Path (Resolve-RepoPath "external_tools\jacoco") "jacococli.jar"
if (-not (Test-Path -LiteralPath $jacocoCli)) {
    throw "JaCoCo CLI not found: $jacocoCli"
}

$reportDir = Join-Path $runDir $ReportDirName
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$scratchDir = Join-Path ([System.IO.Path]::GetTempPath()) (Join-Path "neo4j_cov_tmp" ("whole_jvm_classes_" + $RunName + "_" + [guid]::NewGuid().ToString("N").Substring(0, 8)))
$classDir = Join-Path $scratchDir "classes"
if (Test-Path -LiteralPath $scratchDir) {
    Remove-Item -LiteralPath $scratchDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $classDir | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem
$jarPaths = @()
foreach ($subdir in @("lib", "plugins")) {
    $dir = Join-Path $runtimeHomePath $subdir
    if (Test-Path -LiteralPath $dir) {
        $jarPaths += @(Get-ChildItem -LiteralPath $dir -Filter "*.jar" -File | Sort-Object FullName)
    }
}
if ($jarPaths.Count -eq 0) {
    throw "No runtime jars found under $runtimeHomePath"
}

$copiedClasses = 0
$skippedDuplicateClasses = 0
$skippedMultiReleaseClasses = 0
$seenEntries = @{}
foreach ($jar in $jarPaths) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($jar.FullName)
    try {
        foreach ($entry in $zip.Entries) {
            if (-not $entry.FullName.EndsWith(".class", [System.StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
            if ($entry.FullName -match "^META-INF/versions/") {
                $skippedMultiReleaseClasses += 1
                continue
            }
            if ($seenEntries.ContainsKey($entry.FullName)) {
                $skippedDuplicateClasses += 1
                continue
            }
            $seenEntries[$entry.FullName] = $true
            $dest = Join-Path $classDir ("{0:D8}.class" -f $copiedClasses)
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $false)
            $copiedClasses += 1
        }
    } finally {
        $zip.Dispose()
    }
}

$csvPath = Join-Path $reportDir "jacoco.csv"
$xmlPath = Join-Path $reportDir "jacoco.xml"
$args = @(
    "report", $execFilePath,
    "--classfiles", $classDir,
    "--csv", $csvPath,
    "--xml", $xmlPath
)
$htmlPath = ""
if ($Html) {
    $htmlPath = Join-Path $reportDir "html"
    $args += @("--html", $htmlPath)
}

& java -jar $jacocoCli @args
if ($LASTEXITCODE -ne 0) {
    throw "JaCoCo whole JVM report generation failed"
}

$rows = @(Import-Csv -LiteralPath $csvPath)
$neo4jRows = @($rows | Where-Object {
    ($_.PACKAGE -like "org/neo4j*") -or ($_.PACKAGE -like "org.neo4j*")
})
$summary = [ordered]@{
    runName = $RunName
    reportDir = $reportDir
    execFile = $execFilePath
    runtimeHome = $runtimeHomePath
    jarCount = $jarPaths.Count
    deduplicatedClassDir = $classDir
    copiedClassFiles = $copiedClasses
    skippedDuplicateClassFiles = $skippedDuplicateClasses
    skippedMultiReleaseClassFiles = $skippedMultiReleaseClasses
    csv = $csvPath
    xml = $xmlPath
    html = $htmlPath
    wholeJvm = Summarize-JaCoCoRows -Rows $rows -Label "Neo4j external server whole JVM"
    neo4jOnly = Summarize-JaCoCoRows -Rows $neo4jRows -Label "Neo4j external server org.neo4j.*"
    generatedAt = (Get-Date).ToString("o")
}
Write-JsonFile -Path (Join-Path $reportDir "summary.json") -Data $summary

Write-Host "Whole JVM JaCoCo report generated."
Write-Host "Summary: $(Join-Path $reportDir 'summary.json')"
Write-Host "Whole JVM line coverage: $($summary.wholeJvm.lineCovered)/$($summary.wholeJvm.lineTotal) ($($summary.wholeJvm.lineCoveragePct)%)"
