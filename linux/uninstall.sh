#!/bin/bash
# 解除安裝 QUICK_SEEMD：只刪安裝紀錄（install-manifest.txt）裡、位在安裝位置的檔案。
#
#   ./uninstall.sh           移除程式、選單捷徑、空白鍵預覽、Nemo 右鍵選單、圖示、自動啟動
#   ./uninstall.sh --purge   另外刪除去背元件、下載的模型、設定與快取
#
# 有 QUICK_SEEMD 程式正在執行時，會先列出來問你要不要結束（只對完全符合的程式送出結束訊號）。
set -euo pipefail

QS_LINUX_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=install-lib.sh
source "$QS_LINUX_DIR/install-lib.sh"

QS_PROC=/proc
QS_PYTHON=/usr/bin/python3
QS_STOP_WAIT_SECONDS=3
# 我們的程式：/usr/bin/python3 [-s] -m <模組>，而且 PYTHONPATH 指向安裝的程式資料夾
QS_OUR_MODULES=(quickseemd quickseemd.agent quickseemd.previewer quickseemd.worker)

usage() {
  cat <<'EOF'
用法：uninstall.sh [--purge]

  移除 QUICK_SEEMD 安裝的檔案（只刪安裝紀錄裡的檔案）。

選項：
  --purge      另外刪除去背元件、下載的模型、設定（~/.config/quickseemd）與快取
  -h, --help   顯示這段說明
EOF
}

# ---------------------------------------------------------------- 執行中的程式

# 這個程序是不是我們的程式（看完整的命令列與 PYTHONPATH）
qs_is_our_process() {
  local dir=$1 module i
  local -a argv=()
  [ -r "$dir/cmdline" ] || return 1
  mapfile -d '' -t argv <"$dir/cmdline" 2>/dev/null || return 1
  [ "${#argv[@]}" -ge 3 ] || return 1
  [ "${argv[0]}" = "$QS_PYTHON" ] || return 1
  for ((i = 1; i < ${#argv[@]} - 1; i++)); do
    if [ "${argv[$i]}" = "-m" ]; then
      module=${argv[$((i + 1))]}
      break
    fi
  done
  [ -n "${module:-}" ] || return 1
  local known=0 m
  for m in "${QS_OUR_MODULES[@]}"; do
    if [ "$module" = "$m" ]; then
      known=1
    fi
  done
  [ "$known" = 1 ] || return 1
  [ -r "$dir/environ" ] || return 1
  tr '\0' '\n' <"$dir/environ" 2>/dev/null | grep -Fxq -- "PYTHONPATH=$QS_LIB"
}

# 印出我們程式的 pid（只看自己這個使用者的程序）
qs_find_pids() {
  local dir pid me
  me=$(id -u)
  for dir in "$QS_PROC"/[0-9]*; do
    [ -d "$dir" ] || continue
    pid=${dir##*/}
    [ "$pid" != "$$" ] || continue
    [ "$(stat -c %u -- "$dir" 2>/dev/null || echo x)" = "$me" ] || continue
    if qs_is_our_process "$dir"; then
      printf '%s\n' "$pid"
    fi
  done | sort -n
}

qs_stop_pids() {
  local pid waited=0
  kill -TERM -- "$@" 2>/dev/null || true
  while [ "$waited" -lt "$QS_STOP_WAIT_SECONDS" ]; do
    local alive=0
    for pid in "$@"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    [ "$alive" = 1 ] || return 0
    sleep 1
    waited=$((waited + 1))
  done
  qs_warn "有些 QUICK_SEEMD 程式沒有結束，登出再登入後就會停止"
}

qs_offer_stop() {
  local -a pids=()
  local pid
  mapfile -t pids < <(qs_find_pids)
  [ ${#pids[@]} -gt 0 ] || return 0
  qs_say "▸ 這些 QUICK_SEEMD 程式正在執行："
  for pid in "${pids[@]}"; do
    printf '    %s  %s\n' "$pid" "$(tr '\0' ' ' <"$QS_PROC/$pid/cmdline" 2>/dev/null)"
  done
  if qs_confirm "要結束它們嗎？[y/N] "; then
    qs_stop_pids "${pids[@]}"
  else
    qs_say "沒有結束程式；檔案移除後，它們在下次登入時就不會再啟動。"
  fi
  return 0
}

# ---------------------------------------------------------------- 移除

remove_manifest_files() {
  local line
  local -a dirs=()
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    qs_remove_owned "$line"
    if qs_is_ours "$line"; then
      dirs+=("$(dirname -- "$line")" "$(dirname -- "$line")/__pycache__")
    fi
  done <"$QS_MANIFEST"
  rm -f -- "$QS_MANIFEST"
  dirs+=("$QS_DATA")
  qs_prune_dirs "${dirs[@]}"
}

# 刪除一個資料夾；如果它是符號連結，只刪連結本身
qs_remove_tree() {
  local p=$1
  if [ -L "$p" ]; then
    rm -f -- "$p"
  elif [ -d "$p" ]; then
    rm -rf --one-file-system -- "$p"
  else
    return 0
  fi
  qs_say "  已刪除 $p"
}

purge() {
  qs_say "▸ 刪除去背元件、模型、設定與快取"
  qs_remove_tree "$QS_VENV"
  qs_remove_tree "$QS_MODELS"
  qs_remove_tree "$QS_CONFIG"
  qs_remove_tree "$QS_CACHE"
  qs_prune_dirs "$QS_DATA"
}

main() {
  local want_purge=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --purge) want_purge=1 ;;
      -h | --help)
        usage
        return 0
        ;;
      *)
        printf '✗ 未知的參數：%s\n' "$1" >&2
        usage >&2
        return 2
        ;;
    esac
    shift
  done
  qs_init_paths
  qs_offer_stop
  if [ -f "$QS_MANIFEST" ]; then
    qs_say "▸ 移除 QUICK_SEEMD 安裝的檔案"
    remove_manifest_files
    qs_refresh_icon_cache
  else
    qs_say "找不到安裝紀錄（$QS_MANIFEST），沒有可以移除的程式檔案。"
  fi
  if [ "$want_purge" = 1 ]; then
    purge
  else
    qs_say "  去背元件、模型與設定都保留著（要一併刪除請加 --purge）。"
  fi
  qs_say "✓ 已解除安裝。請執行 nemo -q 後重新打開 Nemo，右鍵選單才會消失。"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
