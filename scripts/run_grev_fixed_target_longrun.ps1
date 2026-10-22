param(
    [ValidateSet("memgraph", "falkordb")]
    [string]$Target = "memgraph",
    [int]$DurationSeconds = 86400,
    [string]$RunName = "",
    [string]$OutputRoot = "",
    [int]$MemgraphPort = 17687,
    [int]$FalkorPort = 16380,
    [string]$MemgraphImage = $(if ($env:MEMGRAPH_IMAGE) { $env:MEMGRAPH_IMAGE } else { "memgraph/memgraph:3.9.0" }),
    [string]$FalkorImage = $(if ($env:FALKORDB_IMAGE) { $env:FALKORDB_IMAGE } else { "falkordb/falkordb:v4.16.8" }),
    [int]$MemgraphQueryTimeoutSeconds = 5,
    [string]$RunnerImage = "grev-fixed-runner:local",
    [string]$InputFile = "",
    [int]$QueryLimit = 2000,
    [int]$TransformTimes = 5,
    [int]$OperationTimeoutSeconds = 60,
    [int]$ReadyTimeoutSeconds = 180,
    [switch]$Smoke,
    [switch]$KeepContainers,
    [switch]$RemoveExistingContainers,
    [switch]$SkipImageBuild,
    [switch]$UseHostPython,
    [string]$PythonExe = ""
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
    $Data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Test-PortListening {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if ($task.Wait(300)) {
            return $client.Connected
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Assert-PortAvailable {
    param([int]$Port)
    if (Test-PortListening -Port $Port) {
        throw "Port $Port is already in use. Choose a different port or use -SkipContainers with an existing target."
    }
}

function Get-ContainerStatus {
    param([string]$Name)
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $status = & docker inspect -f "{{.State.Status}}" $Name 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($status)) {
            return [string]$status
        }
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    return ""
}

function Wait-PortReady {
    param(
        [string]$Name,
        [int]$Port,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) {
            Start-Sleep -Seconds 2
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not open localhost:$Port within $TimeoutSeconds seconds."
}

function Start-TargetContainer {
    param(
        [string]$Name,
        [string]$Image,
        [int]$HostPort,
        [int]$ContainerPort,
        [string]$TargetName,
        [string[]]$ImageArgs = @()
    )

    $existingStatus = Get-ContainerStatus -Name $Name
    if (-not [string]::IsNullOrWhiteSpace($existingStatus)) {
        if ($RemoveExistingContainers) {
            & docker rm -f $Name | Out-Null
        } elseif ($existingStatus -eq "running") {
            Write-Host "Reusing running container $Name for $TargetName."
            return [pscustomobject]@{ name = $Name; startedByScript = $false; image = $Image; hostPort = $HostPort }
        } else {
            throw "Container $Name already exists with status '$existingStatus'. Use -RemoveExistingContainers or a different -RunName."
        }
    }

    Assert-PortAvailable -Port $HostPort

    $dockerArgs = @(
        "run", "-d",
        "--name", $Name,
        "--label", "project=graph-bug-fuzz-mvp",
        "--label", "purpose=grev-fixed-target-longrun",
        "--label", "baseline-tool=GRev",
        "--label", "baseline-target=$TargetName",
        "--restart", "unless-stopped",
        "-p", "${HostPort}:${ContainerPort}",
        $Image
    )
    foreach ($arg in $ImageArgs) {
        if (-not [string]::IsNullOrWhiteSpace($arg)) {
            $dockerArgs += $arg
        }
    }
    Write-Host "Starting $TargetName container $Name on localhost:$HostPort..."
    & docker @dockerArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start $TargetName container $Name."
    }
    return [pscustomobject]@{ name = $Name; startedByScript = $true; image = $Image; hostPort = $HostPort }
}

function Stop-TargetContainer {
    param($ContainerInfo)
    if ($KeepContainers -or $null -eq $ContainerInfo) {
        return
    }
    if ($ContainerInfo.startedByScript) {
        Write-Host "Removing container $($ContainerInfo.name)..."
        & docker rm -f $ContainerInfo.name | Out-Null
    }
}

if ($Smoke) {
    $DurationSeconds = [Math]::Min($DurationSeconds, 60)
    $QueryLimit = [Math]::Min($QueryLimit, 20)
}

$repoRoot = Resolve-RepoPath "."
$grevRoot = Resolve-RepoPath "external_tools\GRev"
$scriptsRoot = Resolve-RepoPath "scripts"
$runnerScript = Resolve-RepoPath "scripts\run_grev_fixed_target_longrun.py"

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPython = Join-Path $repoRoot ".venv_grev\Scripts\python.exe"
    $PythonExe = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
}

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $suffix = Get-Date -Format "yyyyMMdd_HHmmss"
    $RunName = if ($Smoke) { "grev_${Target}_fixed_smoke_$suffix" } else { "grev_${Target}_fixed_24h_$suffix" }
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "coverage_runs"
}
$runDir = Join-Path $OutputRoot $RunName
$artifactDir = Join-Path $runDir "artifacts"
$runnerOut = Join-Path $runDir "runner"
New-Item -ItemType Directory -Force -Path $artifactDir, $runnerOut | Out-Null

