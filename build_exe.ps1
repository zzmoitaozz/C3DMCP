$ErrorActionPreference = "Stop"

# Isolated build venv: global Pythons may contain backports that break PyInstaller.
$basePython = if ($env:BUILD_PYTHON) { $env:BUILD_PYTHON } else { "python" }
$venv = Join-Path $PSScriptRoot ".buildvenv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    & $basePython -m venv $venv
    & $python -m pip install --upgrade pip
    & $python -m pip install fastmcp pywin32 pythonnet pyinstaller
}

& $python -m PyInstaller --noconfirm --clean --onefile `
    --name civil3d-mcp `
    --paths (Join-Path $PSScriptRoot "src") `
    --collect-all fastmcp `
    --collect-all pythonnet `
    --collect-submodules civil3d_mcp `
    --hidden-import win32com.client `
    --hidden-import pythoncom `
    --hidden-import pywintypes `
    (Join-Path $PSScriptRoot "src\civil3d_mcp\__main__.py")

Write-Host "Built: $PSScriptRoot\dist\civil3d-mcp.exe"
