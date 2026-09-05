#!/usr/bin/env bash
# 发版辅助：在 main 上整理变更后打 tag。

set -euo pipefail

# 1) 提示当前版本
VERSION=$(grep -Po '(?<=versionName = ")[^"]+' android/app/build.gradle.kts | head -1 || true)
echo "当前 Android versionName: $VERSION"

# 2) 确认工作区干净
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ 工作区不干净，先 commit 或 stash 再发版。" >&2
  exit 1
fi

# 3) 拉取最新
git fetch origin
git checkout main
git pull --rebase origin main

# 4) 选择新版本号
read -rp "新版本号（例 v0.3.0）: " TAG
if [[ ! $TAG =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "版本号必须为 vX.Y.Z 形式" >&2
  exit 1
fi

# 5) 检查 CHANGELOG
if ! grep -q "## \[${TAG#v}\]" CHANGELOG.md; then
  echo "❌ CHANGELOG.md 没有 [${TAG#v}] 段，先补全再发版。" >&2
  exit 1
fi

# 6) 打 tag + push
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
echo "✓ 已推送 $TAG，记得去 GitHub → Releases 写说明。"
