// Draws the AI Harness app icon (1024x1024 PNG) with Core Graphics, so the repo holds no image binaries.
//   swift icon.swift out.png
// A lightning bolt (the "Flash" model) on a chip, held by an orbit ring — the harness — with three
// bright tokens racing along it (MTP drafts), on a midnight-to-violet squircle.

import AppKit

let S: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0,
                    space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: a)
}
func gradient(_ colors: [CGColor], _ locs: [CGFloat]) -> CGGradient {
    CGGradient(colorsSpace: cs, colors: colors as CFArray, locations: locs)!
}

// Background squircle (macOS icon grid: 824 pt body inside 1024 with ~100 pt margins).
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let squircle = CGPath(roundedRect: body, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -14), blur: 30, color: rgb(0x000000, 0.45))
ctx.addPath(squircle); ctx.setFillColor(rgb(0x0b0b1e)); ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(squircle); ctx.clip()
ctx.drawLinearGradient(gradient([rgb(0x0a0a23), rgb(0x241a5c), rgb(0x5b21b6)], [0, 0.55, 1]),
                       start: CGPoint(x: 512, y: 100), end: CGPoint(x: 512, y: 924), options: [])
// Soft nebula glow behind the chip.
ctx.drawRadialGradient(gradient([rgb(0xa855f7, 0.55), rgb(0x6366f1, 0.18), rgb(0x6366f1, 0)], [0, 0.45, 1]),
                       startCenter: CGPoint(x: 512, y: 540), startRadius: 0,
                       endCenter: CGPoint(x: 512, y: 540), endRadius: 430, options: [])
// A few stars.
let stars: [(CGFloat, CGFloat, CGFloat)] = [(210, 800, 5), (300, 860, 3), (790, 830, 4), (850, 700, 3),
                                             (180, 330, 3), (820, 260, 4), (705, 880, 2.5), (260, 600, 2.5)]
for (x, y, r) in stars {
    ctx.setFillColor(rgb(0xffffff, 0.75)); ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
}
// Top sheen.
ctx.drawLinearGradient(gradient([rgb(0xffffff, 0.10), rgb(0xffffff, 0)], [0, 1]),
                       start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 640), options: [])
ctx.restoreGState()

let c = CGPoint(x: 512, y: 520)

// Orbit ring (the harness), tilted; drawn in two halves so the chip sits "inside" it.
func ring(back: Bool) {
    ctx.saveGState()
    ctx.translateBy(x: c.x, y: c.y); ctx.rotate(by: -0.42)
    let r = CGRect(x: -330, y: -118, width: 660, height: 236)
    ctx.addRect(back ? CGRect(x: -400, y: 0, width: 800, height: 200) : CGRect(x: -400, y: -200, width: 800, height: 200))
    ctx.clip()
    ctx.setLineWidth(22)
    ctx.setStrokeColor(rgb(0x22d3ee, back ? 0.35 : 0.95))
    ctx.setShadow(offset: .zero, blur: back ? 0 : 26, color: rgb(0x22d3ee, 0.9))
    ctx.strokeEllipse(in: r)
    ctx.restoreGState()
}
ring(back: true)

// Chip with pins.
let chip = CGRect(x: c.x - 170, y: c.y - 170, width: 340, height: 340)
ctx.saveGState()
ctx.setFillColor(rgb(0xc4b5fd, 0.9))
for i in 0..<5 {
    let o = chip.minX + 58 + CGFloat(i) * 56
    for r in [CGRect(x: o - 11, y: chip.maxY - 4, width: 22, height: 48), CGRect(x: o - 11, y: chip.minY - 44, width: 22, height: 48),
              CGRect(x: chip.maxX - 4, y: o - 11, width: 48, height: 22), CGRect(x: chip.minX - 44, y: o - 11, width: 48, height: 22)] {
        ctx.addPath(CGPath(roundedRect: r, cornerWidth: 8, cornerHeight: 8, transform: nil))
    }
}
ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
let chipPath = CGPath(roundedRect: chip, cornerWidth: 54, cornerHeight: 54, transform: nil)
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: rgb(0x000000, 0.5))
ctx.addPath(chipPath); ctx.setFillColor(rgb(0x1e1b4b)); ctx.fillPath()
ctx.restoreGState()
ctx.saveGState()
ctx.addPath(chipPath); ctx.clip()
ctx.drawLinearGradient(gradient([rgb(0x312e81), rgb(0x1e1b4b)], [0, 1]),
                       start: CGPoint(x: chip.minX, y: chip.maxY), end: CGPoint(x: chip.maxX, y: chip.minY), options: [])
ctx.restoreGState()
ctx.addPath(chipPath); ctx.setLineWidth(10); ctx.setStrokeColor(rgb(0xa78bfa)); ctx.strokePath()

// Lightning bolt.
let bolt = CGMutablePath()
let pts: [(CGFloat, CGFloat)] = [(40, 150), (-85, -15), (-5, -15), (-40, -150), (85, 25), (5, 25)]
bolt.move(to: CGPoint(x: c.x + pts[0].0, y: c.y + pts[0].1))
for p in pts.dropFirst() { bolt.addLine(to: CGPoint(x: c.x + p.0, y: c.y + p.1)) }
bolt.closeSubpath()
ctx.saveGState()
ctx.setShadow(offset: .zero, blur: 40, color: rgb(0xfde047, 0.95))
ctx.addPath(bolt); ctx.setFillColor(rgb(0xfde047)); ctx.fillPath()
ctx.restoreGState()
ctx.saveGState()
ctx.addPath(bolt); ctx.clip()
ctx.drawLinearGradient(gradient([rgb(0xfffbeb), rgb(0xfacc15), rgb(0xf97316)], [0, 0.5, 1]),
                       start: CGPoint(x: c.x, y: c.y + 150), end: CGPoint(x: c.x, y: c.y - 150), options: [])
ctx.restoreGState()

ring(back: false)

// Three draft tokens racing along the front of the ring, brightest first.
for (i, t) in [CGFloat(0.5), 0.88, 1.22].enumerated() {
    let a = Double.pi + Double(t)
    let px = 330 * CGFloat(cos(a)), py = 118 * CGFloat(sin(a))
    let rot: CGFloat = -0.42
    let x = c.x + px * cos(rot) - py * sin(rot), y = c.y + px * sin(rot) + py * cos(rot)
    let r: CGFloat = [30, 23, 17][i]
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 30, color: rgb(0xf0abfc, 1))
    ctx.setFillColor(rgb(0xffffff, [1, 0.85, 0.65][i]))
    ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
    ctx.restoreGState()
}

let img = ctx.makeImage()!
let rep = NSBitmapImageRep(cgImage: img)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
