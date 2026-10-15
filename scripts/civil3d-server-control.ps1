param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "start", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$stateDirectory = Join-Path $env:LOCALAPPDATA "Civil3DMCP"
$pidFile = Join-Path $stateDirectory "server.pid"
$stdoutLog = Join-Path $stateDirectory "server.stdout.log"
$stderrLog = Join-Path $stateDirectory "server.stderr.log"

function Get-ManagedServerProcess {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return $null
    }

    $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $serverPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$serverPid) -or $serverPid -le 0) {
        throw "Invalid MCP server PID file: $pidFile"
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if (-not $process) {
        Remove-Item -LiteralPath $pidFile -Force
        return $null
    }

    $expectedPython = [IO.Path]::GetFullPath($env:CIVIL3D_MCP_PYTHON)
    $actualPython = [IO.Path]::GetFullPath($process.ExecutablePath)
    if (
        -not [string]::Equals(
            $expectedPython,
            $actualPython,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $process.CommandLine -notmatch '(?i)(-m\s+civil3d_mcp|civil3d_mcp\.server)'
    ) {
        throw "PID $serverPid does not identify the configured Civil 3D MCP server; refusing to manage it."
    }

    return Get-Process -Id $serverPid
}

function Test-ServerPort {
    $portNumber = 0
    if (-not [int]::TryParse($env:CIVIL3D_MCP_PORT, [ref]$portNumber)) {
        throw "CIVIL3D_MCP_PORT must be an integer."
    }

    $listener = Get-NetTCPConnection -State Listen -LocalPort $portNumber -ErrorAction SilentlyContinue
    return [bool]$listener
}

switch ($Action) {
    "status" {
        $process = Get-ManagedServerProcess
        if (-not $process) {
            Write-Output "STOPPED"
            exit 0
        }
        if (-not (Test-ServerPort)) {
            throw "MCP process $($process.Id) is running but port $env:CIVIL3D_MCP_PORT is not listening."
        }
        Write-Output "RUNNING pid=$($process.Id) port=$env:CIVIL3D_MCP_PORT"
    }

    "start" {
        if ($env:GITHUB_REF -and $env:GITHUB_REF -ne "refs/heads/main") {
            throw "Start is allowed only from the main branch."
        }
        if ([string]::IsNullOrWhiteSpace($env:CIVIL3D_MCP_PYTHON)) {
            throw "Set repository variable CIVIL3D_MCP_PYTHON to the runner's venv Python executable."
        }
        if (-not (Test-Path -LiteralPath $env:CIVIL3D_MCP_PYTHON -PathType Leaf)) {
            throw "Configured Python executable does not exist."
        }
        if ([string]::IsNullOrWhiteSpace($env:CIVIL3D_MCP_REPOSITORY)) {
            throw "Set repository variable CIVIL3D_MCP_REPOSITORY to the local checkout path."
        }
        if (-not (Test-Path -LiteralPath $env:CIVIL3D_MCP_REPOSITORY -PathType Container)) {
            throw "Configured local repository directory does not exist."
        }
        if ($env:CIVIL3D_MCP_HTTP_TOKEN.Length -lt 32) {
            throw "Add secret CIVIL3D_MCP_HTTP_TOKEN with at least 32 random characters."
        }
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
            throw "The Civil 3D MCP server must run on Windows."
        }

        $sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
        if ($sessionId -eq 0) {
            throw "The self-hosted runner is in Session 0. Run the runner interactively in the logged-in Civil 3D user session."
        }
        $civil3d = Get-Process -Name acad -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $sessionId } |
            Select-Object -First 1
        if (-not $civil3d) {
            throw "Civil 3D (acad.exe) must be open in the same interactive session as the runner."
        }

        $existing = Get-ManagedServerProcess
        if ($existing) {
            if (-not (Test-ServerPort)) {
                throw "MCP process $($existing.Id) is running but its configured port is not listening."
            }
            Write-Output "ALREADY_RUNNING pid=$($existing.Id) port=$env:CIVIL3D_MCP_PORT"
            exit 0
        }

        $hostName = $env:CIVIL3D_MCP_HOST
        $portNumber = 0
        if (
            [string]::IsNullOrWhiteSpace($hostName) -or
            $hostName -match '[\s"]' -or
            -not [int]::TryParse($env:CIVIL3D_MCP_PORT, [ref]$portNumber) -or
            $portNumber -lt 1 -or $portNumber -gt 65535
        ) {
            throw "Set valid CIVIL3D_MCP_HOST and CIVIL3D_MCP_PORT repository variables."
        }

        $arguments = @(
            "-m", "civil3d_mcp", "serve",
            "--web-port", "-1",
            "--transport", "http",
            "--host", $hostName,
            "--port", "$portNumber"
        )
        if ($env:CIVIL3D_MCP_CERTFILE -or $env:CIVIL3D_MCP_KEYFILE) {
            if (
                [string]::IsNullOrWhiteSpace($env:CIVIL3D_MCP_CERTFILE) -or
                [string]::IsNullOrWhiteSpace($env:CIVIL3D_MCP_KEYFILE) -or
                -not (Test-Path -LiteralPath $env:CIVIL3D_MCP_CERTFILE -PathType Leaf) -or
                -not (Test-Path -LiteralPath $env:CIVIL3D_MCP_KEYFILE -PathType Leaf)
            ) {
                throw "TLS requires existing local certificate and key files."
            }
            $arguments += @(
                "--ssl-certfile", $env:CIVIL3D_MCP_CERTFILE,
                "--ssl-keyfile", $env:CIVIL3D_MCP_KEYFILE
            )
        }
        if ($env:CIVIL3D_MCP_ALLOWED_HOST) {
            if ($env:CIVIL3D_MCP_ALLOWED_HOST -match '[\s"]') {
                throw "CIVIL3D_MCP_ALLOWED_HOST must be a single hostname without whitespace."
            }
            $arguments += @("--allowed-host", $env:CIVIL3D_MCP_ALLOWED_HOST)
        }

        New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
        $quotedArguments = $arguments | ForEach-Object {
            '"' + ([string]$_).Replace('"', '\"') + '"'
        }
        $process = Start-Process `
            -FilePath $env:CIVIL3D_MCP_PYTHON `
            -ArgumentList ($quotedArguments -join " ") `
            -WorkingDirectory $env:CIVIL3D_MCP_REPOSITORY `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru
        Set-Content -LiteralPath $pidFile -Value $process.Id -NoNewline

        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 1
            $process.Refresh()
            if ($process.HasExited) {
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
                Get-Content -LiteralPath $stderrLog -Tail 40 -ErrorAction SilentlyContinue
                throw "MCP server exited during startup (code $($process.ExitCode))."
            }
            if (Test-ServerPort) {
                $ready = $true
                break
            }
        }
        if (-not $ready) {
            Stop-Process -Id $process.Id -Force
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            throw "MCP server did not listen on port $portNumber within 30 seconds."
        }
        Write-Output "STARTED pid=$($process.Id) port=$portNumber"
    }

    "stop" {
        $process = Get-ManagedServerProcess
        if (-not $process) {
            Write-Output "ALREADY_STOPPED"
            exit 0
        }
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(10000) | Out-Null
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Output "STOPPED pid=$($process.Id)"
    }
}
