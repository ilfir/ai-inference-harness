// Makes the iOS app icon (full-bleed 1024 PNG) from the Mac icon drawing.
//   swift ios-icon.swift mac-icon.png out.png
import AppKit
let a = CommandLine.arguments
let src = NSImage(contentsOfFile: a[1])!
let S: CGFloat = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1024, pixelsHigh: 1024, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor(srgbRed: 0x0b / 255, green: 0x0b / 255, blue: 0x1e / 255, alpha: 1).setFill()
NSRect(x: 0, y: 0, width: S, height: S).fill()
let k: CGFloat = S / 824  // the Mac icon body is 824 pt inside 1024
let size = S * k
src.draw(in: NSRect(x: (S - size) / 2, y: (S - size) / 2, width: size, height: size))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[2]))
