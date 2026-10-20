import AppKit
import CoreGraphics

// 四種圖示方向：A 稜鏡、B 紙鶴、C 流光 Q、D 蝴蝶；各自附一個狀態列 template 圖示。
let out = URL(fileURLWithPath: CommandLine.arguments[1])
let cs = CGColorSpace(name: CGColorSpace.displayP3)!
func c(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(colorSpace: cs, components: [CGFloat((hex >> 16) & 0xff) / 255, CGFloat((hex >> 8) & 0xff) / 255, CGFloat(hex & 0xff) / 255, a])!
}
func grad(_ colors: [CGColor], _ locs: [CGFloat]? = nil) -> CGGradient { CGGradient(colorsSpace: cs, colors: colors as CFArray, locations: locs)! }
func canvas(_ size: Int) -> CGContext {
    CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
}
func save(_ ctx: CGContext, _ name: String) {
    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    try! rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name))
}
func squircle(_ r: CGRect, n: CGFloat = 5.2) -> CGPath {
    let p = CGMutablePath()
    let a = r.width / 2, b = r.height / 2
    for i in 0...720 {
        let t = CGFloat(i) * .pi / 360
        let x = r.midX + a * (cos(t) >= 0 ? 1 : -1) * pow(abs(cos(t)), 2 / n)
        let y = r.midY + b * (sin(t) >= 0 ? 1 : -1) * pow(abs(sin(t)), 2 / n)
        i == 0 ? p.move(to: CGPoint(x: x, y: y)) : p.addLine(to: CGPoint(x: x, y: y))
    }
    p.closeSubpath()
    return p
}
func poly(_ pts: [CGPoint]) -> CGPath {
    let p = CGMutablePath(); p.addLines(between: pts); p.closeSubpath(); return p
}
func P(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x, y: y) }
let body = CGRect(x: 100, y: 100, width: 824, height: 824)

/// 共用外框：陰影、裁切、最後的玻璃反光與細邊
func icon(_ name: String, background: (CGContext) -> Void, art: (CGContext) -> Void, sheen: CGFloat = 0.10, edge: CGFloat = 0.10) {
    let ctx = canvas(1024)
    let shape = squircle(body)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 30, color: c(0x000000, 0.32))
    ctx.addPath(shape); ctx.setFillColor(c(0x000000)); ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(shape); ctx.clip()
    background(ctx)
    art(ctx)
    var rng = SystemRandomNumberGenerator()
    for _ in 0..<18000 {
        ctx.setFillColor(c(Bool.random(using: &rng) ? 0xFFFFFF : 0x000000, CGFloat.random(in: 0.015...0.045, using: &rng)))
        ctx.fill(CGRect(x: CGFloat.random(in: 100...924, using: &rng), y: CGFloat.random(in: 100...924, using: &rng), width: 1.5, height: 1.5))
    }
    ctx.drawLinearGradient(grad([c(0xFFFFFF, sheen), c(0xFFFFFF, 0)]), start: P(512, 924), end: P(512, 640), options: [])
    ctx.restoreGState()
    ctx.addPath(shape); ctx.setStrokeColor(c(0xFFFFFF, edge)); ctx.setLineWidth(2); ctx.strokePath()
    save(ctx, name)
}

