import PDFKit
import Security
import SwiftUI
import UniformTypeIdentifiers

// 保護 PDF（加密碼與權限）、解鎖 PDF、編輯 PDF 資訊。

extension PDFToolCatalog {
    static let protect = ToolDefinition(
        id: "pdf.protect",
        category: .pdf,
        title: "保護 PDF",
        subtitle: "設定開啟密碼，並可限制列印、複製或修改內容。",
        symbol: "lock.doc",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "加密 PDF",
        keywords: ["protect", "encrypt", "password", "lock", "permissions", "加密", "密碼", "保護", "權限"],
        makeSession: { PTProtectSession() }
    )

    static let unlock = ToolDefinition(
        id: "pdf.unlock",
        category: .pdf,
        title: "解鎖 PDF",
        subtitle: "輸入密碼後移除 PDF 的開啟密碼與權限限制，另存成不需密碼的檔案。",
        symbol: "lock.open",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "解鎖 PDF",
        keywords: ["unlock", "decrypt", "remove password", "解鎖", "解密", "移除密碼"],
        makeSession: { PTUnlockSession() }
    )

    static let metadata = ToolDefinition(
        id: "pdf.metadata",
        category: .pdf,
        title: "編輯 PDF 資訊",
        subtitle: "修改 PDF 的標題、作者、主旨與關鍵字，或清除所有文件資訊。",
        symbol: "info.circle",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "儲存資訊",
        keywords: ["metadata", "properties", "title", "author", "info", "內容資訊", "屬性", "作者", "標題"],
        makeSession: { PTMetadataSession() }
    )
}

// MARK: - 保護

struct PTProtectOptions: Sendable {
    var password = ""
    var confirm = ""
    var allowPrinting = true
    var allowCopying = true
    var allowEditing = true
    var ownerPassword = ""

    var restricted: Bool { !allowPrinting || !allowCopying || !allowEditing }

    /// PDFAccessPermissions 是列舉（不是 OptionSet），以位元組合。
    var permissions: UInt {
        func bit(_ p: PDFAccessPermissions) -> UInt { p.rawValue }
        var p = bit(.allowsContentAccessibility)
        if allowPrinting { p |= bit(.allowsLowQualityPrinting) | bit(.allowsHighQualityPrinting) }
        if allowCopying { p |= bit(.allowsContentCopying) }
        if allowEditing { p |= bit(.allowsDocumentChanges) | bit(.allowsDocumentAssembly) | bit(.allowsCommenting) | bit(.allowsFormFieldEntry) }
        return p
    }
}

enum PTPasswordCheck {
    static func isASCII(_ s: String) -> Bool { s.unicodeScalars.allSatisfy { $0.isASCII && $0.value >= 0x20 && $0.value < 0x7F } }

    static func problem(_ o: PTProtectOptions) -> String? {
        if o.password.isEmpty { return "請輸入開啟密碼" }
        if !isASCII(o.password) { return "密碼只能使用英文字母、數字與符號" }
        if o.password.utf8.count > 32 { return "密碼最多 32 個字元" }
        if o.password != o.confirm { return "兩次輸入的密碼不一致" }
        if !o.ownerPassword.isEmpty {
            if !isASCII(o.ownerPassword) { return "權限密碼只能使用英文字母、數字與符號" }
            if o.ownerPassword == o.password { return "權限密碼不能與開啟密碼相同" }
        }
        return nil
    }

    static func randomPassword() -> String {
        var bytes = [UInt8](repeating: 0, count: 18)
        if SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) != errSecSuccess {
            bytes = (0..<18).map { _ in UInt8.random(in: 0...255) }
        }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "/", with: "x").replacingOccurrences(of: "+", with: "y")
    }
}

final class PTProtectSession: PTSession {
    @Published var options = PTProtectOptions()

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        return PTPasswordCheck.problem(options)
    }

    override func optionsView() -> AnyView { AnyView(PTProtectPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTProtectOptions) -> ToolJob {
        .perFile { file, context in
            if let problem = PTPasswordCheck.problem(o) { throw ConversionError.other(problem + "。") }
            let doc = try PTPDF.open(file)
            if doc.isEncrypted { throw ConversionError.other("「\(file.lastPathComponent)」已經加密，請先解鎖後再重新設定密碼。") }
            context.progress(0.2, "加密「\(file.lastPathComponent)」…")
            // 擁有者（權限）密碼：有限制時用使用者指定的或隨機密碼；沒有限制時與開啟密碼相同
            let owner = o.restricted ? (o.ownerPassword.isEmpty ? PTPasswordCheck.randomPassword() : o.ownerPassword)
                                     : (o.ownerPassword.isEmpty ? o.password : o.ownerPassword)
            let tmp = try context.makeWorkDirectory().appendingPathComponent("protected.pdf")
            try PTPDF.write(doc, to: tmp, options: [
                .userPasswordOption: o.password,
                .ownerPasswordOption: owner,
                .accessPermissionsOption: NSNumber(value: o.permissions),
            ])
            guard let check = PDFDocument(url: tmp), check.isLocked else {
                throw ConversionError.writeFailed("加密失敗，請再試一次。")
            }
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "已加密", ext: "pdf"), for: file)]
        }
    }
}

