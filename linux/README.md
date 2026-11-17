# QUICK_SEEMD Linux 版

在 Linux 上提供跟 macOS 版一樣的四種用法：空白鍵預覽、分類工具箱、右鍵一鍵轉換、拖曳轉換輪盤。所有處理都在這台電腦上離線完成，不會上傳任何檔案；輸出一律另存新檔，同名時自動加編號，不會覆蓋任何檔案。

支援環境：Ubuntu 24.04、GNOME（X11 工作階段）、Nemo 檔案管理員。拖曳轉換輪盤只能在 X11 使用。

## 四種用法

| 用法 | 怎麼用 |
|---|---|
| 空白鍵預覽 | 在 Nemo 選取檔案，按空白鍵；再按一次空白鍵、Esc 或 q 關閉，f 全螢幕 |
| 工具箱 | 從應用程式選單打開 QUICK_SEEMD，依分類挑工具：選檔 → 調整選項 → 完成 |
| 右鍵一鍵轉換 | 在 Nemo 對檔案按右鍵 →「QUICK_SEEMD 轉換」，只列出適合這個檔案的動作 |
| 拖曳轉換輪盤 | 拖著檔案時「點一下」Shift、Alt 或 Ctrl（不要按住），游標旁會出現輪盤 |

- 右鍵與輪盤的結果存在原檔旁邊，進度與結果顯示在螢幕右上角。
- 背景程式會在系統匣待命（系統匣選單可以開工具箱、切換輪盤、設定登入時自動啟動）。

### 拖曳轉換輪盤

| 按鍵（點一下） | 輪盤 | 內容 |
|---|---|---|
| ⇧ Shift | 轉換 | 依檔案類型列出可以轉成的格式 |
| ⌥ Alt | 工具 | 壓縮、去背、拆分、合併等 |
| ⌃ Ctrl | 動作 | 用其他程式開啟、浮動暫存、拷貝、移動到…、拷貝路徑、終端機 |

1. 從 Nemo、桌面或任何程式拖起檔案。
2. 點一下按鍵叫出輪盤（按住超過 0.3 秒不算，所以不會跟 Nemo 的 Shift 移動、Ctrl 拷貝衝突）。點另一個鍵可以切換輪盤。
3. 把檔案放到想要的項目上就開始處理。放在中間會打開主程式。

按鍵可以在設定裡更改（Shift、Ctrl、Alt、Super）。

## 安裝

```bash
cd linux
./install.sh --deps --models
nemo -q
```

- `--deps`：列出需要的系統套件、先模擬安裝讓你確認，之後才跳出密碼視窗（不會執行 autoremove）。已經裝過可以省略。
- `--models`：下載去背元件（rembg，獨立的虛擬環境，套件以雜湊值鎖定）與臉部偵測模型（核對 SHA-256）。已經有的會略過。
- `--no-autostart`：不要在登入時自動啟動背景程式。
- `nemo -q`：讓 Nemo 重新載入右鍵擴充（會關掉開著的 Nemo 視窗）。空白鍵預覽不用重開就會生效。

全部只裝在你自己的資料夾，不需要 root：

| 位置 | 內容 |
|---|---|
| `~/.local/share/quickseemd/` | 程式、資料、模型、安裝清單 |
| `~/.local/bin/quickseemd*` | 啟動檔 |
| `~/.local/share/applications/dev.quickseemd.App.desktop` | 選單捷徑（不登記任何檔案類型，不會搶走預設開啟程式） |
| `~/.local/share/dbus-1/services/` | `org.nemo.Preview`（空白鍵預覽）、`dev.quickseemd.Agent`（背景程式） |
| `~/.local/share/nemo-python/extensions/quickseemd_nemo.py` | Nemo 右鍵擴充 |
| `~/.config/autostart/dev.quickseemd.Agent.desktop` | 登入時自動啟動（可在設定關掉） |

重新安裝只會取代安裝清單裡的檔案；位置上已經有別的程式的檔案時會停下來，不會覆蓋。

