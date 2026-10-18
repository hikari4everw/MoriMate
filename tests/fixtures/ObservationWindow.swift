import AppKit

final class FixtureView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.setFill()
        bounds.fill()
        let text = "小明：今天一起去看海吧。" as NSString
        text.draw(at: NSPoint(x: 24, y: 80), withAttributes: [
            .font: NSFont.systemFont(ofSize: 38), .foregroundColor: NSColor.black
        ])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let window = NSWindow(contentRect: NSRect(x: 80, y: 80, width: 800, height: 240),
                      styleMask: [.titled], backing: .buffered, defer: false)
window.title = "Companion capture test fixture"
window.animationBehavior = .none
window.contentView = FixtureView(frame: NSRect(x: 0, y: 0, width: 800, height: 240))
window.orderFrontRegardless()
FileHandle.standardOutput.write(Data("\(window.windowNumber)\n".utf8))
app.run()
