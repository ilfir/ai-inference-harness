// AI Harness.app: a native window around harness-ui.
//
// Starts the bundled harness-ui/server.py on a free localhost port with the system python3, shows it in a
// WKWebView, and stops the server when the app quits. Settings live in
// ~/Library/Application Support/AI Harness/settings.json. Built by harness-ui/macos/build-app.sh.

import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var serverURL: URL?
    var logTail = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "AI Harness"
        window.contentView = web
        window.setFrameAutosaveName("AIHarnessMain")
        window.center()
        window.makeKeyAndOrderFront(nil)
        web.loadHTMLString("<body style='font:15px -apple-system;color:#888;display:flex;align-items:center;justify-content:center;height:90vh'>Starting…</body>", baseURL: nil)
        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func startServer() {
        let res = Bundle.main.resourceURL!.appendingPathComponent("harness")
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("AI Harness")
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-u", res.appendingPathComponent("harness-ui/server.py").path, "--port", "0", "--no-browser"]
        p.currentDirectoryURL = res
        var env = ProcessInfo.processInfo.environment
        env["AIH_UI_SETTINGS"] = support.appendingPathComponent("settings.json").path
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // keep the signed bundle unchanged
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            guard let self = self, let s = String(data: h.availableData, encoding: .utf8), !s.isEmpty else { return }
            DispatchQueue.main.async { self.onServerOutput(s) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.onServerExit(proc.terminationStatus) }
        }
        do {
            try p.run()
            server = p
        } catch {
            fail("Could not start /usr/bin/python3: \(error.localizedDescription)\n\nInstall the Command Line Tools: xcode-select --install")
        }
    }

    func onServerOutput(_ s: String) {
        logTail = String((logTail + s).suffix(4000))
        guard serverURL == nil,
              let r = logTail.range(of: #"http://127\.0\.0\.1:\d+"#, options: .regularExpression),
              let url = URL(string: String(logTail[r])) else { return }
        serverURL = url
        web.load(URLRequest(url: url))
    }

    func onServerExit(_ code: Int32) {
        server = nil
        if NSApp.isRunning && !terminating {
            fail("The harness-ui server stopped (exit \(code)).\n\n\(logTail)")
        }
    }

    var terminating = false

    func fail(_ message: String) {
        let a = NSAlert()
        a.messageText = "AI Harness"
        a.informativeText = message
        a.addButton(withTitle: "Quit")
        a.runModal()
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        terminating = true
        server?.terminate()
    }

    // Reload when the page fails to load (e.g. the server was still binding).
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if let url = serverURL {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { webView.load(URLRequest(url: url)) }
        }
    }

    // Open links that leave the app's server in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, url.scheme?.hasPrefix("http") == true,
           host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    // JavaScript alert() and confirm() as native sheets.
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About AI Harness", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide AI Harness", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit AI Harness", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        viewItem.submenu = view

        let winItem = NSMenuItem()
        main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        NSApp.mainMenu = main
    }

    @objc func reload() {
        if let url = serverURL { web.load(URLRequest(url: url)) }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
