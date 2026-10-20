#!/bin/bash
# 移除 QUICK_SEEMD 與它的擴充。
set -uo pipefail
for d in /Applications "$HOME/Applications"; do
  if [ -d "$d/QUICK_SEEMD.app" ]; then
    pkill -x QUICK_SEEMD 2>/dev/null
    pluginkit -r "$d/QUICK_SEEMD.app/Contents/PlugIns/QuickSeemdPreview.appex" 2>/dev/null
    pluginkit -r "$d/QUICK_SEEMD.app/Contents/PlugIns/QuickSeemdFinder.appex" 2>/dev/null
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$d/QUICK_SEEMD.app" 2>/dev/null
    rm -rf "$d/QUICK_SEEMD.app" && echo "已移除 $d/QUICK_SEEMD.app"
  fi
done
qlmanage -r >/dev/null 2>&1
echo "✓ 已解除安裝"
