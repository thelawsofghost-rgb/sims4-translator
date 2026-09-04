# ww_p28c_rollback.ps1 -- P28C rollback (Windows real machine)
# Strictly reverts ww_p28c_deploy.ps1 effects only:
#   1. Delete P28C_Overrides\WW_P28C_TEST300_Override.package
#   2. Delete P28C_Overrides if empty
#   3. Restore Resource.cfg from Resource.cfg.p28c_backup (only if current cfg contains P28C_Overrides marker)
#   4. Verify restored cfg SHA == backup SHA
#   5. Delete localthumbcache.package if present
# Never touches any other mod / source WW package / other P27/P28A/P28B artifacts.
# ASCII-only.
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS          = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$RESOURCE_CFG  = Join-Path $MODS "Resource.cfg"
$CFG_BACKUP    = Join-Path $MODS "Resource.cfg.p28c_backup"
$OVERRIDE_DIR  = Join-Path $MODS "P28C_Overrides"
$OVERRIDE_PKG  = Join-Path $OVERRIDE_DIR "WW_P28C_TEST300_Override.package"
$CACHE         = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

function Fail($reason) { Write-Output "VERDICT=ROLLBACK_FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

Write-Output "=== P28C ROLLBACK ==="

if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup missing: $CFG_BACKUP" }
$backupSha = Sha256 $CFG_BACKUP
Write-Output "CFG_BACKUP_SHA=$backupSha"

# 1. delete override package
if (Test-Path -LiteralPath $OVERRIDE_PKG) {
    Remove-Item -LiteralPath $OVERRIDE_PKG -Force
    if (Test-Path -LiteralPath $OVERRIDE_PKG) { Fail "override pkg delete failed" }
    Write-Output "OVERRIDE_PKG=DELETED"
} else {
    Write-Output "OVERRIDE_PKG=NOT_PRESENT"
}

# 2. delete empty dir
if ((Test-Path -LiteralPath $OVERRIDE_DIR) -and -not (Get-ChildItem -LiteralPath $OVERRIDE_DIR -Force | Select-Object -First 1)) {
    Remove-Item -LiteralPath $OVERRIDE_DIR -Force
    if (Test-Path -LiteralPath $OVERRIDE_DIR) { Fail "override dir delete failed" }
    Write-Output "OVERRIDE_DIR=DELETED(empty)"
} else {
    Write-Output "OVERRIDE_DIR=KEPT(non-empty or absent)"
}

# 3. restore cfg only if current cfg has our marker
if (Test-Path -LiteralPath $RESOURCE_CFG) {
    $cur = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($cur -match "P28C_Overrides") {
        Copy-Item -LiteralPath $CFG_BACKUP -Destination $RESOURCE_CFG -Force
        Write-Output "RESOURCE_CFG=RESTORED"
    } else {
        Write-Output "RESOURCE_CFG=UNCHANGED (no P28C_Overrides marker)"
    }
} else {
    Copy-Item -LiteralPath $CFG_BACKUP -Destination $RESOURCE_CFG -Force
    Write-Output "RESOURCE_CFG=RESTORED(missing was restored)"
}

# 4. verify restored SHA
if (Test-Path -LiteralPath $RESOURCE_CFG) {
    $restoredSha = Sha256 $RESOURCE_CFG
    Write-Output "RESOURCE_CFG_SHA_AFTER=$restoredSha"
    if ($restoredSha -ne $backupSha) { Fail "restored cfg SHA mismatch" }
    Write-Output "RESOURCE_CFG_SHA_MATCH=YES"
} else {
    Fail "restored cfg missing"
}

# 5. delete cache
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

Write-Output "VERDICT=ROLLBACK_OK"
exit 0
