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

    enum CodingKeys: String, CodingKey {
        case cpu, memory
        case temperatureC = "temperature_c"
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
