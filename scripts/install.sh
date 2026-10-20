#!/bin/bash
# 編譯 QUICK_SEEMD、安裝到「應用程式」資料夾並啟用空白鍵預覽擴充。
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DIR="${BUILD_DIR:-$PWD/build}"
echo "▸ 編譯中（Release）…"
xcodebuild -project QUICK_SEEMD.xcodeproj -scheme QUICK_SEEMD -configuration Release \
  -derivedDataPath "$BUILD_DIR/DerivedData" build 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)" || true
APP="$BUILD_DIR/DerivedData/Build/Products/Release/QUICK_SEEMD.app"
[ -d "$APP" ] || { echo "✗ 編譯失敗"; exit 1; }

DEST_DIR="/Applications"
[ -w "$DEST_DIR" ] || DEST_DIR="$HOME/Applications"
mkdir -p "$DEST_DIR"
DEST="$DEST_DIR/QUICK_SEEMD.app"
echo "▸ 安裝到 $DEST"
pkill -x QUICK_SEEMD 2>/dev/null || true
rm -rf "$DEST"
ditto "$APP" "$DEST"

echo "▸ 登記並啟用空白鍵預覽與 Finder 右鍵選單"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -f -R -trusted "$DEST"
pluginkit -a "$DEST/Contents/PlugIns/QuickSeemdPreview.appex"
pluginkit -a "$DEST/Contents/PlugIns/QuickSeemdFinder.appex"
pluginkit -e use -i dev.quickseemd.app.Preview
pluginkit -e use -i dev.quickseemd.app.FinderSync
/System/Library/CoreServices/pbs -update 2>/dev/null || true
qlmanage -r >/dev/null 2>&1 || true
qlmanage -r cache >/dev/null 2>&1 || true

echo "▸ 目前狀態："
pluginkit -m -i dev.quickseemd.app.Preview || true
pluginkit -m -i dev.quickseemd.app.FinderSync || true
echo "▸ 啟動 QUICK_SEEMD（在選單列待命，右鍵轉換與拖曳輪盤才能使用）"
open "$DEST"
echo "✓ 完成！空白鍵預覽、右鍵「QUICK_SEEMD 轉換」、拖曳檔案時點一下 Shift（轉換）、Command（工具）、Control（動作）叫出輪盤。"
