# QUICK_SEEMD

macOS 的萬用檔案工具：按空白鍵預覽、分類工具箱、右鍵一鍵轉換、拖曳轉換輪盤。所有處理都在這台 Mac 上離線完成，不會上傳任何檔案。

## 四種用法

| 用法 | 怎麼用 |
|---|---|
| 空白鍵預覽 | 在 Finder 選取檔案，按空白鍵 |
| 工具箱 | 打開 QUICK_SEEMD，依分類挑工具：選檔 → 調整選項 → 完成 |
| 右鍵一鍵轉換 | 在 Finder 對檔案按右鍵 →「QUICK_SEEMD 轉換」，只列出適合這個檔案的動作，點一下就完成 |
| 拖曳轉換輪盤 | 拖著檔案時點一下 Shift、Command 或 Control，游標旁會出現輪盤（見下方） |

- 右鍵與輪盤的結果存在原檔旁邊；同名時自動加編號，不會覆蓋任何檔案。
- 關閉視窗後會留在選單列待命。可以在設定裡關掉，或設成登入時自動啟動。

## 拖曳轉換輪盤

| 按鍵 | 輪盤 | 內容 |
|---|---|---|
| ⇧ Shift | 轉換 | 依檔案類型列出可以轉成的格式 |
| ⌘ Command | 工具 | 壓縮、去背、拆分、合併等 |
| ⌃ Control | 動作 | AirDrop、浮動暫存、拷貝、移動、拷貝路徑、在終端機開啟 |

1. 從 Finder、桌面或任何 App 拖起檔案。
2. 點一下按鍵叫出輪盤；放開按鍵輪盤仍會留著，點另一個鍵可以切換。
3. 把檔案放到想要的項目上就開始處理。放在中間會打開主視窗，按 Esc 取消。

按鍵可以在設定裡更改。

## 工具箱

| 分類 | 工具 |
|---|---|
| PDF | 合併、拆分、壓縮、PDF 轉圖片、圖片轉 PDF、整理頁面、刪除頁面、旋轉、浮水印、頁碼、加密、解鎖、文字辨識（可搜尋）、PDF 轉文字、HTML 轉 PDF、編輯 PDF 資訊 |
| Office 文件 | DOCX／XLSX／PPTX ↔ PDF、文件格式互轉、簡報轉 PNG／JPG |
| 圖片 | 轉換格式、壓縮、調整大小、裁剪（可拖曳裁切框）、旋轉／翻轉、移除背景、浮水印、模糊臉部、文字辨識、移除相片資訊、放大、製作圖示 |
| 影音 | 影片轉檔、壓縮、剪輯、轉 GIF、擷取音訊、移除聲音、旋轉、合併、擷取畫面、音訊轉檔 |
| 其他 | 萬用格式轉換、壓縮檔案、解壓縮、文字編碼轉換、字幕（SRT ↔ VTT）、讀取 QR Code、計算校驗碼 |

每個工具都有附說明的選項卡片，例如壓縮程度、輸出格式、頁碼範圍。完成頁會顯示結果與節省比例，並推薦接著可以用的工具。

### PDF 文字品質

macOS 內建的 PDF 函式庫在刪頁、重排、合併時會損壞 PDF 的隱藏文字層：看起來正常，但複製或搜尋會變亂碼。本專案的 PDF 工具使用自己的低階 PDF 複製器（`App/Tools/PDF/PTPDFWriter.swift`），字型與頁面內容原封不動搬移，壓縮時只重新壓縮圖片。

## 空白鍵預覽

| 類型 | 效果 |
|---|---|
| 程式碼 | 50 多種語言語法上色、行號、編碼與換行偵測 |
| Markdown | 表格、待辦清單、程式碼區塊、front matter、同資料夾圖片 |
| JSON / YAML / TOML / INI / plist | 壓縮過的 JSON 自動排版並檢查格式；二進位 plist 轉成 XML 顯示 |
| CSV / TSV | 表格檢視 |
| 壓縮檔 | ZIP / TAR / TAR.GZ / TAR.XZ 樹狀清單；.gz / .xz 直接看內容 |
| 資料庫、筆記本、記錄檔、Diff | SQLite 資料表、.ipynb 儲存格、依等級上色、增刪標示 |
| 二進位與未知檔案 | 資訊面板、Mach-O / ELF / PE 架構、SHA-256、彩色十六進位 |

