#!/bin/bash
# QUICK_SEEMD Linux 版安裝程式：只裝在使用者自己的資料夾（~/.local、~/.config），不需要 root。
#
#   ./install.sh                  安裝或更新：程式、選單捷徑、空白鍵預覽、Nemo 右鍵選單、圖示、登入自動啟動
#   ./install.sh --deps           另外安裝需要的系統套件（先列出並模擬，確認後才跳出密碼視窗）
#   ./install.sh --models         另外下載去背元件與臉部偵測模型（已經有的會略過）
#   ./install.sh --no-autostart   不要登入時自動啟動背景程式
#
# 只會取代自己上次裝的檔案（看安裝紀錄）；遇到不是自己裝的同名檔案就停止，絕不覆蓋。
# 不會重新啟動 Nemo，也不會執行 apt autoremove。
set -euo pipefail
shopt -u patsub_replacement 2>/dev/null || true

QS_LINUX_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=install-lib.sh
source "$QS_LINUX_DIR/install-lib.sh"

QS_REPO_DIR=$(dirname -- "$QS_LINUX_DIR")
QS_PKG_SRC=$QS_LINUX_DIR/quickseemd
QS_DATA_SRC=$QS_LINUX_DIR/data
QS_TEMPLATES=$QS_DATA_SRC/templates
QS_APP_ICON_SRC=$QS_REPO_DIR/Design/AppIcon-1024.png
QS_SYMBOLIC_SRC=$QS_DATA_SRC/icons/$QS_SYMBOLIC_NAME.svg
QS_ICON_SIZES=(16 24 32 48 64 128 256 512)
QS_NEMO_HINT="請執行 nemo -q 後重新打開 Nemo，右鍵選單才會出現"

# 外部程式一律用固定的絕對路徑
QS_PYTHON=/usr/bin/python3
QS_APT_GET=/usr/bin/apt-get
QS_PKEXEC=/usr/bin/pkexec
QS_DPKG_QUERY=/usr/bin/dpkg-query
QS_SHA256SUM=/usr/bin/sha256sum

# 執行時需要的系統套件（Ubuntu 24.04）
APT_PACKAGES=(
  nemo-python
  python3-gi python3-gi-cairo python3-cairo python3-xlib python3-pil python3-numpy
  python3-venv python3-yaml python3-markdown-it python3-pygments
  python3-charset-normalizer python3-chardet python3-docx
  python3-pikepdf qpdf poppler-utils ghostscript
  python3-opencv opencv-data python3-pyzbar
  gir1.2-gtk-3.0 gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit2-4.1 gir1.2-poppler-0.18
  gir1.2-ayatanaappindicator3-0.1 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
  gstreamer1.0-gtk3 gstreamer1.0-libav gstreamer1.0-plugins-good
  ffmpeg
  libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui
  libheif-examples libheif-plugin-x265 libheif-plugin-libde265
  libimage-exiftool-perl pngquant libjpeg-turbo-progs
  tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-kor
  pandoc weasyprint libarchive-tools 7zip
)

# 啟動檔名稱 → Python 模組
declare -A QS_MODULE_OF=(
  [quickseemd]=quickseemd
  [quickseemd-agent]=quickseemd.agent
  [quickseemd-preview]=quickseemd.previewer
)

QS_STAGE=""
PLAN_SRC=()
PLAN_DEST=()
PLAN_MODE=()
QS_REMEMBER=() # 不安裝、但要留在紀錄裡的路徑（使用者關掉的自動啟動）

usage() {
  cat <<'EOF'
用法：install.sh [選項]

  安裝或更新 QUICK_SEEMD（只裝在你自己的資料夾，不需要 root）。

選項：
  --deps           另外安裝需要的系統套件（先列出、模擬安裝，確認後才會跳出密碼視窗）
  --models         另外下載去背元件（約 1 GB）與臉部偵測模型；已經有的會略過
  --no-autostart   不要在登入時自動啟動 QUICK_SEEMD 背景程式
  -h, --help       顯示這段說明
EOF
}

cleanup() {
  if [ -n "$QS_TMP_FILE" ] && [ -f "$QS_TMP_FILE" ]; then
    rm -f -- "$QS_TMP_FILE"
  fi
  if [ -n "$QS_STAGE" ] && [ -d "$QS_STAGE" ]; then
    rm -rf -- "$QS_STAGE"
  fi
}

