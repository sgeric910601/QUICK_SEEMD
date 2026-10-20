import AppKit

/// QUICK_SEEMD 的單色標誌（稜鏡：一道光進去、散成三道出來），給選單列與 Finder 右鍵選單用；template 會自動配合深淺色。
enum AppMark {
    static func templateImage(size: CGFloat = 18) -> NSImage {
        let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
            let s = rect.width / 18
            func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: x * s, y: y * s) }
            NSColor.black.setStroke()
            // 稜鏡
            let prism = NSBezierPath()
            prism.move(to: p(8.4, 15.4))
            prism.line(to: p(13.6, 4.2))
            prism.line(to: p(3.2, 4.2))
            prism.close()
            prism.lineWidth = 1.6 * s
            prism.lineJoinStyle = .round
            prism.stroke()
            // 入射光
            let beam = NSBezierPath()
            beam.move(to: p(0.4, 8.0))
            beam.line(to: p(5.6, 9.6))
            beam.lineWidth = 1.5 * s
            beam.lineCapStyle = .round
            beam.stroke()
            // 散開的三道光
            for (i, y) in [CGFloat(11.4), 9.2, 7.0].enumerated() {
                let ray = NSBezierPath()
                ray.move(to: p(11.6, 9.4))
                ray.line(to: p(17.4, y))
                ray.lineWidth = (1.5 - CGFloat(i) * 0.15) * s
                ray.lineCapStyle = .round
                ray.stroke()
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}
