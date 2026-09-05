package com.survolocking.engine

import android.content.Context
import com.survolocking.data.Constants
import com.survolocking.data.RuleDatabase
import com.survolocking.net.ApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap

/**
 * 四层决策引擎——端侧拦截的核心。
 *
 * 硬约束：整体决策必须在 200ms 内返回，系统层的 onScreenCall 有 5 秒
 * 超时，超时会放行，因此任何一层都不允许阻塞。
 *
 * 第1层 本地硬规则   0-50ms   无需联网
 * 第2层 起零风险缓存 0-5ms    只读本地缓存；未命中降级并后台预热（见下）
 * 第3层 本地微行为   0-30ms   无需联网
 * 第4层 兜底放行     0ms      无法判定则正常响铃
 *
 * 第2层设计说明：起零（iStero）API 实测响应 ~3.7s，无法满足 150ms 实时预算，
 * 因此实时链路只读本地缓存；缓存未命中时立即降级到第3层，并由后台协程
 * 异步预热缓存——同一号码第二次来电即可在预算内完成拦截。骚扰号码往往
 * 反复拨打，该策略对其命中率很高；首次来电遵循「宁可漏接，不可误拦」。
 */
class DecisionEngine(context: Context) {

    private val appContext = context.applicationContext
    private val db = RuleDatabase.getInstance(appContext)
    private val contacts = ContactsHelper(appContext)
    private val api = ApiClient(appContext)

    /** 起零查询结果的本地缓存，避免同一号码短时间内重复联网 */
    private val riskCache = ConcurrentHashMap<String, CachedRisk>()

    /** 后台预热专用作用域：独立于来电决策生命周期，fire-and-forget */
    private val bgScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** in-flight 去重：同一号码并发只发起一次后台预热请求 */
    private val inFlight = ConcurrentHashMap.newKeySet<String>()

    /** 决策动作 */
    enum class Action {
        /** 正常放行 */
        ALLOW,

        /** 静默拦截：不响铃，直接挂断并计入通话记录 */
        BLOCK,

        /** 静音放行：不响铃但保留来电，适合"疑似骚扰"场景 */
        SILENCE,

        /** 放行但打警告标签，由用户自行判断（iOS 风格） */
        WARN
    }

    /** 决策来源，用于日志上报与后续分析 */
    enum class Source(val code: Int) {
        LOCAL_BLACKLIST(1),
        QILING(2),
        MICRO_BEHAVIOR(3),
        USER_MARK(4),
        FALLBACK(0)
    }

    data class Decision(
        val action: Action,
        val layer: Int,
        val source: Source,
        val reason: String,
        val costMs: Long
    )

    data class CachedRisk(val action: String, val expireAt: Long)

    /**
     * 执行四层决策。
     *
     * @param rawNumber 来电号码（原始明文，仅在设备内存中使用，不落盘不上传）
     */
    suspend fun decide(rawNumber: String): Decision {
        val start = System.currentTimeMillis()
        val normalized = PhoneUtils.normalize(rawNumber)
        val hash = PhoneUtils.hash(normalized)

        // -------------------------------------------------- 第1层：本地硬规则
        val layer1 = layer1LocalRules(hash, normalized)
        if (layer1 != null) {
            return layer1.copy(costMs = System.currentTimeMillis() - start)
        }

        // -------------------------------------------------- 第2层：起零风险缓存
        // 只读本地缓存（0ms）。命中 block 即拦截；未命中/过期立即降级到第3层，
        // 同时后台预热缓存（fire-and-forget），绝不阻塞决策。
        val cached = riskCache[hash]
        if (cached != null && cached.expireAt > System.currentTimeMillis()) {
            if (cached.action == "block") {
                return Decision(
                    Action.BLOCK, 2, Source.QILING, "起零数据判定为诈骗号码",
                    System.currentTimeMillis() - start
                )
            }
            // action=analyze：起零未定性为诈骗，继续后续层
        } else {
            if (cached != null) riskCache.remove(hash)
            warmRiskCacheInBackground(hash, normalized)
        }

        // -------------------------------------------------- 第3层：本地微行为分析
        val layer3 = layer3MicroBehavior(hash)
        if (layer3 != null) {
            return layer3.copy(costMs = System.currentTimeMillis() - start)
        }

        // -------------------------------------------------- 第4层：兜底放行
        return Decision(
            Action.ALLOW, 4, Source.FALLBACK, "未命中任何规则，正常响铃",
            System.currentTimeMillis() - start
        )
    }