if ((-not $UseHostPython) -and (-not $SkipImageBuild)) {
    $imageExists = $false
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker image inspect $RunnerImage > $null 2> $null
    $inspectExitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    if ($inspectExitCode -eq 0) {
        $imageExists = $true
    }
    if (-not $imageExists) {
        Write-Host "Building GRev runner image $RunnerImage..."
        & docker build -t $RunnerImage $grevRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build $RunnerImage."
        }
    }
}

$container = $null
$targetPort = if ($Target -eq "memgraph") { $MemgraphPort } else { $FalkorPort }
$targetImage = if ($Target -eq "memgraph") { $MemgraphImage } else { $FalkorImage }
$manifestPath = Join-Path $runDir "manifest.json"
$manifest = [ordered]@{
    tool = "GRev"
    target = $Target
    runName = $RunName
    startedAt = (Get-Date).ToString("o")
    durationSeconds = $DurationSeconds
    targetImage = $targetImage
    fixedVersionPolicy = "Memgraph uses memgraph/memgraph:3.9.0; FalkorDB uses falkordb/falkordb:v4.16.8 unless overridden by env/args."
    mode = if ($Target -eq "memgraph") { "artifact_native_memgraph_generator_wrapper" } else { "redisgraph_input_wrapper" }
    queryLimit = $QueryLimit
    transformTimes = $TransformTimes
    operationTimeoutSeconds = $OperationTimeoutSeconds
    memgraphQueryTimeoutSeconds = $MemgraphQueryTimeoutSeconds
    runDir = $runDir
}
Write-JsonFile -Path $manifestPath -Data $manifest