## 安裝

### 用 DMG 安裝（建議）

1. 到 GitHub 倉庫的 Releases 頁，下載最新的 `QUICK_SEEMD-版本.dmg`。
2. 打開 DMG，把 QUICK_SEEMD 拖到「應用程式」。
3. 從「應用程式」打開 QUICK_SEEMD。右鍵選單與空白鍵預覽會自動啟用，App 會在選單列待命。

這個 App 沒有經過 Apple 公證，第一次打開時 macOS 會擋下來：到「系統設定 › 隱私權與安全性」，在下方按「強制打開」，只需要一次。

### 從原始碼安裝

需要 macOS 14 以上與 Xcode。

```bash
./scripts/install.sh
```

腳本會編譯 Release 版、用本機憑證簽署、安裝到「應用程式」資料夾、啟用空白鍵預覽與 Finder 右鍵選單，並啟動 QUICK_SEEMD。

- 右鍵選單或預覽沒出現：打開 QUICK_SEEMD →「右鍵、拖曳與空白鍵預覽」頁查看狀態，或到「系統設定 › 一般 › 登入項目與延伸功能」打開。
- 解除安裝：`./scripts/uninstall.sh`

## 一鍵更新

選單列圖示 →「更新到最新版本」（有新版本時出現在最上面），或「設定 › 更新」→「立即更新」。

1. 從 GitHub Releases 下載最新的 DMG。
2. 確認新版本是同一張憑證簽署的；不符合就停止，不會安裝。
3. 替換並重新開啟，約 1 分鐘，已經給過的權限都會保留。

- App 每 6 小時自動檢查一次，有新版本會在右上角提醒；可以在「設定 › 更新」關掉。
- 倉庫是私人的，需要先用 GitHub 命令列工具登入一次（`gh auth login`）。倉庫改成公開後就不需要。
- 更新記錄：`~/Library/Logs/QUICK_SEEMD/update.log`

## 發佈新版本

```bash
./scripts/release.sh 1.1.0
```

1. 把 `VERSION` 改成新版本號並提交。
2. 編譯、用本機憑證簽署，做成 `build/Release/QUICK_SEEMD-1.1.0.dmg`。
3. 推送提交與 `v1.1.0` 標籤，建立 GitHub Release 並附上 DMG。

發佈完，已安裝的 App 就能一鍵更新。一定要在有本機簽署憑證的這台 Mac 上發佈：換一張憑證，使用者更新後就得重新允許權限。

## 權限

| 權限 | 什麼時候需要 | 怎麼給 |
|---|---|---|
| 控制 Word／Excel／PowerPoint | 用 Office 引擎轉換文件時 | 第一次會跳出系統詢問，按「好」 |
| 存取桌面、文件、下載 | 右鍵轉換這些資料夾裡的檔案時 | 第一次會跳出系統詢問，按「好」 |
| 延伸功能 | 右鍵選單、空白鍵預覽 | 第一次打開時自動啟用 |
| 拖曳輪盤 | 不需要 | — |

權限只要給一次。所有版本（原始碼安裝與 Releases 上的 DMG）都用同一張本機自簽憑證簽署，macOS 會把每一版當成同一個 App，更新後不會再重問。憑證在第一次執行 `scripts/install.sh` 時建立，存在 `~/Library/Application Support/QUICK_SEEMD/Signing/` 的專用鑰匙圈裡，不會加進「登入」鑰匙圈，也不會變更系統的信任設定。目前狀態可以在「右鍵、拖曳與空白鍵預覽」頁的「權限」卡片查看。

## 已知限制

