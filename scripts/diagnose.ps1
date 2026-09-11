# MiQroForge Environment Diagnostic Script (Windows PowerShell)
# Run: .\diagnose.ps1  or  irm https://... | iex
# Output: paste into GitHub Issue "环境" section

$ErrorActionPreference = "SilentlyContinue"

Write-Host "--- MiQroForge DIAGNOSTIC ---"
Write-Host ""

# ---- OS ----
Write-Host "**OS**"
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "Caption  : $($os.Caption)"
Write-Host "Version  : $($os.Version)"
Write-Host "Build    : $($os.BuildNumber)"
Write-Host "Arch     : $($os.OSArchitecture)"
Write-Host ""

# ---- Hardware ----
Write-Host "**Hardware**"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Host "CPU      : $($cpu.Name.Trim())"
$mem = Get-CimInstance Win32_ComputerSystem
$totalGB = [math]::Round($mem.TotalPhysicalMemory / 1GB, 1)
Write-Host "Memory   : ${totalGB} GB"
Write-Host ""

# ---- Python ----
Write-Host "**Python**"
try {
    $py = python --version 2>&1
    Write-Host $py
    Write-Host "Path: $(Get-Command python -ErrorAction Stop).Source"
} catch {
    Write-Host "NOT FOUND"
}
Write-Host ""

# ---- MiQroForge ----
Write-Host "**MiQroForge**"
try {
    $ver = python -c "import miqi; print(miqi.__version__)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "version: $ver"
    } else {
        Write-Host "NOT INSTALLED"
    }
} catch {
    Write-Host "NOT INSTALLED"
}
Write-Host ""

# ---- Node.js ----
Write-Host "**Node.js**"
try { Write-Host (node --version 2>&1) } catch { Write-Host "NOT FOUND" }
try { Write-Host "npm $(npm --version 2>&1)" } catch { Write-Host "npm: NOT FOUND" }
Write-Host ""

# ---- WSL ----
Write-Host "**WSL**"
try {
    wsl --list --verbose 2>&1 | ForEach-Object { Write-Host $_ }
} catch {
    Write-Host "NOT AVAILABLE"
}
Write-Host ""

# ---- bwrap ----
Write-Host "**bwrap**"
$found = $false
try {
    $bw = Get-Command bwrap -ErrorAction Stop
    Write-Host "Host: $($bw.Source)"
    bwrap --version 2>$null
    $found = $true
} catch {}

if (-not $found) {
    $distros = @("AIShadowSandbox", "Ubuntu")
    foreach ($d in $distros) {
        try {
            $check = wsl -d $d -- which bwrap 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "($d)"
                wsl -d $d -- bwrap --version
                $found = $true
                break
            }
        } catch {}
    }
}

if (-not $found) {
    try {
        $check = wsl -- which bwrap 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "(default WSL)"
            wsl -- bwrap --version
            $found = $true
        }
    } catch {}
}

if (-not $found) {
    Write-Host "NOT FOUND"
}
Write-Host ""

# ---- Sandbox State ----
Write-Host "**Sandbox State**"
$stateFile = "$env:USERPROFILE\.miqi\sandbox_state.json"
if (Test-Path $stateFile) {
    Get-Content $stateFile
} else {
    Write-Host "NO STATE FILE"
}
Write-Host ""

# ---- Disk ----
Write-Host "**Disk**"
Get-PSDrive C | Select-Object Used, Free | ForEach-Object {
    $usedGB = [math]::Round($_.Used / 1GB, 1)
    $freeGB = [math]::Round($_.Free / 1GB, 1)
    Write-Host "C: used=${usedGB}GB free=${freeGB}GB"
}
Write-Host ""

# ---- PATH check ----
Write-Host "**PATH tools**"
@("python", "node", "npm", "wsl", "bwrap") | ForEach-Object {
    $found = Get-Command $_ -ErrorAction SilentlyContinue
    if ($found) { Write-Host "  [OK] $_ -> $($found.Source)" }
    else        { Write-Host "  [MISSING] $_" }
}
Write-Host ""

# ---- Git ----
Write-Host "**Git**"
try    { git log --oneline -1 2>&1 | ForEach-Object { Write-Host $_ } }
catch  { Write-Host "NOT A GIT REPO" }
Write-Host ""

Write-Host "--- END DIAGNOSTIC ---"