    /**
     * 第1层：本地硬规则（0-50ms，无需联网）。
     * 返回 null 表示未命中，需继续下一层。
     */
    private fun layer1LocalRules(hash: String, normalized: String): Decision? {
        // 1) 通讯录白名单——最高优先级，任何规则不可覆盖
        if (contacts.isContact(hash)) {
            return Decision(Action.ALLOW, 1, Source.LOCAL_BLACKLIST, "通讯录联系人，直接放行", 0)
        }

        // 2) 用户手动白名单（含误拦纠正产生的豁免）
        if (db.isWhitelisted(hash)) {
            return Decision(Action.ALLOW, 1, Source.USER_MARK, "用户白名单", 0)
        }

        // 3) 用户手动黑名单 / 家庭共享黑名单
        if (db.isBlacklisted(hash)) {
            return Decision(Action.BLOCK, 1, Source.LOCAL_BLACKLIST, "黑名单号码", 0)
        }

        // 4) 异常号段：境外来电、虚拟运营商、高仿号
        if (PhoneUtils.isSuspiciousPrefix(normalized)) {
            return Decision(Action.BLOCK, 1, Source.LOCAL_BLACKLIST, "异常号段", 0)
        }

        // 5) 云端下发的号段/前缀规则
        val gray = db.matchGrayPrefix(normalized)
        if (gray != null && gray.confidence >= GRAY_BLOCK_THRESHOLD) {
            return Decision(
                Action.BLOCK, 1, Source.LOCAL_BLACKLIST,
                "号段规则 ${gray.prefix}（置信度 ${"%.2f".format(gray.confidence)}）", 0
            )
        }

        return null
    }

    /**
     * 第2层：后台预热起零风险缓存（fire-and-forget，绝不阻塞决策）。
     *
     * hash 仅用于本地缓存键与去重；phone 为原始号码，传给云端起零做匹配
     * （服务端仅用于查询，不持久化）。
     * in-flight 去重：同一号码并发只发一次请求；失败结果短缓存，避免
     * 同一号码每次来电都发起注定失败的网络请求。
     */
    private fun warmRiskCacheInBackground(hash: String, phone: String) {
        if (!api.isLoggedIn) return
        if (!inFlight.add(hash)) return
        bgScope.launch {
            try {
                val result = api.queryRisk(phone)
                riskCache[hash] = CachedRisk(
                    result.action, System.currentTimeMillis() + RISK_CACHE_TTL_MS
                )
            } catch (e: Exception) {
                // 网络异常/超时：短缓存降级结果（analyze），下次来电不再重复请求
                riskCache[hash] = CachedRisk(
                    "analyze", System.currentTimeMillis() + RISK_FAIL_TTL_MS
                )
            } finally {
                inFlight.remove(hash)
            }
        }
    }

    /**
     * 第3层：本地微行为分析（0-30ms，无需联网）。
     *
     * 依据：
     * - 该号码历史上多次"响铃≤3秒后主动挂断" → 典型骚扰特征；
     * - 1小时内同号来电≥3次 → 视为紧急情况，强制放行，避免误拦重要来电。
     */
    private fun layer3MicroBehavior(hash: String): Decision? {
        // 紧急情况优先于一切行为判定：短时间内反复来电通常是真人有急事
        val recent = db.recentCallCount(hash, Constants.URGENT_WINDOW_MS)
        if (recent >= Constants.URGENT_MIN_COUNT) {
            return Decision(
                Action.ALLOW, 3, Source.MICRO_BEHAVIOR,
                "1小时内第 ${recent + 1} 次来电，疑似紧急情况强制放行", 0
            )
        }

        val shortRings = db.shortRingCount(hash, Constants.RING_SUSPICIOUS_MAX_SEC)
        if (shortRings >= SHORT_RING_BLOCK_THRESHOLD) {
            return Decision(
                Action.BLOCK, 3, Source.MICRO_BEHAVIOR,
                "历史响铃≤${Constants.RING_SUSPICIOUS_MAX_SEC}秒挂断 $shortRings 次", 0
            )
        }

        // 单次短响铃不足以定性，仅静音提示，不直接拦截
        if (shortRings >= 1) {
            return Decision(
                Action.SILENCE, 3, Source.MICRO_BEHAVIOR, "疑似骚扰，已静音", 0
            )
        }

        return null
    }

    /** 记录本次来电行为，供后续微行为分析使用 */
    fun recordIncoming(hash: String, action: Int) {
        db.recordBehavior(hash, null, action)
    }

    /** 通话结束后补全响铃时长 */
    fun recordRingDuration(hash: String, ringSeconds: Int, action: Int) {
        db.recordBehavior(hash, ringSeconds, action)
    }

    /** 供 UI 调用：加入黑名单 */
    fun addToBlacklist(rawNumber: String) {
        db.addBlacklist(PhoneUtils.hashOf(rawNumber), SOURCE_USER)
    }

    /** 供 UI 调用：加入白名单（误拦纠正） */
    fun addToWhitelist(rawNumber: String) {
        db.addWhitelist(PhoneUtils.hashOf(rawNumber), SOURCE_USER)
    }

    suspend fun refreshAll() = withContext(Dispatchers.IO) {
        contacts.refresh()
        db.trimBehaviorLog()
    }

    companion object {
        /** 号段规则达到该置信度才直接拦截，否则仅静音 */
        const val GRAY_BLOCK_THRESHOLD = 0.8

        /** 历史短响铃次数达到该值直接拦截 */
        const val SHORT_RING_BLOCK_THRESHOLD = 2

        private const val RISK_CACHE_TTL_MS = 5 * 60 * 1000L

        /** 起零查询失败后的短缓存：避免同号码每次来电都发起无效请求 */
        private const val RISK_FAIL_TTL_MS = 60 * 1000L

        private const val SOURCE_USER = 1
    }
}
