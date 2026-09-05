import Foundation
import LiveCallerIDLookup

/**
 * Live Caller ID Lookup 扩展（iOS 18.2+）。
 *
 * 能力：在来电界面展示号码身份标签（如"疑似骚扰"），用户仍需手动拒接，
 * iOS 不允许第三方静默挂断。
 *
 * ⚠️ 重大前提：本扩展依赖 PIR（Private Information Retrieval）协议。
 * 系统会把号码哈希以同态加密的形式交给扩展，扩展需要与**支持 PIR 的专用服务端**
 * 通信才能在不泄露查询内容的前提下拿到结果。这不是普通 REST 接口能实现的，
 * 服务端需要：
 *   1) 部署 Apple 开源的 swift-homomorphic-encryption 方案；
 *   2) 维护 PIR 数据库并按 PIR 协议响应查询；
 *   3) 承担显著的计算与带宽成本。
 *
 * 因此，本文件提供的是**客户端骨架**：本地规则能命中时直接返回标签，
 * 需要走 PIR 的部分已标注 TODO，需配合 PIR 服务端落地后才能启用。
 * 在 PIR 服务端就绪前，建议以 CallDirectoryHandler.swift 的
 * 本地黑名单拦截 + 身份识别作为 iOS 端 MVP。
 */
final class LiveCallerIDLookupHandler: LiveCallerIDLookupExtension {

    private let store = RuleStore.shared

    override func beginRequest(with context: LiveCallerIDLookupContext) {
        context.prepareLookup { [weak self] lookup in
            guard let self else {
                context.completeLookup(with: nil)
                return
            }
            self.handle(lookup: lookup, context: context)
        }
    }

    private func handle(lookup: LiveCallerIDLookupContext.Lookup, context: LiveCallerIDLookupContext) {
        // 本地库能命中时，直接返回标签，无需网络
        if let label = localLookupLabel(for: lookup) {
            context.completeLookup(with: label)
            return
        }

        // TODO: 接入 PIR 服务端。示意流程：
        //   1) let query = lookup.encryptedQuery          // 已加密的查询，服务端无法反推号码
        //   2) let response = try? await PIRClient.query(query)
        //   3) context.completeLookup(with: response?.label)
        // 在 PIR 服务端上线前，放弃本次查询，不展示标签。
        context.completeLookup(with: nil)
    }

    /// 基于本地规则库的标签查询
    private func localLookupLabel(for lookup: LiveCallerIDLookupContext.Lookup) -> String? {
        // lookup 提供的是号码哈希，与本地库直接比对
        let hashes = lookup.phoneNumberHashes

        for hashData in hashes {
            let hash = hashData.map { String(format: "%02x", $0) }.joined()
            if store.isBlacklisted(hash) {
                return "骚扰电话"
            }
            if store.isWhitelisted(hash) {
                return nil // 白名单不展示任何标签
            }
        }
        return nil
    }
}

// MARK: - 说明

/*
 Xcode 配置要点（本文件无法自动完成，需手动操作）：

 1. File → New → Target → 选择 "Live Caller ID Lookup Extension"
 2. 主 App 与两个 Extension 都加入同一个 App Group：
    group.com.survolocking.shared
 3. 主 App 的 Info.plist 添加：
    - NSContactsUsageDescription：用于建立通讯录白名单
 4. Extension 的 Info.plist 中 NSExtensionPrincipalClass 指向本类
 5. 主 App 中调用 CXCallDirectoryManager.sharedInstance.reloadExtension
    触发 Call Directory 重新加载

 注意：LiveCallerIDLookup 框架的 API 签名以 Xcode 16.2+ 的实际头文件为准，
 若 beginRequest / prepareLookup 的签名有差异，按头文件提示调整即可。
 */
