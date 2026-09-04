# ww_p29c_read_log.ps1 -- P29-C: read + summarize the caller-chain trace (real).
# Reads ww_p29c_display_caller_trace.log from the writable roots, prints ONLY a short
# summary (not the thousands of potential lines), and exports the FULL trace to
# <repo-root>\p29c_target_trace.txt so Dorothy can paste it back to ChatGPT.
#
# Summary keys and their derivation (by scanning the log text, no game access):
#   P29C_MODULE_IMPORTED = YES if a P29C_MODULE_IMPORTED=YES boot marker line is found
#   HOOK_INSTALLED       = YES if a HOOK_TARGET=...get_display_name + HOOK_INSTALLED=YES
#                           line pair is found
#   TARGET_CALL_COUNT    = number of P29C_TARGET_CALL_BEGIN markers in the log
#   TARGET_TRACE_FOUND   = YES if any P29C_TARGET_CALL_BEGIN..P29C_TARGET_CALL_END block
#                           is present (i.e. a detailed caller trace was captured)
#   P29C_RESULT          = derived strictly:
#       no log at all, or log without P29C_MODULE_IMPORTED=YES -> MODULE_NOT_IMPORTED
#       imported but no HOOK_INSTALLED=YES                    -> HOOK_NOT_INSTALLED
#       imported+installed but 0 target blocks                -> TARGET_GET_DISPLAY_NAME_NOT_CALLED
#       imported+installed and >=1 target block              -> TARGET_CALLER_TRACE_CAPTURED
# ASCII-only; no 2>&1 / no &&.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$name       = "ww_p29c_display_caller_trace.log"
$repoRoot   = Split-Path $PSScriptRoot -Parent
$TRACE_OUT  = Join-Path $repoRoot "p29c_target_trace.txt"

$candidates = @(
    (Join-Path $env:TEMP $name),
    (Join-Path $env:TMP $name),
    (Join-Path ([System.Environment]::GetFolderPath('UserProfile')) $name),
    (Get-Location).Path
)
$found = $null
foreach ($p in $candidates) {
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        $found = $p
        break
    }
}

Write-Output "=== P29-C CALLER-CHAIN LOG ==="
if (-not $found) {
    Write-Output "P29C_MODULE_IMPORTED=NO"
    Write-Output "HOOK_INSTALLED=NO"
    Write-Output "TARGET_CALL_COUNT=0"
    Write-Output "TARGET_TRACE_FOUND=NO"
    Write-Output "P29C_RESULT=MODULE_NOT_IMPORTED"
    Write-Output "SEARCHED="
    foreach ($p in $candidates) { Write-Output "  $p" }
    Write-Output "(no log at all: the p29c module body never executed an import-time"
    Write-Output " statement in-game.  It was packaged but not auto-imported.  Confirm"
    Write-Output " the deployed ts4script layout == the working P29-TUNING layout, then"
    Write-Output " re-run deploy and launch the game so the import fires.)"
    exit 0
}

Write-Output "LOG_FILE=$found"
$lines = @(Get-Content -LiteralPath $found)

# ---- scan for the summary ----
$imported   = "NO"
$hooked     = "NO"
$hookTgtSeen = $false
$callCount  = 0
$thisBlk = $false
foreach ($ln in $lines) {
    if ($ln -like "P29C_MODULE_IMPORTED=YES") { $imported = "YES" }
    if ($ln -like "HOOK_TARGET=*get_display_name") { $hookTgtSeen = $true }
    if ($ln -like "HOOK_INSTALLED=YES") { $hooked = "YES" }
    if ($ln -eq "P29C_TARGET_CALL_BEGIN") {
        $callCount++
        $thisBlk = $true
    } elseif ($ln -eq "P29C_TARGET_CALL_END") {
        $thisBlk = $false
    }
}

$traceFound = "NO"
if ($callCount -gt 0) { $traceFound = "YES" }

if ($imported -ne "YES") {
    $result = "MODULE_NOT_IMPORTED"
} elseif ($hooked -ne "YES") {
    $result = "HOOK_NOT_INSTALLED"
} elseif ($callCount -eq 0) {
    $result = "TARGET_GET_DISPLAY_NAME_NOT_CALLED"
} else {
    $result = "TARGET_CALLER_TRACE_CAPTURED"
}

Write-Output "P29C_MODULE_IMPORTED=$imported"
Write-Output "HOOK_INSTALLED=$hooked"
Write-Output "HOOK_TARGET_SEEN=$(if ($hookTgtSeen) { 'YES' } else { 'NO' })"
Write-Output "TARGET_CALL_COUNT=$callCount"
Write-Output "TARGET_TRACE_FOUND=$traceFound"
Write-Output "P29C_RESULT=$result"

# ---- export the FULL trace (default: bounded tail count cap is NOT applied here;
# the in-game module already caps detailed traces at 30, so this file stays small;
# still cap rows defensively at 5000 to be safe. ----
$fullExport = $lines -join [Environment]::NewLine
if ($fullExport.Length -gt 0) {
    [System.IO.File]::WriteAllText($TRACE_OUT, $fullExport, [System.Text.Encoding]::UTF8)
    Write-Output "TARGET_TRACE_FILE=$TRACE_OUT"
    Write-Output "TARGET_TRACE_ROW_COUNT=$($lines.Count)"
} else {
    Write-Output "TARGET_TRACE_FILE=(empty-no-content)"
}

if ($traceFound -eq "YES") {
    Write-Output ""
    Write-Output "NEXT_STEP: send $TRACE_OUT back to ChatGPT.  The detailed caller"
    Write-Output " chain for get_display_name() returning 'TEST300' is in that file."
} else {
    Write-Output ""
    Write-Output "NEXT_STEP: no TEST300 get_display_name call was captured.  Check the"
    Write-Output " P28C deploy summary above (auto-deploy ran?) and that you opened a"
    Write-Output " picker panel that would call get_display_name() on the target row."
}
exit 0
