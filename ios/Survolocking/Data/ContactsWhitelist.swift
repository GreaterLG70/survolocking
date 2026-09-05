import Foundation
import Contacts

/**
 * 通讯录白名单。
 *
 * 原则：宁可漏接，不可误拦。通讯录联系人拥有最高优先级。
 *
 * 首次使用时把全部联系人号码哈希载入内存 Set，查询 O(1)，
 * 保证第1层在 50ms 预算内完成。
 */
final class ContactsWhitelist {

    static let shared = ContactsWhitelist()

    private var hashes: Set<String> = []
    private var lastLoad: Date = .distantPast
    private let queue = DispatchQueue(label: "com.survolocking.contacts")

    private static let ttl: TimeInterval = 600 // 10 分钟

    private init() {}

    var authorizationStatus: CNAuthorizationStatus {
        CNContactStore.authorizationStatus(for: .contacts)
    }

    /// 请求权限并加载。需在 Info.plist 配置 NSContactsUsageDescription。
    func requestAndLoad() async {
        let store = CNContactStore()
        do {
            try await store.requestAccess(for: .contacts)
            await load()
        } catch {
            print("[ContactsWhitelist] 授权失败: \(error)")
        }
    }

    func load() async {
        guard authorizationStatus == .authorized else { return }
        await withCheckedContinuation { continuation in
            queue.async {
                let store = CNContactStore()
                var set = Set<String>()
                let keys = [CNContactPhoneNumbersKey as CNKeyDescriptor]
                let request = CNContactFetchRequest(keysToFetch: keys)
                try? store.enumerateContacts(with: request) { contact, _ in
                    for phone in contact.phoneNumbers {
                        let normalized = PhoneUtils.normalize(phone.value.stringValue)
                        if normalized.count >= 6 {
                            set.insert(PhoneUtils.hash(normalized))
                        }
                    }
                }
                self.hashes = set
                self.lastLoad = Date()
                continuation.resume()
            }
        }
    }

    func contains(_ hash: String) -> Bool {
        if Date().timeIntervalSince(lastLoad) > Self.ttl {
            Task { await load() }
        }
        return queue.sync { hashes.contains(hash) }
    }
}
