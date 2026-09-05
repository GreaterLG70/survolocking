import Foundation

/**
 * 云端 API 客户端。
 *
 * 与 Android 端保持接口一致，仅携带号码哈希，原始号码永不出网。
 */
final class APIClient {

    static let shared = APIClient()

    /// 服务端地址，生产环境替换为实际域名
    var baseURL = "https://survoid.top/sl"

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 10
        return URLSession(configuration: config)
    }()

    /// 来电查询专用短超时会话
    private lazy var fastSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 0.7
        config.timeoutIntervalForResource = 0.8
        return URLSession(configuration: config)
    }()

    private let defaults = UserDefaults(suiteName: RuleStore.appGroupIdentifier)

    private static let phoneSaltKey = "phone_salt"

    private init() {
        // 启动时加载已缓存的服务端盐，保证首个来电决策即可用它哈希
        if let cached = defaults?.string(forKey: APIClient.phoneSaltKey), !cached.isEmpty {
            PhoneUtils.setSalt(cached)
        }
    }

    var isLoggedIn: Bool { (authToken ?? "").isEmpty == false }

    var authToken: String? {
        get { defaults?.string(forKey: "auth_token") }
        set { defaults?.set(newValue, forKey: "auth_token") }
    }

    // MARK: - 通用请求

    private func request(
        _ path: String,
        method: String = "POST",
        body: [String: Any]? = nil,
        query: [String: String]? = nil,
        fast: Bool = false
    ) throws -> URLRequest {
        var components = URLComponents(string: baseURL + path)!
        if let query, !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = authToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return req
    }

    private func send(_ req: URLRequest, fast: Bool = false) async throws -> [String: Any] {
        let (data, response) = try await (fast ? fastSession : session).data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode)
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.invalidResponse
        }
        if let code = json["code"] as? Int, code != 0 {
            throw APIError.business(code: code, message: json["message"] as? String ?? "请求失败")
        }
        return json
    }

    // MARK: - 认证

    func sendCode(phone: String) async throws -> String? {
        let req = try request("/api/auth/send-code", body: ["phone": phone])
        let json = try await send(req)
        let data = json["data"] as? [String: Any]
        return data?["dev_code"] as? String
    }

    @discardableResult
    func login(phone: String, code: String) async throws -> [String: Any] {
        let req = try request("/api/auth/login", body: ["phone": phone, "code": code])
        let json = try await send(req)
        guard let data = json["data"] as? [String: Any],
              let token = data["token"] as? String else {
            throw APIError.invalidResponse
        }
        authToken = token
        captureSalt(from: data)
        return data
    }

    /// 从登录 / me 响应里取出服务端盐并持久化
    private func captureSalt(from data: [String: Any]) {
        if let salt = data["phone_hash_salt"] as? String, !salt.isEmpty {
            PhoneUtils.setSalt(salt)
            defaults?.set(salt, forKey: APIClient.phoneSaltKey)
        }
    }

    /// 拉取当前用户信息并刷新服务端盐（应用重启后调用）
    @discardableResult
    func me() async throws -> [String: Any] {
        let req = try request("/api/auth/me", method: "GET")
        let json = try await send(req)
        let data = json["data"] as? [String: Any] ?? [:]
        captureSalt(from: data)
        return data
    }

    // MARK: - 风险查询

    struct RiskResult {
        let riskLevel: String
        let action: String
    }

    func queryRisk(phone: String) async throws -> RiskResult {
        // 起零按真实号码匹配，传原始号码（服务端仅用于查询，不持久化）
        let req = try request("/api/qiling/query", body: ["phone": phone], fast: true)
        let json = try await send(req, fast: true)
        let data = json["data"] as? [String: Any] ?? [:]
        return RiskResult(
            riskLevel: data["risk_level"] as? String ?? "unknown",
            action: data["action"] as? String ?? "analyze"
        )
    }

    // MARK: - 日志与标记

    @discardableResult
    func uploadLogs(_ logs: [[String: Any]]) async throws -> Int {
        let req = try request("/api/logs/upload", body: ["logs": logs])
        let json = try await send(req)
        return (json["data"] as? [String: Any])?["accepted"] as? Int ?? 0
    }

    func markCall(phoneHash: String, userMark: Int) async throws {
        let req = try request(
            "/api/logs/mark",
            body: ["phone_hash": phoneHash, "user_mark": userMark]
        )
        _ = try await send(req)
    }

    // MARK: - 规则同步

    struct RemoteRule {
        let ruleId: Int
        let ruleType: Int
        let pattern: String
        let confidence: Double
        let source: Int
        let version: Int
    }

    struct SyncResult {
        let version: Int
        let rules: [RemoteRule]
    }

    func syncRules(sinceVersion: Int) async throws -> SyncResult {
        let req = try request(
            "/api/rules/sync", method: "GET",
            query: ["since_version": String(sinceVersion)]
        )
        let json = try await send(req)
        let data = json["data"] as? [String: Any] ?? [:]
        let arr = data["rules"] as? [[String: Any]] ?? []
        let rules = arr.compactMap { o -> RemoteRule? in
            guard let ruleId = o["rule_id"] as? Int,
                  let ruleType = o["rule_type"] as? Int,
                  let pattern = o["pattern"] as? String else { return nil }
            return RemoteRule(
                ruleId: ruleId,
                ruleType: ruleType,
                pattern: pattern,
                confidence: o["confidence"] as? Double ?? 0,
                source: o["source"] as? Int ?? 0,
                version: o["version"] as? Int ?? 0
            )
        }
        return SyncResult(version: data["version"] as? Int ?? sinceVersion, rules: rules)
    }

    func feedback(phoneHash: String, feedbackType: Int) async throws {
        let req = try request(
            "/api/rules/feedback",
            body: ["phone_hash": phoneHash, "feedback_type": feedbackType]
        )
        _ = try await send(req)
    }

    // MARK: - 家庭组

    @discardableResult
    func createFamily(name: String) async throws -> [String: Any] {
        let req = try request("/api/family/create", body: ["name": name])
        let json = try await send(req)
        return json["data"] as? [String: Any] ?? [:]
    }

    @discardableResult
    func inviteMember(familyId: Int, phone: String) async throws -> [String: Any] {
        let req = try request(
            "/api/family/invite",
            body: ["family_id": familyId, "invitee_phone": phone]
        )
        let json = try await send(req)
        return json["data"] as? [String: Any] ?? [:]
    }

    func acceptInvitation(invitationId: Int) async throws {
        let req = try request("/api/family/accept", body: ["invitation_id": invitationId])
        _ = try await send(req)
    }

    func familyMembers() async throws -> [String: Any]? {
        let req = try request("/api/family/members", method: "GET")
        let json = try await send(req)
        return json["data"] as? [String: Any]
    }

    func leaveFamily() async throws {
        let req = try request("/api/family/leave")
        _ = try await send(req)
    }

    enum APIError: LocalizedError {
        case invalidResponse
        case http(status: Int)
        case business(code: Int, message: String)

        var errorDescription: String? {
            switch self {
            case .invalidResponse: return "响应格式错误"
            case .http(let status): return "HTTP 错误 \(status)"
            case .business(_, let message): return message
            }
        }
    }
}
