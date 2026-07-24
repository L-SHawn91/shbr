import AppKit
import SwiftUI

// SHawn 두뇌 로고를 메뉴바 라벨에 쓰기 위한 템플릿 이미지.
//
// make-app.sh가 SwiftPM 리소스 번들을 앱에 넣지 않고 릴리스 바이너리만 복사하므로
// Bundle.module/Bundle.main으로 PNG를 찾을 수 없다. 그래서 88×88 검은 실루엣 PNG
// (953B)를 base64로 소스에 직접 임베드하고 런타임에 NSImage로 디코드한다.
// isTemplate=true로 두면 macOS가 밝은/어두운 메뉴바에 맞춰 자동 틴트한다.
enum BrainIcon {
    // shawn_digital_brain_menubar_v1_dark.png (88×88, alpha, 검은 실루엣)의 base64.
    private static let base64 = "iVBORw0KGgoAAAANSUhEUgAAAFgAAABYCAYAAABxlTA0AAAABmJLR0QA/wD/AP+gvaeTAAADbklEQVR4nO3cO4hcVRzH8c+um8T4wgR8Ij4xsAEN4gPEwkqLqKCFFkYFRbARQdDCWATBSsRGfCHYiIVoZZPKF2gnphGFjYKoIWsSNyiuRk02FofVybCbzMw95547s/8v/KodzuO7d86599xzhiAIgiAIgiAIgtYe07VbsBJuADbcT6+w24sVm3RhDCD5/AnjvfkAB6o2K6ROK12A3qYxo2SxKvwDzbiDPyEb3AWrsRh/FKnmePFFO7AW5h34hV7qszhJemfEvSxDg/hK8NJXS2f4S7dnlNa4178KI/Y/nyJm9rrSre4EO8rI7Y3x/CGNF6vGe6UJqbScnuzF1vb6FxtHpHuCNqUu5wF3Fq+i/V4Xh2xvTkijfsTxy715S7nKO4p29122aG+1P4s4tqSnW6LWfyuvtCVMoezy3W9PNP4XH2RJ8vLxXrfAo+pL/BUOYYbSgkoyenKPaHlzidlFJRhG17FPvXFDZOvpdvIi/MrycO5eFv6ytWW1SR/SOvPM3n1NONqfKu+nJz5UEfWL84zeXKXs1sHruR31BdRMk/kUzU827C0QqMmKQvSq6osTA/5+R0m/63BJml5NQvDCr4lV8Ud5+ZcBQ0r+NJcFXecy3MVNKzgv3JV3HGO5CpoGMEbpMfhtcDmGpXuUn+GbzPb82gbjPU4WKATXc5HWcwNyG2FOtHlLMkwVAw6Bs82rWgMmcKWpoUMKrj683klGvd7UMH7mlY0puxvWsCgj70XSVtIh71vHmd+wGVNCxlU2H580LSyMeO1tiuclfYW1J7d28helV7v3y/tkqktoGQO47pcwkbh0RUaNUmpKpe06F5bQqlkX8wa5a7g59yN6BDzuQscRfC8tKdgEvk4d4GjHuM6Kh06mSSO42EFruJRmJH2EdQeM3PmxayGMrAZn6ovJkfe1K1Dmf+xDk/jkPqSRskc7stupQDrpTXjnXhd9/er7ZFOiI7t2spO9SWulkO4pFzX2+Mp9Y5v9WdJWkd514TIXeYa6XRn/08T7MGDeFxzeX/jdjyD7/v+toBXpFOmE81GSfb10g9s9PKs0fe7LeLuvvKukMbXLdIkHEhC3sNv/v9Kf4EnJWEvOPGt9q/SZLpWdhpl5Ryr35NuwJkttiUIgiAIgiAIgiAIgiAIgiAIgiAIgiAIgiAIgiAIOse/0MHV+ro19pwAAAAASUVORK5CYII="

