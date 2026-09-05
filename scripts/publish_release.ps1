# Usage:
#   ./scripts/publish_release.ps1 -GitHubToken ghp_xxx... -GiteeToken xxxxx...
# Optional: -DryRun
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)] [string] $GitHubToken,
  [Parameter(Mandatory=$true)] [string] $GiteeToken,
  [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

$RepoRoot  = 'F:\WorkBuddyDefault\2026-09-02-04-13-26\survolocking'
$ReleaseDir = Join-Path $RepoRoot 'release\v0.2.0'
$Tag       = 'v0.2.0'
$Title     = 'Survolocking v0.2.0'

$GhOwner = 'GreaterLG70'
$GhRepo  = 'survolocking'
$GeOwner = 'smooge'
$GeRepo  = 'survolocking'

$assets = @(
  'survolocking-v0.2.0-debug.apk',
  'survolocking-0.2.0-source.tar.gz',
  'RELEASE_NOTES.md'
)
$geAssets = @('survolocking-v0.2.0-debug.apk','survolocking-0.2.0-source.tar.gz')

# sanity
foreach ($a in $assets) {
  $p = Join-Path $ReleaseDir $a
  if (-not (Test-Path $p)) { throw "missing asset: $p" }
}
$body = Get-Content -Raw -Path (Join-Path $ReleaseDir 'RELEASE_NOTES.md')

function Write-Step($msg) { Write-Output "[$(Get-Date -Format 'HH:mm:ss')] $msg" }

# ---------- GitHub ----------
Write-Step "GitHub: create release $Tag"
$ghPayload = @{
  tag_name         = $Tag
  name             = $Title
  body             = $body
  target_commitish     = 'main'
  draft            = $false
  prerelease       = $false
} | ConvertTo-Json -Depth 8

if ($DryRun) { Write-Output "Would POST GitHub releases:"; Write-Output $ghPayload; }
else {
  $ghResp = Invoke-RestMethod -Method Post `
    -Uri "https://api.github.com/repos/$GhOwner/$GhRepo/releases" `
    -Headers @{ Authorization = "token $GitHubToken"; Accept='application/vnd.github+json'; 'User-Agent'='survolocking-release' } `
    -ContentType 'application/json' `
    -Body $ghPayload
  $ghReleaseId = $ghResp.id
  Write-Step "GitHub release id = $ghReleaseId  url = $($ghResp.html_url)"
  if (-not $ghReleaseId) { throw 'GitHub release creation returned no id' }

  # upload assets: drop the {?name,label} suffix from upload_url
  $uploadBase = $ghResp.upload_url -replace '\{[^}]+\}$',''
  foreach ($a in $assets) {
    $localPath = Join-Path $ReleaseDir $a
    Write-Step "GitHub upload: $a"
    $resp = Invoke-RestMethod -Method Post `
      -Uri "$uploadBase`?name=$a" `
      -Headers @{ Authorization = "token $GitHubToken"; Accept='application/vnd.github+json'; 'User-Agent'='survolocking-release'; 'Content-Type'='application/octet-stream' } `
      -InFile $localPath
    Write-Output "  -> $($resp.browser_download_url)"
  }
}

# ---------- Gitee ----------
Write-Step "Gitee: create release $Tag"
$gePayload = @{
  access_token     = $GiteeToken
  tag_name         = $Tag
  name             = $Title
  body             = $body
  target_commitish     = 'main'
  prerelease       = $false
} | ConvertTo-Json -Depth 8

if ($DryRun) { Write-Output "Would POST Gitee releases:"; Write-Output $gePayload; }
else {
  $geResp = Invoke-RestMethod -Method Post `
    -Uri "https://gitee.com/api/v5/repos/$GeOwner/$GeRepo/releases" `
    -ContentType 'application/json;charset=UTF-8' `
    -Body $gePayload
  $geReleaseId = $geResp.id
  Write-Step "Gitee release id = $geReleaseId  url = $($geResp.html_url)"
  if (-not $geReleaseId) { throw 'Gitee release creation returned no id' }

  foreach ($a in $geAssets) {
    $localPath = Join-Path $ReleaseDir $a
    Write-Step "Gitee upload: $a"
    $form = @{
      access_token = $GiteeToken
      release_id   = $geReleaseId
      file         = Get-Item $localPath
    }
    $resp = Invoke-RestMethod -Method Post `
      -Uri "https://gitee.com/api/v5/repos/$GeOwner/$GeRepo/releases/$geReleaseId/attach_files" `
      -Form $form
    Write-Output "  -> $($resp.browser_url)"
  }
}

Write-Step 'Done.'
Write-Output "GitHub: https://github.com/$GhOwner/$GhRepo/releases/tag/$Tag"
Write-Output "Gitee : https://gitee.com/$GeOwner/$GeRepo/releases/tag/$Tag"
