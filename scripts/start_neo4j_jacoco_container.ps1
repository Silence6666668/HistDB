param(
    [string]$RunName = "",
    [string]$ContainerName = "",
    [string]$Image = $(if ($env:NEO4J_IMAGE) { $env:NEO4J_IMAGE } else { "neo4j:2026.03.1" }),
    [int]$HttpPort = 7481,
    [int]$BoltPort = 7702,
    [int]$CoveragePort = 36300,
    [string]$Auth = "neo4j/sqlancer",
    [string]$CoverageRoot = "",
    [string]$JaCoCoDir = "",
    [bool]$EnableApoc = $false,
    [switch]$RemoveExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = "neo4j_jacoco_" + (Get-Date -Format "yyyy-MM-dd_HHmmss")
}
if ([string]::IsNullOrWhiteSpace($ContainerName)) {
    $safeRunName = $RunName -replace "[^A-Za-z0-9_.-]", "_"
    $ContainerName = "neo4j_cov_$safeRunName"
}
if ([string]::IsNullOrWhiteSpace($CoverageRoot)) {
    $CoverageRoot = Join-Path $PSScriptRoot "..\coverage_runs"
}
if ([string]::IsNullOrWhiteSpace($JaCoCoDir)) {
    $JaCoCoDir = Join-Path $PSScriptRoot "..\external_tools\jacoco"
}

$coverageRootPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CoverageRoot)
$jacocoDirPath = (Resolve-Path -LiteralPath $JaCoCoDir).Path
$agentPath = Join-Path $jacocoDirPath "jacocoagent.jar"
if (-not (Test-Path -LiteralPath $agentPath)) {
    throw "JaCoCo agent not found: $agentPath. Run scripts\prepare_jacoco.ps1 first."
}

$runDir = Join-Path $coverageRootPath $RunName
$jacocoMountDir = Join-Path $runDir "jacoco"
New-Item -ItemType Directory -Force -Path $jacocoMountDir | Out-Null
Copy-Item -LiteralPath $agentPath -Destination (Join-Path $jacocoMountDir "jacocoagent.jar") -Force

$existing = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $ContainerName }
if ($existing) {
    if ($RemoveExisting) {
        docker rm -f $ContainerName | Out-Null
    } else {
        throw "Container already exists: $ContainerName. Use -RemoveExisting or choose another name."
    }
}

$javaAgentOption = "-javaagent:/jacoco/jacocoagent.jar=output=tcpserver,address=*,port=6300,append=false"
$mountSpec = "${jacocoMountDir}:/jacoco"

Write-Host "Starting JaCoCo-instrumented Neo4j container $ContainerName"
$dockerArgs = @(
    "run", "-d",
    "--name", $ContainerName,
    "--label", "purpose=coverage",
    "--label", "project=graph-bug-fuzz-mvp",
    "-e", "NEO4J_AUTH=$Auth",
    "-e", "NEO4J_server_jvm_additional=$javaAgentOption",
    "-v", $mountSpec,
    "-p", "${HttpPort}:7474",
    "-p", "${BoltPort}:7687",
    "-p", "${CoveragePort}:6300"
)

if ($EnableApoc) {
    $apocEnvFile = Join-Path $jacocoMountDir "neo4j_apoc.env"
    @(
        'NEO4J_PLUGINS=["apoc"]',
        'NEO4J_dbms_security_procedures_unrestricted=apoc.*',
        'NEO4J_dbms_security_procedures_allowlist=apoc.*',
        'NEO4J_apoc_export_file_enabled=true',
        'NEO4J_apoc_import_file_enabled=true',
        'NEO4J_apoc_import_file_use__neo4j__config=true'
    ) | Set-Content -LiteralPath $apocEnvFile -Encoding ASCII
    $dockerArgs += @("--env-file", $apocEnvFile)
}

$dockerArgs += $Image
& docker @dockerArgs | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "Failed to start Neo4j coverage container"
}

$parts = $Auth.Split("/", 2)
$user = $parts[0]
$password = if ($parts.Count -gt 1) { $parts[1] } else { "" }
$deadline = (Get-Date).AddSeconds(150)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if ($Auth -eq "none") {
        $probe = docker exec -e "JAVA_TOOL_OPTIONS=" $ContainerName cypher-shell "RETURN 1 AS ok" 2>&1
    } else {
        $probe = docker exec -e "JAVA_TOOL_OPTIONS=" $ContainerName cypher-shell -u $user -p $password "RETURN 1 AS ok" 2>&1
    }
    $probeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    if ($probeExitCode -eq 0 -and ($probe -match "1")) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    docker logs --tail 120 $ContainerName
    throw "Neo4j coverage container did not become ready"
}

$version = docker exec -e "JAVA_TOOL_OPTIONS=" $ContainerName neo4j --version

$manifest = [ordered]@{
    runName = $RunName
    containerName = $ContainerName
    image = $Image
    version = ($version | Select-Object -First 1)
    httpPort = $HttpPort
    boltPort = $BoltPort
    coveragePort = $CoveragePort
    auth = $Auth
    enableApoc = $EnableApoc
    runDirectory = $runDir
    jacocoDirectory = $jacocoMountDir
    execFile = (Join-Path $jacocoMountDir "neo4j.exec")
    startedAt = (Get-Date).ToString("o")
}
[pscustomobject]$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir "coverage_container_manifest.json") -Encoding UTF8

Write-Host "Neo4j coverage container is ready."
Write-Host "Version: $version"
Write-Host "Bolt: localhost:$BoltPort"
Write-Host "JaCoCo TCP server: localhost:$CoveragePort"
Write-Host "Run directory: $runDir"