    // 16pt 정사각 템플릿 NSImage. 디코드 실패 시 nil → 호출부가 🧠로 폴백.
    static let template: NSImage? = {
        guard let data = Data(base64Encoded: base64),
              let img = NSImage(data: data) else { return nil }
        img.isTemplate = true
        img.size = NSSize(width: 16, height: 16)
        return img
    }()

    // SwiftUI에서 바로 쓰는 뷰. NSImage가 없으면 🧠 텍스트로 폴백한다.
    static var label: some View {
        Group {
            if let img = template {
                Image(nsImage: img).renderingMode(.template)
            } else {
                Text("🧠")
            }
        }
    }
}

// "AI Usage Indicator" 헤더/브랜드 자리의 🧠 이모지를 대체하는 풀컬러 두뇌 글리프.
// 앱 대표 아이콘과 동일한 100% Peak 프레임(BrainFrames.images.last)을 쓴다.
struct BrainMark: View {
    var size: CGFloat
    var body: some View {
        Image(nsImage: BrainFrames.images.last ?? NSImage())
            .resizable()
            .interpolation(.high)
            .scaledToFit()
            .frame(width: size, height: size)
    }
}

// MARK: - 숀애니메이션 (ShawnGlyph) ──────────────────────────────────────────
//
// 아이콘 한 세트(GlyphSet)를 받아 "상태별 애니메이션"을 입히는 재사용 프레임워크.
// 브레인은 그 첫 번째 글리프일 뿐 — 다른 SHawn 아이콘도 GlyphSet만 등록하면
// 똑같은 connecting/error/live 애니를 그대로 얻는다. 핵심은 애니 로직이 오직
// 글리프의 "알파(모양)"에만 의존한다는 것: 그림이 뇌든 로켓이든 상관없이 그
// 실루엣 안쪽에서 빛줄기가 돌고(connecting), 붉게 물들고(error), 맥동한다(live).
//
// 확장 지점은 둘뿐이다.
//   · 새 아이콘 →  GlyphSet.<이름> 정적 프로퍼티 한 줄 추가
//   · 새 상태  →  BrainModel.Visual 케이스 + ShawnGlyph의 switch 한 줄

// 애니에 쓸 아이콘 프레임 묶음. frames = Idle→Peak 계조(맥동용).
// 프레임이 여러 장이면 그 계조로 맥동하고, 1장뿐이면 live는 breathing(크기·투명도)
// 폴백으로 대신한다 → peak 아트가 없는 단일 이미지 아이콘도 그대로 동작한다.
struct GlyphSet {
    let frames: [NSImage]

    var idle: NSImage { frames.first ?? NSImage() }
    var hasPulse: Bool { frames.count > 1 }

    // 음수·초과 인덱스를 안전하게 감아 프레임을 고른다(ping-pong 구동부와 무관).
    func frame(at index: Int) -> NSImage {
        guard !frames.isEmpty else { return NSImage() }
        return frames[((index % frames.count) + frames.count) % frames.count]
    }

    // 등록된 숀 아이콘. 새 아이콘은 여기 한 줄만 추가하면 전 애니가 따라온다.
    static let brain = GlyphSet(frames: BrainFrames.images)
}

// connecting: 글리프 실루엣 "안에서" 밝은 빛줄기가 빙글빙글 도는 로딩 스핀.
// angular gradient 한 줄기를 글리프 알파로 마스킹해 모양 안쪽만 돌게 한다.
struct ShawnGlyphConnecting: View {
    var glyph: GlyphSet
    var size: CGFloat
    @State private var angle: Double = 0
    var body: some View {
        let mark = Image(nsImage: glyph.idle)
            .resizable()
            .interpolation(.high)
            .scaledToFit()
        return mark
            .overlay(
                AngularGradient(
                    gradient: Gradient(stops: [
                        .init(color: .clear, location: 0.00),
                        .init(color: Color.white.opacity(0.95), location: 0.14),
                        .init(color: .clear, location: 0.34),
                        .init(color: .clear, location: 1.00),
                    ]),
                    center: .center
                )
                .rotationEffect(.degrees(angle))
                .mask(mark)
            )
            .frame(width: size, height: size)
            .onAppear {
                withAnimation(.linear(duration: 1.1).repeatForever(autoreverses: false)) {
                    angle = 360
                }
            }
    }
}