# ---------------------------------------------------------------- 事前檢查

preflight() {
  [ -x "$QS_PYTHON" ] || qs_die "找不到 $QS_PYTHON"
  "$QS_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
    qs_die "需要 Python 3.12 以上（$QS_PYTHON）"
  "$QS_PYTHON" -c 'import PIL' 2>/dev/null || qs_die "缺少 Pillow（python3-pil），請用 --deps 安裝"
  local f
  for f in "$QS_PKG_SRC/__init__.py" "$QS_REPO_DIR/VERSION" "$QS_APP_ICON_SRC" "$QS_SYMBOLIC_SRC" \
    "$QS_PKG_SRC/nemo/quickseemd_nemo.py"; do
    [ -f "$f" ] || qs_die "安裝來源不完整，找不到 $f"
  done
  if [ ! -e /usr/lib/x86_64-linux-gnu/nemo/extensions-3.0/libnemo-python.so ]; then
    qs_warn "找不到 nemo-python：Nemo 右鍵選單需要它（可以用 --deps 安裝）"
  fi
  qs_check_path_chars "$HOME" "$QS_DATA_HOME" "$QS_CONFIG_HOME" "$QS_BIN_DIR"
}

# ---------------------------------------------------------------- 要裝哪些檔案

plan_add() {
  PLAN_SRC+=("$1")
  PLAN_DEST+=("$2")
  PLAN_MODE+=("$3")
}

# 整個資料夾（略過 __pycache__、.pyc、隱藏檔與指定的子資料夾）
plan_tree() {
  local src=$1 dest=$2 skip=${3:-} f
  local -a prune=(-name __pycache__ -o -name '.*')
  if [ -n "$skip" ]; then
    prune+=(-o -path "$src/$skip")
  fi
  while IFS= read -r -d '' f; do
    plan_add "$f" "$dest/${f#"$src"/}" 644
  done < <(find "$src" \( "${prune[@]}" \) -prune -o -type f ! -name '*.pyc' -print0 | LC_ALL=C sort -z)
}

# 寫進 .desktop／D-Bus 服務檔的執行路徑：有空白就加雙引號
exec_value() {
  case "$1" in
    *' '*) printf '"%s"' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

