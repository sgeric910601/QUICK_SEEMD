#!/bin/bash
# 編譯 Release 版、記下原始碼版本（給一鍵更新比對），並用本機固定憑證簽署。
# 進度逐行輸出；最後一行是「APP=<編譯好的 App 路徑>」。
set -euo pipefail
# 先記下腳本資料夾的絕對路徑：在 scripts/ 裡執行時，cd 之後相對路徑就失效了
SCRIPTS="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPTS/.."

BUILD_DIR="${BUILD_DIR:-$PWD/build}"
COMMIT=$(git rev-parse HEAD 2>/dev/null || true)
TIME=$(git log -1 --format=%ct 2>/dev/null || true)
# 版本號來自 VERSION 檔（發佈腳本會更新它）；QS_VERSION 可暫時覆寫（測試用）
VERSION="${QS_VERSION:-$(cat VERSION 2>/dev/null || echo 1.0.0)}"
BUILD_NUMBER=$(git rev-list --count HEAD 2>/dev/null || echo 1)
# 去掉網址裡可能夾帶的帳號或權杖
REPO=$(git remote get-url origin 2>/dev/null | sed -E 's#//[^/@]*@#//#' || true)

command -v xcodebuild >/dev/null || { echo "✗ 需要 Xcode 才能編譯"; exit 1; }
# 變數後面緊接中文或全形字時一律寫成 ${…}：macOS 內建 bash 3.2 在 UTF-8 語系下會把那些字當成變數名的一部分
echo "▸ 編譯 ${VERSION}（Release，約 3–5 分鐘）…"
LOG="$BUILD_DIR/build.log"
mkdir -p "$BUILD_DIR"
if ! xcodebuild -project QUICK_SEEMD.xcodeproj -scheme QUICK_SEEMD -configuration Release \
    -derivedDataPath "$BUILD_DIR/DerivedData" \
    MARKETING_VERSION="$VERSION" CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
    QS_SOURCE_COMMIT="$COMMIT" QS_SOURCE_TIME="$TIME" QS_SOURCE_REPOSITORY="$REPO" build > "$LOG" 2>&1; then
  # 記錄裡沒有 error: 時 grep 會失敗，加上 pipefail 會讓腳本在印出失敗訊息前就結束
  grep -E "error:" "$LOG" | sort -u | head -20 || true
  echo "✗ 編譯失敗（完整記錄：${LOG}）"
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
"$SCRIPTS/sign.sh" "$APP"
echo "APP=$APP"
