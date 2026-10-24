#!/bin/bash
# 發佈新版本到 GitHub Release：scripts/release.sh <版本號，例如 1.1.0>
#   1. 更新 VERSION 並提交
#   2. 編譯、用本機憑證簽署，做成 DMG
#   3. 推送提交與標籤，建立 GitHub Release 並附上 DMG
# App 內的一鍵更新會下載這個 DMG。必須在保有本機簽署憑證的這台 Mac 上發佈，
# 新版本才會跟舊版是同一個簽署身分，使用者已經允許過的權限才會保留。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?用法：scripts/release.sh <版本號，例如 1.1.0>}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "✗ 版本號格式要像 1.2.3"; exit 1; }
TAG="v$VERSION"
GH=$(command -v gh) || { echo "✗ 需要 GitHub 命令列工具（gh），並先執行 gh auth login"; exit 1; }
[ -f "$HOME/Library/Application Support/QUICK_SEEMD/Signing/signing.keychain-db" ] || {
  echo "✗ 這台 Mac 沒有本機簽署憑證。用新憑證發佈的話，使用者更新後要重新允許權限。"
  echo "  確定要這樣做，請先執行 scripts/install.sh 建立憑證。"
  exit 1
}
[ -z "$(git status --porcelain)" ] || { echo "✗ 還有沒提交的修改"; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "✗ 請在 main 分支發佈"; exit 1; }
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then echo "✗ 標籤 $TAG 已經存在"; exit 1; fi

GIT_AUTH=(-c credential.helper= -c "credential.helper=!\"$GH\" auth git-credential")
git "${GIT_AUTH[@]}" fetch -q origin main
git merge-base --is-ancestor origin/main HEAD || { echo "✗ GitHub 上有本機沒有的提交，請先同步"; exit 1; }
PREV=$(git describe --tags --abbrev=0 2>/dev/null || true)

echo "▸ 準備發佈 $VERSION"
if [ "$(cat VERSION 2>/dev/null)" != "$VERSION" ]; then
  echo "$VERSION" > VERSION
  git add VERSION
  git commit -q -m "發佈 $VERSION"
fi

APP=""
while IFS= read -r line; do
  case "$line" in
    APP=*) APP="${line#APP=}" ;;
    *) echo "$line" ;;
  esac
done < <(scripts/build.sh)
[ -n "$APP" ] && [ -d "$APP" ] || { echo "✗ 編譯失敗"; exit 1; }

echo "▸ 製作 DMG…"
OUT="$PWD/build/Release"
DMG="$OUT/QUICK_SEEMD-$VERSION.dmg"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"
ditto "$APP" "$STAGE/QUICK_SEEMD.app"
ln -s /Applications "$STAGE/應用程式"
rm -f "$DMG"
hdiutil create -quiet -volname "QUICK_SEEMD $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DMG"
scripts/sign.sh "$DMG"

if [ -n "$PREV" ]; then
  CHANGES=$(git log --format='- %s' "$PREV..HEAD" | grep -v -E '^- 發佈 [0-9.]+$' || true)
else
  CHANGES=$(git log --format='- %s' | grep -v -E '^- 發佈 [0-9.]+$' || true)
fi
NOTES="## 更新內容
${CHANGES:-- 小幅修正}

## 安裝
- 已經安裝的話：選單列圖示 →「更新到最新版本」，或「設定 › 更新」→「立即更新」。
- 第一次安裝：打開 DMG，把 QUICK_SEEMD 拖到「應用程式」，再從「應用程式」打開。"

echo "▸ 推送 $TAG…"
git tag -a "$TAG" -m "QUICK_SEEMD $VERSION"
git "${GIT_AUTH[@]}" push -q origin main "$TAG"

echo "▸ 建立 GitHub Release…"
"$GH" release create "$TAG" "$DMG" --title "QUICK_SEEMD $VERSION" --notes "$NOTES"
echo "✓ 已發佈 $TAG（$DMG）"