# 範本 目的檔 名稱=值…（把 @名稱@ 換成值）
render() {
  local template=$1 out=$2 text pair
  shift 2
  text=$(<"$template")
  for pair in "$@"; do
    text=${text//"@${pair%%=*}@"/${pair#*=}}
  done
  printf '%s\n' "$text" >"$out"
}

make_icons() {
  "$QS_PYTHON" - "$QS_APP_ICON_SRC" "$QS_STAGE/icons" "${QS_ICON_SIZES[@]}" <<'PY'
# 從 1024 像素的程式圖示縮出各種尺寸
import sys
from pathlib import Path

from PIL import Image

source, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
with Image.open(source) as img:
    img = img.convert("RGBA")
    for size in (int(s) for s in sys.argv[3:]):
        img.resize((size, size), Image.LANCZOS).save(out / f"{size}.png", "PNG", optimize=True)
PY
}

plan_rendered() {
  local name lib_q stage=$QS_STAGE
  mkdir -p -- "$stage/bin" "$stage/entries"
  printf -v lib_q '%q' "$QS_LIB"
  for name in "${QS_LAUNCHERS[@]}"; do
    render "$QS_TEMPLATES/launcher.in" "$stage/bin/$name" "LIB=$lib_q" "MODULE=${QS_MODULE_OF[$name]}"
    plan_add "$stage/bin/$name" "$QS_BIN_DIR/$name" 755
  done
  local app agent preview
  app=$(exec_value "$QS_BIN_DIR/quickseemd")
  agent=$(exec_value "$QS_BIN_DIR/quickseemd-agent")
  preview=$(exec_value "$QS_BIN_DIR/quickseemd-preview")
  render "$QS_TEMPLATES/$QS_ICON_NAME.desktop.in" "$stage/entries/app.desktop" "APP_EXEC=$app"
  plan_add "$stage/entries/app.desktop" "$QS_APPS/$QS_ICON_NAME.desktop" 644
  render "$QS_TEMPLATES/org.nemo.Preview.service.in" "$stage/entries/preview.service" \
    "PREVIEW_EXEC=$preview"
  plan_add "$stage/entries/preview.service" "$QS_DBUS/org.nemo.Preview.service" 644
  render "$QS_TEMPLATES/dev.quickseemd.Agent.service.in" "$stage/entries/agent.service" \
    "AGENT_EXEC=$agent"
  plan_add "$stage/entries/agent.service" "$QS_DBUS/dev.quickseemd.Agent.service" 644
  if [ "$WANT_AUTOSTART" = 1 ]; then
    render "$QS_TEMPLATES/dev.quickseemd.Agent.autostart.in" "$stage/entries/autostart.desktop" \
      "AGENT_EXEC=$agent"
    plan_add "$stage/entries/autostart.desktop" "$QS_AUTOSTART/dev.quickseemd.Agent.desktop" 644
  fi
}

plan_icons() {
  local size
  make_icons
  for size in "${QS_ICON_SIZES[@]}"; do
    plan_add "$QS_STAGE/icons/$size.png" "$QS_ICONS/${size}x${size}/apps/$QS_ICON_NAME.png" 644
  done
  plan_add "$QS_SYMBOLIC_SRC" "$QS_ICONS/symbolic/apps/$QS_SYMBOLIC_NAME.svg" 644
}

plan_all() {
  plan_tree "$QS_PKG_SRC" "$QS_LIB/quickseemd"
  plan_add "$QS_REPO_DIR/VERSION" "$QS_LIB/quickseemd/VERSION" 644
  plan_tree "$QS_DATA_SRC" "$QS_SHARE" templates
  plan_add "$QS_PKG_SRC/nemo/quickseemd_nemo.py" "$QS_NEMO_EXT/quickseemd_nemo.py" 644
  plan_rendered
  plan_icons
}

# ---------------------------------------------------------------- 登入自動啟動

# 背景程式自己也會建立／刪除同一個自動啟動檔（系統匣的「登入時自動啟動」）
qs_autostart_path() {
  printf '%s' "$QS_AUTOSTART/dev.quickseemd.Agent.desktop"
}

# 上次裝過、後來被刪掉＝使用者在系統匣關掉了：這次不再打開，並把路徑留在紀錄裡記住這個選擇
decide_autostart() {
  local path
  path=$(qs_autostart_path)
  if [ "$WANT_AUTOSTART" = 1 ] && [ -n "${QS_OLD[$path]+x}" ] && [ ! -e "$path" ]; then
    WANT_AUTOSTART=0
    QS_REMEMBER+=("$path")
    qs_say "▸ 你之前關掉了登入時自動啟動，維持關閉（要打開請在系統匣勾選）"
  fi
}

# 背景程式自己建立的自動啟動檔（名稱是 QUICK_SEEMD、執行背景程式）：當成自己的檔案沿用
adopt_app_autostart() {
  local path
  path=$(qs_autostart_path)
  if [ "$WANT_AUTOSTART" != 1 ] || [ -n "${QS_OLD[$path]+x}" ] || [ ! -f "$path" ] ||
    [ -L "$path" ]; then
    return 0
  fi
  if grep -qx 'Name=QUICK_SEEMD' -- "$path" &&
    grep -Eq '^Exec=.*quickseemd(-agent|\.agent)' -- "$path"; then
    QS_OLD["$path"]=1
    qs_say "▸ 沿用 QUICK_SEEMD 背景程式建立的自動啟動檔"
  fi
  return 0
}

# ---------------------------------------------------------------- 衝突檢查與安裝

check_conflicts() {
  local -a conflicts=()
  local dest
  for dest in "${PLAN_DEST[@]}"; do
    qs_root_of "$dest" >/dev/null || qs_die "內部錯誤：$dest 不在安裝位置"
    if [ -e "$dest" ] || [ -L "$dest" ]; then
      if [ -z "${QS_OLD[$dest]+x}" ] || { [ -d "$dest" ] && [ ! -L "$dest" ]; }; then
        conflicts+=("$dest")
      fi
    fi
  done
  if [ ${#conflicts[@]} -gt 0 ]; then
    printf '✗ 下列檔案已經存在，而且不是 QUICK_SEEMD 安裝的。為了不覆蓋你的檔案，安裝已停止：\n' >&2
    printf '    %s\n' "${conflicts[@]}" >&2
    printf '  請先把它們移到別的地方，再重新執行安裝。\n' >&2
    exit 1
  fi
}

install_files() {
  local i
  for i in "${!PLAN_DEST[@]}"; do
    qs_put_file "${PLAN_SRC[$i]}" "${PLAN_DEST[$i]}" "${PLAN_MODE[$i]}"
  done
}

# 上次裝過、這次不再需要的檔案（舊版的模組、取消的自動啟動）
remove_stale() {
  local -A planned=()
  local -a dirs=()
  local dest
  for dest in "${PLAN_DEST[@]}" "${QS_REMEMBER[@]}"; do
    planned["$dest"]=1
  done
  for dest in "${!QS_OLD[@]}"; do
    if [ -z "${planned[$dest]+x}" ]; then
      qs_remove_owned "$dest"
      dirs+=("$(dirname -- "$dest")")
    fi
  done
  if [ ${#dirs[@]} -gt 0 ]; then
    qs_prune_dirs "${dirs[@]}"
  fi
}

install_all() {
  qs_say "▸ 安裝 QUICK_SEEMD $(<"$QS_REPO_DIR/VERSION") 到 $QS_DATA"
  # 先把「舊紀錄＋這次要裝的」寫進紀錄：中途失敗時，下次安裝仍認得這些檔案
  qs_write_manifest "${!QS_OLD[@]}" "${PLAN_DEST[@]}"
  install_files
  remove_stale
  qs_write_manifest "${PLAN_DEST[@]}" "${QS_REMEMBER[@]}"
  qs_refresh_icon_cache
  qs_say "✓ 已安裝 ${#PLAN_DEST[@]} 個檔案（清單：$QS_MANIFEST）"
}

# ---------------------------------------------------------------- --deps：系統套件

qs_pkg_installed() {
  local status
  status=$("$QS_DPKG_QUERY" -W -f='${Status}' "$1" 2>/dev/null) || return 1
  [ "$status" = "install ok installed" ]
}

step_deps() {
  local -a missing=()
  local pkg simulation added
  for pkg in "${APT_PACKAGES[@]}"; do
    if ! qs_pkg_installed "$pkg"; then
      missing+=("$pkg")
    fi
  done
  if [ ${#missing[@]} -eq 0 ]; then
    qs_say "✓ 需要的系統套件都已安裝"
    return 0
  fi
  qs_say "▸ 需要安裝這些系統套件："
  printf '    %s\n' "${missing[@]}"
  simulation=$(LC_ALL=C "$QS_APT_GET" -s install --no-install-recommends "${missing[@]}" 2>&1) ||
    qs_die "模擬安裝失敗：$(tail -n 3 <<<"$simulation")"
  if grep -q '^Remv ' <<<"$simulation"; then
    grep '^Remv ' <<<"$simulation" >&2
    qs_die "模擬安裝的結果會移除上面這些套件，為了安全已停止，請手動確認"
  fi
  added=$(grep -c '^Inst ' <<<"$simulation" || true)
  qs_say "▸ 模擬安裝：會新增 ${added} 個套件，不會移除任何套件。"
  if ! qs_confirm "要安裝嗎？會跳出密碼視窗 [y/N] "; then
    qs_say "略過安裝系統套件"
    return 0
  fi
  "$QS_PKEXEC" "$QS_APT_GET" install -y --no-install-recommends "${missing[@]}" ||
    qs_die "安裝系統套件失敗"
  qs_say "✓ 系統套件安裝完成"
}

# ---------------------------------------------------------------- --models：模型與去背元件

qs_json_get() {
  "$QS_PYTHON" -c 'import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data[sys.argv[2]][sys.argv[3]])' "$QS_DATA_SRC/models.json" "$1" "$2"
}

qs_sha256() {
  local out
  out=$("$QS_SHA256SUM" -- "$1")
  printf '%s\n' "${out%% *}"
}

# 下載到暫存檔、核對 SHA-256 後才放到目的地（不覆蓋已存在的檔案）
qs_download() {
  local url=$1 dest=$2 sum=$3 tmp
  tmp=$(mktemp -- "$(dirname -- "$dest")/.download.XXXXXX")
  QS_TMP_FILE=$tmp
  qs_say "▸ 下載 $(basename -- "$dest")…"
  "$QS_PYTHON" - "$url" "$tmp" <<'PY'
# 只接受 https，下載到指定的暫存檔
import sys
import urllib.request

url, out = sys.argv[1], sys.argv[2]
if not url.startswith("https://"):
    raise SystemExit("只接受 https 網址")
with urllib.request.urlopen(url, timeout=60) as resp, open(out, "wb") as fh:
    while chunk := resp.read(1 << 20):
        fh.write(chunk)
PY
  if [ "$(qs_sha256 "$tmp")" != "$sum" ]; then
    qs_die "下載的檔案雜湊不符，已丟棄：$url"
  fi
  chmod 644 -- "$tmp"
  mv -nT -- "$tmp" "$dest"
  QS_TMP_FILE=""
  qs_say "✓ 已下載 $(basename -- "$dest")"
}

models_yunet() {
  local file url sum dest
  file=$(qs_json_get yunet file)
  url=$(qs_json_get yunet url)
  sum=$(qs_json_get yunet sha256)
  dest=$QS_MODELS/$file
  if [ -f "$dest" ]; then
    if [ "$(qs_sha256 "$dest")" = "$sum" ]; then
      qs_say "✓ 已有臉部偵測模型，略過"
    else
      qs_warn "現有的臉部偵測模型雜湊不符，沒有覆蓋它，請手動確認：$dest"
    fi
    return 0
  fi
  mkdir -p -- "$QS_MODELS"
  qs_download "$url" "$dest" "$sum"
}

models_rembg_venv() {
  if [ -x "$QS_VENV/bin/python" ]; then
    qs_say "✓ 已有去背元件，略過"
    return 0
  fi
  [ ! -e "$QS_VENV" ] || qs_die "$QS_VENV 已經存在但不完整，請先移走它再試"
  "$QS_PYTHON" -c 'import ensurepip' 2>/dev/null || qs_die "缺少 python3-venv，請用 --deps 安裝"
  qs_say "▸ 建立去背元件（獨立的虛擬環境，約 1 GB）…"
  "$QS_PYTHON" -m venv "$QS_VENV"
  "$QS_VENV/bin/python" -m pip install --disable-pip-version-check --no-input \
    --require-hashes --only-binary=:all: -r "$QS_DATA_SRC/rembg-requirements.txt"
  qs_say "✓ 去背元件安裝完成"
}

models_rembg_model() {
  local rel sum name model
  rel=$(qs_json_get rembg model_path)
  sum=$(qs_json_get rembg sha256)
  name=$(qs_json_get rembg model)
  model=$QS_MODELS/$rel
  if [ -f "$model" ]; then
    qs_say "✓ 已有去背模型，略過"
    return 0
  fi
  qs_say "▸ 下載去背模型 $name（約 180 MB）…"
  mkdir -p -- "$QS_MODELS"
  U2NET_HOME=$QS_MODELS "$QS_VENV/bin/python" -c \
    'import sys; from rembg import new_session; new_session(sys.argv[1])' "$name"
  [ -f "$model" ] || qs_die "去背模型沒有下載成功：$model"
  if [ "$(qs_sha256 "$model")" != "$sum" ]; then
    qs_die "去背模型的雜湊不符，請刪除 $model 後重試"
  fi
  qs_say "✓ 去背模型已就緒"
}

step_models() {
  models_yunet
  models_rembg_venv
  models_rembg_model
}

# ---------------------------------------------------------------- 主程式

main() {
  local want_deps=0 want_models=0
  WANT_AUTOSTART=1
  while [ $# -gt 0 ]; do
    case "$1" in
      --deps) want_deps=1 ;;
      --models) want_models=1 ;;
      --no-autostart) WANT_AUTOSTART=0 ;;
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
  if [ "$want_deps" = 1 ]; then
    step_deps
  fi
  preflight
  trap cleanup EXIT
  QS_STAGE=$(mktemp -d "${TMPDIR:-/tmp}/quickseemd-install.XXXXXX")
  qs_read_manifest
  decide_autostart
  plan_all
  adopt_app_autostart
  check_conflicts
  install_all
  if [ "$want_models" = 1 ]; then
    step_models
  fi
  qs_say ""
  qs_say "✓ 安裝完成。在 Nemo 按空白鍵就會用 QUICK_SEEMD 預覽。"
  qs_say "  $QS_NEMO_HINT"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
