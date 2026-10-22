param(
    [string]$Version = "0.8.13",
    [string]$OutDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $PSScriptRoot "..\external_tools\jacoco"
}

$resolvedOut = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutDir)
New-Item -ItemType Directory -Force -Path $resolvedOut | Out-Null

Write-Host "Preparing JaCoCo $Version under $resolvedOut"

& mvn -q org.apache.maven.plugins:maven-dependency-plugin:3.6.1:copy `
    "-Dartifact=org.jacoco:org.jacoco.agent:${Version}:jar:runtime" `
    "-DoutputDirectory=$resolvedOut" `
    "-Dmdep.stripVersion=true"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to download JaCoCo agent"
}

& mvn -q org.apache.maven.plugins:maven-dependency-plugin:3.6.1:copy `
    "-Dartifact=org.jacoco:org.jacoco.cli:${Version}:jar:nodeps" `
    "-DoutputDirectory=$resolvedOut" `
    "-Dmdep.stripVersion=true"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to download JaCoCo CLI"
}

$agent = Get-ChildItem -LiteralPath $resolvedOut -Filter "org.jacoco.agent*runtime*.jar" | Select-Object -First 1
$cli = Get-ChildItem -LiteralPath $resolvedOut -Filter "org.jacoco.cli*nodeps*.jar" | Select-Object -First 1
if (-not $agent) {
    throw "JaCoCo agent jar not found in $resolvedOut"
}
if (-not $cli) {
    throw "JaCoCo CLI jar not found in $resolvedOut"
}

Copy-Item -LiteralPath $agent.FullName -Destination (Join-Path $resolvedOut "jacocoagent.jar") -Force
Copy-Item -LiteralPath $cli.FullName -Destination (Join-Path $resolvedOut "jacococli.jar") -Force

[pscustomobject]@{
    Version = $Version
    Directory = $resolvedOut
    Agent = (Join-Path $resolvedOut "jacocoagent.jar")
    Cli = (Join-Path $resolvedOut "jacococli.jar")
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $resolvedOut "jacoco_manifest.json") -Encoding UTF8

Write-Host "JaCoCo is ready."
Write-Host "Agent: $(Join-Path $resolvedOut "jacocoagent.jar")"
Write-Host "CLI: $(Join-Path $resolvedOut "jacococli.jar")"
