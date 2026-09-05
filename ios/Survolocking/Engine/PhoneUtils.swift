import Foundation
import CryptoKit

/**
 * 号码处理。
 *
 * 端云哈希契约：必须与 Android 端 PhoneUtils、服务端 security.hash_phone 完全一致，
 * 三端统一为 HMAC-SHA256(salt, normalize(phone))。
 *
 * 盐的来源：服务端在登录 / me 时下发 PHONE_HASH_SALT，端侧缓存到 Keychain/UserDefaults
 * （salt 变量），作为哈希计算的唯一真相源；内置 "survolocking_phone_salt_v1" 仅为
 * 登录前尚未拿到服务端盐时的兜底默认值，真实部署会被覆盖。
 * 否则同一号码在 Android 上传的日志，iOS 无法匹配服务端下发的规则。
 */
enum PhoneUtils {

    /// 当前生效的盐，默认取兜底常量，登录后被服务端下发值覆盖
    private static var _salt = "survolocking_phone_salt_v1"
    static var salt: String { _salt }

    /// 用服务端下发的盐更新，空值忽略
    static func setSalt(_ salt: String) {
        if !salt.isEmpty { _salt = salt }
    }

    /// 归一化：去除空格、横线、括号，保留前导 +
    static func normalize(_ phone: String) -> String {
        let allowed = Set("0123456789+")
        return String(phone.filter { allowed.contains($0) })
    }

    static func hash(_ normalizedPhone: String) -> String {
        let key = SymmetricKey(data: Data(salt.utf8))
        let mac = HMAC<SHA256>.authenticationCode(
            for: Data(normalizedPhone.utf8), using: key
        )
        return mac.map { String(format: "%02x", $0) }.joined()
    }

    static func hash(of rawPhone: String) -> String {
        hash(normalize(rawPhone))
    }

    /// 异常号段：境外来电、虚拟运营商、高仿号
    static func isSuspiciousPrefix(_ normalized: String) -> Bool {
        var n = normalized
        if n.hasPrefix("+") { n.removeFirst() }
        let prefixes = [
            "00852", "00853", "00886",           // 中国香港 / 中国澳门 / 中国台湾
            "001", "0060", "0065", "0081", "0082", // 常见境外诈骗来源
            "170", "171", "162", "165", "167", "174", "149" // 虚拟运营商
        ]
        return prefixes.contains { n.hasPrefix($0) }
    }

    /// 展示用脱敏号码
    static func masked(_ raw: String) -> String {
        let n = normalize(raw)
        guard n.count >= 7 else { return "未知号码" }
        let head = n.prefix(3)
        let tail = n.suffix(4)
        return "\(head)****\(tail)"
    }
}
