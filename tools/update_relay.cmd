@echo off
rem Updates the VaultMC relay on the VPS: backup (relay.py.bak), new file, service restart.
rem The owner types the VPS root password (input is hidden). Keep this file ASCII-only: cmd breaks on UTF-8.
echo.
echo === Updating VaultMC relay on VPS 13.143.207.131 ===
echo When you see "password:" type the VPS root password and press Enter (letters are hidden).
echo.
type "%~dp0..\server\relay\relay.py" | ssh root@13.143.207.131 "cp /opt/vaultmc-relay/relay.py /opt/vaultmc-relay/relay.py.bak && cat > /opt/vaultmc-relay/relay.py && systemctl restart vaultmc-relay && echo OK-RELAY-UPDATED"
echo.
echo === Done ===
