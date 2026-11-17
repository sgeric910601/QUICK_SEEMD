"""QUICK_SEEMD 背景程式（quickseemd-agent，GTK3）。

常駐在背景，負責：系統匣圖示、拖曳轉換輪盤、右上角進度面板、浮動暫存、
右鍵與輪盤的工作佇列。D-Bus 名稱 dev.quickseemd.Agent，需要時由 D-Bus 自動啟動。

純邏輯的模組（taps、queue、hud_model、tray_model、shelf_model、file_actions 的文字與
剪貼簿內容、launcher）不載入 GTK，可以單獨測試；介面模組才載入 GTK3。
"""
