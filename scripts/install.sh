#!/bin/bash
# 編譯 QUICK_SEEMD、安裝到「應用程式」資料夾，並啟用空白鍵預覽與 Finder 右鍵選單。
#   --app <路徑>      直接安裝已編譯好的 App，不重新編譯（App 內一鍵更新用）
#   --wait-pid <pid>  等這個行程自己結束後才替換（一鍵更新時等舊版結束）
set -euo pipefail
cd "$(dirname "$0")/.."

APP=""
WAIT_PID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --wait-pid) WAIT_PID="$2"; shift 2 ;;
    *) echo "未知參數：$1"; exit 2 ;;
  esac
done

if [ -z "$APP" ]; then
  while IFS= read -r line; do
    case "$line" in
      APP=*) APP="${line#APP=}" ;;
      *) echo "$line" ;;
    esac
  done < <(scripts/build.sh)
fi
[ -n "$APP" ] && [ -d "$APP" ] || { echo "✗ 沒有可安裝的 App"; exit 1; }

DEST_DIR="/Applications"
[ -w "$DEST_DIR" ] || DEST_DIR="$HOME/Applications"
mkdir -p "$DEST_DIR"
DEST="$DEST_DIR/QUICK_SEEMD.app"
# 中途失敗時盡量把 App 重新打開，使用者才看得到失敗訊息
trap 'echo "✗ 安裝失敗"; [ -d "$DEST" ] && open "$DEST"' ERR

if [ -n "$WAIT_PID" ]; then
  echo "▸ 等候舊版結束…"
  for _ in $(seq 1 120); do kill -0 "$WAIT_PID" 2>/dev/null || break; sleep 0.5; done
fi
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
