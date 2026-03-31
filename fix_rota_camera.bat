@echo off
echo Removendo rota do Tailscale para 192.168.15.0/24...
route delete 192.168.15.0 mask 255.255.255.0 100.100.100.100
echo Garantindo rota local permanente...
route -p add 192.168.15.0 mask 255.255.255.0 192.168.15.1 metric 1
echo.
echo Rotas atuais para 192.168.15.x:
route print | findstr "192.168.15"
echo.
echo Pronto! Pressione qualquer tecla para fechar.
pause
