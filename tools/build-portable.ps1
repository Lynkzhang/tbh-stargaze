# Build a portable zip for TBHStargaze
# Output: dist\TBHStargaze-portable-YYYYMMDD.zip
#
# What's inside:
#   ├─ 启动.bat             ← double-click to run
#   ├─ src\
#   │  ├─ tbh_reader.py
#   │  └─ web\, resources\
#   ├─ python-portable\     ← Python 3.12 embeddable (~10 MB)
#   ├─ wheels\              ← frida + psutil offline wheels
#   ├─ tools\
#   │  └─ install-deps.bat  ← first-run wheel installer
#   └─ README.txt
#
# Total size: ~60 MB (frida is the heavy one)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildDir    = Join-Path $ProjectRoot 'build'
$DistDir     = Join-Path $ProjectRoot 'dist'
$StageDir    = Join-Path $BuildDir 'stage'

$DateTag = Get-Date -Format 'yyyyMMdd'
$ZipName = "TBHStargaze-portable-$DateTag.zip"
$ZipPath = Join-Path $DistDir $ZipName

function Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function OK  ([string]$m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  !!  $m" -ForegroundColor Yellow }
function Err ([string]$m) { Write-Host "  XX  $m" -ForegroundColor Red }

# ---------------------------------------------------------------- 0. clean
Step "Clean build dirs"
# Wipe stage but KEEP the build/cache subdir so we don't re-download
# python-embed.zip and get-pip.py every build.
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
$CacheDir = Join-Path $BuildDir 'cache'
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir  | Out-Null
OK "Stage: $StageDir"
OK "Cache: $CacheDir"

# ---------------------------------------------------------------- 1. copy source
Step "Copying source files"
$srcSrc = Join-Path $ProjectRoot 'src'
$dstSrc = Join-Path $StageDir 'src'
Copy-Item $srcSrc $dstSrc -Recurse -Force
OK "src\ copied"

# also copy launcher (find by pattern - filename has Chinese chars that PS5 mangles)
$launcher = Get-ChildItem $ProjectRoot -Filter '*.bat' | Where-Object {
    $_.Length -gt 0 -and -not $_.Name.StartsWith('_')
} | Select-Object -First 1
if ($launcher) {
    Copy-Item $launcher.FullName $StageDir -Force
    OK "Launcher copied: $($launcher.Name)"
} else {
    Warn "No launcher .bat found in $ProjectRoot"
}

# clean __pycache__ inside copied src
Get-ChildItem $dstSrc -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
OK "__pycache__ removed"

# ---------------------------------------------------------------- 2. Python embeddable
Step "Downloading Python 3.12 embeddable"
# Python 3.12.8 embeddable, win64. Adjust version if you want newer.
$pyVer = '3.12.8'
$pyZipUrl = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-amd64.zip"
$pyZip = Join-Path $CacheDir "python-embed-$pyVer.zip"

if (-not (Test-Path $pyZip)) {
    Write-Host "  Downloading from python.org..."
    try {
        Invoke-WebRequest -Uri $pyZipUrl -OutFile $pyZip -UseBasicParsing -TimeoutSec 120
        OK "Downloaded $((Get-Item $pyZip).Length / 1MB) MB"
    } catch {
        Err "Download failed: $_"
        Err "Manual: download $pyZipUrl, save as $pyZip, rerun"
        exit 1
    }
} else {
    OK "Reusing cached $pyZip"
}

$pyDest = Join-Path $StageDir 'python-portable'
Expand-Archive -Path $pyZip -DestinationPath $pyDest -Force
OK "Extracted to python-portable\"

# Patch python._pth to enable site-packages (embeddable disables it by default)
$pthFile = Get-ChildItem $pyDest -Filter "python3*._pth" | Select-Object -First 1
if ($pthFile) {
    $content = Get-Content $pthFile.FullName -Raw
    # Uncomment 'import site' line
    $content = $content -replace '#import site', 'import site'
    # Add Lib\site-packages to path
    if ($content -notmatch 'Lib\\site-packages') {
        $content = $content.TrimEnd() + "`r`nLib\site-packages`r`n"
    }
    Set-Content -Path $pthFile.FullName -Value $content -NoNewline
    OK "Patched $($pthFile.Name) to enable site-packages"
} else {
    Warn "No _pth file found - site packages may not work"
}

# Make sure pip is bootstrappable: cache get-pip.py
Step "Bootstrapping pip in embeddable Python"
$getPip = Join-Path $pyDest 'get-pip.py'
$getPipCache = Join-Path $CacheDir 'get-pip.py'
if (Test-Path $getPipCache) {
    Copy-Item $getPipCache $getPip -Force
    OK "Reusing cached get-pip.py"
} else {
    try {
        Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getPipCache -UseBasicParsing -TimeoutSec 60
        Copy-Item $getPipCache $getPip -Force
        OK "get-pip.py downloaded and cached"
    } catch {
        Warn "get-pip.py download failed: $_"
        Warn "Wheels will need to be installed via a different python on target machine"
    }
}

# ---------------------------------------------------------------- 3. Download wheels (with cache)
Step "Downloading frida + psutil wheels for offline install"
$wheelDir = Join-Path $StageDir 'wheels'
$wheelCache = Join-Path $CacheDir 'wheels'
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
New-Item -ItemType Directory -Force -Path $wheelCache | Out-Null

# Skip download if cache already has pip + runtime wheels
$cachedWheels = Get-ChildItem $wheelCache -Filter '*.whl' -ErrorAction SilentlyContinue
$havePip = $cachedWheels | Where-Object { $_.Name -like 'pip-*' }
$haveFrida = $cachedWheels | Where-Object { $_.Name -like 'frida-*' }
$havePsutil = $cachedWheels | Where-Object { $_.Name -like 'psutil-*' }
if ($havePip -and $haveFrida -and $havePsutil) {
    Copy-Item "$wheelCache\*.whl" $wheelDir -Force
    OK "Reusing cached wheels"
    $cachedWheels | ForEach-Object { Write-Host "    $($_.Name) ($([Math]::Round($_.Length/1MB,1)) MB)" }
    $downloaded = $true
} else {
    $downloaded = $false
}

# Use current Python's pip to download (target = same platform: win_amd64, cp312)
$mirrors = @(
    'https://pypi.tuna.tsinghua.edu.cn/simple',
    'https://mirrors.aliyun.com/pypi/simple',
    'https://pypi.org/simple'
)

if (-not $downloaded) {
    foreach ($mirror in $mirrors) {
        Write-Host "  trying $mirror ..."
        $logFile = Join-Path $BuildDir "wheel-dl.log"
        $errFile = "$logFile.err"
        $host_ = ([System.Uri]$mirror).Host
        $proc = Start-Process -FilePath python `
            -ArgumentList '-m','pip','download',
                          '--dest', $wheelCache,
                          '-i', $mirror,
                          '--trusted-host', $host_,
                          '--platform','win_amd64',
                          '--python-version','312',
                          '--only-binary=:all:',
                          '--timeout','120',
                          'pip','frida','psutil' `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $logFile `
            -RedirectStandardError $errFile

        if ($proc.ExitCode -eq 0) {
            $wheels = Get-ChildItem $wheelCache -Filter '*.whl' -ErrorAction SilentlyContinue
            if ($wheels) {
                Copy-Item "$wheelCache\*.whl" $wheelDir -Force
                OK "Downloaded $($wheels.Count) wheels via $host_ (cached)"
                $wheels | ForEach-Object { Write-Host "    $($_.Name) ($([Math]::Round($_.Length/1MB,1)) MB)" }
                $downloaded = $true
                break
            }
        }
        Warn "Failed via $host_; trying next mirror"
        Get-Content $logFile -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
}

if (-not $downloaded) {
    Err "All mirrors failed to download wheels."
    Err "Manual: drop frida-*.whl and psutil-*.whl into $wheelDir, rerun --skip-wheel-download"
    exit 2
}

# ---------------------------------------------------------------- 4. Preinstall dependencies into portable Python
Step "Installing bundled dependencies into portable Python"
$pipCheck = Start-Process -FilePath (Join-Path $pyDest 'python.exe') `
    -ArgumentList '-m','pip','--version' `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput (Join-Path $BuildDir 'pip-check.log') `
    -RedirectStandardError (Join-Path $BuildDir 'pip-check.err')
if ($pipCheck.ExitCode -ne 0) {
    if (-not (Test-Path $getPip)) {
        Err "get-pip.py missing; cannot preinstall dependencies"
        exit 4
    }
    $pipBootstrap = Start-Process -FilePath (Join-Path $pyDest 'python.exe') `
        -ArgumentList $getPip,'--no-warn-script-location','--no-index','--find-links',$wheelDir `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $BuildDir 'pip-bootstrap.log') `
        -RedirectStandardError (Join-Path $BuildDir 'pip-bootstrap.err')
    if ($pipBootstrap.ExitCode -ne 0) {
        Err "pip bootstrap failed"
        Get-Content (Join-Path $BuildDir 'pip-bootstrap.err') -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        exit 4
    }
}

$depInstall = Start-Process -FilePath (Join-Path $pyDest 'python.exe') `
    -ArgumentList '-m','pip','install','--no-warn-script-location','--no-index','--find-links',$wheelDir,'frida','psutil' `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput (Join-Path $BuildDir 'deps-install.log') `
    -RedirectStandardError (Join-Path $BuildDir 'deps-install.err')
if ($depInstall.ExitCode -ne 0) {
    Err "Dependency preinstall failed"
    Get-Content (Join-Path $BuildDir 'deps-install.err') -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    exit 5
}

$depCheck = Start-Process -FilePath (Join-Path $pyDest 'python.exe') `
    -ArgumentList '-c "import frida, psutil"' `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput (Join-Path $BuildDir 'deps-check.log') `
    -RedirectStandardError (Join-Path $BuildDir 'deps-check.err')
if ($depCheck.ExitCode -ne 0) {
    Err "Dependency import check failed"
    exit 6
}
OK "frida + psutil preinstalled"

# ---------------------------------------------------------------- 5. Install deps script (repair only)
Step "Copying install-deps.bat repair script"
$toolsDir = Join-Path $StageDir 'tools'
New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null

$installSrc = Join-Path $PSScriptRoot 'install-deps.bat.template'
$installDst = Join-Path $toolsDir 'install-deps.bat'
if (Test-Path $installSrc) {
    Copy-Item $installSrc $installDst -Force
    OK "install-deps.bat copied from template"
} else {
    Err "install-deps.bat.template not found at $installSrc"
    exit 3
}

# ---------------------------------------------------------------- 6. README
Step "Copying README.txt"
$readmeSrc = Join-Path $ProjectRoot 'README-portable.txt'
$readmeDst = Join-Path $StageDir 'README.txt'
if (Test-Path $readmeSrc) {
    Copy-Item $readmeSrc $readmeDst -Force
    OK "README.txt copied"
} else {
    Warn "README-portable.txt not found at $readmeSrc - skipping"
}

# ---------------------------------------------------------------- 7. Zip it up
Step "Creating zip archive"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

# Use .NET ZipFile.CreateFromDirectory with explicit UTF-8 encoding
# so Chinese filenames survive cross-tool extraction.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$encoding = [System.Text.Encoding]::UTF8
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $StageDir,
    $ZipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false,    # includeBaseDirectory = false (we want contents at root)
    $encoding
)
$zipSize = [Math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
OK "Archive: $ZipPath ($zipSize MB)"

# ---------------------------------------------------------------- 8. Summary
Step "Done"
Write-Host ""
Write-Host "  Portable bundle: $ZipPath" -ForegroundColor White
Write-Host "  Size: $zipSize MB" -ForegroundColor White
Write-Host ""
Write-Host "  To use on another PC:" -ForegroundColor White
Write-Host "    1. Extract the zip anywhere" -ForegroundColor Gray
Write-Host "    2. Right-click 启动.bat -> Run as administrator" -ForegroundColor Gray
Write-Host ""
