param(
    [string]$RunName,
    [string]$ContainerName,
    [int]$CoveragePort = 36300,
    [string]$CoverageRoot = "",
    [string]$JaCoCoDir = "",
    [switch]$StopContainer,
    [switch]$RemoveContainer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

if ([string]::IsNullOrWhiteSpace($CoverageRoot)) {
    $CoverageRoot = Join-Path $PSScriptRoot "..\coverage_runs"
}
if ([string]::IsNullOrWhiteSpace($JaCoCoDir)) {
    $JaCoCoDir = Join-Path $PSScriptRoot "..\external_tools\jacoco"
}
if ([string]::IsNullOrWhiteSpace($RunName)) {
    throw "RunName is required"
}
if ([string]::IsNullOrWhiteSpace($ContainerName)) {
    throw "ContainerName is required"
}

$coverageRootPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CoverageRoot)
$runDir = Join-Path $coverageRootPath $RunName
$jacocoDirPath = (Resolve-Path -LiteralPath $JaCoCoDir).Path
$cliPath = Join-Path $jacocoDirPath "jacococli.jar"
if (-not (Test-Path -LiteralPath $cliPath)) {
    throw "JaCoCo CLI not found: $cliPath. Run scripts\prepare_jacoco.ps1 first."
}

$execFile = Join-Path $runDir "jacoco\neo4j.exec"
$isRunning = @(docker ps --format "{{.Names}}" | Where-Object { $_ -eq $ContainerName }).Count -gt 0
if ($isRunning) {
    & java -jar $cliPath dump --address localhost --port $CoveragePort --destfile $execFile
    if ($LASTEXITCODE -ne 0) {
        throw "JaCoCo dump failed from localhost:$CoveragePort"
    }
}

if ($StopContainer -or $RemoveContainer) {
    docker stop $ContainerName | Out-Null
}

if (-not (Test-Path -LiteralPath $execFile)) {
    throw "JaCoCo exec file not found: $execFile. Dump coverage before stopping the container."
}

$classDir = Join-Path $runDir "neo4j_classfiles"
if (Test-Path -LiteralPath $classDir) {
    Remove-Item -LiteralPath $classDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $classDir | Out-Null

docker cp "${ContainerName}:/var/lib/neo4j/lib/." $classDir | Out-Null
if ($LASTEXITCODE -ne 0) {
    docker cp "${ContainerName}:/var/lib/neo4j/." (Join-Path $runDir "neo4j_container_copy") | Out-Null
    throw "Failed to copy /var/lib/neo4j/lib from container"
}

Get-ChildItem -LiteralPath $classDir -Filter "*.jar" -File |
    Where-Object {
        ($_.Name -notmatch "^(neo4j|cypher-)") -or
        ($_.Name -match "^cypher-shell")
    } |
    Remove-Item -Force

$safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
$flatClassDir = Join-Path ([System.IO.Path]::GetTempPath()) "neo4j_cov_flat_$safeRunName"
if (Test-Path -LiteralPath $flatClassDir) {
    Remove-Item -LiteralPath $flatClassDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $flatClassDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($jar in Get-ChildItem -LiteralPath $classDir -Filter "*.jar" -File) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($jar.FullName)
    try {
        foreach ($entry in $zip.Entries) {
            if (-not $entry.FullName.EndsWith(".class")) {
                continue
            }
            if ($entry.FullName.StartsWith("META-INF/versions/")) {
                continue
            }
            $relative = $entry.FullName -replace "/", [System.IO.Path]::DirectorySeparatorChar
            $destination = Join-Path $flatClassDir $relative
            $destinationDir = Split-Path -Parent $destination
            $longDestination = Get-LongPath $destination
            [System.IO.Directory]::CreateDirectory((Get-LongPath $destinationDir)) | Out-Null
            if (-not [System.IO.File]::Exists($longDestination)) {
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
            }
        }
    } finally {
        $zip.Dispose()
    }
}

$reportDir = Join-Path $runDir "jacoco_report"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$csvPath = Join-Path $reportDir "jacoco.csv"
$xmlPath = Join-Path $reportDir "jacoco.xml"
$htmlPath = Join-Path $reportDir "html"

& java -jar $cliPath report $execFile `
    --classfiles $flatClassDir `
    --csv $csvPath `
    --xml $xmlPath `
    --html $htmlPath
if ($LASTEXITCODE -ne 0) {
    throw "JaCoCo report generation failed"
}

if ($RemoveContainer) {
    docker rm $ContainerName | Out-Null
}

Write-Host "JaCoCo report generated."
Write-Host "CSV: $csvPath"
Write-Host "XML: $xmlPath"
Write-Host "HTML: $htmlPath"
