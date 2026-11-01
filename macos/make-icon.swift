import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let context = NSGraphicsContext.current!.cgContext
        context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
        let plate = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 200, yRadius: 200)
        NSColor(calibratedRed: 0.13, green: 0.16, blue: 0.18, alpha: 1).setFill()
        plate.fill()
        // Three quiet columns: a reading instrument, not a literal flame.
        let heights: [CGFloat] = [220, 380, 540]
        for (index, height) in heights.enumerated() {
            NSColor(calibratedRed: 0.87, green: 0.65 + Double(index) * 0.035, blue: 0.40, alpha: 1).setFill()
            NSBezierPath(roundedRect: NSRect(x: 274 + CGFloat(index) * 172, y: 242, width: 132, height: height), xRadius: 35, yRadius: 35).fill()
        }
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let suffix = scale == 2 ? "@2x" : ""
        let name = "icon_\(size)x\(size)\(suffix).png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(name))
    }
}
