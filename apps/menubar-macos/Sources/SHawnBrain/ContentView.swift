import SwiftUI
import ServiceManagement

// User-selectable panel layout. MenuBarExtra(.window) auto-sizes to its content
// and macOS blocks drag-to-resize, so instead of size tiers (which looked
// nearly identical) the user picks a *shape*: a tall single column, a wide
// two-column board, or a master-detail split. The choice persists across
// launches and drives width, the dashboard arrangement, and detail height.
enum PanelLayout: String, CaseIterable, Identifiable {
    case vertical    // 세로형: narrow single column, drill-down (classic dropdown)
    case horizontal  // 가로형: wide, dashboard flows in two columns
    case split       // 분리형: dashboard on the left, detail pinned on the right
    var id: String { rawValue }

    var width: CGFloat {
        switch self {
        case .vertical:   return 340
        case .horizontal: return 620
        case .split:      return 660
        }
    }

    // Max scroll height for a detail view. Wider layouts get shorter caps so the
    // window stays balanced rather than tall-and-skinny.
    var detailMaxHeight: CGFloat {
        switch self {
        case .vertical:   return 440
        case .horizontal: return 400
        case .split:      return 460
        }
    }

    // The dashboard fans into two columns in the wide layout; split keeps a
    // single narrow column on the left because the right pane carries detail.
    var twoColumnDashboard: Bool { self == .horizontal }

    var label: String {
        switch self {
        case .vertical:   return "세로형"
        case .horizontal: return "가로형"
        case .split:      return "분리형"
        }
    }

    var icon: String {
        switch self {
        case .vertical:   return "rectangle.portrait"
        case .horizontal: return "rectangle"
        case .split:      return "rectangle.split.2x1"
        }
    }
}

// The dropdown panel. Read-only: every value comes straight from
// `shbr menubar --json`; the only extra read is opening a memory/agent file
// the user already owns, on demand, when they tap it. Nothing here mutates.
struct ContentView: View {
    @ObservedObject var model: BrainModel
    // Opens the standalone, movable window (see SHawnBrainApp). The menu-bar
    // popover is pinned to its icon by macOS; this is the escape hatch.
    @Environment(\.openWindow) private var openWindow

    // A simple navigation stack inside the popover. Empty == dashboard.
    // Routes carry identifiers only; detail views pull live data from the
    // current snapshot each render so they stay fresh across refreshes.
    enum Route: Equatable {
        case provider(String)
        case sessions
        case memory
        case processes(String)      // sort key: "cpu" | "mem"
        case file(String, String)   // path, display name
    }
    @State private var route: [Route] = []
    // Detail-view disclosure: preview/opt-in quotas stay collapsed until tapped.
    // Persisted so 환경설정에서 기본값(항상 펼치기)을 지정할 수 있다.
    @AppStorage("showAllQuotas") private var showAllQuotas = false

    // Panel layout. The MenuBarExtra window auto-sizes to its content and macOS
    // doesn't allow dragging its edges, so the user picks a shape instead
    // (세로/가로/분리); the choice persists across launches.
    @AppStorage("panelLayout") private var panelLayoutRaw = PanelLayout.split.rawValue
    private var panelLayout: PanelLayout { PanelLayout(rawValue: panelLayoutRaw) ?? .split }

    // 분리형에서 왼쪽 대시보드를 접어 오른쪽 상세 패널에 집중하는 상태.
    // 지속 저장 — 마지막 접힘 상태로 다시 열리고, 환경설정에서도 켤 수 있다.
    @AppStorage("sidebarCollapsed") private var sidebarCollapsed = false
    // 반대로 오른쪽 상세 패널을 접어 대시보드에 집중하는 상태. 둘 다 접히면
    // 빈 화면이 되므로 한쪽을 접으면 다른 쪽은 자동으로 펼친다(상호 배타).
    @AppStorage("detailCollapsed") private var detailCollapsed = false

    // 좌/우 접힘을 상호 배타로 토글 — 한쪽을 접으면 반대쪽은 강제로 펼친다.
    private func collapseSidebar(_ on: Bool) {
        withAnimation(.easeInOut(duration: 0.18)) {
            sidebarCollapsed = on
            if on { detailCollapsed = false }
        }
    }
    private func collapseDetail(_ on: Bool) {
        withAnimation(.easeInOut(duration: 0.18)) {
            detailCollapsed = on
            if on { sidebarCollapsed = false }
        }
    }

    // Default-routed models (primary != false) show; the rest collapse. Providers
    // that don't tag primary (claude/codex) leave it nil → everything is primary.
    private func splitQuotas(_ quotas: [AgentMeter.Quota])
        -> (primary: [AgentMeter.Quota], secondary: [AgentMeter.Quota]) {
        (quotas.filter { $0.primary != false }, quotas.filter { $0.primary == false })
    }

    var body: some View {
        Group {
            if panelLayout == .split {
                splitBody
            } else {
                stackBody
            }
        }
        .padding(16)
        .frame(width: panelLayout.width)
        .tint(Theme.accent)
        .preferredColorScheme(model.appearance.colorScheme)
    }

