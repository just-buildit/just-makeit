<#
.SYNOPSIS
    install.ps1 end to end, in whichever PowerShell runs this.

.DESCRIPTION
    CI's Windows examples job runs this twice, with pwsh 7 and with Windows
    PowerShell 5.1: the one-liner the docs give a Windows reader is
    `irm .../install.ps1 | iex`, and either edition may be the one they
    type it into.

    It installs through `iex`, the way a reader without jbx does, with install.ps1 taken
    from this checkout and just-makeit from a wheel of it (JM_INSTALL_FROM:
    the single dist\just_makeit-*.whl, which the job builds with `make
    wheel`), into a scratch venv (JM_VENV), then proves:

      - `jm` resolves to an Application inside that venv, and
        `jm --version` runs and exits 0 -- in THIS session, which is the
        property a pip or uv tool install did not give (#2601);
      - the venv's Scripts folder is first on PATH, and VIRTUAL_ENV is
        still unset: the venv is on PATH, never activated;
      - `iex` left no function and no variable behind, by a before/after
        diff of the session, so a helper added later is held too;
      - a second run is a no-op: it says "already current" and writes
        nothing to the venv, and PATH holds the folder once;
      - run as jbx runs it -- the file's text dot-sourced as a script
        block -- it installs too, and its PATH change reaches the caller;
      - a Python below the floor is refused naming the floor, and the
        session is left as it was (the floor itself is held to
        pyproject.toml by tests/test_gh2601_install_ps1.py).

    Every failure throws, so the step fails naming the check.
#>
# `irm | iex` is the install this test exists to prove, so the analyzer's
# advice against Invoke-Expression is declined here, by name, for this file.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingInvokeExpression', '',
    Justification = 'iex is the user-facing install path under test')]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InformationPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root 'install.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ('jm-installps1-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
Write-Information "=== $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)"

function Assert-That {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Information "ok  $Message"
}

$wheels = @(Get-ChildItem -Path (Join-Path $root 'dist') -Filter 'just_makeit-*.whl' -ErrorAction SilentlyContinue)
Assert-That ($wheels.Count -eq 1) "dist holds exactly one just-makeit wheel to install (got $($wheels.Count); run make wheel)"
$env:JM_INSTALL_FROM = $wheels[0].FullName
$venv = Join-Path $work 'venv'
$env:JM_VENV = $venv
Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$bin = Join-Path $venv $(if ($onWindows) { 'Scripts' } else { 'bin' })
$sep = [IO.Path]::PathSeparator

# What the session holds before, so whatever iex leaves is a diff, not a
# list of names this test would have to keep in step with install.ps1.
# The automatic variables a command line sets are not install.ps1's.
$auto = @('?', '^', '$', '_', 'args', 'input', 'LASTEXITCODE', 'Matches',
    'MyInvocation', 'PSItem', 'StackTrace', 'Error', 'foreach',
    'PSCmdlet', 'PSBoundParameters', 'this')
$funcs0 = @(Get-ChildItem Function: | ForEach-Object { $_.Name })
$vars0 = @(Get-Variable -Scope Script | ForEach-Object { $_.Name })

function Get-Leftover {
    param([string[]]$Funcs, [string[]]$Vars)
    # ForEach-Object, not .Name: under StrictMode an empty result has no
    # .Name to read.
    $f = @(Get-ChildItem Function: | ForEach-Object { $_.Name } |
            Where-Object { $Funcs -notcontains $_ })
    $v = @(Get-Variable -Scope Script | ForEach-Object { $_.Name } |
            Where-Object { $Vars -notcontains $_ -and $auto -notcontains $_ })
    @($f + $v)
}
$funcs0 += 'Get-Leftover'
$vars0 += 'funcs0', 'vars0', 'log', 'left'

# -- first run: installs ------------------------------------------------------
$log = Get-Content -Raw -LiteralPath $script | Invoke-Expression 6>&1 | Out-String
Write-Information $log
$left = @(Get-Leftover -Funcs $funcs0 -Vars $vars0)
Assert-That ($left.Count -eq 0) "iex left nothing behind (got: $($left -join ', '))"

$jm = Get-Command jm -ErrorAction SilentlyContinue | Select-Object -First 1
Assert-That ([bool]$jm) 'jm resolves in this session'
Assert-That ($jm.CommandType -eq 'Application') "jm is an Application (got $($jm.CommandType))"
Assert-That ($jm.Source.StartsWith($bin, [StringComparison]::OrdinalIgnoreCase)) "jm is the venv's ($($jm.Source) under $bin)"
$ver = jm --version
Assert-That ($LASTEXITCODE -eq 0) "jm --version exits 0 (got $LASTEXITCODE)"
Write-Information "    jm --version: $ver"
$want = ($wheels[0].Name -split '-')[1]
Assert-That ("$ver".Trim() -eq $want) "jm --version is the wheel's $want (got '$ver')"
Assert-That (($env:PATH -split [regex]::Escape($sep))[0] -eq $bin) 'the venv Scripts folder is first on PATH'
Assert-That (-not (Test-Path Env:VIRTUAL_ENV)) 'VIRTUAL_ENV is unset: the venv is on PATH, not activated'

