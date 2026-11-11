<#
.SYNOPSIS
    Install just-makeit (jm) on native Windows from PowerShell.

.DESCRIPTION
    The Windows twin of install.sh. The documented way in is jbx, the same
    line on every platform:

        jbx just-makeit:install

    which, from PowerShell, runs this file in the calling session. It can
    also be run on its own, with nothing but PowerShell:

        irm https://just-buildit.github.io/just-makeit/install.ps1 | iex

    It works from Windows PowerShell 5.1 and from PowerShell 7, and needs
    nothing but a Python 3.9 or newer. In order, it:

      1. Finds a Python: the `py` launcher (`py -3`), else `python` or
         `python3` on PATH, else the one `uv python find` reports when uv
         is installed. The first that runs and is new enough is used;
         below the floor the run stops naming the version to install.
      2. Creates a venv for jm, or reuses the one a previous run made:
         $env:LOCALAPPDATA\just-makeit\venv. Not install.sh's /tmp/jm-venv:
         Windows' temp folder is cleaned behind your back, and this venv is
         the jm you will come back to.
      3. Installs just-makeit into it with that venv's own `python -m pip`,
         unless the version there is already the latest: then nothing is
         downloaded or written ("already current").
      4. Copies the venv's console-script launchers (jm.exe,
         just-makeit.exe, ...: every one just-makeit declares, read from
         its entry points) into %USERPROFILE%\.local\bin, beside jbx. A
         pip launcher carries its venv python's absolute path, as a uv
         tool's does, so a copy runs from anywhere. A file there that is
         not a just-makeit launcher is never replaced: it is named, and
         the rest are copied.
      5. Puts the venv's Scripts folder FIRST on this session's PATH, then
         that folder, so `jm` works the moment the script ends; and when
         the folder is not on your user Path, says that `jbx update-path`
         keeps it there for new windows.
      6. Runs `jm --version` through that PATH, to prove it.
      7. Reports whether clang-cl and cmake are on PATH, which building a
         generated project needs. A missing one is a warning, not a
         failure: jm itself works without them. A Developer PowerShell for
         VS has both; jm does not install a compiler.

    Why the venv is put on PATH and NOT activated. A generated project
    makes its own `.venv` (`py -m venv .venv`, or `uv sync`), and the
    build is pointed at that one. An activated venv sets VIRTUAL_ENV,
    which makes `py` run the activated venv's Python instead of choosing
    one, and makes uv warn that the active environment is not the
    project's. Only PATH is touched, so neither happens.

    This script edits no profile and no user Path itself: `jbx
    update-path` does that, once. Without it, run the same line again in a
    new window to get `jm` back; with jm already current it only sets
    PATH, in a second or two.

    Safe to `iex`, and to dot-source as a script block (how jbx runs it,
    so the PATH change reaches the caller): nothing here calls `exit`, its
    helper functions and variables are removed again when it finishes, and
    a failure is one thrown error that names what to do -- your session
    stays open either way.

    A caller that sets $JmInstallDefineOnly = $true before dot-sourcing
    gets the functions and no install; that is how the tests reach them.
    It is a variable to set, not InvocationName -eq '.', because a
    dot-sourced script block is a real install.

    Options are environment variables, since `irm | iex` passes no
    arguments:

      JM_VENV          the venv to install into
                       (default $env:LOCALAPPDATA\just-makeit\venv)
      JM_FORCE         1 rebuilds the venv and reinstalls even when current
      JM_INSTALL_FROM  what to install instead of just-makeit from PyPI:
                       a wheel file, as CI does to test its own build
      JM_PYTHON        the Python to create the venv with; skips the search
      JM_BIN_DIR       where the launchers are copied
                       (default %USERPROFILE%\.local\bin; tests)

    install.sh's --check has no twin: `iex` has no exit status to carry a
    verdict in.

.EXAMPLE
    irm https://just-buildit.github.io/just-makeit/install.ps1 | iex

    Install, then `jm new my_project` in the same window.