解除安裝：`./uninstall.sh`（加 `--purge` 會一併移除模型、去背元件與設定）。

## 工具箱

| 分類 | 工具 |
|---|---|
| PDF | 合併、拆分、壓縮、PDF 轉圖片、圖片轉 PDF、整理頁面、刪除頁面、旋轉、浮水印、頁碼、加密、解鎖、文字辨識（可搜尋）、PDF 轉文字、HTML 轉 PDF、編輯 PDF 資訊 |
| 圖片 | 轉換格式、壓縮、調整大小、裁剪、旋轉／翻轉、移除背景、浮水印、模糊臉部、文字辨識、移除相片資訊、放大、製作圖示 |
| 影音 | 影片轉檔、壓縮、剪輯、轉 GIF、擷取音訊、移除聲音、旋轉、合併、擷取畫面、調整速度、調整尺寸、音訊轉檔、剪輯音訊 |
| 其他 | 萬用格式轉換、壓縮檔案、解壓縮、文字編碼轉換、字幕（SRT ↔ VTT）、讀取 QR Code、計算校驗碼 |

Office 文件類工具將在下一版加入；目前可以用「萬用格式轉換」做 Word、Excel、PowerPoint 與 PDF 的互轉（使用 LibreOffice）。

## 跟 macOS 版的差異

- 文字辨識改用 Tesseract（繁中、簡中、日、韓、英），準確度比 macOS 的 Vision 低一些；去背用 rembg、臉部偵測用 OpenCV YuNet。
- 「動作」輪盤的 AirDrop 換成「用其他程式開啟」。
- 輪盤要「點一下」按鍵才會出現；按 Esc 會取消整個拖曳。輪盤出現後如果滑鼠完全沒動就放開，檔案會落在原本底下的位置。
- PDF 加密使用 AES-256。
- HTML 轉 PDF 使用 WeasyPrint，依頁面自然分頁。
- 安全強化：預覽頁面不執行 JavaScript、封鎖所有網路載入；LibreOffice 在沒有網路、看不到家目錄的沙盒裡轉換；壓縮檔解開時檢查路徑與連結，並限制解開的大小；圖片與影音有大小與時間上限。

## 開發與測試

```bash
cd linux
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- /usr/bin/python3 -m pytest -q
QS_EXPECT_COMPLETE=1 …   # 要求所有工具都已登記
QS_TEST_DISPLAY=:130 …   # 在隔離的 Xvfb 畫面跑介面測試（:120 以上）
```

測試會自動隔離環境：家目錄與設定都改到暫存資料夾、D-Bus 指向不存在的位址、擋下會碰到真實系統的指令、只允許對測試自己啟動的程序送訊號，並限制記憶體用量。

```bash
/usr/bin/python3 -m quickseemd tools            # 列出所有工具與動作代碼
/usr/bin/python3 -m quickseemd run 工具或動作 [--out 資料夾] 檔案…
/usr/bin/python3 -m quickseemd convert --list 檔案
/usr/bin/python3 -m quickseemd render 檔案… 輸出資料夾
```

### 專案結構

```
quickseemd/core/          共用核心（不載入任何介面函式庫）：工具框架、絕不覆蓋的寫入、右鍵與輪盤動作清單、設定、工作程序協定
quickseemd/core/tools/    各分類工具（pdf、image、media、other）
quickseemd/core/converter/ 萬用格式轉換
quickseemd/core/preview/  空白鍵預覽的網頁產生器
quickseemd/worker.py      工作程序（每個工作在獨立程序執行，可以真正取消）
quickseemd/app/           主程式（GTK4＋libadwaita）
quickseemd/agent/         背景程式（GTK3）：系統匣、拖曳輪盤、進度面板、浮動暫存
quickseemd/previewer/     空白鍵預覽視窗（GTK3＋WebKit2），D-Bus 名稱 org.nemo.Preview
quickseemd/nemo/          Nemo 右鍵擴充
data/                     範本、圖示、模型清單
tests/                    測試
```
