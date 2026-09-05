import SwiftUI
import CallKit

@main
struct SurvolockingApp: App {

    @StateObject private var viewModel = AppViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(viewModel)
                .onAppear {
                    Task { await viewModel.refreshStatus() }
                }
        }
    }
}

/// 全局状态：拦截启用状态、登录状态、通讯录授权
@MainActor
final class AppViewModel: ObservableObject {

    @Published var blockingEnabled = false
    @Published var isLoggedIn = false
    @Published var contactsAuthorized = false
    @Published var statusMessage = ""

    private let engine = DecisionEngine.shared
    private let api = APIClient.shared

    func refreshStatus() async {
        isLoggedIn = api.isLoggedIn
        contactsAuthorized = ContactsWhitelist.shared.authorizationStatus == .authorized
        blockingEnabled = await currentBlockingStatus()
    }

    /// 查询 Call Directory 扩展是否已启用
    private func currentBlockingStatus() async -> Bool {
        await withCheckedContinuation { continuation in
            CXCallDirectoryManager.sharedInstance.getEnabledStatusForExtension(
                withIdentifier: "com.survolocking.SurvolockingCallDirectory"
            ) { status, _ in
                continuation.resume(returning: status == .enabled)
            }
        }
    }

    /// 触发 Call Directory 重新加载，使最新黑名单生效
    func reloadExtension() {
        CXCallDirectoryManager.sharedInstance.reloadExtension(
            withIdentifier: "com.survolocking.SurvolockingCallDirectory"
        ) { error in
            Task { @MainActor in
                if let error {
                    self.statusMessage = "重载失败：\(error.localizedDescription)"
                } else {
                    self.statusMessage = "拦截名单已更新"
                }
            }
        }
    }

    func requestContacts() async {
        await ContactsWhitelist.shared.requestAndLoad()
        contactsAuthorized = ContactsWhitelist.shared.authorizationStatus == .authorized
    }

    /// 用户标记为骚扰：写入哈希黑名单 + CallKit 明文阻塞表
    func markSpam(rawNumber: String) {
        let hash = PhoneUtils.hash(of: rawNumber)
        RuleStore.shared.addBlacklist(hash, source: 4)
        let e164 = e164Format(rawNumber)
        RuleStore.shared.addLocalBlockingNumber(e164, label: "骚扰电话")
        reloadExtension()
        Task { try? await api.markCall(phoneHash: hash, userMark: 1) }
    }

    /// 误拦纠正：加入白名单并移出 CallKit 阻塞表
    func markSafe(rawNumber: String) {
        let hash = PhoneUtils.hash(of: rawNumber)
        RuleStore.shared.addWhitelist(hash, source: 4)
        RuleStore.shared.removeLocalBlockingNumber(e164Format(rawNumber))
        reloadExtension()
        Task { try? await api.feedback(phoneHash: hash, feedbackType: 1) }
    }

    /// 转为 E.164 无加号格式，CallKit 要求纯数字
    private func e164Format(_ raw: String) -> String {
        var n = PhoneUtils.normalize(raw).replacingOccurrences(of: "+", with: "")
        // 中国大陆号码补齐国家码
        if n.hasPrefix("1"), n.count == 11 {
            n = "86" + n
        }
        return n
    }
}
