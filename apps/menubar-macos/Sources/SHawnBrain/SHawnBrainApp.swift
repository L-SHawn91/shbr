import SwiftUI

// SHawn Brain — native macOS menu-bar app.
//
// A self-contained menu-bar-resident app (no SwiftBar / no host). The menu-bar
// item shows the always-on glance; clicking opens a rich panel rendering the
// read-only `shbr` core (host resources, per-agent usage + quota, sessions).
@main
struct SHawnBrainApp: App {
    @StateObject private var model = BrainModel()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            ContentView(model: model)
        } label: {
            // The always-visible menu-bar line.
            Text(model.labelText)
                .onAppear { model.start() }
        }
        .menuBarExtraStyle(.window)
    }
}

// Menu-bar-only app: no dock icon, no app-switcher entry.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
    }
}
