#!/bin/bash
# 用本機固定的簽署憑證簽署 App（含擴充）。
# 憑證只在第一次建立，之後每次編譯、更新都用同一張，macOS 才會記住已經給過的權限
# （控制 Office、存取「文件」等資料夾）。改用臨時簽署的話，每次重新編譯都會被當成新的 App。
#
# 憑證放在專用的鑰匙圈 ~/Library/Application Support/QUICK_SEEMD/Signing/，
# 不會加進「登入」鑰匙圈，也不會變更任何系統信任設定。
set -euo pipefail
APP="${1:?用法：sign.sh <App 或 DMG 路徑>}"

DIR="$HOME/Library/Application Support/QUICK_SEEMD/Signing"
KC="$DIR/signing.keychain-db"
PWFILE="$DIR/keychain-password"
NAME="QUICK_SEEMD Local Signing"
OPENSSL=/usr/bin/openssl   # 系統內建版本，產生的 .p12 才能被 security 匯入

if [ ! -f "$KC" ] || [ ! -f "$PWFILE" ]; then
  echo "▸ 第一次建立本機簽署憑證（只會做一次）"
  mkdir -p "$DIR"
  chmod 700 "$DIR"
  rm -f "$KC" "$PWFILE"
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  PW=$("$OPENSSL" rand -hex 24)
  "$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 7300 \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -subj "/CN=$NAME" \
    -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
  "$OPENSSL" pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" -passout "pass:$PW" -name "$NAME"
  security create-keychain -p "$PW" "$KC"
  security set-keychain-settings "$KC"
  security unlock-keychain -p "$PW" "$KC"
  security import "$TMP/id.p12" -k "$KC" -P "$PW" -T /usr/bin/codesign >/dev/null
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$PW" "$KC" >/dev/null
  (umask 077 && printf '%s' "$PW" > "$PWFILE")
fi

security unlock-keychain -p "$(cat "$PWFILE")" "$KC"
ID=$(security find-identity -p codesigning "$KC" | awk -v name="\"$NAME\"" '$0 ~ name { print $2; exit }')
[ -n "$ID" ] || { echo "✗ 找不到本機簽署憑證：$KC"; exit 1; }

sign() {
  local out
  out=$(codesign --force --sign "$ID" --keychain "$KC" \
    --preserve-metadata=identifier,entitlements,flags,runtime "$1" 2>&1) || { echo "$out"; exit 1; }
}
if [[ "$APP" == *.dmg ]]; then
  out=$(codesign --force --sign "$ID" --keychain "$KC" "$APP" 2>&1) || { echo "$out"; exit 1; }
  codesign --verify "$APP"
else
  for x in "$APP"/Contents/PlugIns/*.appex; do [ -d "$x" ] && sign "$x"; done
  sign "$APP"
  codesign --verify --deep --strict "$APP"
fi
echo "▸ 已用本機憑證簽署（$NAME）"
