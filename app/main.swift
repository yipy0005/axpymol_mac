// AxPyMOL Helper: menu-bar app that keeps the AxPyMOL bridge running and sets up PowerPoint.
// It runs the bridge on PyMOL.app's own bundled Python, so users need no Python of their own.
//   AxPyMOLHelper --check   print environment status (no UI)
//   AxPyMOLHelper --setup   run cert + manifest setup headlessly
import AppKit
import ServiceManagement

let port = 3443
let fm = FileManager.default
let home = fm.homeDirectoryForCurrentUser
let support = home.appendingPathComponent("Library/Application Support/AxPyMOL")
let certsDir = support.appendingPathComponent("certs")
let logsDir = home.appendingPathComponent("Library/Logs/AxPyMOL")
let wefDir = home.appendingPathComponent("Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef")
let resources = Bundle.main.resourceURL ?? URL(fileURLWithPath: fm.currentDirectoryPath)
let loginKeychain = home.appendingPathComponent("Library/Keychains/login.keychain-db")

@discardableResult
func run(_ exe: String, _ args: [String]) -> (status: Int32, out: String) {
    let p = Process(); p.executableURL = URL(fileURLWithPath: exe); p.arguments = args
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
    return (p.terminationStatus, String(decoding: data, as: UTF8.self))
}

// MARK: - PyMOL discovery
func pymolCandidates() -> [String] {
    var c: [String] = []
    if let s = UserDefaults.standard.string(forKey: "pymolApp") { c.append(s) }
    c += ["/Applications/PyMOL.app", home.path + "/Applications/PyMOL.app"]
    for dir in ["/Applications", home.path + "/Applications"] {
        for n in (try? fm.contentsOfDirectory(atPath: dir)) ?? [] where n.hasPrefix("PyMOL") && n.hasSuffix(".app") {
            c.append(dir + "/" + n)
        }
    }
    return c
}
func findPyMOL() -> String? {
    pymolCandidates().first { fm.isExecutableFile(atPath: $0 + "/Contents/bin/python") && fm.isExecutableFile(atPath: $0 + "/Contents/bin/pymol") }
}

// MARK: - Certificate (per-user, no sudo)
var certFile: String { certsDir.appendingPathComponent("localhost.crt").path }
var keyFile: String { certsDir.appendingPathComponent("localhost.key").path }

func certValid() -> Bool {
    fm.fileExists(atPath: certFile) && fm.fileExists(atPath: keyFile)
        && run("/usr/bin/openssl", ["x509", "-checkend", "2592000", "-noout", "-in", certFile]).status == 0 // >30 days left
}
func certTrusted() -> Bool {
    run("/usr/bin/security", ["verify-cert", "-c", certFile, "-p", "ssl", "-s", "localhost"]).status == 0
}
/// Generates (if needed) and trusts the certificate. Trusting shows the macOS password/Touch ID prompt.
func ensureCert() -> String? {
    do {
        try fm.createDirectory(at: certsDir, withIntermediateDirectories: true)
        if !certValid() {
            let r = run("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                "-keyout", keyFile, "-out", certFile, "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
                "-addext", "extendedKeyUsage=serverAuth"])
            if r.status != 0 { return "Could not create certificate: \(r.out)" }
            try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: keyFile)
        }
    } catch { return "Could not create \(certsDir.path): \(error.localizedDescription)" }
    if !certTrusted() {
        let r = run("/usr/bin/security", ["add-trusted-cert", "-r", "trustRoot", "-k", loginKeychain.path, certFile])
        if r.status != 0 { return "Certificate was not trusted (was the prompt cancelled?): \(r.out)" }
    }
    return nil
}

// MARK: - Manifests
// PowerPoint's container is protected by macOS ("access data from other apps"): a direct write is
// refused unless the user granted it, and ad-hoc signed apps often never get the prompt. Finder is
// allowed in, so we fall back to asking Finder (one-time Automation prompt) to do the copy.
let manifestNames = ["manifest.content.xml"]
let legacyManifests = ["manifest.xml"] // old task-pane add-in; removed so only "AxPyMOL Live Scene" is listed