// error: 글리프 안쪽으로 붉은 빛이 은은하게 차올랐다 빠지는 경고 맥동(0.9초 왕복).
// 스핀처럼 급하게 돌지 않아 "무언가 잘못됐고 재시도 대기 중"이라는 느낌을 준다.
struct ShawnGlyphError: View {
    var glyph: GlyphSet
    var size: CGFloat
    @State private var glow: Double = 0
    var body: some View {
        let mark = Image(nsImage: glyph.idle)
            .resizable()
            .interpolation(.high)
            .scaledToFit()
        return mark
            .overlay(
                Color.red
                    .opacity(0.16 + 0.5 * glow)
                    .blendMode(.plusLighter)
                    .mask(mark)
            )
            .frame(width: size, height: size)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                    glow = 1
                }
            }
    }
}

// live: 정상 상태의 "살아있는" 맥동. peak 프레임이 있으면 Idle→Peak 계조를
// index로 구동하고(부모가 넘긴 animIndex = CPU 부하 반영), 계조가 없으면
// 크기·투명도로 숨쉬는 breathing 폴백을 쓴다. 그래서 아무 아이콘이나 살아난다.
struct ShawnGlyphPulse: View {
    var glyph: GlyphSet
    var index: Int
    var size: CGFloat
    @State private var breathe: Double = 0
    var body: some View {
        Group {
            if glyph.hasPulse {
                Image(nsImage: glyph.frame(at: index))
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
            } else {
                Image(nsImage: glyph.idle)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
                    .scaleEffect(0.92 + 0.08 * breathe)
                    .opacity(0.78 + 0.22 * breathe)
                    .onAppear {
                        withAnimation(.easeInOut(duration: 1.3).repeatForever(autoreverses: true)) {
                            breathe = 1
                        }
                    }
            }
        }
        .frame(width: size, height: size)
    }
}

// 상태 스위처: 어떤 글리프든 시각 상태(BrainModel.Visual)에 맞는 애니를 고른다.
// 상태 계산은 BrainModel.visual이 전담하고, 여기선 그 결과만 그림에 매핑한다.
//   connecting → 흰 sweep 스핀   error → 붉은 경고 맥동   live → 계조/breathing 맥동
struct ShawnGlyph: View {
    var glyph: GlyphSet
    var visual: BrainModel.Visual
    var index: Int
    var size: CGFloat
    var body: some View {
        switch visual {
        case .connecting:
            ShawnGlyphConnecting(glyph: glyph, size: size)
        case .error:
            ShawnGlyphError(glyph: glyph, size: size)
        case .live:
            ShawnGlyphPulse(glyph: glyph, index: index, size: size)
        }
    }
}

// 메뉴바 라벨과 동일한 맥동 애니메이션을 쓰는 풀컬러 두뇌 글리프.
// 부모 뷰가 animIndex를 넘겨주면 CPU 부하에 맞춰 프레임이 바뀐다.
// (숀애니메이션 ShawnGlyphPulse의 브레인 전용 얇은 래퍼 — 기존 호출부 호환용.)
struct BrainMarkAnimated: View {
    var index: Int
    var size: CGFloat
    var body: some View {
        ShawnGlyphPulse(glyph: .brain, index: index, size: size)
    }
}

// 앱의 두뇌 글리프 표시 진입점. model.visual 하나만 보고 숀애니메이션이 애니를
// 갈아끼운다. 헤더·환경설정 레일·정보 탭이 모두 이 한 뷰를 쓴다.
struct BrainMarkLive: View {
    @ObservedObject var model: BrainModel
    var size: CGFloat
    var body: some View {
        ShawnGlyph(glyph: .brain, visual: model.visual, index: model.animIndex, size: size)
    }
}
