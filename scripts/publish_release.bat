@echo off
REM Usage: scripts\publish_release.bat GITHUB_TOKEN GITEE_TOKEN
REM Example: scripts\publish_release.bat ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
setlocal EnableDelayedExpansion

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "GITHUB_TOKEN=%~1"
set "GITEE_TOKEN=%~2"

set "REPO_ROOT=F:\WorkBuddyDefault\2026-09-02-04-13-26\survolocking"
set "RELEASE_DIR=%REPO_ROOT%\release\v0.2.0"
set "TAG=v0.2.0"
set "TITLE=Survolocking v0.2.0"
set "GH_OWNER=GreaterLG70"
set "GH_REPO=survolocking"
set "GE_OWNER=smooge"
set "GE_REPO=survolocking"

REM === 0) sanity ===
if not exist "%RELEASE_DIR%\survolocking-v0.2.0-debug.apk" (
  echo [ERR] missing debug apk in %RELEASE_DIR%
  exit /b 1
)
if not exist "%RELEASE_DIR%\survolocking-0.2.0-source.tar.gz" (
  echo [ERR] missing source tarball in %RELEASE_DIR%
  exit /b 1
)
if not exist "%RELEASE_DIR%\RELEASE_NOTES.md" (
  echo [ERR] missing RELEASE_NOTES.md
  exit /b 1
)

REM === 1) Github: create release ===
echo [STEP] publish to GitHub %GH_OWNER%/%GH_REPO% @ %TAG%

set "GH_RELEASE_JSON=%RELEASE_DIR%\_gh_release.json"
del /q "%GH_RELEASE_JSON%" 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$body = Get-Content -Raw -Path '%RELEASE_DIR%\RELEASE_NOTES.md';" ^
  "$payload = @{ tag_name='%TAG%'; name='%TITLE%'; body=$body; target_commitish='v0.2.0'; draft=$false; prerelease=$false; generate_release_notes=$false } | ConvertTo-Json -Depth 8;" ^
  "Invoke-RestMethod -Method Post -Uri 'https://api.github.com/repos/%GH_OWNER%/%GH_REPO%/releases' -Headers @{ Authorization = 'token %GITHUB_TOKEN%'; Accept='application/vnd.github+json'; 'User-Agent'='survolocking-release' } -ContentType 'application/json' -Body $payload | ConvertTo-Json -Depth 12 | Out-File -FilePath '%GH_RELEASE_JSON%' -Encoding utf8"

if not exist "%GH_RELEASE_JSON%" (
  echo [ERR] GitHub release creation failed
  exit /b 1
)

for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-Content -Raw -Path '%GH_RELEASE_JSON%' | ConvertFrom-Json).id"') do set "GH_RELEASE_ID=%%a"
for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-Content -Raw -Path '%GH_RELEASE_JSON%' | ConvertFrom-Json).upload_url"') do set "GH_UPLOAD_URL=%%a"

if "%GH_RELEASE_ID%"=="" (
  echo [ERR] cannot parse GitHub release id
  exit /b 1
)
echo [OK] GitHub release id=%GH_RELEASE_ID%

REM upload assets via direct curl upload (github asset endpoint)
set "GH_UPLOAD_BASE=%GH_UPLOAD_URL:{%?name,label}=%"
for %%f in (survolocking-v0.2.0-debug.apk survolocking-0.2.0-source.tar.gz RELEASE_NOTES.md) do (
  echo [UPLOAD] GitHub asset: %%f
  curl.exe -sS -X POST -H "Authorization: token %GITHUB_TOKEN%" -H "Accept: application/vnd.github+json" -H "User-Agent: survolocking-release" --data-binary "@%RELEASE_DIR%\%%f" "%GH_UPLOAD_BASE%?name=%%f" -o "%RELEASE_DIR%\_gh_asset_%%~nf.json"
  if errorlevel 1 (
    echo [ERR] GitHub asset upload failed: %%f
    exit /b 1
  )
)

REM === 2) Gitee: create release ===
echo [STEP] publish to Gitee %GE_OWNER%/%GE_REPO% @ %TAG%

set "GE_RELEASE_JSON=%RELEASE_DIR%\_ge_release.json"
del /q "%GE_RELEASE_JSON%" 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$body = Get-Content -Raw -Path '%RELEASE_DIR%\RELEASE_NOTES.md';" ^
  "$payload = @{ access_token='%GITEE_TOKEN%'; tag_name='%TAG%'; name='%TITLE%'; body=$body; target_commitish='v0.2.0'; prerelease=$false } | ConvertTo-Json -Depth 8;" ^
  "Invoke-RestMethod -Method Post -Uri 'https://gitee.com/api/v5/repos/%GE_OWNER%/%GE_REPO%/releases' -ContentType 'application/json;charset=UTF-8' -Body $payload | ConvertTo-Json -Depth 12 | Out-File -FilePath '%GE_RELEASE_JSON%' -Encoding utf8"

if not exist "%GE_RELEASE_JSON%" (
  echo [ERR] Gitee release creation failed
  exit /b 1
)

for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-Content -Raw -Path '%GE_RELEASE_JSON%' | ConvertFrom-Json).id"') do set "GE_RELEASE_ID=%%a"

if "%GE_RELEASE_ID%"=="" (
  echo [ERR] cannot parse Gitee release id
  exit /b 1
)
echo [OK] Gitee release id=%GE_RELEASE_ID%

REM upload assets (gitee multipart upload expects single attachment per request)
for %%f in (survolocking-v0.2.0-debug.apk survolocking-0.2.0-source.tar.gz) do (
  echo [UPLOAD] Gitee asset: %%f
  curl.exe -sS -X POST -H "Access-Token: %GITEE_TOKEN%" -F "access_token=%GITEE_TOKEN%" -F "release_id=%GE_RELEASE_ID%" -F "file=@%RELEASE_DIR%\%%f" "https://gitee.com/api/v5/repos/%GE_OWNER%/%GE_REPO%/releases/%GE_RELEASE_ID%/attach_files" -o "%RELEASE_DIR%\_ge_attach_%%~nf.json"
  if errorlevel 1 (
    echo [ERR] Gitee asset upload failed: %%f
    exit /b 1
  )
)

echo [DONE] Releases published.
echo   GitHub: https://github.com/%GH_OWNER%/%GH_REPO%/releases/tag/%TAG%
echo   Gitee : https://gitee.com/%GE_OWNER%/%GE_REPO%/releases/tag/%TAG%
exit /b 0

:usage
echo Usage: %~nx0 GITHUB_TOKEN GITEE_TOKEN
exit /b 2
