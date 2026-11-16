@echo off
:: Run once as Administrator so phones/laptops on the LAN can reach port 5000
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting Administrator permission for firewall rule...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="ESS Travel Buddy (5000)" >nul 2>&1
netsh advfirewall firewall add rule name="ESS Travel Buddy (5000)" dir=in action=allow protocol=TCP localport=5000
echo.
echo Firewall rule added: TCP 5000 inbound allow
echo Ab start.bat chalao aur phone pe Network URL kholo.
echo.
pause