func finderScript(_ body: String) -> (status: Int32, out: String) {
    run("/usr/bin/osascript", ["-e", "set wef to (POSIX file \"\(wefDir.path)\") as alias", "-e", body])
}
func inWef(_ name: String) -> Bool {
    if fm.fileExists(atPath: wefDir.appendingPathComponent(name).path) { return true }
    // Direct check may be blocked; ask Finder (read-only).
    return finderScript("tell application \"Finder\" to exists file \"\(name)\" of folder wef").out.contains("true")
}
func manifestsInstalled() -> Bool { manifestNames.allSatisfy(inWef) && !legacyManifests.contains(where: inWef) }
func installManifests() -> String? {
    var direct: String?
    do {
        try fm.createDirectory(at: wefDir, withIntermediateDirectories: true)
        for n in manifestNames {
            let src = resources.appendingPathComponent("addin/" + n), dst = wefDir.appendingPathComponent(n)
            if fm.fileExists(atPath: dst.path) { try fm.removeItem(at: dst) }
            try fm.copyItem(at: src, to: dst)
        }
        for n in legacyManifests { try? fm.removeItem(at: wefDir.appendingPathComponent(n)) }
        if legacyManifests.contains(where: inWef) { throw CocoaError(.fileWriteNoPermission) }
        return nil
    } catch { direct = error.localizedDescription }

    // Fallback: stage the files, then have Finder copy them in.
    let stage = fm.temporaryDirectory.appendingPathComponent("axpymol-manifests-\(getpid())")
    try? fm.removeItem(at: stage)
    do {
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        for n in manifestNames { try fm.copyItem(at: resources.appendingPathComponent("addin/" + n), to: stage.appendingPathComponent(n)) }
    } catch { return "Could not stage manifests: \(error.localizedDescription)" }
    defer { try? fm.removeItem(at: stage) }
    let files = manifestNames.map { "(POSIX file \"\(stage.appendingPathComponent($0).path)\")" }.joined(separator: ", ")
    // (Finder can't delete the legacy manifest, so it only helps on a fresh install.)
    let r = legacyManifests.contains(where: inWef) ? (status: Int32(1), out: "") : finderScript("tell application \"Finder\" to duplicate {\(files)} to folder wef with replacing")
    if r.status == 0 { return nil }

    // Finder can read PowerPoint's container but not write to it. Terminal (Apple-signed) gets the normal
    // one-time macOS permission prompt, so have it run the copy, and wait for the result.
    let cmdFile = stage.appendingPathComponent("install-addin.command")
    let cp = manifestNames.map { "\"\(stage.appendingPathComponent($0).path)\"" }.joined(separator: " ")
    let script = """
    #!/bin/bash
    echo "Installing the AxPyMOL add-in into PowerPoint…"
    if mkdir -p "\(wefDir.path)" && cp \(cp) "\(wefDir.path)/" && rm -f \(legacyManifests.map { "\"\(wefDir.path)/\($0)\"" }.joined(separator: " ")); then echo "Done. You can close this window."; else echo "Failed. If macOS asked for permission, allow it and choose Repair again."; fi
    sleep 1
    """
    do {
        try script.write(to: cmdFile, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: cmdFile.path)
    } catch { return "Could not prepare installer: \(error.localizedDescription)" }
    run("/usr/bin/open", ["-a", "Terminal", cmdFile.path])
    for _ in 0..<90 { // up to ~90 s for the user to approve the macOS prompt
        Thread.sleep(forTimeInterval: 1)
        if manifestsInstalled() { return nil }
    }
    return "Could not install the add-in into PowerPoint (\(direct ?? "")). Allow Terminal to access data from other apps if macOS asks, then choose Repair again. " +
           "Or run: mkdir -p \"\(wefDir.path)\" && cp \"\(resources.path)/addin/manifest.content.xml\" \"\(wefDir.path)/\" && rm -f \"\(wefDir.path)/manifest.xml\""
}

func setup() -> [String] { [ensureCert(), installManifests()].compactMap { $0 } }

