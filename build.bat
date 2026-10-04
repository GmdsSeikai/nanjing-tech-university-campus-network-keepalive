@echo off
chcp 65001 >nul
echo ========================================
echo   校园网登录工具 - 打包脚本
echo ========================================
echo.

cd /d "%~dp0"
python -m venv build\.venv
if errorlevel 1 exit /b 1
build\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 exit /b 1
build\.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
build\.venv\Scripts\python.exe -m py_compile app.py drcom_api.py monitor.py config_manager.py tray_icon.py
if errorlevel 1 exit /b 1
build\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name CampusNetLogin --distpath release --workpath build\pyinstaller --icon=NONE app.py
if errorlevel 1 exit /b 1

echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo 客户端: release\CampusNetLogin.exe
echo.
exit /b 0
