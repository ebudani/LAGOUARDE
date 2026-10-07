@echo off
set "PATH=C:\Users\mebud\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.19.0-win-x64;%PATH%"
cd /d "%~dp0"
call npx --yes http-server -p 3010 -c-1
