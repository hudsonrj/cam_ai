@echo off
echo Limpando rotas conflitantes...
route -p delete 192.168.15.0 mask 255.255.255.0 2>nul
route delete 192.168.15.0 mask 255.255.255.0 100.100.100.100 2>nul

echo Restaurando rota local do subnet 192.168.15.0/24...
route add 192.168.15.0 mask 255.255.255.0 192.168.15.1 metric 10 if 9

echo Reativando accept-routes do Tailscale...
tailscale set --accept-routes

echo.
echo Rotas atuais para 192.168.15.x:
route print | findstr "192.168.15"
echo.
echo Testando camera 192.168.15.13...
ping -n 2 -w 1500 192.168.15.13
echo.
pause
