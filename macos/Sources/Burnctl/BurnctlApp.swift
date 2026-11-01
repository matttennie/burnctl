import SwiftUI
import AppKit

/// One explicit native window owner. This avoids relying on implicit scene
/// restoration when the locally packaged app is rebuilt or moved between paths.
@main
enum BurnctlMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.setActivationPolicy(.regular)
        app.delegate = delegate
        app.run()
        withExtendedLifetime(delegate) {}
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSToolbarDelegate {
    private var store: ReportStore?
    private var window: NSWindow?
    private let controlsID = NSToolbarItem.Identifier("report-controls")

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = ProcessInfo.processInfo.arguments
        let store = ReportStore(demo: args.contains("--demo"))
        if args.contains("--previous") { store.period = .previous }
        self.store = store
        let hosting = NSHostingController(rootView: DashboardView(store: store)
            .preferredColorScheme(args.contains("--snapshot-dark") ? .dark : args.contains("--snapshot-light") ? .light : nil))
        let window = NSWindow(contentViewController: hosting)
        window.title = "Burnctl"
        if args.contains("--snapshot-light") { window.appearance = NSAppearance(named: .aqua) }
        if args.contains("--snapshot-dark") { window.appearance = NSAppearance(named: .darkAqua) }
        window.setContentSize(NSSize(width: 1180, height: 800))
        window.contentMinSize = NSSize(width: 980, height: 680)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let toolbar = NSToolbar(identifier: "BurnctlToolbar")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar
        window.toolbarStyle = .unified
        self.window = window
        installMenus()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Integration-test-only close path exercises real window lifecycle.
        if let index = args.firstIndex(of: "--close-after"), args.indices.contains(index + 1),
           let seconds = Double(args[index + 1]), seconds > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in self?.window?.performClose(nil) }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { store?.cancel() }
    func windowWillClose(_ notification: Notification) { store?.cancel() }
    @objc private func refreshUsage() { store?.refresh() }
    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { [.flexibleSpace, controlsID] }
    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { [.flexibleSpace, controlsID] }
    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier, willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        guard identifier == controlsID, let store else { return nil }
        let item = NSToolbarItem(itemIdentifier: controlsID)
        item.label = "Usage controls"
        let args = ProcessInfo.processInfo.arguments
        let scheme: ColorScheme? = args.contains("--snapshot-dark") ? .dark : args.contains("--snapshot-light") ? .light : nil
        let host = NSHostingView(rootView: ReportToolbar(store: store).preferredColorScheme(scheme))
        Snapshot.toolbarView = host
        host.frame = NSRect(x: 0, y: 0, width: 310, height: 32)
        item.view = host
        return item
    }
    private func installMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Burnctl")
        appMenu.addItem(withTitle: "About Burnctl", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Burnctl", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        let fileItem = NSMenuItem()
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        main.addItem(fileItem)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        main.addItem(editItem)
        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        let refresh = viewMenu.addItem(withTitle: "Refresh Usage", action: #selector(refreshUsage), keyEquivalent: "r")
        refresh.target = self
        viewItem.submenu = viewMenu
        main.addItem(viewItem)
        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
    }
}

struct ReportToolbar: View {
    @ObservedObject var store: ReportStore
    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 2) {
                ForEach(ReportPeriod.allCases) { period in
                    Button { store.period = period } label: {
                        Text(period.title).font(.system(size: 12, weight: store.period == period ? .semibold : .regular))
                            .foregroundStyle(Color.primary)
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                            .background(store.period == period ? Color.primary.opacity(0.10) : Color.clear, in: RoundedRectangle(cornerRadius: 7))
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .accessibilityAddTraits(store.period == period ? .isSelected : [])
                }
            }.frame(width: 255)
                .help("Each provider uses its own billing period or rolling window.")
            Button { store.refresh() } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.borderless)
                .help("Refresh usage (⌘R)").accessibilityLabel("Refresh usage")
        }.frame(width: 310, height: 32).background(Color(nsColor: .windowBackgroundColor))
    }
}

@MainActor
enum Snapshot {
    static var saved = false
    static weak var toolbarView: NSView?
    static func captureIfRequested() {
        let args = ProcessInfo.processInfo.arguments
        guard !saved, let index = args.firstIndex(of: "--snapshot"), args.indices.contains(index + 1) else { return }
        saved = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            guard let view = NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil })?.contentView?.superview,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
                fputs("Could not capture app window.\n", stderr)
                NSApp.terminate(nil)
                return
            }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            do {
                guard let png = bitmap.representation(using: .png, properties: [:]) else { throw ReportError.failed }
                try png.write(to: URL(fileURLWithPath: args[index + 1]))
                if let toolbar = toolbarView, let toolbarBitmap = toolbar.bitmapImageRepForCachingDisplay(in: toolbar.bounds) {
                    toolbar.cacheDisplay(in: toolbar.bounds, to: toolbarBitmap)
                    if let toolbarPNG = toolbarBitmap.representation(using: .png, properties: [:]) {
                        let toolbarURL = URL(fileURLWithPath: args[index + 1]).deletingPathExtension().appendingPathExtension("toolbar.png")
                        try toolbarPNG.write(to: toolbarURL)
                    }
                }
                print("Snapshot saved: \(args[index + 1])")
            } catch { fputs("Could not save snapshot.\n", stderr) }
            NSApp.terminate(nil)
        }
    }
}
