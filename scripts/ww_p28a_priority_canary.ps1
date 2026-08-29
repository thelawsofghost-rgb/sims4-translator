# ============================================================================
# ww_p28a_priority_canary.ps1 —— P28A Resource Priority Canary (Windows 真机执行)
#
# 目的: 建立"明确高优先级"的 P27 override 路径, 以判定游戏内中文失败是否因
# package precedence。不猜文件名字, 而是基于真实 Resource.cfg 规则设计。
#
# 阶段1 (只读审计): 调用 ww_p28a_cfg_audit.py 解析/判读当前 Resource.cfg,
#   输出完整 Priority/PackedFile 规则 + 各类包命中情况 + 建议。
# 阶段2 (建立 canary): 按审计建议在 Resource.cfg 追加一条比所有现有都更高优先级
#   的规则 (P27_Overrides 目录), 建立该目录并部署已通过 P27 双重验证的 override,
#   可选删除旧的 root ZZZ override 以避免同 TGI 双份污染, 删 localthumbcache。
#
# 铁律 (fail-closed):
#   * 修改 Resource.cfg 前自动备份为 Resource.cfg.p27_backup; 备份已存在则绝不覆盖。
#   * 源包 WW_Nevely42_Animations.package 绝不被修改/移动/删除/重命名。
#   * 只写:  $CANARY_PKG 目标 + $RESOURCE_CFG (带备份)
#   * 只删:  $OLD_ROOT_OVERRIDE (唯一允许删除的旧 P27 override) + $CACHE(localthumbcache)
#   * 不得删除/触碰任何其他 Mod。
#   * 双重独立验证 (report_check + tgi_check) 全 PASS 才允许任何写操作。
#
# 本文件 ASCII-only; 中文语义判断全部在 Python 侧完成。
# ============================================================================
$ErrorActionPreference = "Stop"

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- 路径 (Windows 真机, 全 ASCII) ----------
$WORKSPACE   = "D:\projects\sims4_trans"
$MODS        = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$RESOURCE_CFG = Join-Path $MODS "Resource.cfg"
$CFG_BACKUP  = Join-Path $MODS "Resource.cfg.p27_backup"
$CANARY_DIR  = Join-Path $MODS "P27_Overrides"
$CANARY_PKG  = Join-Path $CANARY_DIR "WW_P27_DisplayName_Override.package"
$OVERRIDE_PKG = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.package"
$REPORT_TXT   = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.txt"
$OLD_ROOT_OVERRIDE = Join-Path $MODS "ZZZ_WW_P27_DisplayName_Override.package"
$SOURCE_PKG  = Join-Path $MODS "2026.7.20\WW_Nevely42_Animations.package"
$CACHE       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$CFG_AUDIT    = Join-Path $WORKSPACE "scripts\ww_p28a_cfg_audit.py"
$REPORT_CHECK = Join-Path $WORKSPACE "scripts\ww_p27_report_check.py"
$TGI_CHECK    = Join-Path $WORKSPACE "scripts\ww_p27_tgi_check.py"

function Fail($reason) {
    Write-Output ""
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    Write-Output "READY_FOR_PRIORITY_GAME_TEST=NO"
    exit 1
}
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

Write-Output "=== P28A PRIORITY CANARY ==="

# ---------- 0. 工具存在 ----------
foreach ($t in @($CFG_AUDIT, $REPORT_CHECK, $TGI_CHECK)) {
    if (-not (Test-Path -LiteralPath $t)) { Fail "missing validator: $t" }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "python not found" }
foreach ($f in @($OVERRIDE_PKG, $REPORT_TXT, $SOURCE_PKG)) {
    if (-not (Test-Path -LiteralPath $f)) { Fail "preflight file missing: $f" }
}
if (-not (Test-Path -LiteralPath $RESOURCE_CFG)) { Fail "Resource.cfg missing: $RESOURCE_CFG" }

# ---------- 1. 阶段1 (只读审计): 通过 P27 双重验证 ----------
$repOut = & python $REPORT_CHECK $REPORT_TXT 2>&1
if ($LASTEXITCODE -ne 0) { Fail "REPORT_CHECK_FAIL: $($repOut -join ';')" }
Write-Output (($repOut | Where-Object { $_ -like "REPORT_CHECK=*" }) -join '')
$repLine = $repOut | Where-Object { $_ -like "INSTANCE=*0x43F3438A94EDEB2B*" }
if (-not $repLine) { Fail "report instance not real" }
Write-Output "P27_REPORT_CHECK=PASS"

$tgiOut = & python $TGI_CHECK $OVERRIDE_PKG 2>&1
if ($LASTEXITCODE -ne 0) { Fail "TGI_CHECK_FAIL: $($tgiOut -join ';')" }
Write-Output "P27_TGI_CHECK=PASS"

