@echo off
echo ===========================================
echo ALI Brain - Local Sync ^& Backup Utility
echo ===========================================
echo.

REM 1. Pull latest changes from GitHub (where Son of Anton writes)
echo [1/3] Syncing latest brain data from GitHub...
git pull origin master

REM 2. Ensure backup directory exists
if not exist "ruflow_brain_backup" (
    mkdir ruflow_brain_backup
)

REM 3. Create two-copy backup
echo [2/3] Creating local backup of ruflow_brain...
xcopy /Y /S /I ruflow_brain ruflow_brain_backup\

echo [3/3] Sync ^& Backup Complete.
echo.
pause