# -- second run: already current, writes nothing --------------------------------
$vars1 = @(Get-Variable -Scope Script | ForEach-Object { $_.Name }) +
    'vars1', 'stamp'
$stamp = @(Get-ChildItem -LiteralPath $venv -Recurse -File |
        ForEach-Object { "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)" })
$log = Get-Content -Raw -LiteralPath $script | Invoke-Expression 6>&1 | Out-String
Write-Information $log
Assert-That ($log -match 'already current') "a second run says already current (got '$log')"
$stamp2 = @(Get-ChildItem -LiteralPath $venv -Recurse -File |
        ForEach-Object { "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)" })
$changed = @(Compare-Object $stamp $stamp2)
Assert-That ($changed.Count -eq 0) "a second run writes nothing in the venv (changed: $(($changed | ForEach-Object { $_.InputObject }) -join '; '))"
$copies = @($env:PATH -split [regex]::Escape($sep) | Where-Object { $_ -eq $bin })
Assert-That ($copies.Count -eq 1) "PATH holds the venv once after two runs (got $($copies.Count))"
$left = @(Get-Leftover -Funcs $funcs0 -Vars ($vars1 + 'stamp2', 'changed', 'copies'))
Assert-That ($left.Count -eq 0) "the second run left nothing behind (got: $($left -join ', '))"

# -- third run: as jbx runs it, a dot-sourced script block ----------------------
# jbx's PowerShell function runs install.ps1's text dot-sourced, so the PATH
# change reaches its caller. With the venv taken off PATH first, jm must
# resolve again afterwards, and the run must leave nothing behind.
$vars2 = @(Get-Variable -Scope Script | ForEach-Object { $_.Name }) +
    'vars2', 'block'
$env:PATH = (@($env:PATH -split [regex]::Escape($sep) |
            Where-Object { $_ -ne $bin }) -join $sep)
$block = [scriptblock]::Create((Get-Content -Raw -LiteralPath $script))
$log = . $block 6>&1 | Out-String
Write-Information $log
Assert-That ($log -match 'already current') 'a dot-sourced script block runs the install (already current)'
$jm = Get-Command jm -ErrorAction SilentlyContinue | Select-Object -First 1
Assert-That ($jm -and $jm.Source.StartsWith($bin, [StringComparison]::OrdinalIgnoreCase)) 'its PATH change reaches the caller: jm resolves to the venv again'
$left = @(Get-Leftover -Funcs $funcs0 -Vars $vars2)
Assert-That ($left.Count -eq 0) "the dot-sourced run left nothing behind (got: $($left -join ', '))"

# -- below the floor: refused, naming it ----------------------------------------
# The floor is read from install.ps1 itself, dot-sourced with
# $JmInstallDefineOnly set (which defines its functions and installs
# nothing); the Python is a stub reporting the minor below it.
$JmInstallDefineOnly = $true
. $script
Remove-Variable -Name JmInstallDefineOnly
$floor = [version]$JmPyFloor
Assert-That (Test-JmPythonFloor -Version $floor) "Test-JmPythonFloor accepts the floor $floor"
$below = [version]"$($floor.Major).$($floor.Minor - 1)"
Assert-That (-not (Test-JmPythonFloor -Version $below)) "Test-JmPythonFloor refuses $below"
if ($onWindows) {
    $stub = Join-Path $work 'oldpy.cmd'
    Set-Content -LiteralPath $stub -Value "@echo $below" -Encoding ASCII
} else {
    $stub = Join-Path $work 'oldpy'
    Set-Content -LiteralPath $stub -Value "#!/bin/sh`necho $below"
    & chmod +x $stub
}
$pathBefore = $env:PATH
$env:JM_PYTHON = $stub
$threw = ''
try { Get-Content -Raw -LiteralPath $script | Invoke-Expression 6>&1 | Out-Null } catch { $threw = "$_" }
Remove-Item Env:JM_PYTHON
Assert-That ($threw -like "*Python $below found, but $floor+ is required*") "a Python below the floor is refused naming it (got '$threw')"
Assert-That ($env:PATH -eq $pathBefore) 'a refused run leaves PATH as it was'

Remove-Item -Recurse -Force -LiteralPath $work
Write-Information "=== install.ps1 passed on $($PSVersionTable.PSEdition)"
exit 0
