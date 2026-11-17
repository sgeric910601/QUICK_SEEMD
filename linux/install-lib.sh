# shellcheck shell=bash
# QUICK_SEEMD 安裝與解除安裝共用的函式（由 install.sh、uninstall.sh 載入，不單獨執行）。
#
# 安全原則：
# - 只寫使用者自己的資料夾（~/.local、~/.config），不需要 root。
# - 安裝紀錄（install-manifest.txt）列出裝過的每個檔案。只有「在紀錄裡」而且「位在固定的
#   安裝位置」的檔案才會被取代或刪除；紀錄裡其他路徑（例如被改過的紀錄檔）一律略過。
# - 經過符號連結跑到安裝位置外面的路徑也一律略過，不會刪到別處的檔案。

QS_ICON_NAME=dev.quickseemd.App
QS_SYMBOLIC_NAME=quickseemd-symbolic
QS_LAUNCHERS=(quickseemd quickseemd-agent quickseemd-preview)
QS_TMP_FILE=""
declare -A QS_OLD=()

qs_say() { printf '%s\n' "$*"; }
qs_warn() { printf '⚠ %s\n' "$*" >&2; }
qs_die() {
  printf '✗ %s\n' "$*" >&2
  exit 1
}

# XDG 規格：沒設定或不是絕對路徑就用預設值
qs_xdg_dir() {
  case "${1:-}" in
    /*) printf '%s' "$1" ;;
    *) printf '%s' "$2" ;;
  esac
}

qs_init_paths() {
  [ -n "${HOME:-}" ] || qs_die "沒有設定 HOME"
  case "$HOME" in /*) ;; *) qs_die "HOME 必須是絕對路徑：$HOME" ;; esac
  QS_DATA_HOME=$(qs_xdg_dir "${XDG_DATA_HOME:-}" "$HOME/.local/share")
  QS_CONFIG_HOME=$(qs_xdg_dir "${XDG_CONFIG_HOME:-}" "$HOME/.config")
  QS_CACHE_HOME=$(qs_xdg_dir "${XDG_CACHE_HOME:-}" "$HOME/.cache")
  QS_BIN_DIR=$HOME/.local/bin
  QS_DATA=$QS_DATA_HOME/quickseemd
  QS_LIB=$QS_DATA/lib
  QS_SHARE=$QS_DATA/data
  QS_MODELS=$QS_DATA/models
  QS_VENV=$QS_DATA/venv-rembg
  QS_MANIFEST=$QS_DATA/install-manifest.txt
  QS_ICONS=$QS_DATA_HOME/icons/hicolor
  QS_APPS=$QS_DATA_HOME/applications
  QS_DBUS=$QS_DATA_HOME/dbus-1/services
  QS_NEMO_EXT=$QS_DATA_HOME/nemo-python/extensions
  QS_AUTOSTART=$QS_CONFIG_HOME/autostart
  QS_CONFIG=$QS_CONFIG_HOME/quickseemd
  QS_CACHE=$QS_CACHE_HOME/quickseemd
}

# 會寫進啟動檔、.desktop、D-Bus 服務檔的路徑：不能有會被誤解的字元
qs_check_path_chars() {
  local p
  for p in "$@"; do
    case "$p" in
      *[\"\'\`\$\\%\&]*)
        qs_die "安裝路徑含有不支援的字元（引號、\$、\\、%、&）：$p"
        ;;
    esac
    if [[ "$p" =~ [[:cntrl:]] ]]; then
      qs_die "安裝路徑含有不支援的字元（控制字元）：$p"
    fi
  done
}

# ---------------------------------------------------------------- 哪些路徑是我們的

# 印出這個路徑所屬的安裝根目錄；不在固定安裝位置就回傳 1（只看文字，不碰檔案）
qs_root_of() {
  local p=$1 name
  case "$p" in /*) ;; *) return 1 ;; esac
  case "$p" in *//* | */./* | */../* | */. | */..) return 1 ;; esac
  case "$p" in
    "$QS_LIB"/*)
      printf '%s' "$QS_LIB"
      return 0
      ;;
    "$QS_SHARE"/*)
      printf '%s' "$QS_SHARE"
      return 0
      ;;
  esac
  for name in "${QS_LAUNCHERS[@]}"; do
    if [ "$p" = "$QS_BIN_DIR/$name" ]; then
      printf '%s' "$QS_BIN_DIR"
      return 0
    fi
  done
  case "$p" in
    "$QS_APPS/$QS_ICON_NAME.desktop") printf '%s' "$QS_APPS" ;;
    "$QS_DBUS/org.nemo.Preview.service" | "$QS_DBUS/dev.quickseemd.Agent.service")
      printf '%s' "$QS_DBUS"
      ;;
    "$QS_NEMO_EXT/quickseemd_nemo.py") printf '%s' "$QS_NEMO_EXT" ;;
    "$QS_AUTOSTART/dev.quickseemd.Agent.desktop") printf '%s' "$QS_AUTOSTART" ;;
    "$QS_ICONS/symbolic/apps/$QS_SYMBOLIC_NAME.svg") printf '%s' "$QS_ICONS/symbolic/apps" ;;
    "$QS_ICONS"/*/apps/"$QS_ICON_NAME.png")
      [[ "${p#"$QS_ICONS"/}" =~ ^[0-9]+x[0-9]+/apps/[^/]+$ ]] || return 1
      printf '%s' "${p%/*}"
      ;;
    *) return 1 ;;
  esac
}

