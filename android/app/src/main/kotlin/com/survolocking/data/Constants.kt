package com.survolocking.data

/**
 * 全局常量。
 *
 * 端云哈希契约（重要）：
 *   端侧 phoneHash = HMAC-SHA256(activeSalt, normalize(phone))
 *   activeSalt 默认取下方兜底常量，登录后被服务端下发的 PHONE_HASH_SALT 覆盖，
 *   并持久化到本地。服务端是唯一真相源，无需手动同步三端源码常量。
 *
 * 安全说明：服务端下发的盐理论上可被反编译/抓包提取。手机号空间约 10^10，
 * 存在暴力枚举风险，属于行业通病。生产加固建议：
 *   1) 改用账号级盐并用 Android Keystore 存储，而非全局共享盐；
 *   2) 服务端对高频哈希查询做限频与审计。
 */
object Constants {
    /** 登录前尚未拿到服务端盐时的兜底默认值（仅占位，真实部署会被覆盖） */
    const val PHONE_SALT = "survolocking_phone_salt_v1"

    /** 第1层本地硬规则预算 */
    const val BUDGET_LAYER1_MS = 50L

    /** 第2层云端查询预算，超时立即跳过，绝不阻塞来电 */
    const val BUDGET_LAYER2_MS = 150L

    /** 第3层微行为分析预算 */
    const val BUDGET_LAYER3_MS = 30L

    /** 端侧整体决策预算 */
    const val BUDGET_TOTAL_MS = 200L

    // 微行为判定阈值
    /** 响铃不超过该秒数且对方主动挂断 → 高危（典型骚扰特征） */
    const val RING_SUSPICIOUS_MAX_SEC = 3

    /** 响铃超过该秒数 → 疑似重要来电，提示回拨 */
    const val RING_IMPORTANT_MIN_SEC = 15

    /** 该时间窗内同号来电达到该次数 → 强制放行（可能是紧急情况） */
    const val URGENT_WINDOW_MS = 60 * 60 * 1000L
    const val URGENT_MIN_COUNT = 3

    /** 用户事后标记，与服务端 intercept_logs.user_mark 保持一致 */
    const val MARK_SPAM = 1
    const val MARK_CUSTOMER = 2
    const val MARK_UNKNOWN = 3

    /** 决策来源，与服务端 intercept_logs.decision_source 保持一致 */
    const val SOURCE_LOCAL = 1
    const val SOURCE_QILING = 2
    const val SOURCE_BEHAVIOR = 3
    const val SOURCE_USER = 4

    // 规则同步
    const val RULE_SYNC_INTERVAL_HOURS = 4L
    const val LOG_UPLOAD_INTERVAL_HOURS = 6L

    const val PREF_NAME = "survolocking_prefs"
    const val KEY_AUTH_TOKEN = "auth_token"
    const val KEY_USER_ID = "user_id"
    const val KEY_RULE_VERSION = "rule_version"
    /** 服务端下发的哈希盐，登录后缓存，决策引擎离线可用 */
    const val KEY_PHONE_SALT = "phone_salt"
    const val KEY_INTERCEPT_ENABLED = "intercept_enabled"

    // 用户资料缓存
    const val KEY_NICKNAME = "nickname"
    const val KEY_AVATAR = "avatar"
    const val KEY_CREATED_AT = "created_at"
    /** 首次启动时间（毫秒），用于统计"已守护天数" */
    const val KEY_FIRST_LAUNCH = "first_launch"
}
