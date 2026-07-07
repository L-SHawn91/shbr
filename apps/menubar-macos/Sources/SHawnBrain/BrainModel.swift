import Foundation
import SwiftUI

// Drives the app: periodically runs `shbr menubar --json`, decodes it, and
// publishes the snapshot for the menu-bar label + dropdown to render.
@MainActor
final class BrainModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var error: String?
    @Published var lastUpdated: Date?

    // Persisted refresh cadence (seconds). RunCat-tight by default.
    @AppStorage("refreshSeconds") var refreshSeconds: Int = 5 {
        didSet { restartTimer() }
    }

    // 메뉴바 라벨에 무엇을 보일지. 환경설정에서 토글하면 objectWillChange로 라벨을
    // 즉시 다시 그린다(refreshSeconds와 같은 @AppStorage+didSet 패턴). 🧠는 항상 표시.
    @AppStorage("labelShowCpu") var labelShowCpu = true { didSet { objectWillChange.send() } }
    @AppStorage("labelShowTemp") var labelShowTemp = true { didSet { objectWillChange.send() } }
    @AppStorage("labelShowMem") var labelShowMem = true { didSet { objectWillChange.send() } }
    @AppStorage("labelShowAlert") var labelShowAlert = true { didSet { objectWillChange.send() } }

    // 밝은/어두운/시스템 모드. 패널·설정 루트에 preferredColorScheme으로 반영한다.
    @AppStorage("appearance") var appearance: Appearance = .system { didSet { objectWillChange.send() } }

    private var timer: Timer?
    private var inFlight = false

    func start() {
        refresh()
        restartTimer()
    }

    private func restartTimer() {
        timer?.invalidate()
        let interval = TimeInterval(max(1, refreshSeconds))
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        guard !inFlight else { return }
        inFlight = true
        Task.detached(priority: .utility) {
            let result = Self.runShbr()
            await MainActor.run {
                self.inFlight = false
                switch result {
                case .success(let snap):
                    self.snapshot = snap
                    self.error = nil
                    self.lastUpdated = Date()
                case .failure(let msg):
                    self.error = msg
                }
            }
        }
    }

    // Menu-bar label: brain + the glance numbers, with an alert marker so the
    // signal survives the menu bar's monochrome rendering.
    var labelText: String {
        guard let g = snapshot?.glance else { return "🧠" }
        var bits: [String] = []
        if labelShowCpu, let c = g.cpuPct { bits.append("\(Int(c.rounded()))%") }
        if labelShowTemp, let t = g.tempC { bits.append("\(Int(t.rounded()))°") }
        if labelShowMem, let m = g.memPct { bits.append("\(Int(m.rounded()))%") }
        let marker = labelShowAlert ? (g.alert == "crit" ? "🔴 " : (g.alert == "warn" ? "🟡 " : "")) : ""
        return bits.isEmpty ? "🧠" : "🧠 " + marker + bits.joined(separator: " · ")
    }

    // MARK: - Running the core

    private enum RunResult { case success(Snapshot); case failure(String) }

    // Resolve `shbr` through a login shell so we inherit the user's PATH
    // (~/.local/bin etc.), matching how the CLI is normally invoked.
    private nonisolated static func runShbr() -> RunResult {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = ["-lc", "shbr menubar --json"]
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do {
            try proc.run()
        } catch {
            return .failure("could not launch shbr: \(error.localizedDescription)")
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        if proc.terminationStatus != 0 {
            let msg = String(data: errData, encoding: .utf8) ?? ""
            return .failure("shbr exited \(proc.terminationStatus): \(msg.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        do {
            let snap = try JSONDecoder().decode(Snapshot.self, from: data)
            return .success(snap)
        } catch {
            return .failure("could not parse shbr output: \(error)")
        }
    }
}

// MARK: - Formatting helpers (mirror shbr.util)

enum Fmt {
    static func tok(_ v: Double?) -> String {
        guard let v = v, v > 0 else { return "0" }
        if v >= 1_000_000 { return String(format: "%.1fM", v / 1_000_000) }
        if v >= 1_000 { return String(format: "%.1fk", v / 1_000) }
        return String(Int(v))
    }

    static func bytes(_ v: Double?) -> String {
        guard let v = v, v > 0 else { return "0B" }
        let units = ["B", "KB", "MB", "GB", "TB"]
        var val = v
        var i = 0
        while val >= 1024 && i < units.count - 1 { val /= 1024; i += 1 }
        return String(format: i == 0 ? "%.0f%@" : "%.1f%@", val, units[i])
    }
}
