# build_sidecar_builder.ps1
# Reproducible Windows build. Single entrypoint after `git pull`:
#   powershell -ExecutionPolicy Bypass -File sidecar_builder\build_sidecar_builder.ps1
#
# Pipeline:
#   find MSBuild -> Restore -> resolve net40 reference assemblies
#   -> build CreateAssemblyVersion (via ProjectReference chain)
#   -> build 5 vendored s4pi projects -> build SidecarBuilder
#   -> bin\Debug\SidecarBuilder.exe
#
# No manual user steps required: no PATH setup, no manual FrameworkPathOverride,
# no manual SolutionDir, no manual pre-build of CreateAssemblyVersion.
#
# ASCII-only by design (Windows PowerShell 5.1 reads .ps1 per ANSI code page when no
# BOM is present; non-ASCII text caused mojibake + parser cascade). Keep this file
# pure ASCII to avoid encoding issues on any Windows environment.

# MSB3644 root cause (verified on Windows MSBuild 18.4):
#   Microsoft.NETFramework.ReferenceAssemblies.net40 targets apply ONLY to the
#   project that declares the PackageReference (SidecarBuilder). Old-style
#   ProjectReference children (vendored s4pi, packages.config era) do NOT get the
#   net40 reference assemblies transitively -> all 5 vendor projects hit MSB3644.
#   Fix: pass FrameworkPathOverride as a GLOBAL MSBuild property so it propagates
#   to every vendored ProjectReference child.

$ErrorActionPreference = 'Stop'

# ---------- 0) Resolve repo root (dir above this script) ----------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Csproj   = Join-Path $RepoRoot 'sidecar_builder\SidecarBuilder.csproj'
if (-not (Test-Path $Csproj)) {
    Write-Error "SidecarBuilder.csproj not found (expected: $Csproj)"
    exit 1
}

# ---------- 1) Locate MSBuild (vswhere -> latest instance with Microsoft.Component.MSBuild) ----------
function Find-MsBuild {
    # (a) vswhere from Visual Studio Installer
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $vs = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' 2>$null
        if ($vs) { return ($vs | Select-Object -First 1) }
    }
    # (b) common default install paths
    $candidates = @(
        'C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe',
        'C:\Program Files\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin\MSBuild.exe'
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    # (c) MSBuild already on PATH
    $p = Get-Command msbuild -ErrorAction SilentlyContinue
    if ($p) { return $p.Source }
    return $null
}
$MsBuild = Find-MsBuild
if (-not $MsBuild) {
    Write-Error "MSBuild not found. Install Visual Studio Build Tools (with Microsoft.Component.MSBuild) or provide vswhere.exe."
    exit 1
}
Write-Host "MSBuild  = $MsBuild"

# ---------- 2) Restore (restore PackageReference) ----------
Write-Host "== Restore =="
& $MsBuild $Csproj /t:Restore /p:Configuration=Debug /p:Platform=AnyCPU /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Restore failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# ---------- 3) Resolve net40 reference assemblies (Restore output) ----------
# Dir: <NuGetPackageRoot>\microsoft.netframework.referenceassemblies.net40\1.0.3\build\.NETFramework\v4.0
$NuGetRoot = if ($env:NUGET_PACKAGES) { $env:NUGET_PACKAGES } else { Join-Path $env:USERPROFILE '.nuget\packages' }
$Net40Ref = Join-Path $NuGetRoot 'microsoft.netframework.referenceassemblies.net40\1.0.3\build\.NETFramework\v4.0'
if (-not (Test-Path $Net40Ref)) {
    Write-Error "net40 reference assemblies not found: $Net40Ref`n(Ensure 'git pull' then Restore ran; or check NUGET_PACKAGES)"
    exit 1
}
Write-Host "Net40Ref = $Net40Ref"

# ---------- 4) Global build properties ----------
# FrameworkPathOverride must be a GLOBAL property to propagate to all vendored
# ProjectReference children. Passing it disables automatic mscorlib/Framework
# resolution; the net40 reference assemblies ship the correct mscorlib, and
# NoStdLib is handled by the package targets. Consistent with manual-pass testing.
$SolutionDir = (Join-Path $RepoRoot 'vendor\')   # trailing backslash; s4pi PreBuildEvent relies on it

# ---------- 5) Build (includes CreateAssemblyVersion via ProjectReference chain) ----------
& $MsBuild $Csproj `
    /t:Build `
    /p:Configuration=Debug `
    /p:Platform=AnyCPU `
    "/p:SolutionDir=$SolutionDir" `
    "/p:FrameworkPathOverride=$Net40Ref" `
    /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Build failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# ---------- 6) Verify output ----------
$Exe = Join-Path $RepoRoot 'sidecar_builder\bin\Debug\SidecarBuilder.exe'
if (-not (Test-Path $Exe)) {
    Write-Error "Build succeeded but output not found: $Exe"
    exit 1
}
$AsmExe = Join-Path $RepoRoot 'vendor\s4pi\CreateAssemblyVersion\bin\Debug\CreateAssemblyVersion.exe'
Write-Host ""
Write-Host "BUILD OK: $Exe"
Write-Host "CreateAssemblyVersion: $([bool](Test-Path $AsmExe)) (built automatically via ProjectReference chain)"
Write-Host ""
Write-Host "Next verify command:"
Write-Host "  & '$Exe' -source <SRC> -out out.package -type 0x220557DA -group 0x80000000 -inst 0x... -m KEYHASH:EXPECTED:VALUE"
exit 0