// MARK: A 稜鏡 —— 一道白光穿過稜鏡，散成彩虹
icon("A.png", background: { ctx in
    ctx.drawRadialGradient(grad([c(0x23306B), c(0x0B1028), c(0x05070F)], [0, 0.55, 1]), startCenter: P(512, 560), startRadius: 0, endCenter: P(512, 560), endRadius: 620, options: [.drawsAfterEndLocation])
}, art: { ctx in
    let apex = P(500, 716), bl = P(312, 392), br = P(688, 392)
    let entry = P(bl.x + (apex.x - bl.x) * 0.42, bl.y + (apex.y - bl.y) * 0.42)
    let exit = P(apex.x + (br.x - apex.x) * 0.55, apex.y + (br.y - apex.y) * 0.55)
    // 入射白光
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 26, color: c(0xFFFFFF, 0.9))
    ctx.setStrokeColor(c(0xFFFFFF, 0.95)); ctx.setLineWidth(13); ctx.setLineCap(.round)
    ctx.move(to: P(60, 452)); ctx.addLine(to: entry); ctx.strokePath()
    ctx.restoreGState()
    // 彩虹扇形
    let colors: [UInt32] = [0xFF3B5C, 0xFF8A2B, 0xFFD43B, 0x3BE07A, 0x2FB8FF, 0x7A5CFF]
    let farX: CGFloat = 1000
    ctx.saveGState()
    ctx.setBlendMode(.screen)
    for (i, col) in colors.enumerated() {
        let a0 = (8 - CGFloat(i) * 6.2) * .pi / 180, a1 = (8 - CGFloat(i + 1) * 6.2) * .pi / 180
        let y0 = exit.y + tan(a0) * (farX - exit.x), y1 = exit.y + tan(a1) * (farX - exit.x)
        let band = poly([P(exit.x, exit.y + 4 - CGFloat(i) * 1.6), P(farX, y0), P(farX, y1), P(exit.x, exit.y + 2.4 - CGFloat(i) * 1.6)])
        ctx.saveGState()
        ctx.setShadow(offset: .zero, blur: 22, color: c(col, 0.9))
        ctx.addPath(band); ctx.clip()
        ctx.drawLinearGradient(grad([c(col, 0.55), c(col, 1)]), start: exit, end: P(farX, exit.y), options: [])
        ctx.restoreGState()
    }
    ctx.restoreGState()
    // 稜鏡內部光
    ctx.saveGState()
    ctx.setStrokeColor(c(0xFFFFFF, 0.35)); ctx.setLineWidth(8)
    ctx.move(to: entry); ctx.addLine(to: exit); ctx.strokePath()
    ctx.restoreGState()
    // 玻璃稜鏡
    let prism = poly([apex, br, bl])
    ctx.saveGState()
    ctx.addPath(prism); ctx.clip()
    ctx.drawLinearGradient(grad([c(0xFFFFFF, 0.22), c(0x9FB4FF, 0.06), c(0xFFFFFF, 0.14)]), start: P(300, 700), end: P(660, 380), options: [])
    ctx.restoreGState()
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 14, color: c(0xBFD0FF, 0.8))
    ctx.addPath(prism); ctx.setStrokeColor(c(0xFFFFFF, 0.9)); ctx.setLineWidth(6); ctx.setLineJoin(.round); ctx.strokePath()
    ctx.restoreGState()
    ctx.setStrokeColor(c(0xFFFFFF, 0.55)); ctx.setLineWidth(3)
    ctx.move(to: P(bl.x + 26, bl.y + 30)); ctx.addLine(to: P(apex.x - 6, apex.y - 48)); ctx.strokePath()
}, sheen: 0.08)

// MARK: B 紙鶴 —— 一張紙摺成新的樣子
icon("B.png", background: { ctx in
    ctx.drawLinearGradient(grad([c(0xFF7A59), c(0xE5396B), c(0x8E2A7E)], [0, 0.55, 1]), start: P(140, 920), end: P(900, 110), options: [])
    ctx.drawRadialGradient(grad([c(0xFFE2B8, 0.85), c(0xFFC48A, 0.0)]), startCenter: P(620, 700), startRadius: 0, endCenter: P(620, 700), endRadius: 320, options: [])
    ctx.setFillColor(c(0xFFE7C2, 0.5)); ctx.fillEllipse(in: CGRect(x: 520, y: 600, width: 230, height: 230))
}, art: { ctx in
    let facets: [([CGPoint], UInt32)] = [
        ([P(500, 520), P(408, 872), P(566, 540)], 0xE3D8CC),            // 後翅
        ([P(452, 500), P(572, 900), P(650, 496)], 0xFFFFFF),            // 前翅
        ([P(572, 900), P(560, 520), P(650, 496)], 0xF0E8DE),            // 前翅陰影面
        ([P(436, 484), P(214, 772), P(246, 778), P(476, 458)], 0xF4EEE6),  // 頸
        ([P(214, 772), P(166, 716), P(240, 760)], 0xD5C7B7),            // 頭
        ([P(600, 458), P(830, 760), P(800, 772), P(566, 480)], 0xEFE7DD),  // 尾
        ([P(430, 486), P(512, 352), P(512, 532)], 0xEDE4D9),            // 身體左
        ([P(512, 532), P(512, 352), P(606, 486)], 0xD8CBBC),            // 身體右
    ]
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -24), blur: 42, color: c(0x4A0E33, 0.45))
    ctx.beginTransparencyLayer(auxiliaryInfo: nil)
    for (pts, col) in facets {
        ctx.addPath(poly(pts)); ctx.setFillColor(c(col)); ctx.fillPath()
    }
    ctx.endTransparencyLayer()
    ctx.restoreGState()
    ctx.setStrokeColor(c(0xC9B9A8, 0.85)); ctx.setLineWidth(2.5); ctx.setLineCap(.round)
    for (a, b) in [(P(512, 352), P(512, 532)), (P(572, 900), P(560, 520)), (P(452, 500), P(650, 496))] {
        ctx.move(to: a); ctx.addLine(to: b); ctx.strokePath()
    }
}, sheen: 0.14, edge: 0.18)

