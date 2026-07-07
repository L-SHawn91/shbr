import SwiftUI

// The dropdown panel. Read-only: every value comes straight from
// `shbr menubar --json`; the only extra read is opening a memory/agent file
// the user already owns, on demand, when they tap it. Nothing here mutates.
struct ContentView: View {
    @ObservedObject var model: BrainModel

    // A simple navigation stack inside the popover. Empty == dashboard.
    // Routes carry identifiers only; detail views pull live data from the
    // current snapshot each render so they stay fresh across refreshes.
    enum Route: Equatable {
        case provider(String)
        case sessions
        case memory
        case file(String, String)   // path, display name
    }
    @State private var route: [Route] = []
    // Detail-view disclosure: preview/opt-in quotas stay collapsed until tapped.
    @State private var showAllQuotas = false

    // Default-routed models (primary != false) show; the rest collapse. Providers
    // that don't tag primary (claude/codex) leave it nil → everything is primary.
    private func splitQuotas(_ quotas: [AgentMeter.Quota])
        -> (primary: [AgentMeter.Quota], secondary: [AgentMeter.Quota]) {
        (quotas.filter { $0.primary != false }, quotas.filter { $0.primary == false })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let top = route.last {
                detailBar(top)
                detail(top)
            } else {
                dashboard
                footer
            }
        }
        .padding(16)
        .frame(width: 340)
    }

    // MARK: - Dashboard

    @ViewBuilder
    private var dashboard: some View {
        header
        if let err = model.error, model.snapshot == nil {
            errorRow(err)
        } else if let snap = model.snapshot {
            hostHero(snap.system)
            if !snap.agents.isEmpty {
                agentsSection(snap.agents)
            }
            sessionsSection(snap)
            memorySection(snap)
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
            Picker("", selection: $model.refreshSeconds) {
                Text("2s").tag(2); Text("5s").tag(5)
                Text("10s").tag(10); Text("30s").tag(30)
            }
            .pickerStyle(.segmented)
            .frame(width: 170)
            .help("Refresh interval")
            Spacer()
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
        case .file(_, let n): return n
        }
    }

    @ViewBuilder
    private func detail(_ top: Route) -> some View {
        switch top {
        case .provider(let n): providerDetail(n)
        case .sessions: sessionsDetail()
        case .memory: memoryDetail()
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
                HStack(spacing: 0) {
                    GaugeRing(value: sys.cpu?.utilPct.map { $0 / 100 },
                              display: pct(sys.cpu?.utilPct), caption: "CPU",
                              sub: sys.cpu?.ncpu.map { "\($0) cores" })
                    Spacer()
                    GaugeRing(value: sys.memory?.usedPct.map { $0 / 100 },
                              display: pct(sys.memory?.usedPct), caption: "MEM",
                              sub: sys.memory.map { Fmt.bytes($0.used) })
                    Spacer()
                    GaugeRing(value: sys.temperatureC.map { min(max($0 / 100, 0), 1) },
                              display: sys.temperatureC.map { "\(Int($0.rounded()))°" } ?? "–",
                              caption: "TEMP",
                              sub: sys.temperatureC != nil ? "CPU" : nil,
                              accent: tempColor(sys.temperatureC))
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
        return HStack(spacing: 8) {
            Text(q.window ?? q.id ?? "quota")
                .font(.caption2.monospaced()).foregroundStyle(.secondary)
                .frame(width: 32, alignment: .leading)
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
            .frame(maxHeight: 420)
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
        return VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(q.id ?? q.window ?? "quota").font(.caption.weight(.medium))
                Spacer()
                Text(q.window ?? "").font(.caption2.monospaced()).foregroundStyle(.secondary)
            }
            StatBar(fraction: min(max(rem / 100, 0), 1), color: quotaColor(rem))
            HStack {
                Text("\(Int(rem.rounded()))% remaining")
                    .font(.caption2).foregroundStyle(quotaColor(rem))
                Spacer()
                if let u = used {
                    Text("\(Int(u.rounded()))% used")
                        .font(.caption2).foregroundStyle(.secondary)
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
        .frame(maxHeight: 420)
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
        .frame(maxHeight: 420)
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
        .frame(maxHeight: 440)
    }

    // MARK: - Building blocks

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

    private func tempColor(_ t: Double?) -> Color {
        guard let t = t else { return .accentColor }
        return t >= 90 ? .red : (t >= 80 ? .orange : .accentColor)
    }

    // Low remaining quota is the danger direction.
    private func quotaColor(_ remaining: Double) -> Color {
        remaining <= 10 ? .red : (remaining <= 25 ? .orange : .green)
    }
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
        guard let v = value else { return .secondary }
        return v >= 0.9 ? .red : (v >= 0.7 ? .orange : .accentColor)
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
