import Foundation
import SwiftUI

// Drives the app: periodically runs `shbr menubar --json`, decodes it, and
// publishes the snapshot for the menu-bar label + dropdown to render.
@MainActor
final class BrainModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var error: String?
    @Published var lastUpdated: Date?

    // 사용자가 새로고침 버튼을 누른 동안 켜지는 플래그. 켜져 있으면 "살아있는" 두뇌
    // 글리프가 연결 스핀(BrainMarkConnecting)을 보여준다. shbr 응답이 눈 깜짝할 새
    // 끝나도 스핀이 최소 한 바퀴는 돌도록 최소 표시 시간을 둔다(아래 refresh 참고).
    @Published private(set) var busy = false

    // 두뇌 글리프의 시각 상태. 모델 상태(manualHold/error/lastUpdated) 하나로 계산해
    // BrainMarkLive가 이 값만 보고 애니메이션을 갈아끼운다. 상태별로 다른 애니를
    // 붙이는 프레임워크의 단일 진입점 — 새 상태를 추가하면 여기와 BrainMarkLive만 손댄다.
    //   connecting: 첫 로드 전(데이터 없음) 또는 사용자가 수동 새로고침한 짧은 순간 → 흰 sweep 스핀
    //   error:      마지막 조회 실패, 재시도 대기 중                              → 붉은 맥동 경고
    //   live:       정상, CPU 부하에 맞춘 맥동                                    → BrainMarkAnimated
    //
    // 주의: 자동 폴링 중(background inFlight/busy)은 connecting 트리거에서 뺐다.
    // shbr 조회가 ~4.5초 걸려 5초 폴링 창을 거의 채우므로, busy를 트리거로 쓰면
    // 정상 상태에서도 스핀이 사실상 영구히 돌아버린다. 그래서 배경 새로고침은
    // 조용히 처리하고, 스핀은 진짜 첫 로드나 사용자의 명시적 수동 새로고침에만 보인다.
    enum Visual { case connecting, error, live }
    var visual: Visual {
        if lastUpdated == nil { return error != nil ? .error : .connecting }
        if manualHold { return .connecting }
        if error != nil { return .error }
        return .live
    }

    // `shbr providers --json`가 채우는 알려진 제공자 목록. 설정의 "모델" 탭에서만
    // 쓰이며 5초 폴링과 별개로 탭 진입 시(fetchProviders)와 토글 후에만 갱신한다.
    @Published var providers: [ProviderRow] = []

    // Persisted refresh cadence (seconds). 조회의 **주 트리거는 이제 배경 타이머가
    // 아니라 "패널을 여는 순간"**이다(panelAppeared→refresh). 대시보드를 볼 때만
    // 신선한 수치를 당겨오므로, 아무도 안 보는 동안 매 N초 서브프로세스를 띄우던
    // 배터리 소모(에너지 주범)를 없앤다. 이 타이머는 패널을 몇 시간 안 열어도
    // 메뉴바 숫자가 완전히 굳지 않게 하는 아주 느슨한 백업일 뿐이라 3시간(10800s).
    // 큰 tolerance(restartTimer)로 시스템이 다른 wakeup과 합쳐 처리하게 해 전용
    // wakeup·유휴 소모를 없앤다. 일반 Timer라 시스템 잠자기 중엔 아예 안 울리고,
    // 이 앱은 어떤 power assertion도 잡지 않는다 → 맥북 잠자기를 방해하지 않는다.
    @AppStorage("refreshSeconds") var refreshSeconds: Int = 10800 {
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
    private var inFlight = false { didSet { updateBusy() } }
    // 수동 클릭이 건 "최소 표시창"이 살아 있는지. 조회가 순식간에 끝나도 스핀이
    // 한 바퀴는 돌도록 이 플래그가 켜져 있는 동안 busy를 유지한다.
    private var manualHold = false

    // MARK: - 메뉴바 두뇌 맥동 애니메이션
    // 라벨 두뇌 아이콘을 ping-pong으로 맥동시킨다. 진폭은 항상 Idle↔Peak
    // 전체(0…count-1)를 왕복하고, CPU 부하는 오직 '속도'만 바꾼다.
    // 프레임 자체는 BrainFrames(Idle→Peak 계조)이고, 여기서 인덱스만 구동한다.
    @Published var animIndex = 0
    private var animTimer: Timer?
    private var animDir = 1

    // 왕복 상한은 항상 Peak(마지막 프레임). 부하와 무관하게 전 범위를 오간다.
    private var animCeil: Int { BrainFrames.count - 1 }

    // 틱 간격(초): 프레임 개수와 무관하게 한 번의 Idle→Peak 스윕 시간을 고정한다.
    // 프레임을 늘려도 템포는 그대로, 계조만 촘촘해진다.
    //
    // 속도는 CPU 부하를 5단계로 나눈 리소스 사용량 tier로 결정한다(연속 공식 대신
    // 이산 단계). 부하가 한 구간 오를 때마다 스윕이 한 칸씩 빨라져 아이콘 맥동만
    // 봐도 대략 어느 부하대인지 읽힌다. 유휴는 원래의 빠른 2.1s로 되돌렸다 — 배터리
    // 절감은 스윕 속도가 아니라 '패널을 닫으면 맥동을 아예 멈추는' 쪽(setPanel~)이
    // 담당하므로, 보이는 동안엔 굼뜰 이유가 없다.
    private var animInterval: TimeInterval {
        let cpu = min(100, max(0, snapshot?.glance.cpuPct ?? 0))
        let steps = Double(max(1, animCeil))
        let sweep: TimeInterval
        switch cpu {
        case ..<10:  sweep = 2.1   // 유휴
        case ..<30:  sweep = 1.6   // 낮음
        case ..<55:  sweep = 1.1   // 보통
        case ..<80:  sweep = 0.8   // 높음
        default:     sweep = 0.5   // 최대
        }
        return sweep / steps
    }

    func start() {
        refresh()
        restartTimer()
        // 맥동 애니는 여기서 켜지 않는다 — 팝업(패널/창)이 열려 화면에 보일 때만
        // 돈다(setPanelVisible/panelAppeared). 메뉴바 아이콘은 항상 떠 있지만
        // 아무도 안 보는 동안 매 틱 재그리기로 App Nap/코어 슬립을 방해하지 않도록.
    }

    // MARK: - 패널 표시 여부에 따른 맥동 게이팅 (배터리 절감의 핵심)
    // ContentView는 두 곳(MenuBarExtra 팝오버 + 독립 창)에서 떠서 각각
    // onAppear/onDisappear를 쏘므로, 참조 카운트로 "하나라도 보이는가"를 센다.
    // 하나라도 보이면 맥동을 돌리고, 전부 닫히면 타이머를 멈춰 아이콘을 유휴
    // 프레임에 고정한다 → 그 동안 이 앱은 재그리기를 안 해 CPU가 잠들 수 있다.
    private var visiblePanels = 0

    // 패널을 여는 순간 신선한 수치를 당겨오되, 이 창을 다시 연 지 얼마 안 됐으면
    // 서브프로세스를 또 띄우지 않도록 최소 간격을 둔다(팝오버를 연타로 여닫아도
    // shbr 프로세스 폭주 방지). 배경 타이머(3h)와 별개인 온디맨드 조회의 디바운스.
    private static let onOpenMinInterval: TimeInterval = 60

    func panelAppeared() {
        visiblePanels += 1
        if visiblePanels == 1 { startAnim() }
        // 배경 타이머는 3시간이라 평소엔 굳어 있다 — 사용자가 대시보드를 여는
        // 바로 이 순간이 신선한 데이터를 당겨오는 주 트리거다. 마지막 갱신이
        // onOpenMinInterval보다 오래됐을 때만(또는 한 번도 못 받았을 때) 조회한다.
        let stale = lastUpdated.map { Date().timeIntervalSince($0) >= Self.onOpenMinInterval } ?? true
        if stale { refresh() }
    }

    func panelDisappeared() {
        visiblePanels = max(0, visiblePanels - 1)
        if visiblePanels == 0 { stopAnim() }
    }

    private func startAnim() {
        animTimer?.invalidate()
        scheduleAnimTick()
    }

    // 맥동 정지: 타이머를 끄고 유휴 프레임(0)으로 되돌려 아이콘을 정적 상태로 둔다.
    private func stopAnim() {
        animTimer?.invalidate()
        animTimer = nil
        animIndex = 0
        animDir = 1
    }

    private func scheduleAnimTick() {
        animTimer = Timer.scheduledTimer(withTimeInterval: animInterval, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in self?.animStep() }
        }
    }

    private func animStep() {
        let ceil = animCeil
        var next = animIndex + animDir
        if next >= ceil {
            next = ceil
            animDir = -1
        } else if next <= 0 {
            next = 0
            animDir = 1
        }
        animIndex = next
        scheduleAnimTick()  // 다음 틱은 갱신된 부하 기준 간격으로 재예약
    }

    // 아주 느슨한 배경 백업 타이머(기본 3h). 큰 tolerance를 줘서 macOS가 이
    // wakeup을 다른 예약 작업과 합쳐 처리하게 한다(coalescing) → 이 앱만을 위한
    // 전용 wakeup·유휴 CPU 기상을 없앤다. 일반 Timer라 시스템 잠자기 중엔 울리지
    // 않고, 이 앱은 어떤 power assertion도 잡지 않으므로 맥북 잠자기를 방해하지
    // 않는다(신선한 조회의 주 경로는 panelAppeared의 온디맨드 refresh).
    private func restartTimer() {
        timer?.invalidate()
        let interval = TimeInterval(max(1, refreshSeconds))
        let t = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.refresh() }
        }
        t.tolerance = interval * 0.2
        timer = t
    }

    // 조회가 실제로 도는 동안(inFlight) 연결 스핀을 켠다 — 자동 5초 폴링도
    // 포함(자동에도 스핀 반영). 캐시로 조회가 빨라져 자동 스핀은 짧게 반짝인다.
    // manual=true(새로고침 버튼)면 순식간에 끝나도 최소 한 바퀴(~1.4s)는 돌도록
    // 표시창(manualHold)을 추가로 건다.
    func refresh(manual: Bool = false) {
        if manual { holdSpin() }
        // inFlight의 didSet이 스핀(busy)을 켠다. 이미 진행 중이면 중복 조회는 막되
        // 수동 클릭의 표시창은 위에서 이미 걸렸으므로 스핀은 유지된다.
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

    // busy(스핀 표시) = 조회 중이거나 수동 표시창이 살아 있을 때. inFlight/manualHold
    // 어느 쪽이 바뀌든 이 한 곳에서 재계산한다.
    private func updateBusy() { busy = inFlight || manualHold }

    // 수동 클릭이 건 최소 표시창. 조회가 즉시 끝나도 스핀이 한 바퀴(~1.4s)는
    // 돌도록 유지하고, 연타해도 마지막 클릭 기준으로 창을 늘린다(디바운스).
    private var holdToken = 0
    private func holdSpin() {
        manualHold = true
        updateBusy()
        holdToken += 1
        let mine = holdToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) { [weak self] in
            guard let self, self.holdToken == mine else { return }
            self.manualHold = false
            self.updateBusy()
        }
    }

    // Menu-bar label: brain + the glance numbers, with an alert marker so the
    // signal survives the menu bar's monochrome rendering. `labelText`는 🧠
    // 이모지를 포함한 문자열 버전(SwiftBar 등 텍스트 전용 경로·폴백용). 네이티브
    // 라벨은 두뇌 실루엣 이미지 + `labelStats`(이모지 없는 통계만) 조합으로 그린다.
    var labelText: String {
        let s = labelStats
        return s.isEmpty ? "🧠" : "🧠 " + s
    }

    // 라벨의 통계 부분만(🧠 제외). 이미지 아이콘 옆에 붙는 텍스트. 비어 있으면
    // 아이콘만 표시한다. alert 마커는 monochrome 메뉴바에서 신호가 살아남도록 유지.
    var labelStats: String {
        guard let g = snapshot?.glance else { return "" }
        var bits: [String] = []
        if labelShowCpu, let c = g.cpuPct { bits.append("\(Int(c.rounded()))%") }
        if labelShowMem, let m = g.memPct { bits.append("\(Int(m.rounded()))%") }
        if labelShowTemp, let t = g.tempC { bits.append("\(Int(t.rounded()))°") }
        if bits.isEmpty { return "" }
        let marker = labelShowAlert ? (g.alert == "crit" ? "🔴 " : (g.alert == "warn" ? "🟡 " : "")) : ""
        return marker + bits.joined(separator: " · ")
    }

    // MARK: - Providers (설정 "모델" 탭)

    // 탭 진입 시 호출. `shbr providers --json`을 읽어 목록을 채운다. 실패하면
    // 조용히 무시(빈 목록 유지) — 메뉴바 폴링과 독립이라 error 배너에 섞지 않는다.
    func fetchProviders() {
        Task.detached(priority: .utility) {
            let rows = Self.runProviders()
            if let rows = rows {
                await MainActor.run { self.providers = rows }
            }
        }
    }

    // 한 제공자를 숨김/표시로 전환. CLI가 `[providers] hidden`에 영속화한 뒤
    // 목록을 다시 읽고, 메뉴바 미터도 즉시 반영되도록 refresh()를 함께 돌린다.
    func toggleProvider(name: String, hide: Bool) {
        Task.detached(priority: .utility) {
            _ = Self.runShbrRaw(["providers", hide ? "hide" : "show", name])
            let rows = Self.runProviders()
            await MainActor.run {
                if let rows = rows { self.providers = rows }
                self.refresh()
            }
        }
    }

    // MARK: - Running the core

    private enum RunResult { case success(Snapshot); case failure(String) }

    // shbr를 매 폴링마다 로그인 셸(`zsh -lc`)로 부르면 5초마다 .zshrc·플러그인
    // 초기화 비용을 물었다(앱이 무거웠던 주범). 대신 **시작 시 한 번만** 로그인 셸로
    // shbr의 절대경로와 PATH를 알아내 캐시하고, 이후엔 바이너리를 직접 exec한다
    // (RunCat처럼 셸 없이 가볍게). 경로 해석이 실패하면 기존 로그인 셸로 폴백.
    private nonisolated static let bootstrap: (cli: String, path: String)? = {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = [
            "-lc",
            #"printf '%s\n%s' "$(command -v ai-usage-indicator || command -v shbr)" "$PATH""#,
        ]
        let out = Pipe()
        proc.standardOutput = out
        proc.standardError = Pipe()
        do { try proc.run() } catch { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0,
              let text = String(data: data, encoding: .utf8) else { return nil }
        let lines = text.components(separatedBy: "\n")
        guard lines.count >= 2 else { return nil }
        let cli = lines[0].trimmingCharacters(in: .whitespacesAndNewlines)
        let path = lines[1].trimmingCharacters(in: .whitespacesAndNewlines)
        return cli.isEmpty ? nil : (cli, path)
    }()

    // 위 캐시로 실행할 Process를 만든다. 캐시가 있으면 바이너리 직접 exec하고,
    // env에 로그인 PATH를 주입해 shbr가 하위 도구(top/ps 등)를 찾게 한다.
    // 캐시가 없으면 로그인 셸 폴백. 파이프·실행은 호출자가 붙인다.
    private nonisolated static func makeProc(_ args: [String]) -> Process {
        let proc = Process()
        if let b = bootstrap {
            proc.executableURL = URL(fileURLWithPath: b.cli)
            proc.arguments = args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = b.path
            proc.environment = env
        } else {
            proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
            proc.arguments = [
                "-lc",
                #"exec "$(command -v ai-usage-indicator || command -v shbr)" "$@""#,
                "--",
            ] + args
        }
        return proc
    }

    // 임의의 shbr 서브커맨드를 실행하고 (종료코드, stdout)만 돌려준다.
    private nonisolated static func runShbrRaw(_ args: [String]) -> (Int32, Data) {
        let proc = makeProc(args)
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do {
            try proc.run()
        } catch {
            return (-1, Data())
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        _ = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return (proc.terminationStatus, data)
    }

    // `shbr providers --json`을 실행·디코드. 실패 시 nil(목록 미변경).
    private nonisolated static func runProviders() -> [ProviderRow]? {
        let (code, data) = runShbrRaw(["providers", "--json"])
        guard code == 0 else { return nil }
        return try? JSONDecoder().decode(ProvidersPayload.self, from: data).providers
    }

    // 메인 조회: `shbr menubar --json`. bootstrap 캐시(command -v shbr + PATH)를
    // 통해 바이너리를 직접 exec한다 — 매 폴링마다 로그인 셸(zsh -lc)을 띄워
    // .zshrc·플러그인을 다시 로딩하던 지연을 없앤다. 캐시 실패 시에만 셸 폴백.
    private nonisolated static func runShbr() -> RunResult {
        let proc = makeProc(["menubar", "--json"])
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do {
            try proc.run()
        } catch {
            return .failure("could not launch AI Usage Indicator: \(error.localizedDescription)")
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        if proc.terminationStatus != 0 {
            let msg = String(data: errData, encoding: .utf8) ?? ""
            return .failure("AI Usage Indicator exited \(proc.terminationStatus): \(msg.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        do {
            let snap = try JSONDecoder().decode(Snapshot.self, from: data)
            return .success(snap)
        } catch {
            return .failure("could not parse AI Usage Indicator output: \(error)")
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

    static func number(_ v: Double) -> String {
        if v.rounded() == v { return String(Int(v)) }
        let text = String(format: "%.2f", v)
        return text.replacingOccurrences(of: #"\.?0+$"#, with: "", options: .regularExpression)
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
