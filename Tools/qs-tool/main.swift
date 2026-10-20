import Foundation

// 工具命令列測試：qs-tool <工具或右鍵動作代碼> [--out 資料夾] 檔案…　（--list 列出全部）
// 編譯時另外加入一個定義 `enum HarnessTools { static let tools: [ToolDefinition] }` 的檔案，
// 例如只測 PDF 類：static let tools = PDFToolCatalog.tools
let code = await ToolCLI.main(arguments: CommandLine.arguments, tools: HarnessTools.tools)
exit(code)
