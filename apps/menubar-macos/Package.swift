// swift-tools-version:5.9
import PackageDescription

// SHawn Brain — the native macOS menu-bar app.
//
// This is the *product* frontend: a self-contained menu-bar-resident app that
// renders the read-only `shbr` core in the menu bar. It does NOT depend on
// SwiftBar or any host — it draws its own menu-bar item via SwiftUI's
// MenuBarExtra.
//
// Phase A (now): shells out to an installed `shbr menubar --json`.
// Phase B (distribution): bundle a frozen `shbr` binary so the .app is a single
// download with no Python/shbr prerequisite.
let package = Package(
    name: "SHawnBrain",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "SHawnBrain",
            path: "Sources/SHawnBrain"
        )
    ]
)