// MARK: C 流光 Q —— 彩色光環甩出一道尾巴
icon("C.png", background: { ctx in
    ctx.drawLinearGradient(grad([c(0x17161F), c(0x0B0B10)]), start: P(512, 924), end: P(512, 100), options: [])
    ctx.drawRadialGradient(grad([c(0x6B4CFF, 0.30), c(0x6B4CFF, 0)]), startCenter: P(480, 560), startRadius: 0, endCenter: P(480, 560), endRadius: 420, options: [])
}, art: { ctx in
    let center = P(488, 548), radius: CGFloat = 214, width: CGFloat = 104
    let stops: [UInt32] = [0xFF9A3C, 0xFF4F7B, 0xB45CFF, 0x5C7CFF, 0x2FD6E8, 0xFF9A3C]
    func mix(_ t: CGFloat) -> CGColor {
        let s = t * CGFloat(stops.count - 1); let i = min(Int(s), stops.count - 2); let f = s - CGFloat(i)
        let a = stops[i], b = stops[i + 1]
        func ch(_ v: UInt32, _ sh: UInt32) -> CGFloat { CGFloat((v >> sh) & 0xff) / 255 }
        return CGColor(colorSpace: cs, components: [ch(a, 16) * (1 - f) + ch(b, 16) * f, ch(a, 8) * (1 - f) + ch(b, 8) * f, ch(a, 0) * (1 - f) + ch(b, 0) * f, 1])!
    }
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 40, color: c(0x9A6BFF, 0.55))
    ctx.beginTransparencyLayer(auxiliaryInfo: nil)
    ctx.setLineWidth(width); ctx.setLineCap(.butt)
    let steps = 360
    for i in 0..<steps {
        let a0 = CGFloat(i) / CGFloat(steps) * 2 * .pi + .pi / 4, a1 = CGFloat(i + 1) / CGFloat(steps) * 2 * .pi + .pi / 4 + 0.01
        ctx.setStrokeColor(mix(CGFloat(i) / CGFloat(steps)))
        ctx.addArc(center: center, radius: radius, startAngle: a0, endAngle: a1, clockwise: false)
        ctx.strokePath()
    }
    // 尾巴：像字母 Q 一樣從圈內斜穿出去，粗而漸細
    let tail = CGMutablePath()
    tail.move(to: P(center.x + 70, center.y - 40))
    tail.addCurve(to: P(820, 186), control1: P(center.x + 150, center.y - 150), control2: P(720, 200))
    tail.addCurve(to: P(center.x + 20, center.y - 96), control1: P(700, 150), control2: P(center.x + 110, center.y - 190))
    tail.closeSubpath()
    ctx.addPath(tail); ctx.clip()
    ctx.drawLinearGradient(grad([c(0xFF4F7B), c(0xFF9A3C), c(0xFFE25C)]), start: P(center.x + 40, center.y - 70), end: P(820, 186), options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    ctx.endTransparencyLayer()
    ctx.restoreGState()
    // 內圈高光
    ctx.setStrokeColor(c(0xFFFFFF, 0.28)); ctx.setLineWidth(4)
    ctx.addArc(center: center, radius: radius + width / 2 - 8, startAngle: .pi * 0.55, endAngle: .pi * 1.05, clockwise: false); ctx.strokePath()
}, sheen: 0.06)

