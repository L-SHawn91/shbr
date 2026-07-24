import SwiftUI

// AI Usage Indicator 시각 디자인 고정화(SSOT)
// ──────────────────────────────────────────────────────────────────────────
// 색은 여기 한 곳에서만 정의한다. 뷰 코드에는 .red/.orange/.indigo 같은 리터럴
// 색을 두지 않고 Theme 토큰만 참조한다 — 세대가 바뀌면 이 파일만 고친다.
//
// 팔레트 = SHawn 생태계 고정 아이덴티티인 Nord (tmux/fzf dotfiles와 동일).
//   Polar Night  #2e3440 #3b4252 #434c5e #4c566a  (어두운 베이스)
//   Snow Storm   #d8dee9 #e5e9f0 #eceff4          (밝은 베이스)
//   Frost        #8fbcbb #88c0d0 #81a1c1 #5e81ac  (강조·브랜드)
//   Aurora       #bf616a #d08770 #ebcb8b #a3be8c #b48ead (상태 신호)
//
// 전략: "크롬"(배경·글자·테두리·머티리얼)은 시스템 시맨틱 색(Color.primary/
// .secondary/.ultraThinMaterial)을 써서 밝은/어두운 모드에 자동 적응시키고,
// "강조·상태·데이터 시각화"는 아래 고정 Nord 토큰으로 두 모드에서 동일하게
// 유지한다 → 모드가 바뀌어도 브랜드 색은 고정.

// MARK: - Nord 원색 팔레트

enum Nord {
    // Polar Night
    static let n0 = Color(hex: 0x2E3440)
    static let n1 = Color(hex: 0x3B4252)
    static let n2 = Color(hex: 0x434C5E)
    static let n3 = Color(hex: 0x4C566A)
    // Snow Storm
    static let n4 = Color(hex: 0xD8DEE9)
    static let n5 = Color(hex: 0xE5E9F0)
    static let n6 = Color(hex: 0xECEFF4)
    // Frost
    static let n7 = Color(hex: 0x8FBCBB)   // 청록
    static let n8 = Color(hex: 0x88C0D0)   // 밝은 하늘
    static let n9 = Color(hex: 0x81A1C1)   // 차분한 파랑
    static let n10 = Color(hex: 0x5E81AC)  // 딥 블루
    // Aurora
    static let n11 = Color(hex: 0xBF616A)  // 빨강
    static let n12 = Color(hex: 0xD08770)  // 주황
    static let n13 = Color(hex: 0xEBCB8B)  // 노랑
    static let n14 = Color(hex: 0xA3BE8C)  // 초록
    static let n15 = Color(hex: 0xB48EAD)  // 보라

    static let allSwatches: [(name: String, color: Color)] = [
        ("frost 청록", n7), ("frost 하늘", n8), ("frost 파랑", n9), ("frost 딥", n10),
        ("빨강", n11), ("주황", n12), ("노랑", n13), ("초록", n14), ("보라", n15),
    ]
}

// MARK: - 시맨틱 토큰 (뷰는 이것만 참조)

enum Theme {
    // 강조·브랜드
    static let accent = Nord.n8    // 기본 강조(게이지·바·선택)
    static let brand  = Nord.n10   // 브랜드 딥 블루
    static let teal   = Nord.n7
    static let blue   = Nord.n9
    static let purple = Nord.n15

    // 상태 신호 (낮은 잔여/높은 사용 = 위험 방향)
    static let ok   = Nord.n14   // 정상·여유
    static let warn = Nord.n13   // 주의
    static let hot  = Nord.n12   // 과열
    static let crit = Nord.n11   // 위험

    // 고정 형태 값
    static let cardRadius: CGFloat = 12
    static let controlRadius: CGFloat = 8

    // 온도: 90°↑ 위험 · 80°↑ 과열 · 그 외 강조
    static func temp(_ t: Double?) -> Color {
        guard let t = t else { return accent }
        return t >= 90 ? crit : (t >= 80 ? hot : accent)
    }

    // 잔여 할당량: 10%↓ 위험 · 25%↓ 주의 · 그 외 정상
    static func quota(_ remaining: Double) -> Color {
        remaining <= 10 ? crit : (remaining <= 25 ? warn : ok)
    }

    // 사용 비율(0…1): 0.9↑ 위험 · 0.7↑ 과열 · 그 외 강조
    static func gauge(_ v: Double?) -> Color {
        guard let v = v else { return .secondary }
        return v >= 0.9 ? crit : (v >= 0.7 ? hot : accent)
    }
}

// MARK: - 밝은/어두운 모드

enum Appearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: "시스템"
        case .light:  "밝게"
        case .dark:   "어둡게"
        }
    }

    var icon: String {
        switch self {
        case .system: "circle.lefthalf.filled"
        case .light:  "sun.max.fill"
        case .dark:   "moon.fill"
        }
    }

    // nil = 시스템 설정을 따름.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light:  .light
        case .dark:   .dark
        }
    }
}

// MARK: - 유틸

extension Color {
    // 0xRRGGBB 정수 리터럴로 색 생성. 고정 팔레트를 코드에 그대로 박기 위함.
    init(hex: UInt, alpha: Double = 1) {
        self.init(.sRGB,
                  red:   Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue:  Double(hex & 0xFF) / 255,
                  opacity: alpha)
    }
}