// MARK: - Bridge process
final class TrustLocal: NSObject, URLSessionDelegate {
    func urlSession(_ s: URLSession, didReceive c: URLAuthenticationChallenge,
                    completionHandler h: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if c.protectionSpace.host == "localhost", let t = c.protectionSpace.serverTrust { h(.useCredential, URLCredential(trust: t)) }
        else { h(.performDefaultHandling, nil) }
    }
}
let healthSession = URLSession(configuration: .ephemeral, delegate: TrustLocal(), delegateQueue: nil)
func healthy(_ done: @escaping (Bool) -> Void) {
    var r = URLRequest(url: URL(string: "https://localhost:\(port)/health")!); r.timeoutInterval = 2
    healthSession.dataTask(with: r) { d, resp, _ in done((resp as? HTTPURLResponse)?.statusCode == 200) }.resume()
}

enum State { case starting, running, external, noPyMOL, notSetUp, failing }

final class Bridge {
    var proc: Process?
    var quitting = false
    var restarts = 0
    var state: State = .starting { didSet { onChange?() } }
    var onChange: (() -> Void)?

    func start() {
        guard proc == nil else { return }
        guard let pymol = findPyMOL() else { state = .noPyMOL; return }
        guard certValid() else { state = .notSetUp; return }
        let bridgePy = resources.appendingPathComponent("bridge/axpymol_bridge.py").path
        try? fm.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let log = logsDir.appendingPathComponent("bridge.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil) }
        guard let fh = try? FileHandle(forWritingTo: log) else { state = .failing; return }
        fh.seekToEndOfFile()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: pymol + "/Contents/bin/python")
        p.arguments = [bridgePy]
        var env = ProcessInfo.processInfo.environment
        env["PYMOL_APP"] = pymol; env["AXPYMOL_PORT"] = String(port)
        p.environment = env
        p.standardOutput = fh; p.standardError = fh
        p.terminationHandler = { [weak self] _ in DispatchQueue.main.async { self?.exited() } }
        do { try p.run(); proc = p; state = .starting } catch { state = .failing }
    }
    func exited() {
        proc = nil
        if quitting { return }
        restarts += 1; state = .failing
        DispatchQueue.main.asyncAfter(deadline: .now() + min(30, pow(2, Double(restarts)))) { [weak self] in self?.start() }
    }
    func stop() { quitting = true; proc?.terminate() }
    func restart() { restarts = 0; if let p = proc { p.terminate() } else { start() } }

    /// Called every few seconds: reconcile reality (our process, or someone else's bridge) with the state.
    func poll() {
        healthy { [weak self] ok in DispatchQueue.main.async {
            guard let self = self else { return }
            if ok { self.restarts = 0; self.state = self.proc == nil ? .external : .running }
            else if self.proc == nil && self.state == .external { self.start() }
            else if self.proc == nil && (self.state == .noPyMOL || self.state == .notSetUp) { self.start() }
        } }
    }
}