.EXAMPLE
    $env:JM_VENV = "$HOME\jm-venv"
    irm https://just-buildit.github.io/just-makeit/install.ps1 | iex

    Install into a venv of your choosing.

.NOTES
    tests/install-ps1.windows.ps1 runs this file through `iex` and as a
    dot-sourced script block on Windows, in both PowerShell editions,
    against a wheel of the checkout.
#>

# Every name below is removed again when the script finishes (see the end
# of the file), because `irm | iex` defines them in the caller's session.

# The oldest Python just-makeit supports: pyproject.toml's
# `requires-python`. This file is fetched alone, with no pyproject.toml to
# read, so the floor is written here once;
# tests/test_gh2601_install_ps1.py holds it to pyproject's, as
# tests/test_gh1916_install_sh_floor.py does install.sh's.
$JmPyFloor = '3.9'
$JmPackage = 'just-makeit'

function Test-JmWindows {
    <#
    .SYNOPSIS
        True on Windows, in 5.1 (which has no $IsWindows) and in 7.
    #>
    [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
}

function Invoke-JmQuiet {
    <#
    .SYNOPSIS
        Run a native command; its stdout lines. stderr is discarded and
        $LASTEXITCODE is left for the caller to read.
    .DESCRIPTION
        Windows PowerShell 5.1 turns a redirected native stderr line into
        an error record, which the caller's 'Stop' would make terminating.
        So the preference is relaxed here, in this function's scope only.
    .EXAMPLE
        Invoke-JmQuiet -Command @('py', '-3') -Arguments @('-c', 'print(1)')
    #>
    param(
        [Parameter(Mandatory)][string[]]$Command,
        [string[]]$Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    $exe = $Command[0]
    $pre = @($Command | Select-Object -Skip 1)
    & $exe @pre @Arguments 2>$null
}

function Find-JmCommand {
    <#
    .SYNOPSIS
        The first program named Name on PATH, as a CommandInfo, or nothing.
    #>
    param([Parameter(Mandatory)][string]$Name)
    Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-JmVenvDir {
    <#
    .SYNOPSIS
        Where the venv goes: JM_VENV, else LOCALAPPDATA\just-makeit\venv
        (the XDG data directory off Windows).
    #>
    if ($env:JM_VENV) { return $env:JM_VENV }
    $base = $env:LOCALAPPDATA
    if (-not $base) { $base = Join-Path $HOME '.local/share' }
    Join-Path (Join-Path $base 'just-makeit') 'venv'
}

function Get-JmVenvBin {
    <#
    .SYNOPSIS
        A venv's executables folder: Scripts on Windows, bin elsewhere.
    #>
    param([Parameter(Mandatory)][string]$Venv)
    if (Test-JmWindows) { return (Join-Path $Venv 'Scripts') }
    Join-Path $Venv 'bin'
}

function Get-JmVenvPython {
    <#
    .SYNOPSIS
        A venv's python executable.
    #>
    param([Parameter(Mandatory)][string]$Venv)
    $exe = if (Test-JmWindows) { 'python.exe' } else { 'python' }
    Join-Path (Get-JmVenvBin -Venv $Venv) $exe
}

function Get-JmPythonCandidate {
    <#
    .SYNOPSIS
        The Pythons to try, each as a command (an array: the program, then
        any arguments), most preferred first.
    .DESCRIPTION
        JM_PYTHON alone, when set. Otherwise the `py` launcher, which picks
        the newest installed 3.x; then `python` and `python3` on PATH --
        including the Microsoft Store's stub, which opens the Store instead
        of running, and is left to fail the version probe; then the Python
        `uv python find` reports, for a machine whose only Python is uv's.
    #>
    if ($env:JM_PYTHON) { return , @($env:JM_PYTHON) }
    foreach ($name in 'py', 'python', 'python3') {
        $c = Find-JmCommand -Name $name
        if (-not $c) { continue }
        if ($name -eq 'py') { , @($c.Source, '-3') } else { , @($c.Source) }
    }
    $uv = Find-JmCommand -Name uv
    if ($uv) {
        $found = @(Invoke-JmQuiet -Command @($uv.Source) `
                -Arguments @('python', 'find'))
        if ($LASTEXITCODE -eq 0 -and $found.Count -gt 0 -and $found[0]) {
            , @([string]$found[0])
        }
    }
}

function Get-JmPythonVersion {
    <#
    .SYNOPSIS
        The [version] (major.minor) a Python command reports, or nothing
        when it does not run.
    #>
    param([Parameter(Mandatory)][string[]]$Command)
    # No double quote in it: Windows PowerShell 5.1 passes an embedded "
    # to a native program unescaped, so Python would read print(%d.%d ...)
    # and every Python would look absent. The same holds for every
    # argument this file hands a native command.
    $probe = 'import sys; print(''%d.%d'' % sys.version_info[:2])'
    # A path that does not exist is not a command at all: PowerShell
    # throws rather than setting an exit code.
    try {
        $out = @(Invoke-JmQuiet -Command $Command -Arguments @('-c', $probe))
    } catch [Management.Automation.CommandNotFoundException] { return }
    if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0) { return }
    $v = $null
    if ([version]::TryParse(([string]$out[-1]).Trim(), [ref]$v)) { $v }
}

function Test-JmPythonFloor {
    <#
    .SYNOPSIS
        True when Version is at least the floor jm supports.
    .EXAMPLE
        Test-JmPythonFloor -Version ([version]'3.12')
    #>
    param([Parameter(Mandatory)][version]$Version)
    $Version -ge [version]$JmPyFloor
}

function Find-JmPython {
    <#
    .SYNOPSIS
        The first candidate that runs and meets the floor, as
        @{ Command; Version }; throws naming what to install otherwise.
    #>
    $best = $null
    foreach ($c in @(Get-JmPythonCandidate)) {
        $v = Get-JmPythonVersion -Command $c
        if (-not $v) { continue }
        if (Test-JmPythonFloor -Version $v) {
            return [pscustomobject]@{ Command = $c; Version = $v }
        }
        if (-not $best -or $v -gt $best) { $best = $v }
    }
    if ($best) {
        throw ("install.ps1: Python $best found, but $JmPyFloor+ is " +
            'required. Install a newer Python ' +
            '(https://www.python.org/downloads/windows/) ' +
            'and re-run the one-liner.')
    }
    throw ("install.ps1: Python not found. Install Python $JmPyFloor+ " +
        '(https://www.python.org/downloads/windows/, which includes the py ' +
        'launcher) and re-run the one-liner.')
}

function Get-JmInstalledVersion {
    <#
    .SYNOPSIS
        The just-makeit version installed in a venv, or nothing.
    #>
    param([Parameter(Mandatory)][string]$Python)
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return }
    $probe = 'from importlib.metadata import version; ' +
        "print(version('$JmPackage'))"
    $out = @(Invoke-JmQuiet -Command @($Python) -Arguments @('-c', $probe))
    if ($LASTEXITCODE -eq 0 -and $out.Count -gt 0) {
        ([string]$out[-1]).Trim()
    }
}

function Get-JmTargetVersion {
    <#
    .SYNOPSIS
        The version an install would put in the venv, or nothing when it
        cannot be told without installing.
    .DESCRIPTION
        From a wheel (JM_INSTALL_FROM), its file name says it: the wheel
        format puts the version second, `just_makeit-0.1.0-py3-none-any.whl`.
        From PyPI, `pip index versions` asked through the venv's own pip,
        as install.sh asks -- so a pip configured for another index is
        answered by that index. Nothing means "install".
    .EXAMPLE
        Get-JmTargetVersion -From 'dist\just_makeit-0.1.0-py3-none-any.whl'
    #>
    param([string]$From, [string]$Python)
    if ($From) {
        $leaf = Split-Path -Leaf $From
        if ($leaf -match '^just_makeit-([^-]+)-.*\.whl$') {
            return $Matches[1]
        }
        return
    }
    if (-not $Python) { return }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return }
    $out = @(Invoke-JmQuiet -Command @($Python) -Arguments @('-m', 'pip',
            'index', 'versions', $JmPackage, '--disable-pip-version-check'))
    foreach ($line in $out) {
        if ([string]$line -match "^$JmPackage \(([0-9.]+)\)") {
            return $Matches[1]
        }
    }
}

function Add-JmPath {
    <#
    .SYNOPSIS
        Put Dir first on this session's PATH, once.
    .DESCRIPTION
        Any other copy of Dir is removed, so a re-run moves it to the
        front instead of growing PATH.
    .EXAMPLE
        Add-JmPath -Dir "$env:LOCALAPPDATA\just-makeit\venv\Scripts"
    #>
    param([Parameter(Mandatory)][string]$Dir)
    $sep = [IO.Path]::PathSeparator
    $want = $Dir.TrimEnd('\', '/')
    $rest = @($env:PATH -split [regex]::Escape($sep) |
            Where-Object { $_ -and ($_.TrimEnd('\', '/') -ne $want) })
    $env:PATH = (@($Dir) + $rest) -join $sep
}

function Get-JmBinDir {
    <#
    .SYNOPSIS
        Where the launchers go: JM_BIN_DIR, else ~\.local\bin, the
        folder jbx's installers put jbx in.
    #>
    if ($env:JM_BIN_DIR) { return $env:JM_BIN_DIR }
    Join-Path (Join-Path $HOME '.local') 'bin'
}

function Get-JmScriptName {
    <#
    .SYNOPSIS
        The console scripts the venv's just-makeit declares, by name.
    .DESCRIPTION
        Read from the installed distribution's entry points, so a command
        added to pyproject.toml is copied with no edit here. No double
        quote in the probe (see Get-JmPythonVersion).
    #>
    param([Parameter(Mandatory)][string]$Python)
    $probe = 'from importlib.metadata import distribution as d; ' +
        "print(chr(10).join(e.name for e in d('$JmPackage').entry_points " +
        "if e.group == 'console_scripts'))"
    @(Invoke-JmQuiet -Command @($Python) -Arguments @('-c', $probe) |
            ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
}

function Test-JmLauncher {
    <#
    .SYNOPSIS
        True when Path is a just-makeit launcher: a file jm put there.
    .DESCRIPTION
        A pip launcher holds the script it runs, `from just_makeit... import
        main`, as plain text (an .exe appends it uncompressed; a POSIX
        script is that text), so the bytes name the package. Anything else
        is the user's, and is never replaced.
    #>
    param([Parameter(Mandatory)][string]$Path)
    $bytes = [IO.File]::ReadAllBytes($Path)
    [Text.Encoding]::ASCII.GetString($bytes).Contains('just_makeit.')
}

function Copy-JmLauncher {
    <#
    .SYNOPSIS
        Copy each console-script launcher from the venv into Dir; the
        names it refused.
    .DESCRIPTION
        A launcher already identical is left untouched, so a re-run writes
        nothing; one jm wrote earlier is replaced; any other file of that
        name is left as it is and named.
    #>
    param(
        [Parameter(Mandatory)][string]$Venv,
        [Parameter(Mandatory)][string]$Dir
    )
    $ext = if (Test-JmWindows) { '.exe' } else { '' }
    $names = @(Get-JmScriptName -Python (Get-JmVenvPython -Venv $Venv))
    $copied = 0
    foreach ($name in $names) {
        $src = Join-Path (Get-JmVenvBin -Venv $Venv) "$name$ext"
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
        if (-not (Test-Path -LiteralPath $Dir)) {
            New-Item -ItemType Directory -Path $Dir -Force | Out-Null
        }
        $dest = Join-Path $Dir "$name$ext"
        if (Test-Path -LiteralPath $dest) {
            if (-not (Test-JmLauncher -Path $dest)) {
                Write-Warning "$dest is not a launcher jm made; left as it is"
                $name
                continue
            }
            $a = [Convert]::ToBase64String([IO.File]::ReadAllBytes($src))
            $b = [Convert]::ToBase64String([IO.File]::ReadAllBytes($dest))
            if ($a -eq $b) { $copied++; continue }
        }
        Copy-Item -LiteralPath $src -Destination $dest -Force
        $copied++
    }
    Write-Information "  ok  $copied launcher(s) in $Dir"
}

function Test-JmOnUserPath {
    <#
    .SYNOPSIS
        True when Dir is on the user Path new windows start with. Off
        Windows there is no such thing, and the answer is this session's.
    #>
    param([Parameter(Mandatory)][string]$Dir)
    $path = $env:PATH
    if (Test-JmWindows) {
        $path = [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    $want = $Dir.TrimEnd('\', '/')
    $sep = [regex]::Escape([IO.Path]::PathSeparator)
    $hits = @([string]$path -split $sep |
            Where-Object { $_ -and $_.TrimEnd('\', '/') -eq $want })
    $hits.Count -gt 0
}

function Write-JmToolchain {
    <#
    .SYNOPSIS
        Report clang-cl and cmake: found, or a warning naming the fix.
    .DESCRIPTION
        Checked, never installed. Building a generated project on Windows
        needs both, and a Developer PowerShell for VS has both; installing
        Visual Studio's C++ tools is the user's step, as docs/windows.md
        says (#2601: a third copy of just-bashit's Visual Studio logic is
        not what this script is for).
    #>
    $missing = @()
    foreach ($tool in 'clang-cl', 'cmake') {
        $c = Find-JmCommand -Name $tool
        if ($c) { Write-Information "  ok  $tool ($($c.Source))" }
        else { $missing += $tool }
    }
    if ($missing.Count -gt 0) {
        Write-Warning ("$($missing -join ' and ') not on PATH: jm is " +
            'installed, ' +
            'but building a project needs them. Open a Developer PowerShell ' +
            'for VS (Visual Studio 2022+ with the C++ workload and its ' +
            'clang-cl component) and re-run the one-liner there; see ' +
            'https://just-buildit.github.io/just-makeit/windows/' +
            '#what-you-need')
    }
}

function Install-JustMakeit {
    <#
    .SYNOPSIS
        The whole install; see the script's own help.
    #>
    # Scoped to this function, so `iex` leaves the caller's settings alone.
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $InformationPreference = 'Continue'

    $venv = Get-JmVenvDir
    $venvPy = Get-JmVenvPython -Venv $venv
    $bin = Get-JmVenvBin -Venv $venv
    $from = $env:JM_INSTALL_FROM
    $force = $env:JM_FORCE -eq '1'
    $spec = if ($from) { $from } else { $JmPackage }

    # -- 1. Python ----------------------------------------------------------
    $py = Find-JmPython
    Write-Information "  ok  Python $($py.Version) ($($py.Command -join ' '))"

    # -- 2. Is the venv's just-makeit already current? ------------------------
    $installed = $null
    $current = $false
    if (-not $force) {
        $installed = Get-JmInstalledVersion -Python $venvPy
        if ($installed) {
            $target = Get-JmTargetVersion -From $from -Python $venvPy
            $current = $installed -eq $target
        }
    }

    # -- 3. Create the venv and install --------------------------------------
    if ($current) {
        Write-Information ("  ok  just-makeit $installed in $venv " +
            '(already current)')
    } else {
        $was = if ($installed) { $installed } else { 'not installed' }
        Write-Information "  ->  just-makeit ($was) -> $spec in $venv"
        # A venv whose python no longer runs (its base Python was removed)
        # is rebuilt, as is any venv under JM_FORCE; a working one is
        # upgraded in place.
        if ($force -or -not (Get-JmPythonVersion -Command @($venvPy))) {
            $py0 = $py.Command[0]
            $pyArgs = @($py.Command | Select-Object -Skip 1)
            & $py0 @pyArgs -m venv --clear $venv
            if ($LASTEXITCODE -ne 0) {
                $cmd = $py.Command -join ' '
                throw ("install.ps1: '$cmd -m venv' could not create " +
                    "$venv (exit $LASTEXITCODE); its output above " +
                    'says why. Set JM_VENV to another folder to use one.')
            }
        }
        $pipArgs = @('-m', 'pip', 'install', '--quiet', '--upgrade',
            '--disable-pip-version-check')
        if ($force) { $pipArgs += '--force-reinstall' }
        & $venvPy @pipArgs $spec
        if ($LASTEXITCODE -ne 0) {
            throw ("install.ps1: pip could not install $spec into $venv " +
                "(exit $LASTEXITCODE); its output above says why.")
        }
        # A pip that "succeeds" into a mismatched tree would otherwise be
        # reported ok with no version.
        $installed = Get-JmInstalledVersion -Python $venvPy
        if (-not $installed) {
            throw ('install.ps1: pip finished, but just-makeit does not ' +
                'import ' +
                "from $venv. Re-run with `$env:JM_FORCE = '1' to rebuild it.")
        }
        Write-Information "  ok  just-makeit $installed in $venv"
    }

    # -- 4. The launchers, PATH, and the proof -----------------------------
    $local = Get-JmBinDir
    $refused = @(Copy-JmLauncher -Venv $venv -Dir $local)
    # Venv first: a refused name in $local must not shadow the venv's jm.
    Add-JmPath -Dir $local
    Add-JmPath -Dir $bin
    $jm = Find-JmCommand -Name jm
    $ours = $jm -and $jm.Source.StartsWith($bin,
        [StringComparison]::OrdinalIgnoreCase)
    if (-not $ours) {
        throw ("install.ps1: $bin is first on PATH but `jm` resolves to " +
            "'$(if ($jm) { $jm.Source })'. Look for a jm earlier on PATH.")
    }
    $said = @(Invoke-JmQuiet -Command @($jm.Source) -Arguments @('--version'))
    if ($LASTEXITCODE -ne 0) {
        throw ('install.ps1: jm was installed but did not run ' +
            "(exit $LASTEXITCODE).")
    }
    Write-Information "  ok  jm $(($said -join ' ').Trim()) ($($jm.Source))"

    # -- 5. The toolchain a build needs -------------------------------------
    Write-JmToolchain

    Write-Information ''
    Write-Information '  jm is ready in this window:'
    Write-Information ''
    Write-Information '    jm new my_project --object my_object'
    Write-Information ''
    if ($refused.Count -gt 0) {
        Write-Information ("  $($refused -join ', ') not copied to $local " +
            '(see the warning above).')
    }
    if (-not (Test-JmOnUserPath -Dir $local)) {
        Write-Information "  $local is not on your user Path, so new windows"
        Write-Information '  will not find jm. To keep it, run once:'
        Write-Information ''
        Write-Information '    jbx update-path'
        Write-Information ''
        Write-Information '  or run the install line again in each new window.'
    }
    Write-Information ''
}

# A caller that asks for the functions alone gets them and stops here,
# which is how the tests reach them without installing anything. Not
# `InvocationName -eq '.'`: jbx dot-sources this file's text to install.
if (Get-Variable -Name JmInstallDefineOnly -ValueOnly -ErrorAction Ignore) {
    return
}

try {
    Install-JustMakeit
} finally {
    # `irm | iex` runs in the caller's scope: leave nothing behind but PATH,
    # as install.sh unsets its helpers.
    # A pipeline, not foreach: a loop variable would be left behind too.
    'Test-JmWindows', 'Invoke-JmQuiet', 'Find-JmCommand', 'Get-JmVenvDir',
    'Get-JmVenvBin', 'Get-JmVenvPython', 'Get-JmPythonCandidate',
    'Get-JmPythonVersion', 'Test-JmPythonFloor', 'Find-JmPython',
    'Get-JmInstalledVersion', 'Get-JmTargetVersion', 'Add-JmPath',
    'Get-JmBinDir',
    'Get-JmScriptName', 'Test-JmLauncher', 'Copy-JmLauncher',
    'Test-JmOnUserPath', 'Write-JmToolchain',
    'Install-JustMakeit' | ForEach-Object {
        Remove-Item -Path "Function:\$_" -ErrorAction SilentlyContinue
    }
    Remove-Variable -Name JmPyFloor, JmPackage -ErrorAction SilentlyContinue
}
