# ww_p29a_build_on_win.ps1 -- P29-A: build ww_p29a_debug.ts4script under the GAME python.
#
# Why game python:
#   A *.ts4script must contain *.pyc bytecode whose header magic matches the python
#   embedded by The Sims 4.  The tooling interpreter on Windows may be a different
#   version, so we compile with the game's own python when we can find it.  If we
#   cannot, we FAIL-CLOSED and ask you to pass -GamePython <path> -- we never guess
#   and produce a pyc that the game will silently refuse.
#
# Search order for game python:
#   1. -GamePython <exe|dir>   (explicit)
#   2. env WW_GAME_PYTHON
#   3. common install roots scanning for python*.dll under <GameRoot>\Game\Bin\Python
#      and <GameRoot>\Bin\Python  (we look only for an exe we can run), using the
#      Origin/EA app default The Sims 4 dir guess if present.
#
# Then it runs, under that interpreter:
#   scripts\ww_p29a_build_ts4script.py --src scripts\ww_p29a_mod.py --out dist\ww_p29a_debug.ts4script
# and validates the member imports on the SAME interpreter (self-consistent).
#
# Outputs: VERDICT=OK|FAIL, OUT=<path>, PY_VER=<x.y>, REASON.
# ASCII-only logic.  $PyArgs not $Args (PS5.1).  Run-Python wrapper (no 2>&1).
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

param(
    [string]$GamePython = ""
)

$WORKSPACE = "D:\projects\sims4_trans"
$SRC_MOD   = Join-Path $WORKSPACE "scripts\ww_p29a_mod.py"
$BUILDER   = Join-Path $WORKSPACE "scripts\ww_p29a_build_ts4script.py"
$OUT_DIR   = Join-Path $WORKSPACE "dist"
$OUT_TS4   = Join-Path $OUT_DIR "ww_p29a_debug.ts4script"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

function Resolve-GamePython {
    param([string]$Hint)
    if ($Hint) {
        if (Test-Path -LiteralPath $Hint -PathType Leaf) { return (Resolve-Path -LiteralPath $Hint).Path }
        # a directory containing a python exe
        foreach ($exe in @("python.exe","python3.exe","python3.7.exe")) {
            $c = Join-Path $Hint $exe
            if (Test-Path -LiteralPath $c) { return (Resolve-Path -LiteralPath $c).Path }
        }
        Fail "GamePython hint not found: $Hint"
    }
    if ($env:WW_GAME_PYTHON) {
        $e = $env:WW_GAME_PYTHON
        if (Test-Path -LiteralPath $e) { return (Resolve-Path -LiteralPath $e).Path }
    }
    # common roots
    $roots = @(
        "C:\Program Files (x86)\Electronic Arts\The Sims 4",
        "C:\Program Files\Electronic Arts\The Sims 4",
        "$env:ProgramFiles\EA Games\The Sims 4",
        "$env:USERPROFILE\Documents\Electronic Arts\The Sims 4"
    )
    foreach ($root in $roots) {
        foreach ($sub in @("Game\Bin\Python", "Bin\Python", "Game\Bin")) {
            $cand = Join-Path $root $sub
            if (Test-Path -LiteralPath $cand) {
                foreach ($exe in @("python.exe","python3.exe","python3.7.exe","python-3.7.exe")) {
                    $c = Join-Path $cand $exe
                    if (Test-Path -LiteralPath $c) { return (Resolve-Path -LiteralPath $c).Path }
                }
            }
        }
    }
    Fail "Could not locate the game python automatically. Pass -GamePython <path-to-game-python.exe> or set WW_GAME_PYTHON."
}

Write-Output "=== P29A BUILD-ON-WIN ==="
if (-not (Test-Path -LiteralPath $SRC_MOD)) { Fail "mod source missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $BUILDER)) { Fail "builder missing: $BUILDER" }

$gp = Resolve-GamePython -Hint $GamePython
Write-Output "GAME_PYTHON=$gp"

function Run-GamePython {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29a_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $gp $Script @PyArgs 2> $stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $errText = ""
    if (Test-Path -LiteralPath $stderrFile) { $errText = Get-Content -LiteralPath $stderrFile -Raw; Remove-Item -LiteralPath $stderrFile -Force }
    return ,@($code, @($out), $errText)
}

$r = Run-GamePython -Script $BUILDER -PyArgs @("--src", $SRC_MOD, "--out", $OUT_TS4)
if ($r[0] -ne 0) {
    Write-Output "PY_STDERR=$($r[2])"
    Fail "BUILD_FAIL(exit $($r[0]))"
}
Write-Output (($r[1] | Out-String).Trim())
if (-not (Test-Path -LiteralPath $OUT_TS4)) { Fail "BUILD produced no output" }
Write-Output "VERDICT=OK"
Write-Output "PY_VER=$(& $gp -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])' 2>$null)"
Write-Output "OUT=$OUT_TS4"
exit 0
