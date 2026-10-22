param(
    [string]$OutputDir = "output/reproduction/smoke",
    [string[]]$Dbms = @("neo4j", "memgraph", "falkordb", "arcadedb", "apache_age"),
    [switch]$KeepContainers
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "reproduction/docker-compose.yml"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$ComposeProject = "histdb-repro-$Timestamp-$PID".ToLowerInvariant()
$StartedAt = (Get-Date).ToUniversalTime().ToString("o")

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $ResolvedOutputDir = [System.IO.Path]::GetFullPath($OutputDir)
} else {
    $ResolvedOutputDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDir))
}
New-Item -ItemType Directory -Force -Path $ResolvedOutputDir | Out-Null

if ([string]::IsNullOrWhiteSpace($env:NEO4J_PASSWORD)) {
    $env:NEO4J_PASSWORD = "histdb-repro-password"
}
if ([string]::IsNullOrWhiteSpace($env:ARCADEDB_PASSWORD)) {
    $env:ARCADEDB_PASSWORD = "histdb-repro-password"
}
if ([string]::IsNullOrWhiteSpace($env:AGE_PASSWORD)) {
    $env:AGE_PASSWORD = "histdb-repro-password"
}

$Targets = @{
    neo4j = [pscustomobject]@{
        profile = "neo4j"; service = "neo4j"; family = "HISTDB-NEO-001"
        endpoint_host = "127.0.0.1"; endpoint_port = 17687
        image_env = "NEO4J_IMAGE"; image = "neo4j:2026.04.0"
    }
    memgraph = [pscustomobject]@{
        profile = "memgraph"; service = "memgraph"; family = "HISTDB-MEM-001"
        endpoint_host = "127.0.0.1"; endpoint_port = 17688
        image_env = $null; image = "memgraph/memgraph:3.9.0"
    }
    falkordb = [pscustomobject]@{
        profile = "falkordb"; service = "falkordb"; family = "HISTDB-FAL-001"
        endpoint_host = "127.0.0.1"; endpoint_port = 16379
        image_env = $null; image = "falkordb/falkordb:v4.16.8"
    }
    arcadedb = [pscustomobject]@{
        profile = "arcadedb"; service = "arcadedb"; family = "HISTDB-ARC-001"
        endpoint_host = "127.0.0.1"; endpoint_port = 12480
        image_env = $null; image = "arcadedata/arcadedb:26.4.1"
    }
    apache_age = [pscustomobject]@{
        profile = "apache_age"; service = "apache-age"; family = "HISTDB-AGE-001"
        endpoint_host = "127.0.0.1"; endpoint_port = 15455
        image_env = "AGE_IMAGE"; image = "apache/age:latest"
    }
}

function Wait-TcpEndpoint {
    param(
        [string]$EndpointHost,
        [int]$EndpointPort,
        [int]$TimeoutSeconds = 60
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        $Client = [System.Net.Sockets.TcpClient]::new()
        try {
            $Connect = $Client.ConnectAsync($EndpointHost, $EndpointPort)
            if ($Connect.Wait(1000) -and $Client.Connected) {
                return $true
            }
        } catch {
            # The service can legitimately reject connections while booting.
        } finally {
            $Client.Dispose()
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Write-SmokeSummary {
    param([System.Collections.Generic.List[object]]$Results)

    $Payload = [ordered]@{
        schema_version = 1
        compose_project = $ComposeProject
        started_at = $StartedAt
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
        keep_containers = [bool]$KeepContainers
        results = @($Results)
    }
    $SummaryPath = Join-Path $ResolvedOutputDir "summary.json"
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SummaryPath -Encoding utf8
    Write-Host "Smoke summary: $SummaryPath"
}

$Unknown = @($Dbms | Where-Object { -not $Targets.ContainsKey($_) })
if ($Unknown.Count -gt 0) {
    throw "Unknown DBMS selector(s): $($Unknown -join ', ')"
}

$Results = [System.Collections.Generic.List[object]]::new()
try {
    foreach ($DbmsName in $Dbms) {
        $Target = $Targets[$DbmsName]
        $CaseOutputDir = Join-Path $ResolvedOutputDir $DbmsName
        New-Item -ItemType Directory -Force -Path $CaseOutputDir | Out-Null

        Write-Host "[$DbmsName] checking the local environment"
        & python -m src.cli.reproduce "doctor" --dbms $DbmsName --format json
        $DoctorExitCode = $LASTEXITCODE

        if ($null -ne $Target.image_env) {
            [System.Environment]::SetEnvironmentVariable(
                [string]$Target.image_env,
                [string]$Target.image,
                [System.EnvironmentVariableTarget]::Process
            )
        }

        $ComposeExitCode = 127
        $RunExitCode = 3
        $Ready = $false
        $Outcome = "ENVIRONMENT_ERROR"
        $Message = $null
        try {
            Write-Host "[$DbmsName] starting isolated Compose profile $($Target.profile)"
            & docker compose -f $ComposeFile -p $ComposeProject --profile $Target.profile up -d $Target.service
            $ComposeExitCode = $LASTEXITCODE
            if ($ComposeExitCode -ne 0) {
                $Message = "docker compose up exited with code $ComposeExitCode"
            } else {
                $Ready = Wait-TcpEndpoint `
                    -EndpointHost $Target.endpoint_host `
                    -EndpointPort $Target.endpoint_port
                if (-not $Ready) {
                    $Message = "target did not open its configured endpoint within 60 seconds"
                } else {
                    Write-Host "[$DbmsName] running $($Target.family)"
                    & python -m src.cli.reproduce "run" `
                        --case $Target.family `
                        --output-dir $CaseOutputDir `
                        --no-manage-target
                    $RunExitCode = $LASTEXITCODE

                    $CaseSummaryPath = Join-Path $CaseOutputDir "summary.json"
                    if (Test-Path -LiteralPath $CaseSummaryPath) {
                        $CaseSummary = Get-Content -LiteralPath $CaseSummaryPath -Raw | ConvertFrom-Json
                        $OutcomeProperty = $CaseSummary.families.PSObject.Properties[$Target.family]
                        if ($null -ne $OutcomeProperty) {
                            $Outcome = [string]$OutcomeProperty.Value
                        }
                    }
                }
            }
        } catch {
            $Message = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
        }

        $Results.Add([pscustomobject][ordered]@{
            dbms = $DbmsName
            family_id = $Target.family
            image = $Target.image
            doctor_exit_code = $DoctorExitCode
            compose_exit_code = $ComposeExitCode
            endpoint_ready = $Ready
            run_exit_code = $RunExitCode
            outcome = $Outcome
            message = $Message
            output_dir = $CaseOutputDir
        })
    }
} finally {
    Write-SmokeSummary -Results $Results
    if (-not $KeepContainers) {
        try {
            Write-Host "Stopping only Compose project $ComposeProject"
            & docker compose -f $ComposeFile -p $ComposeProject down --remove-orphans
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "docker compose down exited with code $LASTEXITCODE"
            }
        } catch {
            Write-Warning "Could not clean Compose project ${ComposeProject}: $($_.Exception.Message)"
        }
    } else {
        Write-Host "Keeping Compose project $ComposeProject because -KeepContainers was supplied."
    }
}

if (@($Results | Where-Object { $_.run_exit_code -ne 0 }).Count -gt 0) {
    exit 3
}
exit 0