private struct PTProtectPanel: View {
    @ObservedObject var session: PTProtectSession

    private var mismatch: Bool { !session.options.confirm.isEmpty && session.options.confirm != session.options.password }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "開啟密碼", systemImage: "key") {
                VStack(alignment: .leading, spacing: 10) {
                    OptionTextField(title: "密碼", text: $session.options.password, placeholder: "輸入密碼", secure: true)
                    OptionTextField(title: "再輸入一次", text: $session.options.confirm, placeholder: "確認密碼", secure: true)
                    if mismatch {
                        Label("兩次輸入的密碼不一致", systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                    } else if !session.options.password.isEmpty && !PTPasswordCheck.isASCII(session.options.password) {
                        Label("請只使用英文字母、數字與符號", systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                    } else {
                        Text("開啟檔案時需要輸入這組密碼。請記住它，忘記就無法開啟。").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            OptionGroup(title: "權限", systemImage: "hand.raised") {
                VStack(alignment: .leading, spacing: 12) {
                    OptionToggle(title: "允許列印", isOn: $session.options.allowPrinting)
                    OptionToggle(title: "允許複製文字與圖片", isOn: $session.options.allowCopying)
                    OptionToggle(title: "允許修改、註解與整理頁面", isOn: $session.options.allowEditing)
                }
            }
            if session.options.restricted {
                OptionGroup(title: "權限密碼（選填）", systemImage: "key.horizontal") {
                    VStack(alignment: .leading, spacing: 6) {
                        OptionTextField(title: "擁有者密碼", text: $session.options.ownerPassword, placeholder: "留白則自動產生", secure: true)
                        Text("用這組密碼開啟可取得完整權限。留白時會自動產生隨機密碼，之後將無法解除限制。")
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            OptionNote(text: "使用 macOS 內建的 128 位元 PDF 加密，一般 PDF 閱讀程式都能開啟。權限限制需閱讀程式配合遵守。", systemImage: "lock.shield")
        }
    }
}

// MARK: - 解鎖

final class PTUnlockSession: PTSession {
    @Published var password = ""
    @Published var encryptedNames: [String] = []

    override var allowsLockedFiles: Bool { true }

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        encryptedNames = files.filter { PDFDocument(url: $0)?.isEncrypted ?? false }.map(\.lastPathComponent)
    }

    override func validate(_ files: [URL]) -> String? {
        if password.isEmpty { return "請輸入 PDF 的密碼" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTUnlockPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(password: password) }

    nonisolated static func job(password: String) -> ToolJob {
        .perFile { file, context in
            guard let doc = PDFDocument(url: file) else {
                throw ConversionError.unreadable("「\(file.lastPathComponent)」不是有效的 PDF，或檔案已損毀。")
            }
            guard doc.isEncrypted else { throw ConversionError.other("「\(file.lastPathComponent)」沒有加密，不需要解鎖。") }
            context.progress(0.1, "解鎖「\(file.lastPathComponent)」…")
            if doc.isLocked {
                guard doc.unlock(withPassword: password) else { throw ConversionError.other("「\(file.lastPathComponent)」的密碼不正確。") }
            } else {
                // 只有權限限制（沒有開啟密碼）：需要擁有者密碼
                _ = doc.unlock(withPassword: password)
            }
            let fullAccess = doc.permissionsStatus == .owner || doc.accessPermissions.rawValue & 0xFF == 0xFF
            guard fullAccess else {
                throw ConversionError.other("「\(file.lastPathComponent)」設有權限限制，請輸入「權限（擁有者）密碼」才能完整解鎖。")
            }
            // 以物件層級複製（解密後的內容）到新文件，去掉加密，字型與文字完全保留
            context.progress(0.3, "寫出不需密碼的副本…")
            let tmp = try context.makeWorkDirectory().appendingPathComponent("unlocked.pdf")
            try PTAssemble.write((0..<doc.pageCount).map { PTPageEntry(doc: doc, index: $0) }, outline: PTOutline.nodes(from: doc, map: { $0 }),
                                 info: PTPDF.info(of: doc), to: tmp, context: context)
            if let check = PDFDocument(url: tmp), check.isEncrypted {
                throw ConversionError.writeFailed("無法移除「\(file.lastPathComponent)」的加密。")
            }
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "已解鎖", ext: "pdf"), for: file)]
        }
    }
}

private struct PTUnlockPanel: View {
    @ObservedObject var session: PTUnlockSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "密碼", systemImage: "key") {
                VStack(alignment: .leading, spacing: 6) {
                    OptionTextField(title: "PDF 密碼", text: $session.password, placeholder: "開啟密碼或權限密碼", secure: true)
                    Text("只有開啟密碼時輸入開啟密碼即可；若檔案還限制了列印或複製，請輸入權限（擁有者）密碼。")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            if !session.encryptedNames.isEmpty {
                OptionNote(text: "已加密：" + session.encryptedNames.joined(separator: "、"), systemImage: "lock.fill")
            }
            OptionNote(text: "解鎖後會另存一份不需要密碼的 PDF，原始檔案保持不變。請只解鎖你有權使用的文件。")
        }
    }
}

// MARK: - 編輯資訊

struct PTMetadataOptions: Sendable {
    var title = ""
    var author = ""
    var subject = ""
    var keywords = ""
    var creator = ""
    var clearAll = false
}

final class PTMetadataSession: PTSession {
    @Published var options = PTMetadataOptions()
    private var loadedFrom: URL?

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        guard let first = files.first, first != loadedFrom else { return }
        loadedFrom = first
        guard let doc = PDFDocument(url: first), !doc.isLocked else { return }
        let a = doc.documentAttributes ?? [:]
        options.title = a[PDFDocumentAttribute.titleAttribute] as? String ?? ""
        options.author = a[PDFDocumentAttribute.authorAttribute] as? String ?? ""
        options.subject = a[PDFDocumentAttribute.subjectAttribute] as? String ?? ""
        options.creator = a[PDFDocumentAttribute.creatorAttribute] as? String ?? ""
        if let k = a[PDFDocumentAttribute.keywordsAttribute] as? [String] { options.keywords = k.joined(separator: ", ") }
        else { options.keywords = a[PDFDocumentAttribute.keywordsAttribute] as? String ?? "" }
    }

    override func optionsView() -> AnyView { AnyView(PTMetadataPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTMetadataOptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            context.progress(0.3, "更新「\(file.lastPathComponent)」的文件資訊…")
            var attrs: [AnyHashable: Any] = [:]
            if !o.clearAll {
                func put(_ key: PDFDocumentAttribute, _ value: String) {
                    let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !v.isEmpty { attrs[key] = v }
                }
                put(.titleAttribute, o.title)
                put(.authorAttribute, o.author)
                put(.subjectAttribute, o.subject)
                put(.creatorAttribute, o.creator)
                let words = o.keywords.replacingOccurrences(of: "，", with: ",").split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                if !words.isEmpty { attrs[PDFDocumentAttribute.keywordsAttribute] = words }
                attrs[PDFDocumentAttribute.modificationDateAttribute] = Date()
                if let created = doc.documentAttributes?[PDFDocumentAttribute.creationDateAttribute] { attrs[PDFDocumentAttribute.creationDateAttribute] = created }
            }
            doc.documentAttributes = attrs
            let tmp = try context.makeWorkDirectory().appendingPathComponent("metadata.pdf")
            try PTPDF.write(doc, to: tmp)
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: o.clearAll ? "已清除資訊" : "已更新資訊", ext: "pdf"), for: file)]
        }
    }
}

private struct PTMetadataPanel: View {
    @ObservedObject var session: PTMetadataSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionToggle(title: "清除所有文件資訊", subtitle: "移除標題、作者等資料，保護隱私", isOn: $session.options.clearAll)
            if !session.options.clearAll {
                OptionGroup(title: "文件資訊", systemImage: "info.circle") {
                    VStack(alignment: .leading, spacing: 10) {
                        OptionTextField(title: "標題", text: $session.options.title, placeholder: "文件標題")
                        OptionTextField(title: "作者", text: $session.options.author, placeholder: "作者姓名")
                        OptionTextField(title: "主旨", text: $session.options.subject, placeholder: "文件主旨")
                        OptionTextField(title: "關鍵字", text: $session.options.keywords, placeholder: "以逗號分隔")
                        OptionTextField(title: "建立程式", text: $session.options.creator, placeholder: "例如 Microsoft Word")
                    }
                }
                OptionNote(text: "欄位已帶入第一個檔案目前的資訊；選了多個檔案時，會套用相同的資訊。留白的欄位會被移除。")
            }
        }
    }
}
