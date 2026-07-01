# Setup v2 - Fixes v1 stderr-as-error misdiagnosis + cleans corrupted remnants + China mirrors
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File D:\opencode\TBH攻略站\box-queue-reader\tools\setup.ps1

$ErrorActionPreference = 'Continue'  # Critical: not Stop. pip warnings go to stderr.
$ProgressPreference    = 'SilentlyContinue'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SrcDir      = Join-Path $ProjectRoot 'src'
$ResDir      = Join-Path $SrcDir 'resources'

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function OK  ([string]$msg) { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Err ([string]$msg) { Write-Host "  XX  $msg" -ForegroundColor Red }

# ---- 0. Python ----
Step "Python check"
$pyVer = & python --version 2>&1
if ($LASTEXITCODE -ne 0) { Err "Python not found"; exit 1 }
OK $pyVer

# ---- 1. Clean corrupted site-packages remnants (directories starting with ~) ----
Step "Cleaning corrupted distribution remnants"
$site = & python -c "import site; print(site.getusersitepackages())" 2>&1 | Select-Object -Last 1
if ($site -and (Test-Path $site)) {
    OK "User site: $site"
    $rotten = Get-ChildItem $site -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name.StartsWith('~') }
    if ($rotten) {
        foreach ($r in $rotten) {
            try {
                Remove-Item $r.FullName -Recurse -Force -ErrorAction Stop
                OK "Removed $($r.Name)"
            } catch {
                Warn "Could not remove $($r.Name): $($_.Exception.Message)"
            }
        }
    } else {
        OK "No corrupted remnants"
    }
} else {
    Warn "Could not resolve user site-packages path"
}

# ---- 2. Check what's already installed ----
Step "Checking existing installs"
$checkScript = @'
try:
    import psutil
    print("psutil:" + psutil.__version__)
except Exception:
    print("psutil:MISSING")
try:
    import frida
    print("frida:" + frida.__version__)
except Exception:
    print("frida:MISSING")
'@
$checkFile = "$env:TEMP\tbh_check.py"
Set-Content -Path $checkFile -Value $checkScript -Encoding UTF8
$checkOut = & python $checkFile 2>&1
Remove-Item $checkFile -ErrorAction SilentlyContinue
$checkOut | ForEach-Object { Write-Host "  $_" }
$joined = $checkOut -join "`n"
$hasFrida  = $joined -match 'frida:\d'
$hasPsutil = $joined -match 'psutil:\d'

# ---- 3. Install psutil (usually already there) ----
if (-not $hasPsutil) {
    Step "Installing psutil"
    & python -m pip install --user --upgrade psutil 2>&1 | ForEach-Object { Write-Host "  $_" }
} else {
    OK "psutil already installed"
}

# ---- 4. Install frida (main difficulty) ----
if (-not $hasFrida) {
    Step "Installing frida (~42 MB, may be slow)"

    $mirrors = @(
        @{Name='Tsinghua'; Url='https://pypi.tuna.tsinghua.edu.cn/simple'},
        @{Name='Aliyun';   Url='https://mirrors.aliyun.com/pypi/simple'},
        @{Name='PyPI';     Url='https://pypi.org/simple'}
    )

    $installed = $false
    foreach ($m in $mirrors) {
        if ($installed) { break }
        Write-Host "  trying $($m.Name) ($($m.Url)) ..." -ForegroundColor Gray
        $logFile = "$env:TEMP\tbh_frida_$($m.Name).log"
        $errFile = "$logFile.err"

        $host_ = ([System.Uri]$m.Url).Host
        $proc = Start-Process -FilePath python `
            -ArgumentList '-m','pip','install','--user','--upgrade',
                          '-i', $m.Url,
                          '--trusted-host', $host_,
                          '--timeout','120',
                          'frida' `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $logFile `
            -RedirectStandardError $errFile

        if (Test-Path $errFile) {
            $errContent = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
            if ($errContent) { Add-Content -Path $logFile -Value $errContent }
        }
        if (Test-Path $logFile) {
            Get-Content $logFile -Tail 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }

        if ($proc.ExitCode -eq 0) {
            $verify = & python -c "import frida; print(frida.__version__)" 2>&1
            if ($LASTEXITCODE -eq 0) {
                OK "frida $verify (via $($m.Name))"
                $installed = $true
            } else {
                Warn "pip exit=0 but import failed: $verify"
            }
        } else {
            Warn "pip exit=$($proc.ExitCode) via $($m.Name) -> trying next mirror"
        }
    }

    if (-not $installed) {
        Err "All mirrors failed."
        Write-Host ""
        Write-Host "Manual fallback:" -ForegroundColor White
        Write-Host "  1. Download wheel from https://pypi.org/project/frida/#files" -ForegroundColor Gray
        Write-Host "     (frida-17.15.3-cp37-abi3-win_amd64.whl, ~42 MB)" -ForegroundColor Gray
        Write-Host "  2. Save to: D:\opencode\TBH攻略站\box-queue-reader\tools\" -ForegroundColor Gray
        Write-Host "  3. Install:" -ForegroundColor Gray
        Write-Host "     python -m pip install --user `"D:\opencode\TBH攻略站\box-queue-reader\tools\frida-17.15.3-cp37-abi3-win_amd64.whl`"" -ForegroundColor Gray
        Write-Host ""
        exit 2
    }
} else {
    OK "frida already installed"
}