// MARK: D 蝴蝶 —— 蛻變
icon("D.png", background: { ctx in
    ctx.drawLinearGradient(grad([c(0xFFF6EC), c(0xF3ECFF)]), start: P(140, 920), end: P(900, 110), options: [])
    ctx.drawRadialGradient(grad([c(0xFFD9C2, 0.7), c(0xFFD9C2, 0)]), startCenter: P(280, 300), startRadius: 0, endCenter: P(280, 300), endRadius: 420, options: [])
}, art: { ctx in
    let body = P(512, 500)
    func wing(_ cx: CGFloat, _ cy: CGFloat, _ w: CGFloat, _ h: CGFloat, _ rot: CGFloat, _ cols: [UInt32]) {
        ctx.saveGState()
        ctx.translateBy(x: cx, y: cy); ctx.rotate(by: rot)
        let r = CGRect(x: -w / 2, y: -h / 2, width: w, height: h)
        let path = CGPath(roundedRect: r, cornerWidth: w * 0.48, cornerHeight: h * 0.48, transform: nil)
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 26, color: c(cols[1], 0.45))
        ctx.addPath(path); ctx.setFillColor(c(cols[0])); ctx.fillPath()
        ctx.restoreGState()
        ctx.addPath(path); ctx.clip()
        ctx.drawLinearGradient(grad(cols.map { c($0) }), start: P(0, h / 2), end: P(0, -h / 2), options: [])
        ctx.setStrokeColor(c(0xFFFFFF, 0.6)); ctx.setLineWidth(8)
        ctx.addPath(path); ctx.strokePath()
        ctx.restoreGState()
    }
    ctx.saveGState()
    ctx.setAlpha(0.96)
    wing(body.x - 150, body.y - 120, 210, 250, -0.55, [0xFF9A4A, 0xFFC94A])
    wing(body.x + 150, body.y - 120, 210, 250, 0.55, [0xFF9A4A, 0xFFC94A])
    wing(body.x - 168, body.y + 128, 280, 330, 0.62, [0x6E56FF, 0xFF5FA2])
    wing(body.x + 168, body.y + 128, 280, 330, -0.62, [0x6E56FF, 0xFF5FA2])
    ctx.restoreGState()
    // 身體與觸角
    let torso = CGPath(roundedRect: CGRect(x: body.x - 17, y: body.y - 190, width: 34, height: 400), cornerWidth: 17, cornerHeight: 17, transform: nil)
    ctx.addPath(torso); ctx.setFillColor(c(0x2B2440)); ctx.fillPath()
    ctx.setStrokeColor(c(0x2B2440)); ctx.setLineWidth(9); ctx.setLineCap(.round)
    ctx.move(to: P(body.x - 6, body.y + 200)); ctx.addQuadCurve(to: P(body.x - 90, body.y + 330), control: P(body.x - 30, body.y + 300)); ctx.strokePath()
    ctx.move(to: P(body.x + 6, body.y + 200)); ctx.addQuadCurve(to: P(body.x + 90, body.y + 330), control: P(body.x + 30, body.y + 300)); ctx.strokePath()
    ctx.setFillColor(c(0x2B2440))
    ctx.fillEllipse(in: CGRect(x: body.x - 104, y: body.y + 318, width: 26, height: 26))
    ctx.fillEllipse(in: CGRect(x: body.x + 78, y: body.y + 318, width: 26, height: 26))
}, sheen: 0.30, edge: 0.6)

