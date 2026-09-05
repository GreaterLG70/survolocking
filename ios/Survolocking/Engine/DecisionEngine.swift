import Foundation
import Contacts

/**
 * iOS 端四层决策引擎。
 *
 * 与 Android 的关键差异（系统限制，非实现取舍）：
 * - iOS 无法在来电时静默挂断，只能展示标签，由用户手动拒接；
 * - 真正的拦截由 Call Directory Extension 在「系统设置 → 电话 → 来电阻止与身份识别」
 *   中启用后生效，属于号码级黑名单，不走实时决策；
 * - 因此本引擎的输出是「标签 + 建议动作」，而非直接拦截。
 *
 * 决策预算与 Android 一致：整体 200ms，云端查询单独 150ms 上限。
 */
final class DecisionEngine {

    static let shared = DecisionEngine()

    /// iOS 上可执行的动作
    enum Action {
        case allow      // 正常响铃，无标签
        case warn       // 展示"疑似骚扰"标签，需用户手动拒接
        case block      // 已被 Call Directory 黑名单拦截（本引擎仅作记录）
    }

    enum Source: Int {
        case fallback = 0
        case localBlacklist = 1
        case qiling = 2
        case microBehavior = 3
        case userMark = 4
    }

    struct Decision {
        let action: Action
        let layer: Int
        let source: Source
        let reason: String
        /// 展示在来电界面上的标签文案，nil 表示不展示
        let label: String?
    }

    private let store = RuleStore.shared
    private let api = APIClient.shared
    private let contacts = ContactsWhitelist.shared

    private init() {}

    // MARK: - 主流程

    func decide(rawNumber: String) async -> Decision {
        let normalized = PhoneUtils.normalize(rawNumber)
        let hash = PhoneUtils.hash(normalized)

        // 第1层：本地硬规则
        if let d = layer1(hash: hash, normalized: normalized) {
            return d
        }

        // 第2层：云端查询，150ms 预算，超时降级
        if await api.isLoggedIn {
            let risk = await withTimeout(seconds: 0.15) {
                try? await self.api.queryRisk(phone: normalized)
            }
            if let risk, risk.action == "block" {
                return Decision(
                    action: .block, layer: 2, source: .qiling,
                    reason: "云端判定为诈骗号码", label: "诈骗电话"
                )
            }
        }

        // 第3层：本地微行为
        if let d = layer3(hash: hash) {
            return d
        }

        // 第4层：兜底放行
        return Decision(
            action: .allow, layer: 4, source: .fallback,
            reason: "未命中规则", label: nil
        )
    }

    // MARK: - 第1层

    private func layer1(hash: String, normalized: String) -> Decision? {
        // 通讯录白名单绝对优先，任何规则不可覆盖
        if contacts.contains(hash) {
            return Decision(
                action: .allow, layer: 1, source: .localBlacklist,
                reason: "通讯录联系人", label: nil
            )
        }
        if store.isWhitelisted(hash) {
            return Decision(
                action: .allow, layer: 1, source: .userMark,
                reason: "用户白名单", label: nil
            )
        }
        if store.isBlacklisted(hash) {
            return Decision(
                action: .block, layer: 1, source: .localBlacklist,
                reason: "黑名单号码", label: "已拦截"
            )
        }
        if PhoneUtils.isSuspiciousPrefix(normalized) {
            return Decision(
                action: .warn, layer: 1, source: .localBlacklist,
                reason: "异常号段", label: "疑似骚扰"
            )
        }
        if let gray = store.grayPrefixMatch(normalized), gray.confidence >= 0.8 {
            return Decision(
                action: .warn, layer: 1, source: .localBlacklist,
                reason: "号段规则 \(gray.prefix)", label: "疑似骚扰"
            )
        }
        return nil
    }

    // MARK: - 第3层

    private func layer3(hash: String) -> Decision? {
        let recent = store.recentCallCount(hash, windowMs: 60 * 60 * 1000)
        if recent >= 3 {
            return Decision(
                action: .allow, layer: 3, source: .microBehavior,
                reason: "1小时内第 \(recent + 1) 次来电，疑似紧急情况", label: "反复来电"
            )
        }
        let shortRings = store.shortRingCount(hash, maxSeconds: 3)
        if shortRings >= 2 {
            return Decision(
                action: .warn, layer: 3, source: .microBehavior,
                reason: "历史短响铃挂断 \(shortRings) 次", label: "疑似骚扰"
            )
        }
        return nil
    }

    // MARK: - 工具

    private func withTimeout<T: Sendable>(
        seconds: Double,
        operation: @escaping @Sendable () async -> T?
    ) async -> T? {
        await withTaskGroup(of: T?.self) { group in
            group.addTask { await operation() }
            group.addTask {
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                return nil
            }
            let first = await group.next()
            group.cancelAll()
            return first ?? nil
        }
    }
}
