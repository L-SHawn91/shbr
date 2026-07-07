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

        // 자유롭게 옮길 수 있는 독립 창. MenuBarExtra 팝오버는 macOS가 메뉴바
        // 아이콘에 고정해 드래그로 이동할 수 없으므로, 위치 이동이 필요하면
        // 푸터의 ‘창으로 열기’로 이 창을 띄운다. contentSize로 내용 크기에 맞춘다.
        Window("SHawn Brain", id: "panel") {
            ContentView(model: model)
        }
        .windowResizability(.contentSize)

        // 환경설정 — 새로고침 주기·기본 레이아웃 등 상세 설정. `.accessory` 앱에서는
        // Settings 씬을 여는 showSettingsWindow: 셀렉터가 응답 체인에 닿지 않아
        // 안 열리므로, 이미 잘 동작하는 독립 Window로 만들고 openWindow로 띄운다.
        Window("환경설정", id: "settings") {
            SettingsView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

// Menu-bar-only app: no dock icon, no app-switcher entry.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
    }
}