# ---- 5. Resource files ----
Step "Resource files"
$required = @('drop_items_agent.js', 'item.json', 'item_color.json', 'watched_ids.json')
$missing = @()
foreach ($f in $required) {
    $p = Join-Path $ResDir $f
    if (Test-Path $p) {
        OK "$f ($((Get-Item $p).Length) bytes)"
    } else {
        Err "$f MISSING"
        $missing += $f
    }
}
if ($missing.Count -gt 0) {
    Err "Run: python `"$ProjectRoot\tools\extract_resources.py`""
    exit 4
}

# ---- 6. Game + Frida smoke test ----
Step "Game process check"
$game = Get-Process -Name 'TaskBarHero' -ErrorAction SilentlyContinue
if (-not $game) {
    Warn "TaskBarHero not running - skipping smoke test."
    Warn "Start the game, then:"
    Warn "  python `"$SrcDir\tbh_reader.py`" cli"
} else {
    OK "TaskBarHero pid=$($game.Id)"

    Step "Frida attach smoke test"
    $smokeFile = "$env:TEMP\tbh_smoke.py"
    $smokeBody = @"
import sys, frida, json, time
try:
    session = frida.attach($($game.Id))
    script = session.create_script('send(JSON.stringify({modules: Process.enumerateModules().length}));')
    msgs = []
    script.on('message', lambda m, d: msgs.append(m))
    script.load()
    time.sleep(1.0)
    script.unload()
    session.detach()
    if msgs and msgs[0].get('type') == 'send':
        print('SMOKE_OK ' + msgs[0]['payload'])
        sys.exit(0)
    print('SMOKE_NO_MSG')
    sys.exit(1)
except frida.PermissionDeniedError:
    print('SMOKE_PERMISSION_DENIED')
    sys.exit(2)
except Exception as e:
    print(f'SMOKE_FAIL {type(e).__name__}: {e}')
    sys.exit(3)
"@
    Set-Content -Path $smokeFile -Value $smokeBody -Encoding UTF8
    $smoke = & python $smokeFile 2>&1
    $smoke | ForEach-Object { Write-Host "  $_" }
    Remove-Item $smokeFile -ErrorAction SilentlyContinue

    if ($LASTEXITCODE -eq 0) {
        OK "Frida can attach!"
    } elseif ($LASTEXITCODE -eq 2) {
        Warn "Permission denied -> run PowerShell as Administrator"
    } else {
        Warn "Smoke test failed - antivirus may be blocking frida-helper"
    }
}

# ---- 7. Done ----
Step "Done"
Write-Host ""
Write-Host "Run modes:" -ForegroundColor White
Write-Host "  CLI:  python `"$SrcDir\tbh_reader.py`" cli" -ForegroundColor Gray
Write-Host "  HTTP: python `"$SrcDir\tbh_reader.py`" http --port 18765" -ForegroundColor Gray
Write-Host ""
