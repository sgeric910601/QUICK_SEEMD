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

需要 macOS 14 以上與 Xcode。

```bash
./scripts/install.sh
```

腳本會編譯 Release 版、安裝到「應用程式」資料夾、啟用空白鍵預覽與 Finder 右鍵選單，並啟動 QUICK_SEEMD（在選單列待命）。

1. 右鍵選單或預覽沒出現：打開 QUICK_SEEMD →「右鍵、拖曳與空白鍵預覽」頁查看狀態，或到「系統設定 › 一般 › 登入項目與延伸功能」打開。
2. 第一次用辦公軟體引擎轉換文件時，macOS 會詢問是否允許 QUICK_SEEMD 控制該軟體，請按「好」。
3. 解除安裝：`./scripts/uninstall.sh`

## 已知限制

- `.txt`、`.csv` 的空白鍵預覽固定由 macOS 內建程式處理。App 內的「預覽測試」頁可以看本專案的版本。
- 圖片、PDF、影片、Office 文件這類系統已有預覽的格式，維持系統效果。
- 輸出 MP3、Ogg、WebP 需要另外安裝對應的命令列轉檔工具，偵測到就會自動出現這些選項。
- PDF 加密使用 RC4-128（系統唯一提供的方式），密碼須為英數字。
- 使用本機簽署；每次重新編譯安裝後，macOS 可能會再詢問一次自動化權限。

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
scripts/                安裝與解除安裝
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