# 解析符號連結後，檔案所在的資料夾仍在安裝根目錄裡
qs_inside_root() {
  local dir real_root
  dir=$(realpath -m -- "$(dirname -- "$1")") || return 1
  real_root=$(realpath -m -- "$2") || return 1
  [ "$dir" = "$real_root" ] || [[ "$dir" == "$real_root"/* ]]
}

qs_is_ours() {
  local root
  root=$(qs_root_of "$1") || return 1
  qs_inside_root "$1" "$root"
}

# 讀取上次的安裝紀錄到 QS_OLD（只收位在安裝位置的路徑）
qs_read_manifest() {
  QS_OLD=()
  [ -f "$QS_MANIFEST" ] || return 0
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    if qs_is_ours "$line"; then
      QS_OLD["$line"]=1
    else
      qs_warn "略過安裝紀錄裡不在 QUICK_SEEMD 安裝位置的路徑：$line"
    fi
  done <"$QS_MANIFEST"
  return 0
}

# 把路徑清單寫成安裝紀錄（暫存檔＋改名）
qs_write_manifest() {
  mkdir -p -- "$QS_DATA"
  local tmp
  tmp=$(mktemp -- "$QS_DATA/.install-manifest.XXXXXX")
  QS_TMP_FILE=$tmp
  if [ $# -gt 0 ]; then
    printf '%s\n' "$@" | LC_ALL=C sort -u >"$tmp"
  else
    : >"$tmp"
  fi
  chmod 644 -- "$tmp"
  mv -fT -- "$tmp" "$QS_MANIFEST"
  QS_TMP_FILE=""
}

# ---------------------------------------------------------------- 放檔案與刪檔案

# 用暫存檔＋改名放好一個檔案；目的地必須不存在，或是上次安裝紀錄裡的檔案
qs_put_file() {
  local src=$1 dest=$2 mode=$3 dir
  dir=$(dirname -- "$dest")
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ -z "${QS_OLD[$dest]+x}" ]; then
      qs_die "$dest 不是 QUICK_SEEMD 安裝的，停止安裝"
    fi
    if [ -d "$dest" ] && [ ! -L "$dest" ]; then
      qs_die "$dest 是資料夾，停止安裝"
    fi
  fi
  mkdir -p -- "$dir"
  QS_TMP_FILE=$(mktemp -- "$dir/.quickseemd-install.XXXXXX")
  cat -- "$src" >"$QS_TMP_FILE"
  chmod -- "$mode" "$QS_TMP_FILE"
  mv -fT -- "$QS_TMP_FILE" "$dest"
  QS_TMP_FILE=""
}

# Python 為這個模組產生的 .pyc（在旁邊的 __pycache__ 裡）
qs_remove_pyc() {
  local dir stem f
  dir=$(dirname -- "$1")/__pycache__
  stem=$(basename -- "$1" .py)
  if [ ! -d "$dir" ] || [ -L "$dir" ]; then
    return 0
  fi
  for f in "$dir/$stem".cpython-*.pyc; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then
      rm -f -- "$f"
    fi
  done
  return 0
}

# 刪掉一個我們裝的檔案（不是我們的就略過）
qs_remove_owned() {
  local p=$1
  if ! qs_is_ours "$p"; then
    qs_warn "略過不在 QUICK_SEEMD 安裝位置的路徑：$p"
    return 0
  fi
  if [ -L "$p" ] || [ -f "$p" ]; then
    rm -f -- "$p"
  fi
  case "$p" in *.py) qs_remove_pyc "$p" ;; esac
  return 0
}

# 可以順手刪掉的空資料夾：QUICK_SEEMD 自己的資料夾、圖示的尺寸資料夾、擴充的 __pycache__
qs_prunable() {
  case "$1" in
    "$QS_DATA" | "$QS_DATA"/*) return 0 ;;
    "$QS_ICONS"/*) return 0 ;;
    "$QS_NEMO_EXT/__pycache__") return 0 ;;
  esac
  return 1
}

# 從這些資料夾往上，刪掉變空的資料夾（rmdir 只能刪空的）
qs_prune_dirs() {
  local d
  for d in "$@"; do
    while [ -d "$d" ] && [ ! -L "$d" ] && qs_prunable "$d"; do
      rmdir -- "$d" 2>/dev/null || break
      d=$(dirname -- "$d")
    done
  done
  return 0
}

qs_refresh_icon_cache() {
  local tool=/usr/bin/gtk-update-icon-cache
  if [ -x "$tool" ] && [ -d "$QS_ICONS" ]; then
    "$tool" -f -t -q "$QS_ICONS" || qs_warn "無法更新圖示快取（不影響使用）"
  fi
  return 0
}

# 問使用者 y/N；沒有回答（例如不是在終端機執行）一律當成「不要」
qs_confirm() {
  local answer=""
  read -r -p "$1" answer || return 1
  case "$answer" in
    y | Y | yes | YES | Yes) return 0 ;;
  esac
  return 1
}
