import Compression
import CoreGraphics
import Foundation

/// 減色＋調色盤 PNG：截圖、圖表、標誌通常可縮小 50–80%，並保留透明度。
/// 顏色本來就不超過上限時完全無損。熱點迴圈使用原始指標，Debug 組建也不會太慢。
enum ITPalettePNG {
    struct Result {
        let colors: Int
        let lossless: Bool
    }

    private struct Color {
        var r: UInt8, g: UInt8, b: UInt8, a: UInt8
    }

    /// - Parameter losslessOnly: 只在顏色數不超過上限時寫出（不減色）；否則回傳 nil
    static func write(_ image: CGImage, maxColors: Int, dither: Bool, losslessOnly: Bool = false, dpi: Double, to url: URL,
                      checkCancellation: () throws -> Void) throws -> Result? {
        let w = image.width, h = image.height
        let count = w * h
        guard count > 0, count <= 400_000_000 else { throw ConversionError.other("圖片太大，無法減色。") }
        let limit = max(2, min(256, maxColors))
        let space = ITRender.colorSpace(like: image)
        let px = UnsafeMutablePointer<UInt8>.allocate(capacity: count * 4)
        defer { px.deallocate() }
        guard let ctx = CGContext(data: px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            throw ConversionError.other("無法讀取像素。")
        }
        ctx.clear(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        unpremultiply(px, count: count)
        try checkCancellation()

        let indices = UnsafeMutablePointer<UInt8>.allocate(capacity: count)
        defer { indices.deallocate() }
        var palette: [Color]
        var lossless = false
        if let exact = exactPalette(px, count: count, limit: limit, into: indices) {
            palette = exact
            lossless = true
        } else {
            if losslessOnly { return nil }
            try checkCancellation()
            palette = try quantize(px, width: w, height: h, maxColors: limit, dither: dither, into: indices,
                                   checkCancellation: checkCancellation)
        }
        try checkCancellation()

        // 半透明的顏色排前面，tRNS 才能截短
        let order = palette.indices.sorted { (palette[$0].a < 255 ? 0 : 1, $0) < (palette[$1].a < 255 ? 0 : 1, $1) }
        var remap = [UInt8](repeating: 0, count: 256)
        for (newIndex, old) in order.enumerated() { remap[old] = UInt8(newIndex) }
        remap.withUnsafeBufferPointer { m in
            for i in 0..<count { indices[i] = m[Int(indices[i])] }
        }
        let sorted = order.map { palette[$0] }

        let data = try encode(width: w, height: h, palette: sorted, indices: indices, space: space, dpi: dpi)
        try data.write(to: url)
        return Result(colors: sorted.count, lossless: lossless)
    }

    /// 還原未預乘的顏色；幾乎透明的像素統一成「全透明」。
    private static func unpremultiply(_ p: UnsafeMutablePointer<UInt8>, count: Int) {
        var o = 0
        for _ in 0..<count {
            let a = Int(p[o + 3])
            if a < 8 {
                p[o] = 0; p[o + 1] = 0; p[o + 2] = 0; p[o + 3] = 0
            } else if a < 255 {
                p[o] = UInt8(min(255, (Int(p[o]) * 255 + a / 2) / a))
                p[o + 1] = UInt8(min(255, (Int(p[o + 1]) * 255 + a / 2) / a))
                p[o + 2] = UInt8(min(255, (Int(p[o + 2]) * 255 + a / 2) / a))
            }
            o += 4
        }
    }

    // MARK: - 無損調色盤

    /// 顏色數不超過上限時直接建立調色盤（開放定址雜湊表）。
    private static func exactPalette(_ p: UnsafeMutablePointer<UInt8>, count: Int, limit: Int,
                                     into indices: UnsafeMutablePointer<UInt8>) -> [Color]? {
        let slots = 1024
        let keys = UnsafeMutablePointer<UInt32>.allocate(capacity: slots)
        let values = UnsafeMutablePointer<UInt8>.allocate(capacity: slots)
        let used = UnsafeMutablePointer<Bool>.allocate(capacity: slots)
        defer { keys.deallocate(); values.deallocate(); used.deallocate() }
        used.initialize(repeating: false, count: slots)
        var palette: [Color] = []
        palette.reserveCapacity(limit)
        var last: UInt32 = 0xFFFF_FFFF, lastIndex: UInt8 = 0
        var o = 0
        for i in 0..<count {
            let key = UInt32(p[o]) << 24 | UInt32(p[o + 1]) << 16 | UInt32(p[o + 2]) << 8 | UInt32(p[o + 3])
            o += 4
            if key == last { indices[i] = lastIndex; continue }
            var slot = Int((key &* 2_654_435_761) >> 22) & (slots - 1)
            while used[slot] && keys[slot] != key { slot = (slot + 1) & (slots - 1) }
            if used[slot] {
                lastIndex = values[slot]
            } else {
                if palette.count >= limit { return nil }
                lastIndex = UInt8(palette.count)
                used[slot] = true
                keys[slot] = key
                values[slot] = lastIndex
                palette.append(Color(r: UInt8(key >> 24), g: UInt8(key >> 16 & 0xFF), b: UInt8(key >> 8 & 0xFF), a: UInt8(key & 0xFF)))
            }
            last = key
            indices[i] = lastIndex
        }
        return palette
    }

    // MARK: - 減色（median cut ＋ k-means 微調 ＋ Floyd–Steinberg 抖色）

    private struct Bucket {
        var r: Float, g: Float, b: Float, a: Float, n: Float

        func channel(_ axis: Int) -> Float {
            switch axis { case 0: return r; case 1: return g; case 2: return b; default: return a }
        }
    }

    /// 5-5-5-4 位元的格子數
    private static let histogramSize = 1 << 19

    @inline(__always) private static func key(_ r: Int, _ g: Int, _ b: Int, _ a: Int) -> Int {
        (r >> 3) << 14 | (g >> 3) << 9 | (b >> 3) << 4 | (a >> 4)
    }

    /// 調色盤（浮點，給最近色搜尋用）
    private final class Palette {
        let count: Int
        let r: UnsafeMutablePointer<Float>, g: UnsafeMutablePointer<Float>, b: UnsafeMutablePointer<Float>, a: UnsafeMutablePointer<Float>

        init(_ colors: [Bucket]) {
            count = colors.count
            r = .allocate(capacity: max(count, 1)); g = .allocate(capacity: max(count, 1))
            b = .allocate(capacity: max(count, 1)); a = .allocate(capacity: max(count, 1))
            update(colors)
        }

        func update(_ colors: [Bucket]) {
            for (i, c) in colors.enumerated() { r[i] = c.r; g[i] = c.g; b[i] = c.b; a[i] = c.a }
        }

        deinit { r.deallocate(); g.deallocate(); b.deallocate(); a.deallocate() }

        @inline(__always) func nearest(_ cr: Float, _ cg: Float, _ cb: Float, _ ca: Float) -> Int {
            var best = 0, bestD = Float.greatestFiniteMagnitude
            for i in 0..<count {
                let dr = r[i] - cr, dg = g[i] - cg, db = b[i] - cb, da = a[i] - ca
                let d = dr * dr * 0.9 + dg * dg * 1.2 + db * db * 0.7 + da * da * 1.5
                if d < bestD { bestD = d; best = i }
            }
            return best
        }
    }

    private static func quantize(_ p: UnsafeMutablePointer<UInt8>, width w: Int, height h: Int, maxColors: Int, dither: Bool,
                                 into indices: UnsafeMutablePointer<UInt8>, checkCancellation: () throws -> Void) throws -> [Color] {
        let count = w * h
        let size = histogramSize
        let n = UnsafeMutablePointer<UInt32>.allocate(capacity: size)
        let sums = UnsafeMutablePointer<UInt64>.allocate(capacity: size * 4)
        defer { n.deallocate(); sums.deallocate() }
        n.initialize(repeating: 0, count: size)
        sums.initialize(repeating: 0, count: size * 4)
        var hasTransparent = false
        var o = 0
        for _ in 0..<count {
            let a = Int(p[o + 3])
            if a == 0 { hasTransparent = true; o += 4; continue }
            let r = Int(p[o]), g = Int(p[o + 1]), b = Int(p[o + 2])
            let k = key(r, g, b, a)
            n[k] += 1
            let s = k * 4
            sums[s] += UInt64(r); sums[s + 1] += UInt64(g); sums[s + 2] += UInt64(b); sums[s + 3] += UInt64(a)
            o += 4
        }
        var buckets: [Bucket] = []
        for k in 0..<size where n[k] > 0 {
            let c = Float(n[k]), s = k * 4
            buckets.append(Bucket(r: Float(sums[s]) / c, g: Float(sums[s + 1]) / c, b: Float(sums[s + 2]) / c, a: Float(sums[s + 3]) / c, n: c))
        }
        try checkCancellation()
        let target = max(2, maxColors - (hasTransparent ? 1 : 0))

        // median cut
        var order = Array(buckets.indices)
        struct Box { var lo: Int; var hi: Int; var score: Float; var axis: Int }
        func analyze(_ lo: Int, _ hi: Int) -> Box {
            var mn: (Float, Float, Float, Float) = (255, 255, 255, 255), mx: (Float, Float, Float, Float) = (0, 0, 0, 0)
            var total: Float = 0
            for i in lo..<hi {
                let b = buckets[order[i]]
                total += b.n
                mn = (min(mn.0, b.r), min(mn.1, b.g), min(mn.2, b.b), min(mn.3, b.a))
                mx = (max(mx.0, b.r), max(mx.1, b.g), max(mx.2, b.b), max(mx.3, b.a))
            }
            let ranges = [mx.0 - mn.0, (mx.1 - mn.1) * 1.2, mx.2 - mn.2, mx.3 - mn.3] // 人眼對綠色較敏感
            var axis = 0
            for a in 1..<4 where ranges[a] > ranges[axis] { axis = a }
            return Box(lo: lo, hi: hi, score: hi - lo < 2 ? -1 : ranges[axis] * total.squareRoot(), axis: axis)
        }
        var boxes = [analyze(0, order.count)]
        while boxes.count < target {
            guard let pick = boxes.indices.max(by: { boxes[$0].score < boxes[$1].score }), boxes[pick].score > 0 else { break }
            let box = boxes[pick]
            let axis = box.axis
            order[box.lo..<box.hi].sort { buckets[$0].channel(axis) < buckets[$1].channel(axis) }
            var total: Float = 0
            for i in box.lo..<box.hi { total += buckets[order[i]].n }
            var acc: Float = 0, mid = box.lo + 1
            for i in box.lo..<box.hi {
                acc += buckets[order[i]].n
                if acc >= total / 2 { mid = i + 1; break }
            }
            mid = min(max(mid, box.lo + 1), box.hi - 1)
            boxes[pick] = analyze(box.lo, mid)
            boxes.append(analyze(mid, box.hi))
        }
        var colors: [Bucket] = boxes.map { box in
            var c = Bucket(r: 0, g: 0, b: 0, a: 0, n: 0)
            for i in box.lo..<box.hi {
                let b = buckets[order[i]]
                c.r += b.r * b.n; c.g += b.g * b.n; c.b += b.b * b.n; c.a += b.a * b.n; c.n += b.n
            }
            return Bucket(r: c.r / c.n, g: c.g / c.n, b: c.b / c.n, a: c.a / c.n, n: c.n)
        }
        try checkCancellation()

        // k-means 微調
        let pal = Palette(colors)
        for _ in 0..<3 {
            var acc = [Bucket](repeating: Bucket(r: 0, g: 0, b: 0, a: 0, n: 0), count: colors.count)
            for b in buckets {
                let i = pal.nearest(b.r, b.g, b.b, b.a)
                acc[i].r += b.r * b.n; acc[i].g += b.g * b.n; acc[i].b += b.b * b.n; acc[i].a += b.a * b.n; acc[i].n += b.n
            }
            for i in colors.indices where acc[i].n > 0 {
                colors[i] = Bucket(r: acc[i].r / acc[i].n, g: acc[i].g / acc[i].n, b: acc[i].b / acc[i].n, a: acc[i].a / acc[i].n, n: acc[i].n)
            }
            pal.update(colors)
            try checkCancellation()
        }

        var palette = colors.map { c in
            Color(r: UInt8(max(0, min(255, c.r.rounded()))), g: UInt8(max(0, min(255, c.g.rounded()))),
                  b: UInt8(max(0, min(255, c.b.rounded()))), a: UInt8(max(0, min(255, c.a.rounded()))))
        }
        // 抖色時用實際寫出的整數顏色計算誤差
        let exact = Palette(palette.map { Bucket(r: Float($0.r), g: Float($0.g), b: Float($0.b), a: Float($0.a), n: 0) })
        var transparentIndex: UInt8 = 0
        if hasTransparent {
            transparentIndex = UInt8(palette.count)
            palette.append(Color(r: 0, g: 0, b: 0, a: 0))
        }

        // 對應每個像素（以 5-5-5-4 位元的格子快取最近色）
        let cache = UnsafeMutablePointer<UInt16>.allocate(capacity: size)
        defer { cache.deallocate() }
        cache.initialize(repeating: 0xFFFF, count: size)
        @inline(__always) func lookup(_ r: Int, _ g: Int, _ b: Int, _ a: Int) -> UInt8 {
            let k = key(r, g, b, a)
            let cached = cache[k]
            if cached != 0xFFFF { return UInt8(truncatingIfNeeded: cached) }
            let i = exact.nearest(Float(r | 4), Float(g | 4), Float(b | 4), Float(a | 8))
            cache[k] = UInt16(i)
            return UInt8(truncatingIfNeeded: i)
        }
        if !dither {
            var o = 0
            for i in 0..<count {
                indices[i] = p[o + 3] == 0 ? transparentIndex : lookup(Int(p[o]), Int(p[o + 1]), Int(p[o + 2]), Int(p[o + 3]))
                o += 4
            }
            return palette
        }
        let strength: Float = 0.75
        let rowFloats = (w + 2) * 4
        var cur = UnsafeMutablePointer<Float>.allocate(capacity: rowFloats)
        var next = UnsafeMutablePointer<Float>.allocate(capacity: rowFloats)
        defer { cur.deallocate(); next.deallocate() }
        cur.initialize(repeating: 0, count: rowFloats)
        next.initialize(repeating: 0, count: rowFloats)
        for y in 0..<h {
            if y % 128 == 0 { try checkCancellation() }
            next.update(repeating: 0, count: rowFloats)
            var o = y * w * 4
            var e = 4
            for x in 0..<w {
                defer { o += 4; e += 4 }
                if p[o + 3] == 0 { indices[y * w + x] = transparentIndex; continue }
                let r = max(0, min(255, Float(p[o]) + cur[e]))
                let g = max(0, min(255, Float(p[o + 1]) + cur[e + 1]))
                let b = max(0, min(255, Float(p[o + 2]) + cur[e + 2]))
                let a = max(8, min(255, Float(p[o + 3]) + cur[e + 3]))
                let idx = lookup(Int(r), Int(g), Int(b), Int(a))
                indices[y * w + x] = idx
                let c = Int(idx)
                let er = (r - exact.r[c]) * strength, eg = (g - exact.g[c]) * strength
                let eb = (b - exact.b[c]) * strength, ea = (a - exact.a[c]) * strength
                cur[e + 4] += er * 0.4375; cur[e + 5] += eg * 0.4375; cur[e + 6] += eb * 0.4375; cur[e + 7] += ea * 0.4375
                next[e - 4] += er * 0.1875; next[e - 3] += eg * 0.1875; next[e - 2] += eb * 0.1875; next[e - 1] += ea * 0.1875
                next[e] += er * 0.3125; next[e + 1] += eg * 0.3125; next[e + 2] += eb * 0.3125; next[e + 3] += ea * 0.3125
                next[e + 4] += er * 0.0625; next[e + 5] += eg * 0.0625; next[e + 6] += eb * 0.0625; next[e + 7] += ea * 0.0625
            }
            swap(&cur, &next)
        }
        return palette
    }

    // MARK: - PNG 編碼

    private static func encode(width w: Int, height h: Int, palette: [Color], indices: UnsafeMutablePointer<UInt8>,
                               space: CGColorSpace, dpi: Double) throws -> Data {
        let depth = palette.count <= 2 ? 1 : palette.count <= 4 ? 2 : palette.count <= 16 ? 4 : 8
        let perByte = 8 / depth
        let rowBytes = (w + perByte - 1) / perByte
        let rawCount = (rowBytes + 1) * h
        let raw = UnsafeMutablePointer<UInt8>.allocate(capacity: rawCount)
        defer { raw.deallocate() }
        raw.initialize(repeating: 0, count: rawCount)
        for y in 0..<h {
            let row = raw + y * (rowBytes + 1) + 1 // 每列第一個位元組是濾波器（0＝不使用）
            let src = indices + y * w
            if depth == 8 {
                row.update(from: src, count: w)
            } else {
                for x in 0..<w {
                    row[x / perByte] |= src[x] << UInt8(8 - depth * (x % perByte + 1))
                }
            }
        }
        var png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        var ihdr = Data()
        ihdr.append(contentsOf: bigEndian(UInt32(w))); ihdr.append(contentsOf: bigEndian(UInt32(h)))
        ihdr.append(contentsOf: [UInt8(depth), 3, 0, 0, 0])
        appendChunk("IHDR", ihdr, to: &png)
        if space.name == CGColorSpace.sRGB {
            appendChunk("sRGB", Data([0]), to: &png)
        } else if let icc = space.copyICCData() as Data? {
            var chunk = Data("ICC Profile".utf8)
            chunk.append(contentsOf: [0, 0])
            chunk.append(try zlib(icc))
            appendChunk("iCCP", chunk, to: &png)
        }
        if abs(dpi - 72) > 0.5 {
            let ppm = UInt32((dpi / 0.0254).rounded())
            var phys = Data()
            phys.append(contentsOf: bigEndian(ppm)); phys.append(contentsOf: bigEndian(ppm)); phys.append(1)
            appendChunk("pHYs", phys, to: &png)
        }
        appendChunk("PLTE", Data(palette.flatMap { [$0.r, $0.g, $0.b] }), to: &png)
        if let lastAlpha = palette.lastIndex(where: { $0.a < 255 }) {
            appendChunk("tRNS", Data(palette[...lastAlpha].map(\.a)), to: &png)
        }
        appendChunk("IDAT", try zlib(Data(bytesNoCopy: raw, count: rawCount, deallocator: .none)), to: &png)
        appendChunk("IEND", Data(), to: &png)
        return png
    }

    private static func appendChunk(_ type: String, _ body: Data, to png: inout Data) {
        png.append(contentsOf: bigEndian(UInt32(body.count)))
        var typed = Data(type.utf8)
        typed.append(body)
        png.append(typed)
        png.append(contentsOf: bigEndian(crc32(typed)))
    }

    /// zlib 串流（RFC 1950）：Compression 框架的 DEFLATE 加上表頭與 Adler-32。
    private static func zlib(_ input: Data) throws -> Data {
        let capacity = input.count + input.count / 8 + 1024
        let out = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
        defer { out.deallocate() }
        let written = input.withUnsafeBytes { src -> Int in
            guard let base = src.bindMemory(to: UInt8.self).baseAddress else { return 0 }
            return compression_encode_buffer(out, capacity, base, input.count, nil, COMPRESSION_ZLIB)
        }
        guard written > 0 || input.isEmpty else { throw ConversionError.writeFailed("PNG 壓縮失敗。") }
        var result = Data([0x78, 0x9C])
        result.append(out, count: written)
        result.append(contentsOf: bigEndian(adler32(input)))
        return result
    }

    private static func adler32(_ data: Data) -> UInt32 {
        var a: UInt32 = 1, b: UInt32 = 0
        data.withUnsafeBytes { buf in
            guard let p = buf.bindMemory(to: UInt8.self).baseAddress else { return }
            let n = buf.count
            var i = 0
            while i < n {
                let end = min(i + 5552, n)
                while i < end { a += UInt32(p[i]); b += a; i += 1 }
                a %= 65521; b %= 65521
            }
        }
        return b << 16 | a
    }

    private static func bigEndian(_ value: UInt32) -> [UInt8] {
        [UInt8(value >> 24 & 0xFF), UInt8(value >> 16 & 0xFF), UInt8(value >> 8 & 0xFF), UInt8(value & 0xFF)]
    }

    private static let crcTable: [UInt32] = (0..<256).map { n -> UInt32 in
        var c = UInt32(n)
        for _ in 0..<8 { c = c & 1 != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
        return c
    }

    private static func crc32(_ data: Data) -> UInt32 {
        var c: UInt32 = 0xFFFF_FFFF
        crcTable.withUnsafeBufferPointer { table in
            data.withUnsafeBytes { buf in
                guard let p = buf.bindMemory(to: UInt8.self).baseAddress else { return }
                for i in 0..<buf.count { c = table[Int((c ^ UInt32(p[i])) & 0xFF)] ^ (c >> 8) }
            }
        }
        return c ^ 0xFFFF_FFFF
    }
}
