<#
.SYNOPSIS
    Set up build dependencies for just-makeit projects on Windows.

.DESCRIPTION
    Installs cmake and a C compiler, then creates a Python venv with
    numpy and just-makeit.

    Package manager detection order:
        1. MSYS2   (MSYSTEM env var set, or pacman.exe in PATH)
        2. winget
        3. Chocolatey  (choco)
        4. Scoop
        5. Direct download via Invoke-WebRequest (final fallback)

.PARAMETER VenvDir
    Path for the Python virtual environment.
    Default: $env:LOCALAPPDATA\jm-venv

.EXAMPLE
    .\install-deps.ps1
    .\install-deps.ps1 C:\my-venv
#>
param(
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA "jm-venv")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Info  { param($m) Write-Host "==> $m" -ForegroundColor Cyan }
function Ok    { param($m) Write-Host "    ok  $m" -ForegroundColor Green }
function Warn  { param($m) Write-Host "  warn  $m" -ForegroundColor Yellow }
function Die   { param($m) Write-Host " error  $m" -ForegroundColor Red; exit 1 }

function Find-Exe {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# ── Expand-Archive compat (PS 3/4 lack the cmdlet) ───────────────────────────

function Expand-ZipCompat {
    param([string]$ZipPath, [string]$DestPath)
    if (Get-Command Expand-Archive -ErrorAction SilentlyContinue) {
        Expand-Archive -Path $ZipPath -DestinationPath $DestPath -Force
    } else {
        # PS 3/4 fallback via .NET
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $DestPath)
    }
}

# ── Direct-download fallbacks ─────────────────────────────────────────────────

function Install-CmakeDirect {
    Info "Downloading cmake (Kitware release zip)"
    $url  = "https://github.com/Kitware/CMake/releases/download/v3.29.3/cmake-3.29.3-windows-x86_64.zip"
    $dest = Join-Path $env:LOCALAPPDATA "jm-cmake"
    $zip  = Join-Path $dest "cmake.zip"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-ZipCompat -ZipPath $zip -DestPath $dest
    Remove-Item $zip -Force
    $bin = Get-ChildItem -Path $dest -Filter "bin" -Recurse -Directory |
               Select-Object -First 1 -ExpandProperty FullName
    if ($bin) {
        $env:PATH = "$bin;$env:PATH"
        [System.Environment]::SetEnvironmentVariable(
            "PATH", $env:PATH, "User")
        Ok "cmake installed to $bin (added to user PATH)"
    } else {
        Warn "cmake extracted but bin dir not found — add it to PATH manually"
    }
}

function Install-MinGWDirect {
    Info "Downloading MinGW-w64 (winlibs ucrt)"
    # winlibs UCRT build — self-contained, no installer needed
    $url  = "https://github.com/brechtsanders/winlibs_mingw/releases/download/" +
            "14.2.0posix-19.1.1-12.0.0-ucrt-r2/" +
            "winlibs-x86_64-posix-seh-gcc-14.2.0-mingw-w64ucrt-12.0.0-r2.zip"
    $dest = Join-Path $env:LOCALAPPDATA "jm-mingw"
    $zip  = Join-Path $dest "mingw.zip"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-ZipCompat -ZipPath $zip -DestPath $dest
    Remove-Item $zip -Force
    $bin = Get-ChildItem -Path $dest -Filter "bin" -Recurse -Directory |
               Select-Object -First 1 -ExpandProperty FullName
    if ($bin) {
        $env:PATH = "$bin;$env:PATH"
        [System.Environment]::SetEnvironmentVariable(
            "PATH", $env:PATH, "User")
        Ok "MinGW-w64 installed to $bin (added to user PATH)"
    } else {
        Warn "MinGW-w64 extracted but bin dir not found — add it to PATH manually"
    }
}

# ── System-dep installer ──────────────────────────────────────────────────────

$NeedCmake = -not (Find-Exe "cmake")
$NeedCC    = -not ((Find-Exe "gcc") -or (Find-Exe "clang") -or (Find-Exe "cl"))

if (-not $NeedCmake) { Ok "cmake $(cmake --version | Select-Object -First 1)" }
if (-not $NeedCC)    { Ok "C compiler found" }

if ($NeedCmake -or $NeedCC) {

    # 1. MSYS2 ─────────────────────────────────────────────────────────────────
    if ($env:MSYSTEM -or (Find-Exe "pacman")) {
        Info "MSYS2 / pacman"
        $pkgs = @()
        if ($NeedCmake) { $pkgs += "mingw-w64-x86_64-cmake" }
        if ($NeedCC)    { $pkgs += "mingw-w64-x86_64-gcc" }
        if ($pkgs) { & pacman -Sy --noconfirm @pkgs }
    }

    # 2. winget ────────────────────────────────────────────────────────────────
    elseif (Find-Exe "winget") {
        Info "winget"
        if ($NeedCmake) {
            & winget install --id Kitware.CMake -e --silent --accept-package-agreements
        }
        if ($NeedCC) {
            # LLVM/clang is the most reliably packaged compiler via winget
            & winget install --id LLVM.LLVM -e --silent --accept-package-agreements
        }
    }

    # 3. Chocolatey ────────────────────────────────────────────────────────────
    elseif (Find-Exe "choco") {
        Info "Chocolatey"
        $pkgs = @()
        if ($NeedCmake) { $pkgs += "cmake" }
        if ($NeedCC)    { $pkgs += "mingw" }
        if ($pkgs) { & choco install -y @pkgs }
    }

    # 4. Scoop ─────────────────────────────────────────────────────────────────
    elseif (Find-Exe "scoop") {
        Info "Scoop"
        if ($NeedCmake) { & scoop install cmake }
        if ($NeedCC)    { & scoop install gcc }
    }

    # 5. Direct download fallback ──────────────────────────────────────────────
    else {
        Warn "No package manager found — falling back to direct download."
        if ($NeedCmake) { Install-CmakeDirect }
        if ($NeedCC)    { Install-MinGWDirect }
    }
}

# ── Venv ──────────────────────────────────────────────────────────────────────

Info "Creating venv at $VenvDir"
$python = if (Find-Exe "python") { "python" } `
          elseif (Find-Exe "python3") { "python3" } `
          else { Die "python not found — install Python 3.11+ first" }

& $python -m venv $VenvDir
Ok "venv created"

$pip    = Join-Path $VenvDir "Scripts\pip.exe"
$python = Join-Path $VenvDir "Scripts\python.exe"

Info "Installing numpy and just-makeit"
& $pip install --quiet --upgrade pip
& $pip install --quiet numpy just-makeit

$npVer = & $python -c "import numpy; print(numpy.__version__)"
Ok "numpy $npVer"
Ok "just-makeit installed"

# ── Done ──────────────────────────────────────────────────────────────────────

$activate = Join-Path $VenvDir "Scripts\Activate.ps1"
Write-Host ""
Info "Done. Activate the venv with:"
Write-Host ""
Write-Host "    . $activate" -ForegroundColor White
Write-Host ""
