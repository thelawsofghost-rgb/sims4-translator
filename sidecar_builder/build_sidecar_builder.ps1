# build_sidecar_builder.ps1
# 固化 Windows 可复现构建: 从 git pull 后只运行本脚本, 即可自动完成
#   find MSBuild -> Restore -> 解析 net40 reference assemblies -> 编 CreateAssemblyVersion
#   -> 编 5 个 vendored s4pi 工程 -> 编 SidecarBuilder -> bin\Debug\SidecarBuilder.exe
#
# 不需要用户: 手工设 PATH / 手工传 FrameworkPathOverride / 手工传 SolutionDir / 手工先编 CreateAssemblyVersion。
#
# 关键点 (基于 Windows 实测 MSBuild 18.4):
#   - PackageReference 的 Microsoft.NETFramework.ReferenceAssemblies.net40 targets 只对本工程
#     (SidecarBuilder) 生效; 旧式 ProjectReference child (vendored s4pi, packages.config 时代)
#     不会因此自动解决 net40 reference assemblies -> 全部 MSB3644。
#     必须把 FrameworkPathOverride 作为【全局 MSBuild property】显式传入, 才能传播到所有 child。
#   - FrameworkPathOverride 由 Restore 出的 net40 包目录动态解析, 不硬编码用户名/本机绝对路径。

$ErrorActionPreference = 'Stop'

# ---------- 0) 定位仓库根 ----------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Csproj   = Join-Path $RepoRoot 'sidecar_builder\SidecarBuilder.csproj'
if (-not (Test-Path $Csproj)) {
    Write-Error "找不到 SidecarBuilder.csproj (应为 $Csproj)"
    exit 1
}

# ---------- 1) 自动定位 MSBuild (vswhere -> 最新带 Microsoft.Component.MSBuild 的实例) ----------
function Find-MsBuild {
    # 候选路径: (a) vswhere; (b) 常见 Build Tools 安装; (c) 最近生成的 MSBuild 版本目录
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $vs = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' 2>$null
        if ($vs) { return ($vs | Select-Object -First 1) }
    }
    # 兜底: 常见默认安装路径
    $candidates = @(
        'C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin\MSBuild.exe'
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    # 再兜底: 看 PATH 里有没有 msbuild
    $p = Get-Command msbuild -ErrorAction SilentlyContinue
    if ($p) { return $p.Source }
    return $null
}
$MsBuild = Find-MsBuild
if (-not $MsBuild) {
    Write-Error "找不到 MSBuild。请安装 Visual Studio Build Tools (含 Microsoft.Component.MSBuild), 或提供 vswhere.exe。"
    exit 1
}
Write-Host "MSBuild  = $MsBuild"

# ---------- 2) Restore (恢复 PackageReference) ----------
Write-Host "== Restore =="
& $MsBuild $Csproj /t:Restore /p:Configuration=Debug /p:Platform=AnyCPU /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Restore 失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# ---------- 3) 动态解析 net40 reference assemblies (Restore 产物) ----------
# 目录: <NuGetPackageRoot>\microsoft.netframework.referenceassemblies.net40\1.0.3\build\.NETFramework\v4.0
$NuGetRoot = if ($env:NUGET_PACKAGES) { $env:NUGET_PACKAGES } else { Join-Path $env:USERPROFILE '.nuget\packages' }
$Net40Ref = Join-Path $NuGetRoot 'microsoft.netframework.referenceassemblies.net40\1.0.3\build\.NETFramework\v4.0'
if (-not (Test-Path $Net40Ref)) {
    Write-Error "未找到 net40 reference assemblies: $Net40Ref`n(请在项目根执行过 git pull + Restore; 或检查 NUGET_PACKAGES)"
    exit 1
}
Write-Host "Net40Ref = $Net40Ref"

# ---------- 4) 全局构建属性 ----------
# FrameworkPathOverride 必须为 global property 才能传播到所有 vendored ProjectReference child。
# 注意: 传 FrameworkPathOverride 会禁用 mscorlib/Framework 自动解析, 但 net40 reference assemblies
#       自带正确 mscorlib; 同时 NoStdLib 由包内 targets 处理, 全局 property 与之前手动传参的实测一致。
$SolutionDir = (Join-Path $RepoRoot 'vendor\')   # 尾部反斜杠, 满足 s4pi PreBuildEvent 硬编码路径

# ---------- 5) Build (含 CreateAssemblyVersion 依赖链) ----------
& $MsBuild $Csproj `
    /t:Build `
    /p:Configuration=Debug `
    /p:Platform=AnyCPU `
    "/p:SolutionDir=$SolutionDir" `
    "/p:FrameworkPathOverride=$Net40Ref" `
    /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Build 失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# ---------- 6) 校验产物 ----------
$Exe = Join-Path $RepoRoot 'sidecar_builder\bin\Debug\SidecarBuilder.exe'
if (-not (Test-Path $Exe)) {
    Write-Error "构建成功但未找到产物 $Exe"
    exit 1
}
$AsmExe = Join-Path $RepoRoot 'vendor\s4pi\CreateAssemblyVersion\bin\Debug\CreateAssemblyVersion.exe'
Write-Host ""
Write-Host "BUILD OK: $Exe"
Write-Host "CreateAssemblyVersion: $([bool](Test-Path $AsmExe)) (由 ProjectReference 链自动构建)"
Write-Host ""
Write-Host "下一步验证命令:"
Write-Host "  & '$Exe' -source <SRC> -out out.package -type 0x220557DA -group 0x80000000 -inst 0x... -m KEYHASH:EXPECTED:VALUE"
exit 0
