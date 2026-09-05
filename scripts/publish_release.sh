#!/usr/bin/env bash
# 创建 GitHub + Gitee v0.2.0 release
# 用法: ./scripts/publish_release.sh GITHUB_TOKEN GITEE_TOKEN
set -euo pipefail

REPO_DIR='F:/WorkBuddyDefault/2026-09-02-04-13-26/survolocking'
RELEASE_DIR="$REPO_DIR/release/v0.2.0"
TAG='v0.2.0'
TITLE='Survolocking v0.2.0'
BODY_FILE="$RELEASE_DIR/RELEASE_NOTES.md"
LOG_FILE="$RELEASE_DIR/_publish.log"

GH_TOKEN="$1"
GE_TOKEN="$2"

if [[ -z "$GH_TOKEN" || -z "$GE_TOKEN" ]]; then
  echo "usage: $0 <GITHUB_TOKEN> <GITEE_TOKEN>" >&2
  exit 2
fi

# 用 python 解析 json（Git Bash 自带 python3）
py_json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)"; }

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

: > "$LOG_FILE"

######################
# 1) GitHub
######################
GH_OWNER='GreaterLG70'
GH_REPO='survolocking'

log "GitHub: 创建 release $TAG"
RESP=$(curl -sS -X POST \
  -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "User-Agent: survolocking-release" \
  -H "Content-Type: application/json" \
  --data-binary "$(python3 -c "import json,sys; print(json.dumps({'tag_name':'$TAG','name':'$TITLE','target_commitish':'main','body':open(r'$BODY_FILE',encoding='utf-8').read()}))")" \
  "https://api.github.com/repos/$GH_OWNER/$GH_REPO/releases")

if ! echo "$RESP" | python3 -c "import sys,json; json.load(sys.stdin)['_print_id']" 2>/dev/null; then
  GH_ERR=$(echo "$RESP" | py_json ".get('message','?')")
  log "ERR GitHub create: $GH_ERR"
  echo "$RESP" | head -c 1000 >> "$LOG_FILE"
  exit 1
fi

GH_ID=$(echo "$RESP" | py_json "['id']")
GH_URL=$(echo "$RESP" | py_json "['html_url']")
GH_UPLOAD=$(echo "$RESP" | py_json "['upload_url'].replace('{?name,label}','')")
log "GitHub release id=$GH_ID url=$GH_URL"

for f in survolocking-v0.2.0-debug.apk survolocking-0.2.0-source.tar.gz RELEASE_NOTES.md; do
  log "GitHub upload: $f"
  RESP=$(curl -sS -X POST \
    -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: survolocking-release" \
    -H "Content-Type: application/octet-stream" \
    --data-binary "@$RELEASE_DIR/$f" \
    "$GH_UPLOAD?name=$f")
  if echo "$RESP" | grep -q '"message"'; then
    log "ERR GitHub upload $f: $(echo "$RESP" | py_json ".get('message','?')")"
    exit 1
  fi
  log "  -> $(echo "$RESP" | py_json ".get('browser_download_url','?')")"
done

log "GitHub 完成。"

######################
# 2) Gitee
######################
GE_OWNER='smooge'
GE_REPO='survolocking'

log "Gitee: 创建 release $TAG"
RESP=$(curl -sS -X POST \
  -H "Content-Type: application/json;charset=UTF-8" \
  --data-binary "$(python3 -c "import json; print(json.dumps({'access_token':'$GE_TOKEN','tag_name':'$TAG','name':'$TITLE','target_commitish':'main','prerelease':False,'body':open(r'$BODY_FILE',encoding='utf-8').read()}, ensure_ascii=False))")" \
  "https://gitee.com/api/v5/repos/$GE_OWNER/$GE_REPO/releases")

if echo "$RESP" | grep -q '"message"'; then
  log "ERR Gitee create: $(echo "$RESP" | py_json ".get('message','?')")"
  echo "$RESP" | head -c 1000 >> "$LOG_FILE"
  exit 1
fi

GE_ID=$(echo "$RESP" | py_json "['id']")
GE_URL=$(echo "$RESP" | py_json "['html_url']")
log "Gitee release id=$GE_ID url=$GE_URL"

for f in survolocking-v0.2.0-debug.apk survolocking-0.2.0-source.tar.gz; do
  log "Gitee upload: $f"
  RESP=$(curl -sS -X POST \
    -H "Access-Token: $GE_TOKEN" \
    -F "access_token=$GE_TOKEN" \
    -F "release_id=$GE_ID" \
    -F "file=@$RELEASE_DIR/$f" \
    "https://gitee.com/api/v5/repos/$GE_OWNER/$GE_REPO/releases/$GE_ID/attach_files")
  if echo "$RESP" | grep -q '"message"'; then
    log "ERR Gitee upload $f: $(echo "$RESP" | py_json ".get('message','?')")"
    exit 1
  fi
  log "  -> $(echo "$RESP" | py_json ".get('browser_url','?')")"
done

log "Gitee 完成。"

log ""
log "==================== 发布结果 ===================="
log "GitHub: https://github.com/$GH_OWNER/$GH_REPO/releases/tag/$TAG"
log "Gitee : https://gitee.com/$GE_OWNER/$GE_REPO/releases/tag/$TAG"
log "=================================================="
