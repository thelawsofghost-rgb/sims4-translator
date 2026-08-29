# ============================================================================
# ww_p28b_zero_xml_rollback.ps1 —— P28B Rollback (Windows 真机执行)
#
# 严格回滚 ww_p28b_zero_xml_deploy.ps1 的效果, 只允许:
#   1. 删除 P28B_Overrides\WW_Nevely42_ZeroXML_Override.package
#   2. 若 P28B_Overrides 空, 删除该目录
#   3. 用 Resource.cfg.p28b_backup 恢复原始 Resource.cfg (需当前 cfg 含 P28B_Overrides 标记)
#   4. 删除 localthumbcache.package (存在才删)
#   5. 验证恢复后的 Resource.cfg SHA 与备份 SHA 相同
# 绝不触碰任何其他 Mod (含 P27 canary / source / 其他), 只回滚 P28B 自身的效果。
#
# 本文件 ASCII-only。
# ============================================================================
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$RESOURCE_CFG = Join-Path $MODS "Resource.cfg"
$CFG_BACKUP  = Join-Path $MODS "Resource.cfg.p28b_backup"
$CANARY_DIR  = Join-Path $MODS "P28B_Overrides"
$CANARY_PKG  = Join-Path $CANARY_DIR "WW_Nevely42_ZeroXML_Override.package"
$CACHE       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

function Fail($reason) { Write-Output "VERDICT=ROLLBACK_FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

Write-Output "=== P28B ROLLBACK ==="

if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup missing: $CFG_BACKUP" }
$backupSha = Sha256 $CFG_BACKUP
Write-Output "CFG_BACKUP_SHA=$backupSha"

# 1. 删除 P28B canary 包
if (Test-Path -LiteralPath $CANARY_PKG) {
    Remove-Item -LiteralPath $CANARY_PKG -Force
    if (Test-Path -LiteralPath $CANARY_PKG) { Fail "canary pkg delete failed" }
    Write-Output "CANARY_PKG=DELETED"
} else {
    Write-Output "CANARY_PKG=NOT_PRESENT"
}

# 2. 若 P28B_Overrides 空则删除目录
if ((Test-Path -LiteralPath $CANARY_DIR) -and -not (Get-ChildItem -LiteralPath $CANARY_DIR -Force | Select-Object -First 1)) {
    Remove-Item -LiteralPath $CANARY_DIR -Force
    if (Test-Path -LiteralPath $CANARY_DIR) { Fail "canary dir delete failed" }
    Write-Output "CANARY_DIR=DELETED(empty)"
} else {
    Write-Output "CANARY_DIR=KEPT(non-empty)"
}

# 3. 恢复 cfg: 必须保证当前 cfg 确实含 P28B_Overrides 标记 (即我们改过), 否则不覆盖
if (Test-Path -LiteralPath $RESOURCE_CFG) {
    $cur = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($cur -match "P28B_Overrides") {
        Copy-Item -LiteralPath $CFG_BACKUP -Destination $RESOURCE_CFG -Force
        Write-Output "RESOURCE_CFG=RESTORED"
    } else {
        Write-Output "RESOURCE_CFG=UNCHANGED (no P28B_Overrides marker)"
    }
} else {
    Copy-Item -LiteralPath $CFG_BACKUP -Destination $RESOURCE_CFG -Force
    Write-Output "RESOURCE_CFG=RESTORED(missing was restored)"
}

# 5. 验证恢复后 SHA == 备份 SHA
if (Test-Path -LiteralPath $RESOURCE_CFG) {
    $restoredSha = Sha256 $RESOURCE_CFG
    Write-Output "RESOURCE_CFG_SHA_AFTER=$restoredSha"
    if ($restoredSha -ne $backupSha) { Fail "restored cfg SHA mismatch" }
    Write-Output "RESOURCE_CFG_SHA_MATCH=YES"
} else {
    Fail "restored cfg missing"
}

# 4. 删缓存 (存在才删)
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

Write-Output "VERDICT=ROLLBACK_OK"
exit 0
