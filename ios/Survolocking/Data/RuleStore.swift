import Foundation
import SQLite3

/**
 * 端侧规则库（原生 SQLite）。
 *
 * 使用原生 sqlite3 而非 Core Data / SwiftData，原因：
 * 1) Call Directory Extension 有严格的内存上限（约 12MB），轻量存储是硬要求；
 * 2) 主 App 与 Extension 通过 App Group 共享同一个数据库文件，原生 SQLite 最可控。
 *
 * 数据库文件放在 App Group 容器目录，主 App 写入、Extension 只读。
 */
final class RuleStore {

    static let shared = RuleStore()

    /// App Group 标识，必须与 Xcode 中配置的 App Groups 一致
    static let appGroupIdentifier = "group.com.survolocking.shared"
    static let databaseFileName = "survolocking_rules.sqlite"

    private var db: OpaquePointer?
    private let queue = DispatchQueue(label: "com.survolocking.rulestore")

    private init() {
        openDatabase()
        createTables()
    }

    deinit {
        if let db { sqlite3_close(db) }
    }

    // MARK: - 路径

    private var databaseURL: URL? {
        let container = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: Self.appGroupIdentifier
        )
        return container?.appendingPathComponent(Self.databaseFileName)
    }

    // MARK: - 建库

    private func openDatabase() {
        guard let url = databaseURL else {
            print("[RuleStore] 无法获取 App Group 容器，请在 Xcode 中配置 App Groups")
            return
        }
        // 扩展只读打开，避免与主 App 争抢写锁
        if sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) != SQLITE_OK {
            print("[RuleStore] 数据库打开失败: \(errorMessage)")
        }
    }

    private var errorMessage: String {
        db.flatMap { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
    }

    private func createTables() {
        exec("""
            CREATE TABLE IF NOT EXISTS whitelist (
                phone_hash TEXT PRIMARY KEY,
                source     INTEGER NOT NULL,
                expire_at  INTEGER
            )
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS blacklist (
                phone_hash TEXT PRIMARY KEY,
                source     INTEGER NOT NULL,
                mark_count INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            )
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS graylist (
                prefix       TEXT NOT NULL,
                pattern_type INTEGER NOT NULL,
                confidence   REAL NOT NULL,
                version      INTEGER DEFAULT 0,
                PRIMARY KEY (prefix, pattern_type)
            )
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS behavior_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_hash    TEXT NOT NULL,
                ring_duration INTEGER,
                call_time     INTEGER NOT NULL,
                action        INTEGER NOT NULL
            )
        """)
        exec("CREATE INDEX IF NOT EXISTS idx_behavior_hash_time ON behavior_log(phone_hash, call_time)")
        exec("""
            CREATE TABLE IF NOT EXISTS upload_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_hash      TEXT NOT NULL,
                call_time       INTEGER NOT NULL,
                ring_duration   INTEGER,
                action          INTEGER NOT NULL,
                decision_source INTEGER NOT NULL
            )
        """)

        // 本地阻塞号码表（明文，仅本机使用）
        //
        // 存在原因：iOS CallKit 的 Call Directory 要求提供明文号码才能拦截，
        // 而我们的隐私设计只上传号码哈希、云端也只下发哈希，哈希无法反解。
        // 因此 iOS 需要一个本地明文库供 CallKit 写入。
        //
        // 约束：此表永不同步到云端、不参与日志上传、不进入任何网络请求。
        exec("""
            CREATE TABLE IF NOT EXISTS local_blocking_numbers (
                phone_number TEXT PRIMARY KEY,
                phone_hash   TEXT NOT NULL,
                label        TEXT,
                created_at   INTEGER NOT NULL
            )
        """)
    }

    private func exec(_ sql: String) {
        queue.sync {
            if sqlite3_exec(db, sql, nil, nil, nil) != SQLITE_OK {
                print("[RuleStore] SQL 执行失败: \(errorMessage) | \(sql)")
            }
        }
    }

    // MARK: - 查询

    func isWhitelisted(_ hash: String) -> Bool {
        queue.sync { exists("SELECT 1 FROM whitelist WHERE phone_hash = ? AND (expire_at IS NULL OR expire_at > ?)", [hash, String(Int(Date().timeIntervalSince1970))]) }
    }

    func isBlacklisted(_ hash: String) -> Bool {
        queue.sync { exists("SELECT 1 FROM blacklist WHERE phone_hash = ?", [hash]) }
    }

    func grayPrefixMatch(_ normalizedPhone: String) -> GrayRule? {
        queue.sync {
            var stmt: OpaquePointer?
            defer { sqlite3_finalize(stmt) }
            guard sqlite3_prepare_v2(db, "SELECT prefix, pattern_type, confidence, version FROM graylist ORDER BY confidence DESC", -1, &stmt, nil) == SQLITE_OK else {
                return nil
            }
            while sqlite3_step(stmt) == SQLITE_ROW {
                let prefix = String(cString: sqlite3_column_text(stmt, 0))
                if normalizedPhone.hasPrefix(prefix) {
                    return GrayRule(
                        prefix: prefix,
                        patternType: Int(sqlite3_column_int(stmt, 1)),
                        confidence: sqlite3_column_double(stmt, 2),
                        version: Int(sqlite3_column_int(stmt, 3))
                    )
                }
            }
            return nil
        }
    }

    /// 时间窗内来电次数，用于紧急放行判定
    func recentCallCount(_ hash: String, windowMs: Int64) -> Int {
        let since = Int64(Date().timeIntervalSince1970 * 1000) - windowMs
        return queue.sync {
            scalarInt("SELECT COUNT(*) FROM behavior_log WHERE phone_hash = ? AND call_time > ?",
                      [hash, String(since)])
        }
    }

    func shortRingCount(_ hash: String, maxSeconds: Int) -> Int {
        queue.sync {
            scalarInt("SELECT COUNT(*) FROM behavior_log WHERE phone_hash = ? AND ring_duration IS NOT NULL AND ring_duration <= ?",
                      [hash, String(maxSeconds)])
        }
    }

    /// 参数化执行，杜绝 SQL 注入
    /// - Parameter params: 支持 String / Int / Double / nil（nil 绑定为 NULL）
    private func execute(_ sql: String, _ params: [Any?]) {
        queue.sync {
            var stmt: OpaquePointer?
            defer { sqlite3_finalize(stmt) }
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
                print("[RuleStore] 预处理失败: \(errorMessage) | \(sql)")
                return
            }
            for (i, value) in params.enumerated() {
                let index = Int32(i + 1)
                switch value {
                case nil:
                    sqlite3_bind_null(stmt, index)
                case let s as String:
                    sqlite3_bind_text(stmt, index, (s as NSString).utf8String, -1, nil)
                case let i as Int:
                    sqlite3_bind_int64(stmt, index, Int64(i))
                case let i as Int64:
                    sqlite3_bind_int64(stmt, index, i)
                case let d as Double:
                    sqlite3_bind_double(stmt, index, d)
                default:
                    sqlite3_bind_null(stmt, index)
                }
            }
            if sqlite3_step(stmt) != SQLITE_DONE {
                print("[RuleStore] 执行失败: \(errorMessage) | \(sql)")
            }
        }
    }

    // MARK: - 写入

    func addWhitelist(_ hash: String, source: Int, expireAt: Int64? = nil) {
        execute(
            "INSERT OR REPLACE INTO whitelist (phone_hash, source, expire_at) VALUES (?, ?, ?)",
            [hash, source, expireAt]
        )
    }

    func addBlacklist(_ hash: String, source: Int) {
        execute(
            """
            INSERT INTO blacklist (phone_hash, source, mark_count, created_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(phone_hash) DO UPDATE SET mark_count = mark_count + 1
            """,
            [hash, source, Int(Date().timeIntervalSince1970)]
        )
    }

    func replaceGrayRules(_ rules: [GrayRule]) {
        execute("DELETE FROM graylist", [])
        for r in rules {
            execute(
                """
                INSERT OR REPLACE INTO graylist (prefix, pattern_type, confidence, version)
                VALUES (?, ?, ?, ?)
                """,
                [r.prefix, r.patternType, r.confidence, r.version]
            )
        }
    }

    func recordBehavior(_ hash: String, ringDuration: Int?, action: Int) {
        execute(
            """
            INSERT INTO behavior_log (phone_hash, ring_duration, call_time, action)
            VALUES (?, ?, ?, ?)
            """,
            [hash, ringDuration, Int(Date().timeIntervalSince1970 * 1000), action]
        )
    }

    func enqueueUpload(_ hash: String, action: Int, decisionSource: Int, ringDuration: Int?) {
        execute(
            """
            INSERT INTO upload_queue (phone_hash, call_time, ring_duration, action, decision_source)
            VALUES (?, ?, ?, ?, ?)
            """,
            [hash, Int(Date().timeIntervalSince1970 * 1000), ringDuration, action, decisionSource]
        )
    }

    // MARK: - 本地阻塞号码（明文，仅本机，供 CallKit 使用）

    /**
     * 加入本地阻塞号码。
     *
     * 仅写入 `local_blocking_numbers` 表，绝不上网、不参与日志上传。
     * 号码按 E.164 无加号格式存储（如 8617012345678）。
     */
    func addLocalBlockingNumber(_ e164: String, label: String?) {
        let hash = PhoneUtils.hash(of: e164)
        execute(
            """
            INSERT OR REPLACE INTO local_blocking_numbers
                (phone_number, phone_hash, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [e164, hash, label, Int(Date().timeIntervalSince1970)]
        )
    }

    func removeLocalBlockingNumber(_ e164: String) {
        execute("DELETE FROM local_blocking_numbers WHERE phone_number = ?", [e164])
    }

    /// 升序返回阻塞号码，CallKit 要求必须按升序添加
    func allLocalBlockingNumbers() -> [(number: Int64, label: String?)] {
        queue.sync {
            var stmt: OpaquePointer?
            defer { sqlite3_finalize(stmt) }
            var result: [(Int64, String?)] = []
            guard sqlite3_prepare_v2(
                db,
                "SELECT phone_number, label FROM local_blocking_numbers ORDER BY phone_number ASC",
                -1, &stmt, nil
            ) == SQLITE_OK else { return [] }

            while sqlite3_step(stmt) == SQLITE_ROW {
                let numberStr = String(cString: sqlite3_column_text(stmt, 0))
                guard let value = Int64(numberStr) else { continue }
                let label: String? = sqlite3_column_text(stmt, 1).map { String(cString: $0) }
                result.append((value, label))
            }
            return result
        }
    }

    func localBlockingNumberCount() -> Int {
        queue.sync { scalarInt("SELECT COUNT(*) FROM local_blocking_numbers", []) }
    }

    // MARK: - 私有辅助

    private func exists(_ sql: String, _ params: [String]) -> Bool {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return false }
        for (i, p) in params.enumerated() {
            sqlite3_bind_text(stmt, Int32(i + 1), (p as NSString).utf8String, -1, nil)
        }
        return sqlite3_step(stmt) == SQLITE_ROW
    }

    private func scalarInt(_ sql: String, _ params: [String]) -> Int {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        for (i, p) in params.enumerated() {
            sqlite3_bind_text(stmt, Int32(i + 1), (p as NSString).utf8String, -1, nil)
        }
        return sqlite3_step(stmt) == SQLITE_ROW ? Int(sqlite3_column_int(stmt, 0)) : 0
    }

    struct GrayRule {
        let prefix: String
        let patternType: Int
        let confidence: Double
        let version: Int
    }
}
