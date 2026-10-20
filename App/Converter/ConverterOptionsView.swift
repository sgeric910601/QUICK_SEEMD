import SwiftUI

/// 依已選格式顯示對應的選項（圖片品質、影片解析度、GIF、音訊位元率、來源編碼…）。
struct CVOptionsCard: View {
    @ObservedObject var model: ConverterModel
    let sections: [ConverterModel.OptionSection]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("選項", systemImage: "slider.horizontal.3")
                .font(.system(size: 13, weight: .semibold))
            ForEach(Array(sections.enumerated()), id: \.element) { index, section in
                if index > 0 { Divider().opacity(0.5) }
                row(for: section)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cvCardStyle()
    }

    @ViewBuilder
    private func row(for section: ConverterModel.OptionSection) -> some View {
        switch section {
        case .imageQuality:
            optionRow("品質", systemImage: "dial.medium") {
                HStack(spacing: 10) {
                    Slider(value: $model.options.imageQuality, in: 0.3...1.0, step: 0.05)
                        .frame(maxWidth: 220)
                    Text("\(Int((model.options.imageQuality * 100).rounded()))%")
                        .font(.system(size: 12).monospacedDigit())
                        .foregroundStyle(.secondary)
                        .frame(width: 40, alignment: .trailing)
                }
            } footnote: { "適用於 JPEG、HEIC、AVIF、JPEG 2000；數值越低檔案越小。" }
        case .imageSize:
            optionRow("尺寸", systemImage: "arrow.up.left.and.arrow.down.right") {
                Picker("尺寸", selection: $model.options.imageMaxPixelSize) {
                    Text("原始大小").tag(Int?.none)
                    Text("最長邊 4096 px").tag(Int?.some(4096))
                    Text("最長邊 2048 px").tag(Int?.some(2048))
                    Text("最長邊 1024 px").tag(Int?.some(1024))
                    Text("最長邊 512 px").tag(Int?.some(512))
                }
                .labelsHidden()
                .fixedSize()
            } footnote: { "只會縮小、不會放大，並保持長寬比例。" }
        case .pdfResolution:
            optionRow("PDF 解析度", systemImage: "doc.viewfinder") {
                Picker("PDF 解析度", selection: $model.options.pdfRenderDPI) {
                    Text("72 DPI（螢幕）").tag(72.0)
                    Text("150 DPI（標準）").tag(150.0)
                    Text("300 DPI（列印）").tag(300.0)
                }
                .labelsHidden()
                .fixedSize()
            } footnote: { "每一頁輸出成一張圖片；多頁時會放進同名資料夾。" }
        case .videoSize:
            optionRow("解析度", systemImage: "rectangle.on.rectangle") {
                Picker("解析度", selection: $model.options.videoSize) {
                    ForEach(ConversionVideoSize.allCases) { size in
                        Text(size.title).tag(size)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            } footnote: { "以短邊計算（720p = 短邊 720 像素），比原始小時才會縮小。" }
        case .gif:
            optionRow("GIF 動畫", systemImage: "photo.stack") {
                CVFlowLayout(spacing: 14, lineSpacing: 8) {
                    Picker("每秒張數", selection: $model.options.gifFramesPerSecond) {
                        ForEach([5, 8, 10, 12, 15, 20, 24], id: \.self) { Text("\($0) fps").tag($0) }
                    }
                    .fixedSize()
                    Picker("寬度", selection: $model.options.gifMaxWidth) {
                        ForEach([240, 320, 480, 640, 800, 1080], id: \.self) { Text("\($0) px").tag($0) }
                    }
                    .fixedSize()
                    Picker("最長", selection: $model.options.gifMaxDuration) {
                        ForEach([3.0, 5.0, 10.0, 15.0, 30.0, 60.0], id: \.self) { Text("\(Int($0)) 秒").tag($0) }
                    }
                    .fixedSize()
                }
                .font(.system(size: 12))
            } footnote: { "GIF 檔案通常很大，建議 10–15 fps、寬度 480 px 以內。" }
        case .frame:
            optionRow("擷取時間點", systemImage: "camera.viewfinder") {
                HStack(spacing: 8) {
                    Toggle("自動", isOn: Binding(
                        get: { model.options.frameTime == nil },
                        set: { model.options.frameTime = $0 ? nil : 0 }
                    ))
                    .toggleStyle(.checkbox)
                    if model.options.frameTime != nil {
                        TextField("秒", value: Binding(
                            get: { model.options.frameTime ?? 0 },
                            set: { model.options.frameTime = max(0, $0) }
                        ), format: .number.precision(.fractionLength(0...2)))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 70)
                        Text("秒").foregroundStyle(.secondary)
                    }
                }
                .font(.system(size: 12))
            } footnote: { "自動：取第 1 秒（短片取中間）的畫面。" }
        case .audioBitrate:
            optionRow("位元率", systemImage: "waveform.badge.plus") {
                Picker("位元率", selection: $model.options.audioBitrate) {
                    Text("128 kbps").tag(128_000)
                    Text("192 kbps").tag(192_000)
                    Text("256 kbps").tag(256_000)
                    Text("320 kbps").tag(320_000)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            } footnote: { "AAC 192 kbps 已接近 CD 音質；無損格式不受此設定影響。" }
        case .textEncoding:
            optionRow("來源編碼", systemImage: "character.book.closed") {
                HStack(spacing: 10) {
                    Picker("來源編碼", selection: $model.options.sourceEncoding) {
                        ForEach(ConversionTextEncoding.allCases) { enc in
                            Text(enc.title).tag(enc)
                        }
                    }
                    .labelsHidden()
                    .fixedSize()
                    if let detected = model.detectedEncodingSummary, model.options.sourceEncoding == .auto {
                        Text("偵測到：\(detected)")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                }
            } footnote: { "若轉出的文字是亂碼，請手動指定原始檔的編碼。" }
        }
    }

    /// 標題在上、控制項在下，窄窗格也不會被撐開。
    private func optionRow<Control: View>(_ title: String, systemImage: String, @ViewBuilder control: () -> Control,
                                          footnote: () -> String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: systemImage)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
            control()
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(footnote())
                .font(.system(size: 10.5))
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 2)
    }
}