- `.txt`、`.csv` 的空白鍵預覽固定由 macOS 內建程式處理。App 內的「預覽測試」頁可以看本專案的版本。
- 圖片、PDF、影片、Office 文件這類系統已有預覽的格式，維持系統效果。
- 輸出 MP3、Ogg、WebP 需要另外安裝對應的命令列轉檔工具，偵測到就會自動出現這些選項。
- PDF 加密使用 RC4-128（系統唯一提供的方式），密碼須為英數字。
- 沒有經過 Apple 公證：第一次打開要在「隱私權與安全性」按「強制打開」。要免除這一步，需要 Apple 付費開發者帳號。
- 簽署憑證只存在這台 Mac。這台 Mac 的憑證遺失的話，之後發佈的版本會被當成新的 App，使用者要重新允許權限一次。

## 專案結構

```
QUICK_SEEMD.xcodeproj   專案檔（資料夾同步，新增檔案不必改專案檔）
App/Shell/              主程式外殼：主視窗、選單列、設定、整合頁
App/Tools/Framework/    工具框架：ToolDefinition / ToolSession / ToolJob / ToolContext、共用選項元件
App/Tools/UI/           工具大廳、工具頁、完成頁、背景轉換佇列、拖曳輪盤
App/Tools/{PDF,Office,Image,Media,Other}/   各分類工具
App/Converter/          萬用格式轉換引擎
Core/                   空白鍵預覽的渲染核心（App 與擴充共用）
PreviewExtension/       空白鍵預覽擴充
FinderExtension/        Finder 右鍵選單擴充
Shared/                 右鍵與輪盤動作清單（主程式與右鍵擴充共用）、選單列標誌
Config/                 Info.plist 與 entitlements
Tools/                  命令列測試工具（qs-render、qs-convert、qs-tool）
Samples/                各種格式的範例檔
Design/                 圖示原稿與產生程式
scripts/                編譯、簽署、安裝、發佈、解除安裝
VERSION                 目前版本號（發佈腳本會更新）
```

### 新增一個工具

1. 在對應分類資料夾建立 `ToolDefinition`（名稱、說明、圖示、接受的檔案類型）與 `ToolSession` 子類別（`@Published` 選項、`optionsView()`、`makeJob()`）。範例：`App/Tools/PDF/PDFToolCatalog.swift` 的「旋轉 PDF」。
2. 加進該分類的 `tools` 清單。
3. 要出現在右鍵選單與輪盤：在 `Shared/QuickActions+分類.swift` 加一筆 `QuickActionInfo`，並在工具的 `quickActions` 對應同一個代碼。

### 開發測試

```bash
# 不開介面測試工具（例：只編譯 PDF 類）
echo 'enum HarnessTools { static let tools = PDFToolCatalog.tools }' > /tmp/H.swift
xcrun swiftc -O App/Converter/*.swift App/Tools/Framework/*.swift App/Tools/PDF/*.swift Shared/*.swift Tools/qs-tool/main.swift /tmp/H.swift -o /tmp/qs-tool
/tmp/qs-tool --list
/tmp/qs-tool pdf.compress-recommended --out /tmp/out 檔案.pdf
```

```bash
# 用 App 本身在背景執行工具（自動化權限會記在 App 名下）
open -n -W /Applications/QUICK_SEEMD.app --args --qs-run office.to-pdf --qs-out /tmp/out --qs-result /tmp/r.json --qs-files 報告.docx
```

預覽擴充的記錄：`/usr/bin/log stream --predicate 'subsystem == "dev.quickseemd.preview"'`

## 致謝

- [@liaw-boy](https://github.com/liaw-boy)：安裝與編譯腳本的修正（#1，部分內容併入 #2）

## 授權

Copyright © 2026 TSENG KUAN-CHENG 及各貢獻者，保留所有權利。這不是開源授權：

- 原始碼公開供檢視；可以在自己的裝置上編譯、修改，供個人使用或準備貢獻，但不得散布
- [Releases](https://github.com/sgeric910601/QUICK_SEEMD/releases) 發佈的版本（包括預先發行版本與 App 內更新）可以免費下載使用
- 提交 PR 即表示同意 LICENSE 第 4 條的貢獻條款

完整條款見 [LICENSE](LICENSE)，中英文有出入時以中文為準。
