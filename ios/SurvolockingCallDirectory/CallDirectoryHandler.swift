import Foundation
import CallKit

/**
 * Call Directory Extension —— iOS 上唯一能真正拦截来电的机制。
 *
 * 生效前提：用户在「系统设置 → 电话 → 来电阻止与身份识别」中启用本 App。
 *
 * 重要限制（iOS 平台硬约束）：
 * CallKit 要求提供明文号码，而我们的隐私设计云端只存号码哈希、哈希无法反解，
 * 因此本扩展只能拦截**本机已知明文号码**（用户在本机标记过的号码）。
 * 家庭组共享的哈希规则无法直接用于 CallKit 拦截，这是隐私设计与 iOS 限制
 * 之间的固有权衡，详细方案见 README「iOS 平台限制」章节。
 */
final class CallDirectoryHandler: CXCallDirectoryProvider {

    private let store = RuleStore.shared

    override func beginRequest(with context: CXCallDirectoryExtensionContext) {
        context.delegate = self

        if context.isIncremental {
            // 增量模式：只处理变化，速度快，但需要自行维护增删记录
            // 当前未在持久化层记录增量变更，故退化为全量重建，保证正确性优先
            removeAllBlockingEntries(from: context)
            addAllBlockingPhoneNumbers(to: context)
        } else {
            addAllBlockingPhoneNumbers(to: context)
        }

        context.completeRequest()
    }

    // MARK: - 拦截名单

    private func addAllBlockingPhoneNumbers(to context: CXCallDirectoryExtensionContext) {
        let numbers = store.allLocalBlockingNumbers()
        for entry in numbers {
            // CallKit 要求号码必须严格升序添加
            context.addBlockingEntry(withNextSequentialPhoneNumber: entry.number)
        }
        print("[CallDirectory] 已写入 \(numbers.count) 条拦截号码")
    }

    /// 为号码附加身份识别标签（在不拦截时展示，如"疑似骚扰"）
    private func addAllIdentificationPhoneNumbers(to context: CXCallDirectoryExtensionContext) {
        let numbers = store.allLocalBlockingNumbers()
        for entry in numbers {
            let label = entry.label ?? "疑似骚扰"
            context.addIdentificationEntry(
                withNextSequentialPhoneNumber: entry.number,
                label: label
            )
        }
    }

    private func removeAllBlockingEntries(from context: CXCallDirectoryExtensionContext) {
        // 全量模式下 CallKit 会自动覆盖，无需显式移除
    }
}

// MARK: - CXCallDirectoryExtensionContextDelegate

extension CallDirectoryHandler: CXCallDirectoryExtensionContextDelegate {

    func requestFailed(for extensionContext: CXCallDirectoryExtensionContext, withError error: Error) {
        let nsError = error as NSError
        NSLog("[CallDirectory] 请求失败 code=%d domain=%@ info=%@",
              nsError.code, nsError.domain, nsError.userInfo)
    }
}
