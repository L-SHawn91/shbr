import Foundation

// Codable mirror of `shbr menubar --json`. Only the fields the menu-bar app
// renders are typed; anything else in the payload is ignored. The shape is the
// contract in engine.menubar_data() — keep the two in sync.

struct Snapshot: Decodable {
    var glance: Glance
    var system: SystemMeter?
    var agents: [AgentMeter]
    var sessions: [Session]
    var memory: [String: MemoryStore]?
    var sessionCount: Int
    var activeCount: Int

    enum CodingKeys: String, CodingKey {
        case glance, system, agents, sessions, memory
        case sessionCount = "session_count"
        case activeCount = "active_count"
    }
}

// Persistent-memory metadata for one source (e.g. "claude", "hermes").
// Metadata only — file *content* is read on demand by the frontend from the
// user's own local files, never carried in the JSON payload.
struct MemoryStore: Decodable {
    var files: Int
    var bytes: Double
    var items: [Item]?

    struct Item: Decodable, Identifiable {
        var path: String
        var name: String
        var size: Double
        var mtime: Double
        var id: String { path }
    }
}

struct Glance: Decodable {
    var cpuPct: Double?
    var tempC: Double?
    var memPct: Double?
    var alert: String?   // "crit" | "warn" | nil

    enum CodingKeys: String, CodingKey {
        case cpuPct = "cpu_pct"
        case tempC = "temp_c"
        case memPct = "mem_pct"
        case alert
    }
}

struct SystemMeter: Decodable {
    var cpu: CPU?
    var memory: Memory?
    var temperatureC: Double?
    var processes: [Process]?

    enum CodingKeys: String, CodingKey {
        case cpu, memory, processes
        case temperatureC = "temperature_c"
    }

    // One running process — metadata only (executable name, never argv).
    struct Process: Decodable, Identifiable {
        var pid: Int
        var name: String
        var cpuPct: Double?
        var rss: Double?     // resident memory, bytes
        var id: Int { pid }

        enum CodingKeys: String, CodingKey {
            case pid, name, rss
            case cpuPct = "cpu_pct"
        }
    }

    struct CPU: Decodable {
        var ncpu: Int?
        var load1: Double?
        var load5: Double?
        var load15: Double?
        var utilPct: Double?

        enum CodingKeys: String, CodingKey {
            case ncpu, load1, load5, load15
            case utilPct = "util_pct"
        }
    }

    struct Memory: Decodable {
        var total: Double?
        var used: Double?
        var available: Double?
        var usedPct: Double?

        enum CodingKeys: String, CodingKey {
            case total, used, available
            case usedPct = "used_pct"
        }
    }
}

// One agent-usage meter. The native `usage` source emits kind="providers" with
// a providers map; an aggregate source (e.g. hermes) emits kind="aggregate"
// with flat totals.
struct AgentMeter: Decodable {
    var kind: String
    var source: String?
    var providers: [String: Provider]?
    // aggregate fields (present when kind == "aggregate")
    var sessions: Int?
    var today: Double?
    var week: Double?
    var actualCostUsd: Double?

    enum CodingKeys: String, CodingKey {
        case kind, source, providers, sessions, today, week
        case actualCostUsd = "actual_cost_usd"
    }

    struct Provider: Decodable {
        var status: String?
        var today: Double?
        var week: Double?
        var month: Double?
        var all: Double?
        var quotas: [Quota]?
        var plan: String?
    }

    struct Quota: Decodable, Identifiable {
        var id: String?
        var window: String?
        var remainingPercent: Double?
        var usedPercent: Double?
        // Default-routed model → shown expanded. Preview/opt-in models arrive
        // false and are collapsed behind a disclosure. Absent (claude/codex) is
        // treated as primary so those providers show every quota as before.
        var primary: Bool?
        // Reset instant the bucket refills, normalized to a Unix epoch. The
        // wire type varies by connector: gemini emits an ISO-8601 string,
        // codex a numeric epoch — decode accepts either. `tokenType` is what
        // the quota counts ("REQUESTS", "TOKENS"); gemini exposes it, others omit.
        var resetsAtEpoch: Double?
        var tokenType: String?

        enum CodingKeys: String, CodingKey {
            case id, window, remainingPercent, usedPercent, primary
            case resetsAt = "resets_at"
            case tokenType
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decodeIfPresent(String.self, forKey: .id)
            window = try c.decodeIfPresent(String.self, forKey: .window)
            remainingPercent = try c.decodeIfPresent(Double.self, forKey: .remainingPercent)
            usedPercent = try c.decodeIfPresent(Double.self, forKey: .usedPercent)
            primary = try c.decodeIfPresent(Bool.self, forKey: .primary)
            tokenType = try c.decodeIfPresent(String.self, forKey: .tokenType)
            // resets_at is either a numeric epoch (codex) or an ISO-8601
            // string (gemini). Normalize both to a Unix epoch.
            if let n = try? c.decodeIfPresent(Double.self, forKey: .resetsAt) {
                resetsAtEpoch = n
            } else if let s = try? c.decodeIfPresent(String.self, forKey: .resetsAt) {
                let f = ISO8601DateFormatter()
                f.formatOptions = [.withInternetDateTime]
                resetsAtEpoch = f.date(from: s)?.timeIntervalSince1970
            }
        }
    }
}

struct Session: Decodable {
    var active: Bool
    var source: String?
    var model: String?
    var tokens: Double?
    var cwd: String?
    var startedAt: Double?

    enum CodingKeys: String, CodingKey {
        case active, source, model, tokens, cwd
        case startedAt = "started_at"
    }
}

// `shbr providers --json` 페이로드 — 설정의 "모델" 탭 전용이며 menubar 스냅샷과는
// 별개 shape이다. cli._provider_rows()가 내보내는 계약과 동기화 유지.
struct ProvidersPayload: Decodable {
    var providers: [ProviderRow]
}

// 알려진 제공자 한 줄: 표시 이름 + 어느 레이어(usage/connector)에서 오는지 +
// 커넥터 신뢰 tier(documented/experimental/local) + 로컬/네트워크 활성 상태 + 숨김 여부.
struct ProviderRow: Decodable, Identifiable {
    var name: String
    var layers: [String]
    var tier: String
    var enabled: Bool
    var localEnabled: Bool?
    var connectorEnabled: Bool?
    var hosts: [String]?
    var hidden: Bool
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, layers, tier, enabled, hosts, hidden
        case localEnabled = "local_enabled"
        case connectorEnabled = "connector_enabled"
    }
}