try {
    if ($Target -eq "memgraph") {
        $container = Start-TargetContainer `
            -Name "${RunName}_memgraph" `
            -Image $MemgraphImage `
            -HostPort $MemgraphPort `
            -ContainerPort 7687 `
            -TargetName "memgraph" `
            -ImageArgs @("--query-execution-timeout-sec=$MemgraphQueryTimeoutSeconds")
    } else {
        $container = Start-TargetContainer `
            -Name "${RunName}_falkordb" `
            -Image $FalkorImage `
            -HostPort $FalkorPort `
            -ContainerPort 6379 `
            -TargetName "falkordb"
    }

    Wait-PortReady -Name $Target -Port $targetPort -TimeoutSeconds $ReadyTimeoutSeconds
    if ($Target -eq "memgraph") {
        # Memgraph may open the Bolt port before the Bolt handshake is ready.
        # Give it a short grace period to avoid treating startup as a DB failure.
        Start-Sleep -Seconds 15
    }

    $inputInContainer = ""
    $generator = "native"
    if ($Target -eq "falkordb") {
        if ([string]::IsNullOrWhiteSpace($InputFile)) {
            $InputFile = Resolve-RepoPath "external_tools\GDsmith\logs\redisgraph\database0-cur.log"
        } else {
            $InputFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InputFile)
        }
        $inputDir = Join-Path $runDir "input"
        New-Item -ItemType Directory -Force -Path $inputDir | Out-Null
        $inputCopy = Join-Path $inputDir (Split-Path -Leaf $InputFile)
        Copy-Item -LiteralPath $InputFile -Destination $inputCopy -Force
        $inputInContainer = "/out/input/" + (Split-Path -Leaf $InputFile)
        $generator = "input"
    }

    $stdoutPath = Join-Path $runDir "runner_stdout.log"
    $stderrPath = Join-Path $runDir "runner_stderr.log"
    if ($UseHostPython) {
        $hostInput = ""
        if ($Target -eq "falkordb") {
            $hostInput = Join-Path $runDir ("input\" + (Split-Path -Leaf $InputFile))
        }
        $pythonArgs = @(
            $runnerScript,
            "--target", $Target,
            "--host", "localhost",
            "--port", "$targetPort",
            "--duration-seconds", "$DurationSeconds",
            "--output-dir", (Join-Path $runDir "runner"),
            "--generator", $generator,
            "--query-limit", "$QueryLimit",
            "--transform-times", "$TransformTimes",
            "--operation-timeout-seconds", "$OperationTimeoutSeconds"
        )
        if (-not [string]::IsNullOrWhiteSpace($hostInput)) {
            $pythonArgs += @("--input-file", $hostInput)
        }
        Write-Host "Running GRev fixed-target wrapper for $Target with host Python..."
        $oldPythonPath = $env:PYTHONPATH
        $oldGrevRoot = $env:GREV_ROOT
        $env:PYTHONPATH = $grevRoot
        $env:GREV_ROOT = $grevRoot
        Push-Location $grevRoot
        try {
            $oldPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $PythonExe @pythonArgs 1> $stdoutPath 2> $stderrPath
            $runnerExitCode = $LASTEXITCODE
            $ErrorActionPreference = $oldPreference
        } finally {
            $ErrorActionPreference = "Stop"
            Pop-Location
            $env:PYTHONPATH = $oldPythonPath
            $env:GREV_ROOT = $oldGrevRoot
        }
    } else {
        $runnerName = "${RunName}_runner"
        $dockerArgs = @(
            "run", "--rm",
            "--name", $runnerName,
            "--add-host", "host.docker.internal:host-gateway",
            "-v", "${grevRoot}:/appdata",
            "-v", "${scriptsRoot}:/runner",
            "-v", "${runDir}:/out",
            "-w", "/appdata",
            "-e", "PYTHONPATH=/appdata",
            "-e", "GREV_ROOT=/appdata",
            $RunnerImage,
            "python", "/runner/run_grev_fixed_target_longrun.py",
            "--target", $Target,
            "--host", "host.docker.internal",
            "--port", "$targetPort",
            "--duration-seconds", "$DurationSeconds",
            "--output-dir", "/out/runner",
            "--generator", $generator,
            "--query-limit", "$QueryLimit",
            "--transform-times", "$TransformTimes",
            "--operation-timeout-seconds", "$OperationTimeoutSeconds"
        )
        if (-not [string]::IsNullOrWhiteSpace($inputInContainer)) {
            $dockerArgs += @("--input-file", $inputInContainer)
        }

        Write-Host "Running GRev fixed-target wrapper for $Target..."
        & docker @dockerArgs 1> $stdoutPath 2> $stderrPath
        $runnerExitCode = $LASTEXITCODE
    }

    & docker logs $container.name > (Join-Path $artifactDir "$Target-container.log") 2>&1
    $manifest.endedAt = (Get-Date).ToString("o")
    $manifest.runnerExitCode = $runnerExitCode
    $manifest.summary = Join-Path $runnerOut "summary.json"
    $manifest.events = Join-Path $runnerOut "events.jsonl"
    Write-JsonFile -Path $manifestPath -Data $manifest

    if ($runnerExitCode -ne 0) {
        throw "GRev fixed-target runner failed with exit code $runnerExitCode. See $stderrPath"
    }
} finally {
    Stop-TargetContainer -ContainerInfo $container
}

Write-Host "GRev fixed-target run finished: $runDir"
