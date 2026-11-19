"""空白鍵預覽程式（D-Bus 名稱 org.nemo.Preview，GTK3＋WebKit2 4.1）。

在 Nemo 按空白鍵時，Nemo 呼叫 org.nemo.Preview.ShowFile(網址, 視窗代碼, 已顯示就關閉)；
這個程式由 D-Bus 自動啟動，只會有一份在跑，閒置 10 分鐘自動結束。

模組分工：
- state     開關狀態（同一個檔案再按一次就關、不同檔案就換內容）
- uri       網址 → 實際檔案（trash://、recent://、sftp://）
- content   在背景執行緒準備要顯示的內容（不碰 GTK）
- loaders   圖片與 PDF 的載入（GdkPixbuf、Poppler，不碰 GTK）
- office    Office 文件用 LibreOffice 轉成 PDF 並快取
- scheme    預覽網頁的自訂網址 qs-preview:// 與內容過濾規則
- fallback  預覽核心不可用時的分類與資訊頁
- geometry  視窗大小與位置
- app / window / pageview / imageview / mediaview / pdfview   GTK3 介面（只在預覽程序裡載入）

這個套件的 __init__ 不載入 GTK，測試可以單獨匯入純邏輯的模組。
"""