// MARK: 狀態列 template（36px = 18pt @2x；黑色＋透明）
func glyph(_ name: String, _ draw: (CGContext) -> Void) {
    let ctx = canvas(108)   // 6x 解析度方便預覽，實際以 18pt 顯示
    ctx.scaleBy(x: 6, y: 6)
    ctx.setFillColor(c(0x000000)); ctx.setStrokeColor(c(0x000000))
    ctx.setLineCap(.round); ctx.setLineJoin(.round)
    draw(ctx)
    save(ctx, name)
}
glyph("A-glyph.png") { ctx in
    ctx.setLineWidth(1.6)
    ctx.addPath(poly([P(8.4, 15.4), P(13.6, 4.2), P(3.2, 4.2)])); ctx.strokePath()
    ctx.setLineWidth(1.5)
    ctx.move(to: P(0.4, 8.0)); ctx.addLine(to: P(5.6, 9.6)); ctx.strokePath()
    for (i, y) in [CGFloat(11.4), 9.2, 7.0].enumerated() {
        ctx.setLineWidth(1.5 - CGFloat(i) * 0.15)
        ctx.move(to: P(11.6, 9.4)); ctx.addLine(to: P(17.4, y)); ctx.strokePath()
    }
}
glyph("B-glyph.png") { ctx in
    let polys: [[CGPoint]] = [
        [P(500, 520), P(408, 872), P(566, 540)], [P(452, 500), P(572, 900), P(650, 496)],
        [P(436, 484), P(214, 772), P(246, 778), P(476, 458)], [P(214, 772), P(166, 716), P(240, 760)],
        [P(600, 458), P(830, 760), P(800, 772), P(566, 480)], [P(430, 486), P(512, 352), P(606, 486), P(512, 532)],
    ]
    let s: CGFloat = 18.0 / 700.0
    for p in polys { ctx.addPath(poly(p.map { P(($0.x - 150) * s, ($0.y - 300) * s + 0.4) })); ctx.fillPath() }
}
glyph("C-glyph.png") { ctx in
    ctx.setLineWidth(2.5)
    ctx.addEllipse(in: CGRect(x: 2.0, y: 4.0, width: 11.6, height: 11.6)); ctx.strokePath()
    // 像字母 Q：尾巴從圈內穿出
    ctx.setLineWidth(2.6)
    ctx.move(to: P(9.6, 8.0)); ctx.addQuadCurve(to: P(16.8, 1.4), control: P(12.4, 3.0)); ctx.strokePath()
}
glyph("D-glyph.png") { ctx in
    func w(_ cx: CGFloat, _ cy: CGFloat, _ ww: CGFloat, _ hh: CGFloat, _ r: CGFloat) {
        ctx.saveGState(); ctx.translateBy(x: cx, y: cy); ctx.rotate(by: r)
        ctx.addPath(CGPath(roundedRect: CGRect(x: -ww / 2, y: -hh / 2, width: ww, height: hh), cornerWidth: ww * 0.48, cornerHeight: hh * 0.48, transform: nil)); ctx.fillPath()
        ctx.restoreGState()
    }
    w(4.9, 9.4, 6.4, 7.6, 0.62); w(13.1, 9.4, 6.4, 7.6, -0.62)     // 上翅（大）
    w(6.0, 3.6, 3.6, 4.2, -0.55); w(12.0, 3.6, 3.6, 4.2, 0.55)     // 下翅（小）
    ctx.setBlendMode(.clear); ctx.fill(CGRect(x: 8.15, y: 0.5, width: 1.7, height: 14.5)); ctx.setBlendMode(.normal)
    ctx.addPath(CGPath(roundedRect: CGRect(x: 8.4, y: 1.8, width: 1.2, height: 12.0), cornerWidth: 0.6, cornerHeight: 0.6, transform: nil)); ctx.fillPath()
    ctx.setLineWidth(1.0)
    ctx.move(to: P(8.7, 13.6)); ctx.addQuadCurve(to: P(6.6, 17.0), control: P(7.6, 15.8)); ctx.strokePath()
    ctx.move(to: P(9.3, 13.6)); ctx.addQuadCurve(to: P(11.4, 17.0), control: P(10.4, 15.8)); ctx.strokePath()
}
print("ok")