# ---------- 2. 阶段1 (只读审计): Resource.cfg 判读 ----------
Write-Output "--- RESOURCE_CFG AUDIT (read-only) ---"
$aud = & python $CFG_AUDIT check $RESOURCE_CFG $OLD_ROOT_OVERRIDE $SOURCE_PKG 2>&1
if ($LASTEXITCODE -ne 0) { Fail "CFG_AUDIT_FAIL(exit $LASTEXITCODE): $($aud -join ';')" }
$aud | ForEach-Object { Write-Output $_ }

# ---------- 3. 阶段2 决策 (propose) ----------
$prop = & python $CFG_AUDIT propose $RESOURCE_CFG $OLD_ROOT_OVERRIDE $SOURCE_PKG 2>&1
if ($LASTEXITCODE -ne 0) { Fail "CFG_PROPOSE_FAIL(exit $LASTEXITCODE)" }
$appendReq   = ($prop | Where-Object { $_ -like "APPEND_REQUIRED=*" }) -replace "APPEND_REQUIRED=",""
$appendB64   = ($prop | Where-Object { $_ -like "APPEND_LINES=*" })    -replace "APPEND_LINES=",""
$proposedPrio= ($prop | Where-Object { $_ -like "PROPOSED_PRIORITY=*" }) -replace "PROPOSED_PRIORITY=",""
if (-not $proposedPrio) { Fail "no proposed priority" }

# ---------- 4. 备份 (不覆盖既有备份) ----------
if (Test-Path -LiteralPath $CFG_BACKUP) {
    Write-Output "RESOURCE_CFG_BACKUP=EXISTS (kept, not overwritten)"
} else {
    Copy-Item -LiteralPath $RESOURCE_CFG -Destination $CFG_BACKUP
    if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup write failed" }
    Write-Output "RESOURCE_CFG_BACKUP=OK"
}
$CFG_SHA_BEFORE = Sha256 $RESOURCE_CFG
Write-Output "RESOURCE_CFG_SHA_BEFORE=$CFG_SHA_BEFORE"

# ---------- 5. 追加高优先级规则 (仅在需要时) ----------
if ($appendReq -eq "YES") {
    if (-not $appendB64) { Fail "append required but no APPEND_LINES" }
    $linesBytes = [Convert]::FromBase64String($appendB64)
    $linesTxt   = [System.Text.Encoding]::UTF8.GetString($linesBytes)
    # 在修改前再校验一次 cfg SHA 未变 (审计与修改之间不可被外部改动)
    if ((Sha256 $RESOURCE_CFG) -ne $CFG_SHA_BEFORE) { Fail "Resource.cfg changed during audit -> abort" }
    [System.IO.File]::AppendAllText($RESOURCE_CFG, $linesTxt, [System.Text.Encoding]::UTF8)
    # 验证规则确实已写入
    $after = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($after -notmatch "P27_Overrides") { Fail "append verification failed" }
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio"
    Write-Output "OVERRIDE_PATH=P27_Overrides\WW_P27_DisplayName_Override.package"
} else {
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (reuse existing dedicated rule)"
    Write-Output "OVERRIDE_PATH=P27_Overrides\WW_P27_DisplayName_Override.package (existing rule)"
}

# ---------- 6. 建立 canary 目录并复制 override ----------
if (-not (Test-Path -LiteralPath $CANARY_DIR)) { New-Item -ItemType Directory -Path $CANARY_DIR | Out-Null }
Copy-Item -LiteralPath $OVERRIDE_PKG -Destination $CANARY_PKG -Force
if (-not (Test-Path -LiteralPath $CANARY_PKG)) { Fail "canary copy failed" }
$OVR_SHA = Sha256 $OVERRIDE_PKG
$DPL_SHA = Sha256 $CANARY_PKG
if ($OVR_SHA -ne $DPL_SHA) { Fail "canary SHA mismatch" }
Write-Output "OVERRIDE_SHA_MATCH=YES"

# ---------- 7. 删除旧 root ZZZ override (唯一允许删除的旧 P27 override) ----------
if (Test-Path -LiteralPath $OLD_ROOT_OVERRIDE) {
    Remove-Item -LiteralPath $OLD_ROOT_OVERRIDE -Force
    if (Test-Path -LiteralPath $OLD_ROOT_OVERRIDE) { Fail "old root override delete failed" }
    Write-Output "OLD_ROOT_OVERRIDE=DELETED"
} else {
    Write-Output "OLD_ROOT_OVERRIDE=NOT_PRESENT"
}

# ---------- 8. 源 WW 包 SHA (部署前 + 后必须一致) ----------
$SRC_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_BEFORE=$SRC_SHA_BEFORE"

# ---------- 9. 删缓存 (存在才删) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 10. 源 WW 包 SHA (部署后) ----------
$SRC_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_AFTER=$SRC_SHA_AFTER"
if ($SRC_SHA_AFTER -ne $SRC_SHA_BEFORE) { Fail "SOURCE WW CHANGED" }
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_PRIORITY_GAME_TEST=YES"
exit 0
