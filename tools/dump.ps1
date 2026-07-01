# M1: Run Il2CppDumper against TaskBarHero
# Usage:
#   1. Download Il2CppDumper from https://github.com/Perfare/Il2CppDumper/releases
#      Extract Il2CppDumper-net8.0-* somewhere
#   2. Set $env:IL2CPPDUMPER to that folder, OR put it in .\tools\Il2CppDumper\
#   3. .\tools\dump.ps1

$ErrorActionPreference = 'Stop'

$GameDir   = 'E:\SteamLibrary\steamapps\common\TaskbarHero'
$GameAsm   = Join-Path $GameDir 'GameAssembly.dll'
$Metadata  = Join-Path $GameDir 'TaskBarHero_Data\il2cpp_data\Metadata\global-metadata.dat'
$OutputDir = Join-Path $PSScriptRoot '..\dump'

# Resolve dumper location
$DumperRoot = if ($env:IL2CPPDUMPER) { $env:IL2CPPDUMPER } else { Join-Path $PSScriptRoot 'Il2CppDumper' }
$DumperExe  = Get-ChildItem $DumperRoot -Filter 'Il2CppDumper.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1

if (-not $DumperExe) {
    Write-Host "ERROR: Il2CppDumper.exe not found." -ForegroundColor Red
    Write-Host "  Looked under: $DumperRoot"
    Write-Host "  Download:     https://github.com/Perfare/Il2CppDumper/releases"
    Write-Host "  Then either:"
    Write-Host "    (a) extract into .\tools\Il2CppDumper\, or"
    Write-Host "    (b) set `$env:IL2CPPDUMPER to its folder"
    exit 1
}

Write-Host "GameAssembly:    $GameAsm"
Write-Host "Metadata:        $Metadata"
Write-Host "Dumper:          $($DumperExe.FullName)"
Write-Host "Output:          $OutputDir"
Write-Host ""

if (-not (Test-Path $GameAsm))   { throw "GameAssembly.dll not found at $GameAsm" }
if (-not (Test-Path $Metadata))  { throw "global-metadata.dat not found at $Metadata" }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Push-Location $OutputDir
try {
    & $DumperExe.FullName $GameAsm $Metadata
    if ($LASTEXITCODE -ne 0) { throw "Il2CppDumper exited with $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== Dump complete ===" -ForegroundColor Green
Get-ChildItem $OutputDir | Select-Object Name, Length | Format-Table -AutoSize
