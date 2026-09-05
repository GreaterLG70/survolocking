package com.survolocking.net

import android.content.Context
import com.survolocking.BuildConfig
import com.survolocking.data.Constants
import com.survolocking.engine.PhoneUtils
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * 云端 API 客户端。
 *
 * 全部请求均只携带号码哈希，原始号码永不出网。
 * 超时设计保守：来电链路上的查询必须能在 150ms 预算内失败并降级。
 */
class ApiClient(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(
        Constants.PREF_NAME, Context.MODE_PRIVATE
    )

    init {
        // 启动时加载已缓存的服务端盐，保证首个来电决策即可用它哈希
        prefs.getString(Constants.KEY_PHONE_SALT, null)?.let { PhoneUtils.setSalt(it) }
    }

    /** 从登录/me 响应里取出服务端盐并持久化 */
    private fun captureSalt(data: JSONObject?) {
        val salt = data?.optString("phone_hash_salt")
        if (!salt.isNullOrBlank()) {
            PhoneUtils.setSalt(salt)
            prefs.edit().putString(Constants.KEY_PHONE_SALT, salt).apply()
        }
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    // 起零查询走后台预热/手动查询，不在实时决策链路上：起零 API 实测 ~3.7s，
    // 使用常规超时（读 5s）即可；实时拦截只读端侧本地缓存（见 DecisionEngine）。

    var authToken: String?
        get() = prefs.getString(Constants.KEY_AUTH_TOKEN, null)
        set(value) = prefs.edit().putString(Constants.KEY_AUTH_TOKEN, value).apply()

    val isLoggedIn: Boolean get() = !authToken.isNullOrBlank()

    /** 当前登录用户 ID（未登录为 0），用于家庭成员列表里识别「我」 */
    val userId: Long
        get() = prefs.getLong(Constants.KEY_USER_ID, 0L)

    // 用户资料缓存（登录后写入，用于个人中心展示）
    var nickname: String?
        get() = prefs.getString(Constants.KEY_NICKNAME, null)
        set(v) = prefs.edit().putString(Constants.KEY_NICKNAME, v).apply()

    var avatar: String?
        get() = prefs.getString(Constants.KEY_AVATAR, null)
        set(v) = prefs.edit().putString(Constants.KEY_AVATAR, v).apply()

    var createdAt: Long
        get() = prefs.getLong(Constants.KEY_CREATED_AT, 0L)
        set(v) = prefs.edit().putLong(Constants.KEY_CREATED_AT, v).apply()

    /** 守护天数：自首次启动计算 */
    val installDays: Int
        get() {
            val first = prefs.getLong(Constants.KEY_FIRST_LAUNCH, 0L)
            if (first == 0L) return 1
            return ((System.currentTimeMillis() - first) / 86_400_000L).toInt().coerceAtLeast(1)
        }

    /** 使用天数：自账号创建计算，未登录则回退到守护天数 */
    val usageDays: Int
        get() {
            if (createdAt == 0L) return installDays
            return ((System.currentTimeMillis() - createdAt) / 86_400_000L).toInt().coerceAtLeast(1)
        }

    fun ensureFirstLaunchRecorded() {
        if (prefs.getLong(Constants.KEY_FIRST_LAUNCH, 0L) == 0L) {
            prefs.edit().putLong(Constants.KEY_FIRST_LAUNCH, System.currentTimeMillis()).apply()
        }
    }

    fun logout() {
        prefs.edit()
            .remove(Constants.KEY_AUTH_TOKEN)
            .remove(Constants.KEY_NICKNAME)
            .remove(Constants.KEY_AVATAR)
            .remove(Constants.KEY_CREATED_AT)
            .remove(Constants.KEY_USER_ID)
            .apply()
    }

    private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

    private fun request(path: String, body: JSONObject? = null, method: String = "POST"): Request {
        val builder = Request.Builder().url(BuildConfig.API_BASE + path)
        authToken?.let { builder.addHeader("Authorization", "Bearer $it") }
        builder.addHeader("Content-Type", "application/json")
        val payload = body?.toString()
        when (method) {
            "GET" -> builder.get()
            "POST" -> builder.post((payload ?: "{}").toRequestBody(JSON_MEDIA))
            "PUT" -> builder.put((payload ?: "{}").toRequestBody(JSON_MEDIA))
            "DELETE" -> builder.delete((payload ?: "{}").toRequestBody(JSON_MEDIA))
        }
        return builder.build()
    }

    private fun parse(resp: okhttp3.Response): JSONObject {
        // 单设备登录：服务端判定账号已在别处登录时返回 401 + X-Kicked: 1。
        // 注意：登录接口的「密码错误/验证码错误」同样返回 401（无 X-Kicked 头），
        // 因此必须严格区分，只在带 X-Kicked 头时才清登录态并触发互踢 UI，
        // 避免用户密码输错被误判成「被踢下线」。
        if (resp.code == 401 && resp.header("X-Kicked") == "1") {
            logout()
            onKicked?.invoke()
            throw KickedException()
        }
        val text = resp.body?.string().orEmpty()
        if (!resp.isSuccessful) throw ApiException(resp.code, text, 0)
        val json = JSONObject(text)
        val code = json.optInt("code", -1)
        if (code != 0) {
            val data = json.optJSONObject("data")
            val retryAfter = data?.optInt("retry_after", 0) ?: 0
            throw ApiException(code, json.optString("message", "请求失败"), retryAfter)
        }
        return json
    }

    // ------------------------------------------------------------ 认证

    suspend fun sendCode(phone: String): String? = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone", phone)
        try {
            val json = parse(client.newCall(request("/api/auth/send-code", body)).execute())
            json.getJSONObject("data").optString("dev_code").ifBlank { null }
        } catch (e: ApiException) {
            // sendCode 1005 携带 retry_after；其他错误沿用
            throw e
        }
    }

    suspend fun login(phone: String, code: String): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone", phone).put("code", code)
        val json = parse(client.newCall(request("/api/auth/login", body)).execute())
        val data = json.getJSONObject("data")
        authToken = data.getString("token")
        prefs.edit().putLong(Constants.KEY_USER_ID, data.getLong("user_id")).apply()
        captureSalt(data)
        persistProfile(data)
        refreshProfile()
        data
    }

    /** 账号密码登录 */
    suspend fun passwordLogin(phone: String, password: String): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone", phone).put("password", password)
        val json = parse(client.newCall(request("/api/auth/login", body)).execute())
        val data = json.getJSONObject("data")
        authToken = data.getString("token")
        prefs.edit().putLong(Constants.KEY_USER_ID, data.getLong("user_id")).apply()
        persistProfile(data)
        refreshProfile()
        data
    }

    /** 注册（验证码 + 密码），昵称可选 */
    suspend fun register(
        phone: String, code: String, password: String, nickname: String? = null
    ): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone", phone).put("code", code).put("password", password)
        if (!nickname.isNullOrBlank()) body.put("nickname", nickname)
        val json = parse(client.newCall(request("/api/auth/register", body)).execute())
        val data = json.getJSONObject("data")
        authToken = data.getString("token")
        prefs.edit().putLong(Constants.KEY_USER_ID, data.getLong("user_id")).apply()
        persistProfile(data)
        refreshProfile()
        data
    }

    /** 更新个人资料（昵称 / 头像） */
    suspend fun updateProfile(nickname: String? = null, avatar: String? = null): Boolean =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
            if (nickname != null) body.put("nickname", nickname)
            if (avatar != null) body.put("avatar", avatar)
            runCatching {
                parse(client.newCall(request("/api/auth/profile", body, "PUT")).execute())
                if (nickname != null) this@ApiClient.nickname = nickname
                if (avatar != null) this@ApiClient.avatar = avatar
                true
            }.getOrDefault(false)
        }

    /** 从 /me 拉取并缓存资料（昵称、头像、创建时间） */
    suspend fun refreshProfile() = withContext(Dispatchers.IO) {
        runCatching { persistProfile(me()) }
    }

    private fun persistProfile(data: JSONObject) {
        captureSalt(data)
        if (data.has("nickname")) {
            nickname = data.optString("nickname").ifBlank { null }
        }
        if (data.has("avatar")) {
            avatar = data.optString("avatar").ifBlank { null }
        }
        val ca = data.optString("created_at", "")
        if (ca.isNotBlank()) {
            runCatching {
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(ca)?.time
            }.getOrNull()?.let { createdAt = it }
        }
    }

    /** 一言：每日一句（前端直接请求公开 API） */
    private val hitokotoClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    suspend fun hitokoto(): String? = withContext(Dispatchers.IO) {
        runCatching {
            val req = Request.Builder().url("https://v1.hitokoto.cn/").get().build()
            val resp = hitokotoClient.newCall(req).execute()
            val json = JSONObject(resp.body?.string().orEmpty())
            val hit = json.optString("hitokoto", "")
            val from = json.optString("from", "")
            if (hit.isBlank()) null else hit + if (from.isBlank()) "" else "  —— $from"
        }.getOrNull()
    }

    suspend fun me(): JSONObject = withContext(Dispatchers.IO) {
        val data = parse(client.newCall(request("/api/auth/me", method = "GET")).execute())
            .getJSONObject("data")
        captureSalt(data)
        data
    }

    // ------------------------------------------------------------ 起零查询

    /**
     * 第2层：查询号码风险。
     * 调用方必须包裹超时（BUDGET_LAYER2_MS），超时即放弃。
     */
    suspend fun queryRisk(phone: String): RiskResult = withContext(Dispatchers.IO) {
        // 起零按真实号码匹配，传原始号码（服务端仅用于查询，不持久化）
        val body = JSONObject().put("phone", phone)
        val json = parse(client.newCall(request("/api/qiling/query", body)).execute())
        val data = json.getJSONObject("data")
        RiskResult(
            riskLevel = data.optString("risk_level", "unknown"),
            action = data.optString("action", "analyze"),
            cached = data.optBoolean("cached", false)
        )
    }

    // ------------------------------------------------------------ 日志与标记

    suspend fun uploadLogs(logs: List<UploadLogItem>): Int = withContext(Dispatchers.IO) {
        if (logs.isEmpty()) return@withContext 0
        val arr = JSONArray()
        logs.forEach { l ->
            arr.put(
                JSONObject()
                    .put("phone_hash", l.phoneHash)
                    .put("call_time", l.callTimeIso)
                    .put("ring_duration", l.ringDuration)
                    .put("action", l.action)
                    .put("decision_source", l.decisionSource)
            )
        }
        val json = parse(client.newCall(request("/api/logs/upload", JSONObject().put("logs", arr))).execute())
        json.getJSONObject("data").optInt("accepted", 0)
    }

    suspend fun markCall(phoneHash: String, userMark: Int) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone_hash", phoneHash).put("user_mark", userMark)
        parse(client.newCall(request("/api/logs/mark", body)).execute())
    }

    // ------------------------------------------------------------ 规则同步

    suspend fun syncRules(sinceVersion: Int): SyncResult = withContext(Dispatchers.IO) {
        val json = parse(
            client.newCall(
                request("/api/rules/sync?since_version=$sinceVersion", method = "GET")
            ).execute()
        )
        val data = json.getJSONObject("data")
        val arr = data.getJSONArray("rules")
        val rules = mutableListOf<RemoteRule>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            rules.add(
                RemoteRule(
                    ruleId = o.getLong("rule_id"),
                    ruleType = o.getInt("rule_type"),
                    pattern = o.getString("pattern"),
                    confidence = o.optDouble("confidence", 0.0),
                    source = o.getInt("source"),
                    version = o.getInt("version")
                )
            )
        }
        SyncResult(version = data.optInt("version", sinceVersion), rules = rules)
    }

    suspend fun feedback(phoneHash: String, feedbackType: Int) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("phone_hash", phoneHash).put("feedback_type", feedbackType)
        parse(client.newCall(request("/api/rules/feedback", body)).execute())
    }

    // ------------------------------------------------------------ 家庭组

    suspend fun createFamily(name: String): JSONObject = withContext(Dispatchers.IO) {
        parse(client.newCall(request("/api/family/create", JSONObject().put("name", name))).execute())
            .getJSONObject("data")
    }

    suspend fun inviteMember(familyId: Long, phone: String): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("family_id", familyId).put("invitee_phone", phone)
        parse(client.newCall(request("/api/family/invite", body)).execute()).getJSONObject("data")
    }

    suspend fun acceptInvitation(invitationId: Long) = withContext(Dispatchers.IO) {
        parse(client.newCall(request("/api/family/accept", JSONObject().put("invitation_id", invitationId))).execute())
    }

    suspend fun rejectInvitation(invitationId: Long) = withContext(Dispatchers.IO) {
        parse(client.newCall(request("/api/family/reject", JSONObject().put("invitation_id", invitationId))).execute())
    }

    /** 我收到的待处理邀请（GET /api/family/invitations） */
    suspend fun myInvitations(): JSONArray? = withContext(Dispatchers.IO) {
        val json = parse(client.newCall(request("/api/family/invitations", method = "GET")).execute())
        json.optJSONObject("data")?.optJSONArray("invitations")
    }

    suspend fun familyMembers(): JSONObject? = withContext(Dispatchers.IO) {
        val json = parse(client.newCall(request("/api/family/members", method = "GET")).execute())
        json.optJSONObject("data")
    }

    suspend fun leaveFamily(): JSONObject = withContext(Dispatchers.IO) {
        parse(client.newCall(request("/api/family/leave")).execute()).getJSONObject("data")
    }

    suspend fun notifications(limit: Int = 30): JSONArray? = withContext(Dispatchers.IO) {
        val json = parse(client.newCall(request("/api/notify/list?limit=$limit", method = "GET")).execute())
        json.optJSONObject("data")?.optJSONArray("notifications")
    }

    suspend fun updatePushToken(token: String) = withContext(Dispatchers.IO) {
        parse(client.newCall(request("/api/auth/push-token?push_token=$token", method = "PUT")).execute())
    }

    data class RiskResult(val riskLevel: String, val action: String, val cached: Boolean)
    data class UploadLogItem(
        val phoneHash: String,
        val callTimeIso: String,
        val ringDuration: Int?,
        val action: Int,
        val decisionSource: Int
    )
    data class RemoteRule(
        val ruleId: Long,
        val ruleType: Int,
        val pattern: String,
        val confidence: Double,
        val source: Int,
        val version: Int
    )
    data class SyncResult(val version: Int, val rules: List<RemoteRule>)

    class ApiException(
        val code: Int,
        msg: String,
        val retryAfterSeconds: Int = 0
    ) : Exception("[$code] $msg")

    /** 账号已在其他设备登录，本端被挤下线。 */
    class KickedException : Exception("账号已在其他设备登录")

    companion object {
        /**
         * 被踢下线的全局监听。由 MainActivity 注册，
         * 收到后弹窗提示并强制回登录页（参考 QQ 的互踢表现）。
         */
        @Volatile
        var onKicked: (() -> Unit)? = null
    }
}
