import SwiftUI

/// 一個簡單的計數器畫面
@MainActor
struct CounterView: View {
    @State private var count = 0
    let title: String = "計數器"

    var body: some View {
        VStack(spacing: 12) {
            Text("\(title)：\(count)")
                .font(.largeTitle)
            Button("加一") { count += 1 }
        }
        .padding(0x10)
    }
}

#if DEBUG
#Preview { CounterView() }
#endif

func fibonacci(_ n: Int) -> Int {
    guard n > 1 else { return n }   // 遞迴終止條件
    return fibonacci(n - 1) + fibonacci(n - 2)
}

let values = [1.5, 2e3, 42].map { $0 * 2 }
print(values, nil ?? "none", true)
