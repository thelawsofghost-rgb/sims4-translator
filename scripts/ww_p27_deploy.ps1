# ============================================================================
# ww_p27_deploy.ps1 —— P27 override 一次性安全部署 (Windows 真机执行)  v3
#
# 只负责把已经 PASS 的真机 P27 override 部署到 Sims 4 Mods, 并做完整性复核.
# 绝不修改原始 WW 源包 / 不启动游戏 / 不启动 P24 / 不改脚本 / 不额外清理.
#
# v3 变更 (P27 deploy v3):
#   * 彻底移除 .ps1 内所有用于安全判断的中文字面量 (-match '抓奸'/'已替换校验' 等).
#   * 一切 report 中文语义判断全部委托给 Python 的 ww_p27_report_check.py
#     (Python3 源码恒 UTF-8, read_text(encoding="utf-8")); 不受 Windows PowerShell
#     .ps1 源码按 ANSI 解析导致中文乱码的影响.
#   * PS1 与 Python 之间全部 ASCII 机器结果.
#   * 双重独立验证(全 PASS 才 Copy-Item):
#       A) ww_p27_report_check.py   -- report txt UTF-8 语义检查
#       B) ww_p27_tgi_check.py      -- override .package 本体 TGI 检查
#   * 本文件保存为 UTF-8 with BOM, 仅为 PS5.1 显示/兼容增强, 不作为安全依赖.
#
# 铁律 (fail-closed):
#   * 任一预检不满足 -> 立即退出 (exit != 0), 不部署.
#   * 只写:  $TARGET (ZZZ_WW_P27_DisplayName_Override.package)
#   * 只删:  $CACHE (localthumbcache.package, 存在才删, 不存在跳过)
#   * 绝对不写/改/删/移: $SOURCE_PKG (原始 WW 源包) 及任何其他 Mod 文件.
#
# 用途: 真机 PowerShell 中:
#   powershell -ExecutionPolicy Bypass -File scripts\ww_p27_deploy.ps1
# ============================================================================
$ErrorActionPreference = "Stop"

# ---- 控制台输出编码 (尽力改善中文显示; 内部 all-ASCII, 不影响判断) ----
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- 路径 (Windows 真机, 全 ASCII) ----------
$WORKSPACE    = "D:\projects\sims4_trans"
$SOURCE_PKG   = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package"
$OVERRIDE_PKG = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.package"
$REPORT_TXT   = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.txt"
$TARGET       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\ZZZ_WW_P27_DisplayName_Override.package"
$CACHE        = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$REPORT_CHECK = Join-Path $WORKSPACE "scripts\ww_p27_report_check.py"
$TGI_CHECK    = Join-Path $WORKSPACE "scripts\ww_p27_tgi_check.py"

function Fail($reason) {
    Write-Output ""
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    Write-Output "READY_FOR_GAME_TEST=NO"
    exit 1
}

function Sha256($p) {
    (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()
}

Write-Output "=== P27 DEPLOYMENT ==="

# ---------- 0. 必要工具存在 ----------
foreach ($t in @($REPORT_CHECK, $TGI_CHECK)) {
    if (-not (Test-Path -LiteralPath $t)) { Fail "missing validator: $t" }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "python not found" }

# ---------- 1. 预检: 文件存在 ----------
foreach ($f in @($SOURCE_PKG, $OVERRIDE_PKG, $REPORT_TXT)) {
    if (-not (Test-Path -LiteralPath $f)) { Fail "preflight file missing: $f" }
}
Write-Output "PREFLIGHT=CHECKING"

# ---------- 2. A) report 语义校验 (纯 Python, ASCII 结果) ----------
$repOut = & python $REPORT_CHECK $REPORT_TXT 2>&1
$repCode = $LASTEXITCODE
if ($repCode -ne 0) {
    Fail "REPORT_CHECK_FAIL(exit $repCode): $($repOut -join ';')"
}
$repOut | ForEach-Object { Write-Output $_ }

# ---------- 3. B) override 包本体 TGI 机验 (复用 dbpf_fast parser, 不依赖 txt) ----------
$tgiOut = & python $TGI_CHECK $OVERRIDE_PKG 2>&1
$tgiCode = $LASTEXITCODE
if ($tgiCode -ne 0) {
    Fail "TGI_CHECK_FAIL(exit $tgiCode): $($tgiOut -join ';')"
}
Write-Output "OVERRIDE_TGI_CHECK=PASS"

# ---------- 4. 源包 SHA(部署前) ----------
$SOURCE_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_SHA_BEFORE=$SOURCE_SHA_BEFORE"

# ---------- 5. override SHA ----------
$OVERRIDE_SHA = Sha256 $OVERRIDE_PKG

# ---------- 6. 复制部署 ----------
Copy-Item -LiteralPath $OVERRIDE_PKG -Destination $TARGET -Force
if (-not (Test-Path -LiteralPath $TARGET)) { Fail "deployed target missing: $TARGET" }
Write-Output "OVERRIDE_DEPLOYED=$TARGET"

# ---------- 7. 部署后 SHA 复核 ----------
$DEPLOYED_SHA = Sha256 $TARGET
if ($DEPLOYED_SHA -ne $OVERRIDE_SHA) {
    Fail "deployed SHA mismatch: $DEPLOYED_SHA vs $OVERRIDE_SHA"
}
Write-Output "OVERRIDE_SHA=$OVERRIDE_SHA"
Write-Output "DEPLOYED_SHA=$DEPLOYED_SHA"
Write-Output "OVERRIDE_SHA_MATCH=YES"

# ---------- 8. 删缓存 (存在才删) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 9. 源包 SHA(部署后) —— 必须与部署前一致 ----------
$SOURCE_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_SHA_AFTER=$SOURCE_SHA_AFTER"
if ($SOURCE_SHA_AFTER -ne $SOURCE_SHA_BEFORE) {
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=SOURCE_CHANGED"
    Write-Output "READY_FOR_GAME_TEST=NO"
    exit 1
}
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_GAME_TEST=YES"
exit 0