// MARK: - Menu-bar UI
final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let bridge = Bridge()
    var statusLine = NSMenuItem(title: "", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ n: Notification) {
        if let b = item.button {
            b.image = NSImage(systemSymbolName: "atom", accessibilityDescription: "AxPyMOL")
            b.image?.isTemplate = true
        }
        bridge.onChange = { [weak self] in self?.refresh() }
        let menu = NSMenu(); menu.delegate = self; item.menu = menu
        let firstRun = !UserDefaults.standard.bool(forKey: "didFirstRun")
        if firstRun { welcome() }
        else if !certValid() || !manifestsInstalled() { _ = setup() }
        bridge.start()
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.bridge.poll() }
        bridge.poll(); refresh()
    }
    func applicationWillTerminate(_ n: Notification) { bridge.stop() }

    func refresh() {
        let (text, bad): (String, Bool) = {
            switch bridge.state {
            case .starting: return ("Starting…", false)
            case .running: return ("Running", false)
            case .external: return ("Running (started elsewhere)", false)
            case .noPyMOL: return ("PyMOL.app not found", true)
            case .notSetUp: return ("Setup incomplete: choose Repair", true)
            case .failing: return ("Bridge failed, retrying (see log)", true)
            }
        }()
        statusLine.title = "Bridge: " + text
        item.button?.title = bad ? " !" : ""
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        refresh(); menu.removeAllItems()
        statusLine.isEnabled = false; menu.addItem(statusLine)
        menu.addItem(.separator())
        if bridge.state == .noPyMOL { add(menu, "Choose PyMOL.app…", #selector(choosePyMOL)) }
        add(menu, "Install / Repair PowerPoint Add-in…", #selector(repair))
        add(menu, "Restart Bridge", #selector(restartBridge))
        add(menu, "Show Log", #selector(showLog))
        menu.addItem(.separator())
        let login = add(menu, "Start at Login", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(.separator())
        add(menu, "How to use…", #selector(howTo))
        add(menu, "Quit AxPyMOL Helper", #selector(quit))
    }
    @discardableResult func add(_ m: NSMenu, _ t: String, _ a: Selector) -> NSMenuItem {
        let i = NSMenuItem(title: t, action: a, keyEquivalent: ""); i.target = self; m.addItem(i); return i
    }

    func alert(_ title: String, _ text: String, accessory: NSView? = nil) -> NSApplication.ModalResponse {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert(); a.messageText = title; a.informativeText = text
        a.accessoryView = accessory; a.addButton(withTitle: "OK"); return a.runModal()
    }

    func welcome() {
        let box = NSButton(checkboxWithTitle: "Start AxPyMOL Helper when I log in", target: nil, action: nil)
        box.state = .on; box.sizeToFit()
        _ = alert("Welcome to AxPyMOL",
                  "This sets up PowerPoint to show live PyMOL scenes. macOS will ask for your password once to trust a local certificate, and may ask permission to write PowerPoint's add-in folder.",
                  accessory: box)
        let problems = setup()
        UserDefaults.standard.set(true, forKey: "didFirstRun")
        if box.state == .on { try? SMAppService.mainApp.register() }
        if problems.isEmpty { howTo() } else { _ = alert("Setup needs attention", problems.joined(separator: "\n\n")) }
    }
    @objc func howTo() {
        _ = alert("Using AxPyMOL in PowerPoint",
                  "1. Quit and reopen PowerPoint.\n2. Insert → Add-ins → My Add-ins → Developer Add-ins → “AxPyMOL Live Scene”.\n3. Pick or upload a .pml / .pse in the slide, then present: drag to rotate, scroll to zoom.\n\nKeep this helper running (atom icon in the menu bar). PyMOL.app must be installed.")
    }
    @objc func repair() {
        let problems = setup()
        bridge.restart()
        _ = alert(problems.isEmpty ? "Add-in installed" : "Setup needs attention",
                  problems.isEmpty ? "Restart PowerPoint to load the add-in." : problems.joined(separator: "\n\n"))
    }
    @objc func restartBridge() { bridge.restart() }
    @objc func showLog() {
        try? fm.createDirectory(at: logsDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logsDir)
    }
    @objc func toggleLogin() {
        do { if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() } }
        catch { _ = alert("Could not change login item", error.localizedDescription) }
    }
    @objc func choosePyMOL() {
        NSApp.activate(ignoringOtherApps: true)
        let p = NSOpenPanel(); p.allowedContentTypes = [.application]; p.directoryURL = URL(fileURLWithPath: "/Applications")
        p.message = "Select PyMOL.app"
        if p.runModal() == .OK, let u = p.url {
            if fm.isExecutableFile(atPath: u.path + "/Contents/bin/python") { UserDefaults.standard.set(u.path, forKey: "pymolApp"); bridge.start() }
            else { _ = alert("Not a PyMOL app", "That app doesn't contain PyMOL's Python (Contents/bin/python).") }
        }
    }
    @objc func quit() { NSApp.terminate(nil) }
}

// MARK: - Entry
let args = CommandLine.arguments
if args.contains("--check") {
    print("pymol:", findPyMOL() ?? "NOT FOUND")
    print("resources:", resources.path, fm.fileExists(atPath: resources.appendingPathComponent("bridge/axpymol_bridge.py").path) ? "(bridge present)" : "(bridge MISSING)")
    print("cert valid:", certValid(), " trusted:", fm.fileExists(atPath: certFile) ? certTrusted() : false)
    print("manifests installed:", manifestsInstalled())
    exit(0)
}
if args.contains("--setup") { let p = setup(); p.forEach { print($0) }; exit(p.isEmpty ? 0 : 1) }

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
