package com.survolocking.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.util.concurrent.locks.ReentrantReadWriteLock
import kotlin.concurrent.read
import kotlin.concurrent.write

/**
 * 端侧规则库（SQLite）。
 *
 * 全部判定只在本地完成，不依赖网络，保证第1/3/4层在 200ms 预算内返回。
 */
class RuleDatabase private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DB_NAME, null, DB_VERSION) {

    companion object {
        const val DB_NAME = "survolocking_rules.db"
        const val DB_VERSION = 1

        @Volatile
        private var INSTANCE: RuleDatabase? = null

        fun getInstance(context: Context): RuleDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: RuleDatabase(context).also { INSTANCE = it }
            }
    }

    private val lock = ReentrantReadWriteLock()

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE whitelist (
                phone_hash TEXT PRIMARY KEY,
                source     INTEGER NOT NULL,
                expire_at  INTEGER
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE blacklist (
                phone_hash  TEXT PRIMARY KEY,
                source      INTEGER NOT NULL,
                mark_count  INTEGER DEFAULT 1,
                created_at  INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE graylist (
                prefix       TEXT NOT NULL,
                pattern_type INTEGER NOT NULL,
                confidence   REAL NOT NULL,
                rule_id      INTEGER,
                version      INTEGER DEFAULT 0,
                PRIMARY KEY (prefix, pattern_type)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE behavior_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_hash    TEXT NOT NULL,
                ring_duration INTEGER,
                call_time     INTEGER NOT NULL,
                action        INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_behavior_hash_time ON behavior_log(phone_hash, call_time)")

        // 通话结束后待用户标记的号码
        db.execSQL(
            """
            CREATE TABLE pending_mark (
                phone_hash  TEXT PRIMARY KEY,
                call_time   INTEGER NOT NULL,
                ring_duration INTEGER,
                action      INTEGER NOT NULL
            )
            """.trimIndent()
        )

        // 待上传的脱敏日志队列
        db.execSQL(
            """
            CREATE TABLE upload_queue (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_hash     TEXT NOT NULL,
                call_time      INTEGER NOT NULL,
                ring_duration  INTEGER,
                action         INTEGER NOT NULL,
                decision_source INTEGER NOT NULL
            )
            """.trimIndent()
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // v1 为初始版本，暂无迁移逻辑
        db.execSQL("DROP TABLE IF EXISTS whitelist")
        db.execSQL("DROP TABLE IF EXISTS blacklist")
        db.execSQL("DROP TABLE IF EXISTS graylist")
        db.execSQL("DROP TABLE IF EXISTS behavior_log")
        db.execSQL("DROP TABLE IF EXISTS pending_mark")
        db.execSQL("DROP TABLE IF EXISTS upload_queue")
        onCreate(db)
    }

    // ---------------------------------------------------------------- 白名单

    fun isWhitelisted(hash: String): Boolean = lock.read {
        readableDatabase.query(
            "whitelist",
            arrayOf("phone_hash"),
            "phone_hash = ? AND (expire_at IS NULL OR expire_at > ?)",
            arrayOf(hash, System.currentTimeMillis().toString()),
            null, null, null, "1"
        ).use { it.moveToFirst() }
    }

    fun addWhitelist(hash: String, source: Int, expireAt: Long? = null) = lock.write {
        writableDatabase.insertWithOnConflict(
            "whitelist", null,
            ContentValues().apply {
                put("phone_hash", hash)
                put("source", source)
                put("expire_at", expireAt)
            },
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    // ---------------------------------------------------------------- 黑名单

    fun isBlacklisted(hash: String): Boolean = lock.read {
        readableDatabase.query(
            "blacklist", arrayOf("phone_hash"), "phone_hash = ?",
            arrayOf(hash), null, null, null, "1"
        ).use { it.moveToFirst() }
    }

    fun addBlacklist(hash: String, source: Int) = lock.write {
        val db = writableDatabase
        db.execSQL(
            "INSERT INTO blacklist (phone_hash, source, mark_count, created_at) " +
                "VALUES (?, ?, 1, ?) " +
                "ON CONFLICT(phone_hash) DO UPDATE SET mark_count = mark_count + 1",
            arrayOf(hash, source, System.currentTimeMillis())
        )
    }

    // ---------------------------------------------------------------- 灰名单（号段/前缀）

    fun matchGrayPrefix(normalizedPhone: String): GrayRule? = lock.read {
        readableDatabase.query(
            "graylist", null, null, null, null, null, "confidence DESC"
        ).use { c ->
            while (c.moveToNext()) {
                val prefix = c.getString(c.getColumnIndexOrThrow("prefix"))
                if (normalizedPhone.startsWith(prefix)) {
                    return@use GrayRule(
                        prefix = prefix,
                        patternType = c.getInt(c.getColumnIndexOrThrow("pattern_type")),
                        confidence = c.getDouble(c.getColumnIndexOrThrow("confidence"))
                    )
                }
            }
            null
        }
    }

    fun replaceGrayRules(rules: List<GrayRule>) = lock.write {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.delete("graylist", null, null)
            for (r in rules) {
                db.insertWithOnConflict(
                    "graylist", null,
                    ContentValues().apply {
                        put("prefix", r.prefix)
                        put("pattern_type", r.patternType)
                        put("confidence", r.confidence)
                        put("version", r.version)
                    },
                    SQLiteDatabase.CONFLICT_REPLACE
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    // ---------------------------------------------------------------- 行为日志

    fun recordBehavior(hash: String, ringDuration: Int?, action: Int) = lock.write {
        writableDatabase.insert(
            "behavior_log", null,
            ContentValues().apply {
                put("phone_hash", hash)
                put("ring_duration", ringDuration)
                put("call_time", System.currentTimeMillis())
                put("action", action)
            }
        )
    }

    /** 统计时间窗内的来电次数，用于"紧急情况强制放行"判定 */
    fun recentCallCount(hash: String, windowMs: Long): Int = lock.read {
        val since = System.currentTimeMillis() - windowMs
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM behavior_log WHERE phone_hash = ? AND call_time > ?",
            arrayOf(hash, since.toString())
        ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    /** 该号码历史上短响铃（疑似骚扰）次数 */
    fun shortRingCount(hash: String, maxSec: Int): Int = lock.read {
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM behavior_log WHERE phone_hash = ? AND ring_duration IS NOT NULL AND ring_duration <= ?",
            arrayOf(hash, maxSec.toString())
        ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    fun trimBehaviorLog(keepDays: Int = 30) = lock.write {
        val cutoff = System.currentTimeMillis() - keepDays * 24 * 3600 * 1000L
        writableDatabase.delete("behavior_log", "call_time < ?", arrayOf(cutoff.toString()))
    }

    /** 累计拦截次数（action = 1 为拦截） */
    fun blockedCount(): Int = lock.read {
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM behavior_log WHERE action = 1",
            null
        ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    /** 已守护天数：本地库中有拦截记录（action=1）的不同自然日数量；未拦截过则为 0 */
    fun guardDays(): Int = lock.read {
        readableDatabase.rawQuery(
            "SELECT COUNT(DISTINCT CAST(call_time / 86400000 AS INTEGER)) FROM behavior_log WHERE action = 1",
            null
        ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    /** 黑名单条目数 */
    fun blacklistCount(): Int = lock.read {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM blacklist", null)
            .use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    /** 白名单条目数 */
    fun whitelistCount(): Int = lock.read {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM whitelist", null)
            .use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    /** 黑名单条目列表（时间倒序）：hash 前缀 + 被标记次数 + 加入时间 */
    fun listBlacklist(limit: Int = 100): List<BlackEntry> = lock.read {
        val out = mutableListOf<BlackEntry>()
        readableDatabase.rawQuery(
            "SELECT phone_hash, mark_count, created_at FROM blacklist ORDER BY created_at DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                out.add(
                    BlackEntry(
                        phoneHash = c.getString(0),
                        markCount = c.getInt(1),
                        createdAt = c.getLong(2)
                    )
                )
            }
        }
        out
    }

    /** 白名单条目列表（时间倒序）：hash 前缀 + 来源 + 过期时间 */
    fun listWhitelist(limit: Int = 100): List<WhiteEntry> = lock.read {
        val out = mutableListOf<WhiteEntry>()
        readableDatabase.rawQuery(
            "SELECT phone_hash, source, expire_at FROM whitelist ORDER BY rowid DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                out.add(
                    WhiteEntry(
                        phoneHash = c.getString(0),
                        source = c.getInt(1),
                        expireAt = if (c.isNull(2)) null else c.getLong(2)
                    )
                )
            }
        }
        out
    }

    data class BlackEntry(val phoneHash: String, val markCount: Int, val createdAt: Long)
    data class WhiteEntry(val phoneHash: String, val source: Int, val expireAt: Long?)

    /** 近期拦截记录（仅含时间，号码以哈希存储，不展示明文以保隐私） */
    fun recentBlocked(limit: Int = 30): List<Long> = lock.read {
        val out = mutableListOf<Long>()
        readableDatabase.rawQuery(
            "SELECT call_time FROM behavior_log WHERE action = 1 ORDER BY call_time DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) out.add(c.getLong(0))
        }
        out
    }


    // ---------------------------------------------------------------- 待标记队列

    fun addPendingMark(hash: String, ringDuration: Int?, action: Int) = lock.write {
        writableDatabase.insertWithOnConflict(
            "pending_mark", null,
            ContentValues().apply {
                put("phone_hash", hash)
                put("call_time", System.currentTimeMillis())
                put("ring_duration", ringDuration)
                put("action", action)
            },
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    fun takePendingMarks(limit: Int = 50): List<PendingMark> = lock.write {
        val result = mutableListOf<PendingMark>()
        readableDatabase.query("pending_mark", null, null, null, null, null, "call_time ASC", limit.toString())
            .use { c ->
                while (c.moveToNext()) {
                    result.add(
                        PendingMark(
                            phoneHash = c.getString(c.getColumnIndexOrThrow("phone_hash")),
                            callTime = c.getLong(c.getColumnIndexOrThrow("call_time")),
                            ringDuration = c.getInt(c.getColumnIndexOrThrow("ring_duration")),
                            action = c.getInt(c.getColumnIndexOrThrow("action"))
                        )
                    )
                }
            }
        writableDatabase.delete("pending_mark", null, null)
        result
    }

    // ---------------------------------------------------------------- 上传队列

    fun enqueueUpload(
        hash: String, callTime: Long, ringDuration: Int?, action: Int, decisionSource: Int
    ) = lock.write {
        writableDatabase.insert(
            "upload_queue", null,
            ContentValues().apply {
                put("phone_hash", hash)
                put("call_time", callTime)
                put("ring_duration", ringDuration)
                put("action", action)
                put("decision_source", decisionSource)
            }
        )
    }

    fun takeUploadBatch(limit: Int = 200): List<UploadItem> = lock.read {
        val result = mutableListOf<UploadItem>()
        readableDatabase.query("upload_queue", null, null, null, null, null, "id ASC", limit.toString())
            .use { c ->
                while (c.moveToNext()) {
                    result.add(
                        UploadItem(
                            id = c.getLong(c.getColumnIndexOrThrow("id")),
                            phoneHash = c.getString(c.getColumnIndexOrThrow("phone_hash")),
                            callTime = c.getLong(c.getColumnIndexOrThrow("call_time")),
                            ringDuration = c.getInt(c.getColumnIndexOrThrow("ring_duration")),
                            action = c.getInt(c.getColumnIndexOrThrow("action")),
                            decisionSource = c.getInt(c.getColumnIndexOrThrow("decision_source"))
                        )
                    )
                }
            }
        result
    }

    fun deleteUploaded(ids: List<Long>): Int = lock.write {
        if (ids.isEmpty()) return@write 0
        val placeholders = ids.joinToString(",") { "?" }
        writableDatabase.delete(
            "upload_queue", "id IN ($placeholders)",
            ids.map { it.toString() }.toTypedArray()
        )
    }

    fun pendingUploadCount(): Int = lock.read {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM upload_queue", null)
            .use { if (it.moveToFirst()) it.getInt(0) else 0 }
    }

    data class GrayRule(
        val prefix: String,
        val patternType: Int,
        val confidence: Double,
        val version: Int = 0
    )

    data class PendingMark(
        val phoneHash: String,
        val callTime: Long,
        val ringDuration: Int,
        val action: Int
    )

    data class UploadItem(
        val id: Long,
        val phoneHash: String,
        val callTime: Long,
        val ringDuration: Int,
        val action: Int,
        val decisionSource: Int
    )
}
