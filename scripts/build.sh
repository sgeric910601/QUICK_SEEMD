#!/bin/bash
# 編譯 Release 版、記下原始碼版本（給一鍵更新比對），並用本機固定憑證簽署。
# 進度逐行輸出；最後一行是「APP=<編譯好的 App 路徑>」。
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DIR="${BUILD_DIR:-$PWD/build}"
COMMIT=$(git rev-parse HEAD 2>/dev/null || true)
TIME=$(git log -1 --format=%ct 2>/dev/null || true)
# 去掉網址裡可能夾帶的帳號或權杖
REPO=$(git remote get-url origin 2>/dev/null | sed -E 's#//[^/@]*@#//#' || true)

command -v xcodebuild >/dev/null || { echo "✗ 需要 Xcode 才能編譯"; exit 1; }
echo "▸ 編譯中（Release，約 3–5 分鐘）…"
LOG="$BUILD_DIR/build.log"
mkdir -p "$BUILD_DIR"
if ! xcodebuild -project QUICK_SEEMD.xcodeproj -scheme QUICK_SEEMD -configuration Release \
    -derivedDataPath "$BUILD_DIR/DerivedData" \
    QS_SOURCE_COMMIT="$COMMIT" QS_SOURCE_TIME="$TIME" QS_SOURCE_REPOSITORY="$REPO" build > "$LOG" 2>&1; then
  grep -E "error:" "$LOG" | sort -u | head -20
  echo "✗ 編譯失敗（完整記錄：$LOG）"
  exit 1
fi

BUILT="$BUILD_DIR/DerivedData/Build/Products/Release/QUICK_SEEMD.app"
[ -d "$BUILT" ] || { echo "✗ 找不到編譯結果：$BUILT"; exit 1; }
# 簽署複本：Xcode 自己的產物維持原本的簽署，下次增量編譯才不會因為簽署不一致而失敗
APP="$BUILD_DIR/Signed/QUICK_SEEMD.app"
rm -rf "$APP"
mkdir -p "$BUILD_DIR/Signed"
ditto "$BUILT" "$APP"
echo "▸ 簽署中…"
"$(dirname "$0")/sign.sh" "$APP"
echo "APP=$APP"
