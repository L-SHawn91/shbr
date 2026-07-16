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
            // 팝오버가 열려 화면에 보이는 동안에만 맥동을 돌린다(닫히면 정지 →
            // 재그리기 멈춰 App Nap/코어 슬립 허용). ContentView는 아래 창에도
            // 떠서 각각 신호를 쏘므로 모델이 참조 카운트로 합산한다.
            ContentView(model: model)
                .onAppear { model.panelAppeared() }
                .onDisappear { model.panelDisappeared() }
        } label: {
            // 항상 보이는 메뉴바 라인: CPU 부하로 맥동하는 두뇌 아이콘(풀컬러)
            // + 통계 텍스트. animIndex가 갱신될 때마다 라벨이 다시 그려진다.
            HStack(spacing: 4) {
                Image(nsImage: BrainFrames.images[min(max(0, model.animIndex), BrainFrames.count - 1)])
                    .renderingMode(.original)
                if !model.labelStats.isEmpty {
                    Text(model.labelStats)
                }
            }
            .onAppear { model.start() }
        }
        .menuBarExtraStyle(.window)

        // 자유롭게 옮길 수 있는 독립 창. MenuBarExtra 팝오버는 macOS가 메뉴바
        // 아이콘에 고정해 드래그로 이동할 수 없으므로, 위치 이동이 필요하면
        // 푸터의 ‘창으로 열기’로 이 창을 띄운다. contentSize로 내용 크기에 맞춘다.
        Window("SHawn Brain", id: "panel") {
            ContentView(model: model)
                .onAppear { model.panelAppeared() }
                .onDisappear { model.panelDisappeared() }
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
        guardOffDuplicateInstances()
    }

    /// 두 개 이상의 SHawnBrain 프로세스가 동시에 떠서 UserDefaults/메뉴바
    /// 슬롯 경합으로 hang이 나는 것을 막는다. 같은 번들 ID를 가진 다른
    /// running 인스턴스가 있으면 이(새) 프로세스가 종료된다.
    private func guardOffDuplicateInstances() {
        let ourBundleID = Bundle.main.bundleIdentifier ?? ""
        guard !ourBundleID.isEmpty else { return }
        let ownPID = ProcessInfo.processInfo.processIdentifier
        let others = NSWorkspace.shared.runningApplications
            .filter { $0.bundleIdentifier == ourBundleID && $0.processIdentifier != ownPID }
        if !others.isEmpty {
            // 이미 실행 중인 인스턴스가 있으므로 이 인스턴스는 종료한다.
            // 먼저 기존 인스턴스를 앞으로 끌어올려 사용자가 혼동하지 않게 한다.
            for app in others { app.activate() }
            NSApp.terminate(nil)
        }
    }
}