    // 세로형·가로형: classic drill-down — the detail view replaces the dashboard.
    private var stackBody: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let top = route.last {
                detailBar(top)
                detail(top)
            } else {
                dashboard
                footer
            }
        }
    }

    // 분리형: dashboard stays on the left, the tapped detail is pinned on the
    // right. Selecting a tile fills the right pane instead of hiding the board.
    private var splitBody: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                // 왼쪽: 대시보드 (접힘 가능)
                if sidebarCollapsed {
                    // 접힌 상태: 얇은 레일에 펼치기 버튼만. 상세 패널이 전체 폭 사용.
                    collapsedRail(icon: "sidebar.left", help: "대시보드 펼치기") {
                        collapseSidebar(false)
                    }
                    Divider()
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Spacer()
                            collapseButton(icon: "sidebar.left", help: "대시보드 접기") {
                                collapseSidebar(true)
                            }
                        }
                        ScrollView {
                            VStack(alignment: .leading, spacing: 14) { dashboard }
                        }
                    }
                    .frame(width: detailCollapsed ? nil : 300,
                           height: panelLayout.detailMaxHeight, alignment: .top)
                    .frame(maxWidth: detailCollapsed ? .infinity : nil, alignment: .leading)
                    Divider()
                }
                // 오른쪽: 상세 패널 (접힘 가능)
                if detailCollapsed {
                    // 접힌 상태: 얇은 레일에 펼치기 버튼만. 대시보드가 전체 폭 사용.
                    collapsedRail(icon: "sidebar.right", help: "상세 패널 펼치기") {
                        collapseDetail(false)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Spacer()
                            collapseButton(icon: "sidebar.right", help: "상세 패널 접기") {
                                collapseDetail(true)
                            }
                        }
                        Group {
                            if let top = route.last {
                                VStack(alignment: .leading, spacing: 12) {
                                    detailBar(top)
                                    detail(top)
                                }
                            } else {
                                VStack(spacing: 8) {
                                    Image(systemName: "hand.tap")
                                        .font(.system(size: 22)).foregroundStyle(.tertiary)
                                    Text("왼쪽 항목을 클릭하면\n여기에 자세히 표시됩니다")
                                        .font(.caption).foregroundStyle(.secondary)
                                        .multilineTextAlignment(.center)
                                }
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: panelLayout.detailMaxHeight,
                           alignment: .top)
                }
            }
            footer
        }
    }

    // 접힘 상태의 얇은 레일: 펼치기 버튼만 세로로 놓는다.
    private func collapsedRail(icon: String, help: String,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 13, weight: .semibold))
        }
        .buttonStyle(.plain).foregroundStyle(.secondary)
        .help(help)
        .frame(height: panelLayout.detailMaxHeight, alignment: .top)
    }

    // 펼침 상태의 작은 접기 버튼(패널 우상단).
    private func collapseButton(icon: String, help: String,
                               action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 11, weight: .semibold))
        }
        .buttonStyle(.plain).foregroundStyle(.tertiary)
        .help(help)
    }

    // MARK: - Dashboard

    @ViewBuilder
    private var dashboard: some View {
        header
        if let err = model.error, model.snapshot == nil {
            errorRow(err)
        } else if let snap = model.snapshot {
            if panelLayout.twoColumnDashboard {
                // 가로형: host + agents on the left, sessions + memory on the right.
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 14) {
                        hostHero(snap.system)
                        if !snap.agents.isEmpty { agentsSection(snap.agents) }
                    }
                    VStack(alignment: .leading, spacing: 14) {
                        sessionsSection(snap)
                        memorySection(snap)
                    }
                }
            } else {
                hostHero(snap.system)
                if !snap.agents.isEmpty { agentsSection(snap.agents) }
                sessionsSection(snap)
                memorySection(snap)
            }
        } else {
            HStack { ProgressView().controlSize(.small); Text("Loading…") }
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Header / footer

    private var header: some View {
        HStack(spacing: 8) {
            Text("🧠").font(.title3)
            VStack(alignment: .leading, spacing: 0) {
                Text("SHawn Brain").font(.headline)
                HStack(spacing: 4) {
                    Circle().fill(model.error == nil ? Color.green : Color.orange)
                        .frame(width: 6, height: 6)
                    if let t = model.lastUpdated {
                        Text("updated \(t, style: .time)")
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Text("connecting…").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            Button(action: { model.refresh() }) {
                Image(systemName: "arrow.clockwise").font(.system(size: 12, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("Refresh now")
        }
    }

    private var footer: some View {
        HStack {
            // 레이아웃 빠른 전환 — 자세한 설정은 환경설정 창으로 옮겼다.
            Menu {
                ForEach(PanelLayout.allCases) { opt in
                    Button(action: { panelLayoutRaw = opt.rawValue }) {
                        Label(opt.label,
                              systemImage: panelLayout == opt ? "checkmark" : opt.icon)
                    }
                }
            } label: {
                Image(systemName: panelLayout.icon)
                    .font(.system(size: 11, weight: .semibold))
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .foregroundStyle(.secondary)
            .help("창 레이아웃 (세로/가로/분리)")

            // 메뉴바 팝오버는 macOS가 아이콘에 고정 → 자유 이동 가능한 독립 창을 연다.
            Button {
                NSApp.activate(ignoringOtherApps: true)
                openWindow(id: "panel")
            } label: {
                Image(systemName: "macwindow.on.rectangle")
                    .font(.system(size: 11, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("창으로 열기 (위치 이동 가능)")

            Spacer()

            // 환경설정 — 독립 Window(id: "settings")를 openWindow로 연다.
            // (Settings 씬 + showSettingsWindow: 는 accessory 앱에서 안 열렸다.)
            Button {
                NSApp.activate(ignoringOtherApps: true)
                openWindow(id: "settings")
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 11, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("환경설정")

            Button(action: { NSApplication.shared.terminate(nil) }) {
                Text("Quit").font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
        }
    }

    // Back bar shown at the top of every detail view.
    private func detailBar(_ top: Route) -> some View {
        HStack(spacing: 8) {
            Button(action: { if !route.isEmpty { route.removeLast() } }) {
                HStack(spacing: 3) {
                    Image(systemName: "chevron.left").font(.system(size: 11, weight: .bold))
                    Text("Back").font(.caption)
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            Spacer()
            Text(detailTitle(top)).font(.subheadline.weight(.semibold))
            Spacer()
            // keep the row visually balanced with the back button
            Color.clear.frame(width: 40, height: 1)
        }
    }

    private func detailTitle(_ top: Route) -> String {
        switch top {
        case .provider(let n): return n.capitalized
        case .sessions: return "Sessions"
        case .memory: return "Memory"
        case .processes(let s): return s == "mem" ? "메모리 사용 프로세스" : "CPU 사용 프로세스"
        case .file(_, let n): return n
        }
    }

    @ViewBuilder
    private func detail(_ top: Route) -> some View {
        switch top {
        case .provider(let n): providerDetail(n)
        case .sessions: sessionsDetail()
        case .memory: memoryDetail()
        case .processes(let s): processesDetail(s)
        case .file(let p, let n): fileDetail(p, n)
        }
    }

    private func errorRow(_ msg: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("shbr unavailable", systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange).font(.subheadline)
            Text(msg).font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(.orange.opacity(0.08)))
    }

    // MARK: - Host hero (three rings)

    @ViewBuilder
    private func hostHero(_ sys: SystemMeter?) -> some View {
        card {
            if let sys = sys {
                // Each ring drills into the running-process list. CPU and TEMP
                // both open the CPU-sorted list (temperature is CPU-driven);
                // MEM opens the memory-sorted list. Disabled when there's no
                // process data so the ring doesn't look tappable for nothing.
                let hasProcs = !(sys.processes ?? []).isEmpty
                HStack(spacing: 0) {
                    ringButton(.processes("cpu"), enabled: hasProcs) {
                        GaugeRing(value: sys.cpu?.utilPct.map { $0 / 100 },
                                  display: pct(sys.cpu?.utilPct), caption: "CPU",
                                  sub: sys.cpu?.ncpu.map { "\($0) cores" })
                    }
                    Spacer()
                    ringButton(.processes("mem"), enabled: hasProcs) {
                        GaugeRing(value: sys.memory?.usedPct.map { $0 / 100 },
                                  display: pct(sys.memory?.usedPct), caption: "MEM",
                                  sub: sys.memory.map { Fmt.bytes($0.used) })
                    }
                    Spacer()
                    ringButton(.processes("cpu"), enabled: hasProcs) {
                        GaugeRing(value: sys.temperatureC.map { min(max($0 / 100, 0), 1) },
                                  display: sys.temperatureC.map { "\(Int($0.rounded()))°" } ?? "–",
                                  caption: "TEMP",
                                  sub: sys.temperatureC != nil ? "CPU" : nil,
                                  accent: tempColor(sys.temperatureC))
                    }
                }
            } else {
                Text("system source unavailable")
                    .font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: - Agents

    @ViewBuilder
    private func agentsSection(_ agents: [AgentMeter]) -> some View {
        sectionTitle("AGENTS")
        VStack(spacing: 8) {
            ForEach(Array(agents.enumerated()), id: \.offset) { _, m in
                if let providers = m.providers, !providers.isEmpty {
                    ForEach(providers.sorted(by: { providerRank($0) < providerRank($1) }), id: \.key) { name, p in
                        Button(action: { route.append(.provider(name)) }) {
                            providerCard(name, p)
                        }
                        .buttonStyle(.plain)
                    }
                } else if m.kind == "aggregate" {
                    aggregateCard(m)
                }
            }
        }
    }

    private func providerCard(_ name: String, _ p: AgentMeter.Provider) -> some View {
        let live = (p.status == "ok" || p.status == "active")
        return card(padding: 11) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 7) {
                    Circle().fill(live ? Color.green : Color.secondary.opacity(0.4))
                        .frame(width: 7, height: 7)
                    Text(name.capitalized).font(.subheadline.weight(.medium))
                    if !live, let s = p.status {
                        Text(s).font(.caption2).foregroundStyle(.secondary)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Capsule().fill(Color.secondary.opacity(0.12)))
                    }
                    Spacer()
                    if let today = p.today, today > 0 {
                        Text("\(Fmt.tok(today))").font(.caption.monospacedDigit().weight(.medium))
                        Text("today").font(.caption2).foregroundStyle(.secondary)
                    }
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
                }
                if let quotas = (p.quotas?.filter { $0.remainingPercent != nil }), !quotas.isEmpty {
                    let split = splitQuotas(quotas)
                    ForEach(Array(split.primary.enumerated()), id: \.offset) { _, q in
                        quotaRow(q)
                    }
                    if !split.secondary.isEmpty {
                        Text("＋ \(split.secondary.count)개 모델 더")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    private func quotaRow(_ q: AgentMeter.Quota) -> some View {
        let rem = q.remainingPercent ?? 0
        // Drop the redundant "gemini-" prefix so the model tag stays legible in
        // the narrow dashboard column; claude/codex windows ("5h") are untouched.
        let label = (q.window ?? q.id ?? "quota")
            .replacingOccurrences(of: "gemini-", with: "")
        return HStack(spacing: 8) {
            Text(label)
                .font(.caption2.monospaced()).foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle)
                .frame(width: 84, alignment: .leading)
            StatBar(fraction: min(max(rem / 100, 0), 1), color: quotaColor(rem))
            Text("\(Int(rem.rounded()))%")
                .font(.caption2.monospacedDigit()).foregroundStyle(quotaColor(rem))
                .frame(width: 34, alignment: .trailing)
        }
    }

    private func aggregateCard(_ m: AgentMeter) -> some View {
        card(padding: 11) {
            HStack {
                Text(m.source ?? "aggregate").font(.subheadline.weight(.medium))
                Spacer()
                if let today = m.today {
                    Text("\(Fmt.tok(today)) today")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                if let cost = m.actualCostUsd {
                    Text(String(format: "$%.2f", cost))
                        .font(.caption.monospacedDigit().weight(.medium))
                }
            }
        }
    }

    // MARK: - Sessions

    @ViewBuilder
    private func sessionsSection(_ snap: Snapshot) -> some View {
        HStack {
            sectionTitle("SESSIONS")
            Spacer()
            HStack(spacing: 4) {
                Circle().fill(Color.green).frame(width: 6, height: 6)
                Text("\(snap.activeCount) active").font(.caption2)
                Text("· \(snap.sessionCount) total").font(.caption2).foregroundStyle(.secondary)
            }
        }
        if snap.sessions.isEmpty {
            Text("no recent sessions").font(.caption).foregroundStyle(.secondary)
        } else {
            Button(action: { route.append(.sessions) }) {
                card(padding: 10) {
                    VStack(spacing: 6) {
                        ForEach(Array(snap.sessions.prefix(4).enumerated()), id: \.offset) { i, s in
                            if i > 0 { Divider().opacity(0.4) }
                            sessionRow(s)
                        }
                        if snap.sessions.count > 4 {
                            Divider().opacity(0.4)
                            HStack {
                                Text("+ \(snap.sessions.count - 4) more")
                                    .font(.caption2).foregroundStyle(.secondary)
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

    private func sessionRow(_ s: Session) -> some View {
        HStack(spacing: 7) {
            Circle().fill(s.active ? Color.green : Color.secondary.opacity(0.35))
                .frame(width: 6, height: 6)
            Text(s.source ?? "?").font(.caption.weight(.medium))
            if let model = s.model {
                Text(model).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if let tok = s.tokens, tok > 0 {
                Text(Fmt.tok(tok)).font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Memory (dashboard summary)

    @ViewBuilder
    private func memorySection(_ snap: Snapshot) -> some View {
        let stores = (snap.memory ?? [:]).sorted { $0.key < $1.key }
        if !stores.isEmpty {
            sectionTitle("MEMORY")
            Button(action: { route.append(.memory) }) {
                card(padding: 11) {
                    HStack(spacing: 12) {
                        ForEach(stores, id: \.key) { name, store in
                            HStack(spacing: 5) {
                                Image(systemName: "brain.head.profile")
                                    .font(.system(size: 11)).foregroundStyle(.secondary)
                                Text(name.capitalized).font(.caption.weight(.medium))
                                Text("\(store.files)").font(.caption.monospacedDigit())
                                Text(Fmt.bytes(store.bytes)).font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Detail: provider

    @ViewBuilder
    private func providerDetail(_ name: String) -> some View {
        if let p = liveProvider(name) {
            let live = (p.status == "ok" || p.status == "active")
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    // status
                    card(padding: 11) {
                        HStack(spacing: 7) {
                            Circle().fill(live ? Color.green : Color.secondary.opacity(0.4))
                                .frame(width: 8, height: 8)
                            Text(live ? "live" : (p.status ?? "unknown"))
                                .font(.subheadline.weight(.medium))
                            Spacer()
                        }
                    }
                    // token breakdown
                    sectionTitle("TOKENS")
                    card(padding: 11) {
                        VStack(spacing: 7) {
                            tokenRow("Today", p.today)
                            Divider().opacity(0.4)
                            tokenRow("This week", p.week)
                            Divider().opacity(0.4)
                            tokenRow("This month", p.month)
                            Divider().opacity(0.4)
                            tokenRow("All time", p.all)
                        }
                    }
                    // quotas — primary (default-routed) always shown; preview /
                    // opt-in models collapse behind a tappable disclosure.
                    let quotas = p.quotas ?? []
                    if !quotas.isEmpty {
                        let split = splitQuotas(quotas)
                        sectionTitle("QUOTAS")
                        card(padding: 11) {
                            VStack(spacing: 10) {
                                ForEach(Array(split.primary.enumerated()), id: \.offset) { i, q in
                                    if i > 0 { Divider().opacity(0.4) }
                                    quotaDetailRow(q)
                                }
                                if !split.secondary.isEmpty {
                                    Divider().opacity(0.4)
                                    Button {
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            showAllQuotas.toggle()
                                        }
                                    } label: {
                                        HStack(spacing: 6) {
                                            Image(systemName: showAllQuotas
                                                ? "chevron.down" : "chevron.right")
                                                .font(.system(size: 9, weight: .semibold))
                                            Text(showAllQuotas
                                                ? "미리보기 모델 접기"
                                                : "미리보기 모델 \(split.secondary.count)개 더 보기")
                                                .font(.caption)
                                            Spacer()
                                        }
                                        .foregroundStyle(.secondary)
                                        .contentShape(Rectangle())
                                    }
                                    .buttonStyle(.plain)
                                    if showAllQuotas {
                                        ForEach(Array(split.secondary.enumerated()), id: \.offset) { _, q in
                                            Divider().opacity(0.4)
                                            quotaDetailRow(q)
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        Text("이 제공자는 남은 할당량 정보를 노출하지 않습니다.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxHeight: panelLayout.detailMaxHeight)
        } else {
            Text("provider unavailable").font(.caption).foregroundStyle(.secondary)
        }
    }

    private func tokenRow(_ label: String, _ v: Double?) -> some View {
        HStack {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Spacer()
            Text(v != nil && v! > 0 ? Fmt.tok(v!) : "–")
                .font(.callout.monospacedDigit().weight(.medium))
        }
    }

    private func quotaDetailRow(_ q: AgentMeter.Quota) -> some View {
        let rem = q.remainingPercent ?? 0
        let used = q.usedPercent
        // gemini repeats the model name in both id and window; show it once.
        let name = q.id ?? q.window ?? "quota"
        let sub = (q.window == name) ? nil : q.window
        let reset = q.resetsAtEpoch.map { relTime($0) }
        let unit = q.tokenType.map { $0 == "REQUESTS" ? "요청" : $0.lowercased() }
        return VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(name).font(.caption.monospaced().weight(.medium))
                    .lineLimit(1).truncationMode(.middle)
                if let sub = sub {
                    Text(sub).font(.caption2.monospaced()).foregroundStyle(.secondary)
                }
                Spacer()
            }
            StatBar(fraction: min(max(rem / 100, 0), 1), color: quotaColor(rem))
            HStack(spacing: 6) {
                Text("\(Int(rem.rounded()))% 남음")
                    .font(.caption2).foregroundStyle(quotaColor(rem))
                if let u = used {
                    Text("· \(Int(u.rounded()))% 사용")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                if let reset = reset {
                    Text("\(unit.map { "\($0) · " } ?? "")리셋 \(reset)")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
    }

    // MARK: - Detail: sessions

    @ViewBuilder
    private func sessionsDetail() -> some View {
        let sessions = model.snapshot?.sessions ?? []
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if sessions.isEmpty {
                    Text("no recent sessions").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(sessions.enumerated()), id: \.offset) { _, s in
                    card(padding: 11) {
                        VStack(alignment: .leading, spacing: 5) {
                            sessionRow(s)
                            if let cwd = s.cwd {
                                Text(cwd).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                            if let started = s.startedAt {
                                Text("started \(relTime(started))")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
            }
        }
        .frame(maxHeight: panelLayout.detailMaxHeight)
    }

    // MARK: - Detail: memory

    @ViewBuilder
    private func memoryDetail() -> some View {
        let stores = (model.snapshot?.memory ?? [:]).sorted { $0.key < $1.key }
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(stores, id: \.key) { name, store in
                    sectionTitle("\(name.uppercased()) · \(store.files) files · \(Fmt.bytes(store.bytes))")
                    VStack(spacing: 6) {
                        ForEach(store.items ?? []) { item in
                            Button(action: { route.append(.file(item.path, item.name)) }) {
                                card(padding: 10) {
                                    HStack(spacing: 8) {
                                        Image(systemName: "doc.text")
                                            .font(.system(size: 11)).foregroundStyle(.secondary)
                                        VStack(alignment: .leading, spacing: 1) {
                                            Text(item.name).font(.caption.weight(.medium)).lineLimit(1)
                                            Text("\(Fmt.bytes(item.size)) · \(relTime(item.mtime))")
                                                .font(.caption2).foregroundStyle(.tertiary)
                                        }
                                        Spacer()
                                        Image(systemName: "chevron.right")
                                            .font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                // History is part of the same drill-down intent; be honest when
                // the core has no history/op events yet.
                sectionTitle("HISTORY")
                Text("아직 기록된 메모리 변경 이력이 없습니다.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxHeight: panelLayout.detailMaxHeight)
    }

    // MARK: - Detail: file content (read on demand from the user's own file)

    @ViewBuilder
    private func fileDetail(_ path: String, _ name: String) -> some View {
        let text = (try? String(contentsOfFile: path, encoding: .utf8)) ?? "(파일을 읽을 수 없습니다)"
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(path).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                Divider().opacity(0.4)
                Text(text)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxHeight: panelLayout.detailMaxHeight)
    }

    // MARK: - Detail: running processes (metadata only — executable name,
    // CPU%, and resident memory; never command-line arguments).

    @ViewBuilder
    private func processesDetail(_ sort: String) -> some View {
        let sys = model.snapshot?.system
        let procs = sys?.processes ?? []
        let byMem = sort == "mem"
        let sorted = procs.sorted {
            byMem ? ($0.rss ?? 0) > ($1.rss ?? 0)
                  : ($0.cpuPct ?? 0) > ($1.cpuPct ?? 0)
        }
        // Bar scale: memory against total RAM, CPU against all cores (n×100%).
        let memTotal = sys?.memory?.total ?? 0
        let cpuMax = Double((sys?.cpu?.ncpu ?? 1) * 100)
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                sectionTitle(byMem ? "메모리 상위 · \(sorted.count)개"
                                   : "CPU 상위 · \(sorted.count)개")
                if sorted.isEmpty {
                    Text("프로세스 정보를 읽을 수 없습니다.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 6) {
                        ForEach(sorted) { p in
                            let frac = byMem
                                ? (memTotal > 0 ? (p.rss ?? 0) / memTotal : 0)
                                : (cpuMax > 0 ? (p.cpuPct ?? 0) / cpuMax : 0)
                            card(padding: 10) {
                                VStack(alignment: .leading, spacing: 5) {
                                    HStack(spacing: 8) {
                                        Text(p.name).font(.caption.weight(.medium)).lineLimit(1)
                                        Spacer()
                                        Text(byMem ? Fmt.bytes(p.rss ?? 0)
                                                   : "\(String(format: "%.1f", p.cpuPct ?? 0))%")
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(.secondary)
                                    }
                                    StatBar(fraction: frac,
                                            color: byMem ? .accentColor
                                                         : (frac >= 0.7 ? .orange : .accentColor))
                                    // Secondary metric on the trailing line for context.
                                    Text(byMem
                                         ? "CPU \(String(format: "%.1f", p.cpuPct ?? 0))% · pid \(p.pid)"
                                         : "\(Fmt.bytes(p.rss ?? 0)) · pid \(p.pid)")
                                        .font(.caption2).foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                }
            }
        }
        .frame(maxHeight: panelLayout.detailMaxHeight)
    }

    // MARK: - Building blocks

    // Wraps a ring in a tap target that drills into a detail route. When there's
    // no data to show, it renders the ring plain (no button affordance).
    @ViewBuilder
    private func ringButton<Content: View>(_ dest: Route, enabled: Bool,
                                           @ViewBuilder _ content: () -> Content) -> some View {
        if enabled {
            Button(action: { route.append(dest) }) { content() }
                .buttonStyle(.plain)
        } else {
            content()
        }
    }

    private func card<Content: View>(padding: CGFloat = 13,
                                     @ViewBuilder _ content: () -> Content) -> some View {
        content()
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 11).fill(Color.primary.opacity(0.05)))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.primary.opacity(0.06), lineWidth: 1))
    }

    private func sectionTitle(_ s: String) -> some View {
        Text(s).font(.caption2.weight(.semibold)).tracking(0.8)
            .foregroundStyle(.secondary)
    }

    private func pct(_ v: Double?) -> String {
        guard let v = v else { return "–" }
        return "\(Int(v.rounded()))%"
    }

    private func liveProvider(_ name: String) -> AgentMeter.Provider? {
        for m in model.snapshot?.agents ?? [] {
            if let p = m.providers?[name] { return p }
        }
        return nil
    }

    private func relTime(_ epoch: Double) -> String {
        let d = Date(timeIntervalSince1970: epoch)
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: d, relativeTo: Date())
    }

    private func providerRank(_ e: (key: String, value: AgentMeter.Provider)) -> String {
        // live providers first, then alphabetical
        let live = (e.value.status == "ok" || e.value.status == "active")
        return (live ? "0" : "1") + e.key
    }

    private func tempColor(_ t: Double?) -> Color { Theme.temp(t) }

    // Low remaining quota is the danger direction.
    private func quotaColor(_ remaining: Double) -> Color { Theme.quota(remaining) }
}

// MARK: - Reusable visual components

// Circular gauge with a value in the centre and a caption below.
struct GaugeRing: View {
    let value: Double?        // 0...1, nil = unknown
    let display: String
    let caption: String
    var sub: String? = nil
    var accent: Color? = nil

    private var color: Color {
        if let a = accent { return a }
        return Theme.gauge(value)
    }

    var body: some View {
        VStack(spacing: 5) {
            ZStack {
                Circle().stroke(Color.primary.opacity(0.09), lineWidth: 6)
                if let v = value {
                    Circle()
                        .trim(from: 0, to: max(0.004, min(v, 1)))
                        .stroke(
                            AngularGradient(colors: [color.opacity(0.65), color],
                                            center: .center),
                            style: StrokeStyle(lineWidth: 6, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                        .animation(.easeOut(duration: 0.4), value: v)
                }
                Text(display)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .monospacedDigit()
            }
            .frame(width: 60, height: 60)
            VStack(spacing: 0) {
                Text(caption).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                if let sub = sub {
                    Text(sub).font(.system(size: 9)).foregroundStyle(.tertiary)
                }
            }
        }
    }
}

// Horizontal capsule bar with a gradient fill.
struct StatBar: View {
    let fraction: Double      // 0...1
    let color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.09))
                Capsule()
                    .fill(LinearGradient(colors: [color.opacity(0.6), color],
                                         startPoint: .leading, endPoint: .trailing))
                    .frame(width: max(4, geo.size.width * min(max(fraction, 0), 1)))
                    .animation(.easeOut(duration: 0.4), value: fraction)
            }
        }
        .frame(height: 6)
    }
}

// MARK: - 환경설정 (Settings) — 숀 스타일

// 좌측 브랜드 레일 + 우측 카드형 콘텐츠. 표준 TabView 대신 커스텀 내비게이션으로
// SHawn Brain 정체성(🧠·둥근 폰트·그라디언트·상태 점)을 설정 창까지 이어붙였다.
// refreshSeconds/labelShow*는 model 바인딩으로 두어 didSet이 즉시 반영된다.
struct SettingsView: View {
    @ObservedObject var model: BrainModel
    @State private var tab: SettingsTab = .general

    var body: some View {
        HStack(spacing: 0) {
            rail
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    switch tab {
                    case .general: GeneralPane(model: model)
                    case .menubar: MenuBarPane(model: model)
                    case .panel:   PanelPane()
                    case .theme:   ThemePane(model: model)
                    case .about:   AboutPane()
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color.primary.opacity(0.015))
        }
        .frame(width: 600, height: 470)
        .tint(Theme.accent)
        .preferredColorScheme(model.appearance.colorScheme)
    }

    // 좌측 레일: 브랜드 헤더 + 탭 네비 + 하단 상태 점.
    private var rail: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 3) {
                Text("🧠").font(.system(size: 30))
                Text("SHawn Brain")
                    .font(.system(size: 16, weight: .bold, design: .rounded))
                Text("로컬 관측 레이어")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14).padding(.top, 18).padding(.bottom, 16)

            VStack(spacing: 3) {
                ForEach(SettingsTab.allCases) { t in
                    NavItem(tab: t, selection: $tab)
                }
            }
            .padding(.horizontal, 8)

            Spacer()

            HStack(spacing: 5) {
                Circle().fill(model.error == nil ? Theme.ok : Theme.hot)
                    .frame(width: 6, height: 6)
                Text(model.error == nil ? "실행 중" : "재연결 중…")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16).padding(.bottom, 14)
        }
        .frame(width: 168)
        .background(
            LinearGradient(colors: [Theme.brand.opacity(0.12), Theme.accent.opacity(0.04)],
                           startPoint: .top, endPoint: .bottom)
        )
    }
}

// 설정 탭 정의 — 아이콘·제목·강조색을 한 곳에서 관리.
private enum SettingsTab: String, CaseIterable, Identifiable {
    case general, menubar, panel, theme, about
    var id: String { rawValue }
    var title: String {
        switch self {
        case .general: "일반"
        case .menubar: "메뉴바"
        case .panel:   "패널"
        case .theme:   "테마"
        case .about:   "정보"
        }
    }
    var icon: String {
        switch self {
        case .general: "gearshape.fill"
        case .menubar: "menubar.rectangle"
        case .panel:   "rectangle.split.2x1.fill"
        case .theme:   "paintpalette.fill"
        case .about:   "sparkles"
        }
    }
    var tint: Color {
        switch self {
        case .general: Theme.blue
        case .menubar: Theme.brand
        case .panel:   Theme.teal
        case .theme:   Theme.purple
        case .about:   Theme.accent
        }
    }
}

// 좌측 네비 항목 — 선택 시 강조색 배경 + 굵은 라벨.
private struct NavItem: View {
    let tab: SettingsTab
    @Binding var selection: SettingsTab

    var body: some View {
        let active = selection == tab
        Button {
            withAnimation(.easeOut(duration: 0.12)) { selection = tab }
        } label: {
            HStack(spacing: 9) {
                Image(systemName: tab.icon)
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: 18)
                    .foregroundStyle(active ? tab.tint : Color.secondary)
                Text(tab.title)
                    .font(.system(size: 13, weight: active ? .semibold : .regular))
                    .foregroundStyle(active ? Color.primary : Color.secondary)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(active ? tab.tint.opacity(0.15) : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: 설정 공용 컴포넌트

// 카드 컨테이너 — 패널의 시각 언어(둥근 모서리·연한 채움·얇은 테두리)를 재사용.
private struct Card<Content: View>: View {
    var title: String? = nil
    var icon: String? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            if let title {
                HStack(spacing: 6) {
                    if let icon {
                        Image(systemName: icon)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.tertiary)
                    }
                    Text(title)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)
                        .kerning(0.4)
                }
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.primary.opacity(0.04))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.07), lineWidth: 1)
                )
        )
    }
}

// 페이지 제목 — 큰 제목 + 부제.
private struct PaneTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 20, weight: .bold, design: .rounded))
            Text(subtitle).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.bottom, 2)
    }
}

// 스위치 + 캡션 한 행.
private struct SwitchRow: View {
    let title: String
    let caption: String
    @Binding var isOn: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Toggle(title, isOn: $isOn)
                .toggleStyle(.switch)
                .font(.system(size: 13))
            Text(caption).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: 일반

private struct GeneralPane: View {
    @ObservedObject var model: BrainModel
    private static let intervals = [2, 5, 10, 15, 30, 60]

    var body: some View {
        PaneTitle(title: "일반", subtitle: "새로고침 주기와 로그인 시 자동 실행을 설정합니다.")

        Card(title: "새로고침 주기", icon: "arrow.clockwise") {
            Picker("", selection: $model.refreshSeconds) {
                ForEach(Self.intervals, id: \.self) { Text("\($0)").tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            HStack(spacing: 5) {
                Circle().fill(model.error == nil ? Color.green : Color.orange)
                    .frame(width: 6, height: 6)
                if let t = model.lastUpdated {
                    Text("\(model.refreshSeconds)초마다 갱신 · 마지막").font(.caption)
                    Text(t, style: .time).font(.caption.monospacedDigit())
                } else {
                    Text("연결 중…").font(.caption)
                }
            }
            .foregroundStyle(.secondary)
            Text("기본값 5초. 값이 작을수록 상태를 더 자주 확인하지만 시스템 자원을 조금 더 씁니다.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }

        Card(title: "시작", icon: "power") {
            LaunchAtLoginToggle()
        }
    }
}

// 로그인 항목 등록/해제. SMAppService는 macOS 13+에서 코드서명된 번들 기준으로
// 동작한다 — 실패하면 토글을 되돌리고 사유를 조용히 표시한다(앱은 계속 정상).
private struct LaunchAtLoginToggle: View {
    @State private var enabled = SMAppService.mainApp.status == .enabled
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Toggle("로그인 시 자동 실행", isOn: Binding(
                get: { enabled },
                set: { on in
                    do {
                        if on { try SMAppService.mainApp.register() }
                        else { try SMAppService.mainApp.unregister() }
                        failure = nil
                    } catch {
                        failure = error.localizedDescription
                    }
                    enabled = SMAppService.mainApp.status == .enabled
                }
            ))
            .toggleStyle(.switch)
            .font(.system(size: 13))
            Text(failure.map { "자동 실행을 적용하지 못했습니다: \($0)" }
                 ?? "로그인할 때 SHawn Brain을 메뉴바에 자동으로 띄웁니다.")
                .font(.caption)
                .foregroundStyle(failure == nil ? Color.secondary : Color.red)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: 메뉴바

private struct MenuBarPane: View {
    @ObservedObject var model: BrainModel

    var body: some View {
        PaneTitle(title: "메뉴바", subtitle: "메뉴바 라벨에 표시할 항목을 고릅니다. 🧠 아이콘은 항상 남습니다.")

        // 실제 메뉴바 목업 위에 현재 라벨을 렌더 — 켜고 끌 때 바로 반영된다.
        Card(title: "미리보기", icon: "eye") {
            HStack(spacing: 10) {
                Spacer()
                Text(model.labelText)
                    .font(.system(size: 13, design: .rounded))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(RoundedRectangle(cornerRadius: 6)
                        .fill(Color.accentColor.opacity(0.18)))
                Image(systemName: "wifi").font(.system(size: 11))
                Image(systemName: "battery.100").font(.system(size: 11))
                Text("100%").font(.system(size: 11, design: .rounded))
            }
            .foregroundStyle(.secondary)
            .padding(.horizontal, 10)
            .frame(height: 28)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1))
        }

        Card(title: "표시할 항목", icon: "slider.horizontal.3") {
            SwitchRow(title: "CPU 사용률", caption: "예: 42%", isOn: $model.labelShowCpu)
            Divider().opacity(0.4)
            SwitchRow(title: "온도", caption: "예: 58°", isOn: $model.labelShowTemp)
            Divider().opacity(0.4)
            SwitchRow(title: "메모리", caption: "예: 71%", isOn: $model.labelShowMem)
            Divider().opacity(0.4)
            SwitchRow(title: "경고 표시",
                      caption: "임계치 초과 시 🔴 위험 · 🟡 주의 마커를 앞에 붙입니다.",
                      isOn: $model.labelShowAlert)
            Text("모두 끄면 🧠 아이콘만 남아 가장 조용합니다.")
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: 패널

private struct PanelPane: View {
    @AppStorage("panelLayout") private var panelLayoutRaw = PanelLayout.split.rawValue
    @AppStorage("sidebarCollapsed") private var sidebarCollapsed = false
    @AppStorage("showAllQuotas") private var showAllQuotas = false

    var body: some View {
        PaneTitle(title: "패널", subtitle: "패널이 열릴 때의 기본 모양과 동작을 정합니다.")

        Card(title: "기본 레이아웃", icon: "rectangle.3.group") {
            HStack(spacing: 10) {
                ForEach(PanelLayout.allCases) { opt in
                    LayoutCard(opt: opt,
                               selected: panelLayoutRaw == opt.rawValue) {
                        withAnimation(.easeOut(duration: 0.15)) {
                            panelLayoutRaw = opt.rawValue
                        }
                    }
                }
            }
            Text("메뉴바 팝오버는 macOS가 아이콘에 고정합니다. 창을 옮기려면 패널 하단의 ‘창으로 열기’를 사용하세요.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }

        Card(title: "분리형", icon: "sidebar.left") {
            SwitchRow(title: "대시보드를 접은 채로 열기",
                      caption: "분리형에서 왼쪽 대시보드를 접어 오른쪽 상세에 집중합니다. 패널의 접기 버튼과 상태를 공유합니다.",
                      isOn: $sidebarCollapsed)
        }

        Card(title: "할당량", icon: "gauge.medium") {
            SwitchRow(title: "보조 할당량까지 항상 펼치기",
                      caption: "공급자 상세에서 미리보기·옵트인 모델의 할당량도 접지 않고 표시합니다.",
                      isOn: $showAllQuotas)
        }
    }
}

// 클릭 가능한 레이아웃 썸네일 카드 — 드롭다운보다 모양을 직관적으로 보여준다.
private struct LayoutCard: View {
    let opt: PanelLayout
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 7) {
                LayoutThumbnail(opt: opt, active: selected)
                Text(opt.label)
                    .font(.system(size: 11, weight: selected ? .semibold : .regular))
                    .foregroundStyle(selected ? Color.primary : Color.secondary)
            }
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(selected ? Color.accentColor.opacity(0.14) : Color.primary.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(selected ? Color.accentColor : Color.primary.opacity(0.08),
                                  lineWidth: selected ? 1.6 : 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// 레이아웃 모양을 미니 도형으로 그린다(세로 스택 / 가로 2단 / 분리 좌우).
private struct LayoutThumbnail: View {
    let opt: PanelLayout
    let active: Bool
    private var fg: Color { active ? Color.accentColor : Color.secondary }

    var body: some View {
        RoundedRectangle(cornerRadius: 5, style: .continuous)
            .fill(Color.primary.opacity(0.09))
            .frame(width: 58, height: 40)
            .overlay(shapes.padding(5))
    }

    @ViewBuilder private var shapes: some View {
        switch opt {
        case .vertical:
            VStack(spacing: 3) {
                bar(fg, h: 9)
                bar(fg.opacity(0.55), h: nil)
                bar(fg.opacity(0.55), h: nil)
            }
        case .horizontal:
            HStack(spacing: 3) {
                bar(fg, h: nil)
                bar(fg.opacity(0.55), h: nil)
            }
        case .split:
            HStack(spacing: 3) {
                bar(fg, h: nil).frame(width: 15)
                bar(fg.opacity(0.55), h: nil)
            }
        }
    }

    private func bar(_ c: Color, h: CGFloat?) -> some View {
        RoundedRectangle(cornerRadius: 2, style: .continuous)
            .fill(c)
            .frame(maxWidth: .infinity, maxHeight: h == nil ? .infinity : nil)
            .frame(height: h)
    }
}

// MARK: 테마

private struct ThemePane: View {
    @ObservedObject var model: BrainModel

    var body: some View {
        PaneTitle(title: "테마", subtitle: "화면 밝기와 브랜드 색을 정합니다. 색 팔레트는 숀 생태계와 동일한 Nord로 고정됩니다.")

        Card(title: "화면 모드", icon: "circle.lefthalf.filled") {
            HStack(spacing: 10) {
                ForEach(Appearance.allCases) { opt in
                    AppearanceCard(opt: opt,
                                   selected: model.appearance == opt) {
                        withAnimation(.easeOut(duration: 0.15)) {
                            model.appearance = opt
                        }
                    }
                }
            }
            Text("‘시스템’은 macOS 설정을 따라 밝게/어둡게 자동 전환합니다.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }

        Card(title: "고정 팔레트 · Nord", icon: "paintpalette.fill") {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3),
                      spacing: 8) {
                ForEach(Nord.allSwatches, id: \.name) { sw in
                    HStack(spacing: 7) {
                        RoundedRectangle(cornerRadius: 5, style: .continuous)
                            .fill(sw.color)
                            .frame(width: 22, height: 22)
                            .overlay(RoundedRectangle(cornerRadius: 5, style: .continuous)
                                .strokeBorder(Color.primary.opacity(0.10), lineWidth: 1))
                        Text(sw.name)
                            .font(.caption2).foregroundStyle(.secondary)
                        Spacer(minLength: 0)
                    }
                }
            }
            Text("강조·상태·데이터 시각화 색은 두 모드에서 이 팔레트로 동일하게 유지됩니다.")
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// 화면 모드 선택 카드 — 아이콘 미리보기 + 라벨(레이아웃 카드와 같은 시각 언어).
private struct AppearanceCard: View {
    let opt: Appearance
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 7) {
                Image(systemName: opt.icon)
                    .font(.system(size: 20))
                    .foregroundStyle(selected ? Theme.accent : Color.secondary)
                    .frame(height: 26)
                Text(opt.title)
                    .font(.system(size: 11, weight: selected ? .semibold : .regular))
                    .foregroundStyle(selected ? Color.primary : Color.secondary)
            }
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(selected ? Theme.accent.opacity(0.14) : Color.primary.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(selected ? Theme.accent : Color.primary.opacity(0.08),
                                  lineWidth: selected ? 1.6 : 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: 정보

private struct AboutPane: View {
    private var version: String {
        let info = Bundle.main.infoDictionary
        let v = info?["CFBundleShortVersionString"] as? String
        let b = info?["CFBundleVersion"] as? String
        let joined = [v, b].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        return joined.isEmpty ? "dev build" : joined
    }

    var body: some View {
        PaneTitle(title: "정보", subtitle: "SHawn Brain이 무엇을, 어떻게 다루는지.")

        Card {
            HStack(spacing: 14) {
                Text("🧠").font(.system(size: 42))
                VStack(alignment: .leading, spacing: 3) {
                    Text("SHawn Brain")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                    Text("로컬 AI 에이전트 도구를 관측·집계하는 로컬 우선 레이어")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(version)
                        .font(.caption2.monospacedDigit()).foregroundStyle(.tertiary)
                }
            }
        }

        Text("읽기 전용 · 메타데이터만 · 표준 라이브러리 엔진")
            .font(.caption2).foregroundStyle(.tertiary)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.top, 2)
    }
}
