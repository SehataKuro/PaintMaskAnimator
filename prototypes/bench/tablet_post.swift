// 試作の筆圧の経路を確かめるため、指定したプロセスにだけ「タブレットのペン」の事件を送る。
// 本物のペンは無いので、CGEvent の mouseEventSubtype を tabletPoint にし、筆圧を入れる。
// 使い方：swift tablet_post.swift <pid> <窓の中の x 割合> <y 割合>
import Cocoa

let args = CommandLine.arguments
guard args.count >= 4, let pid = pid_t(args[1]), let fx = Double(args[2]), let fy = Double(args[3]) else {
    print("使い方：tablet_post <pid> <fx> <fy>"); exit(2)
}
let windows = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as! [[String: Any]]
// そのプロセスの窓のうち一番大きいもの（メニューの帯などを除く）。
let area = { (w: [String: Any]) -> Double in
    let b = w[kCGWindowBounds as String] as? [String: Double] ?? [:]
    return (b["Width"] ?? 0) * (b["Height"] ?? 0)
}
guard let info = windows.filter({ ($0[kCGWindowOwnerPID as String] as? pid_t) == pid
        && ($0[kCGWindowLayer as String] as? Int) == 0 }).max(by: { area($0) < area($1) }),
      let b = info[kCGWindowBounds as String] as? [String: Double] else {
    print("窓が見つからない"); exit(1)
}
let x0 = b["X"]! + b["Width"]! * fx, y0 = b["Y"]! + b["Height"]! * fy
print("窓 \(b)、開始 (\(x0), \(y0))")
let src = CGEventSource(stateID: .privateState)
func post(_ type: CGEventType, _ x: Double, _ y: Double, _ pressure: Double) {
    guard let e = CGEvent(mouseEventSource: src, mouseType: type, mouseCursorPosition: CGPoint(x: x, y: y),
                          mouseButton: .left) else { return }
    e.setIntegerValueField(.mouseEventSubtype, value: Int64(CGEventMouseSubtype.tabletPoint.rawValue))
    e.setDoubleValueField(.mouseEventPressure, value: pressure)
    e.setDoubleValueField(.tabletEventPointPressure, value: pressure)
    e.postToPid(pid)
}
post(.leftMouseDown, x0, y0, 0.1)
usleep(20_000)
for i in 1...60 {
    let t = Double(i) / 60
    // 筆圧は 0.1 → 1.0 → 0.1
    post(.leftMouseDragged, x0 + 300 * t, y0 + 30 * sin(t * 6), 0.1 + 0.9 * sin(Double.pi * t))
    usleep(12_000)
}
post(.leftMouseUp, x0 + 300, y0, 0)
print("送った")
