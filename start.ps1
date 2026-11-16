$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$port = if ($env:PORT) { $env:PORT } else { "5000" }

Write-Host ""
Write-Host "  ESS Travel Buddy" -ForegroundColor Cyan
Write-Host "  Installing backend deps (if needed)..." -ForegroundColor DarkGray

py -m pip install -r backend\requirements.txt -q
if ($LASTEXITCODE -ne 0) {
  Write-Host "pip install failed. Is Python installed? Try: py --version" -ForegroundColor Red
  exit 1
}

# Allow other devices on the LAN to reach this port (idempotent)
$ruleName = "ESS Travel Buddy ($port)"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
  try {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Any -ErrorAction Stop | Out-Null
    Write-Host "  Firewall: allowed inbound TCP $port" -ForegroundColor DarkGray
  } catch {
    Write-Host "  Firewall rule skip (admin rights chahiye ho sakte hain): $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  Agar phone connect na ho: Windows Defender Firewall mein port $port allow karo." -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "  Starting on network (0.0.0.0:$port)..." -ForegroundColor Green
Write-Host "  Is PC se:     https://127.0.0.1:$port" -ForegroundColor White
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
  ForEach-Object {
    Write-Host ("  Network se:  https://{0}:{1}" -f $_.IPAddress, $port) -ForegroundColor White
  }
Write-Host "  Colleague ke laptop pe https Network URL kholo. http pe browser location block kar deta hai." -ForegroundColor DarkGray
Write-Host "  Certificate warning aaye to Advanced, phir Continue, phir location Allow." -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

Set-Location backend
$env:PORT = $port
py app.py
