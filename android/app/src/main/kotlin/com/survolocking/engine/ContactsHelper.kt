package com.survolocking.engine

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.ContactsContract
import androidx.core.content.ContextCompat
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 通讯录白名单。
 *
 * 设计原则：宁可漏接，不可误拦。
 * 通讯录联系人拥有最高优先级，任何规则都不能覆盖。
 *
 * 性能：首次使用时把全部联系人号码哈希载入内存 Set，
 * 之后每次查询 O(1)，保证在 50ms 预算内完成。
 */
class ContactsHelper(private val context: Context) {

    @Volatile
    private var hashSet: Set<String> = emptySet()

    @Volatile
    private var lastLoadMs: Long = 0

    private val loading = AtomicBoolean(false)

    private val cache = object : LinkedHashMap<String, Boolean>(256, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Boolean>?): Boolean =
            size > 512
    }

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) ==
            PackageManager.PERMISSION_GRANTED

    /** 加载/刷新通讯录哈希集合。建议在 WorkManager 中定期调用。 */
    @Synchronized
    fun refresh() {
        if (!hasPermission()) return
        if (!loading.compareAndSet(false, true)) return
        try {
            val set = HashSet<String>(1024)
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(ContactsContract.CommonDataKinds.Phone.NUMBER),
                null, null, null
            )?.use { c: Cursor ->
                val idx = c.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                while (c.moveToNext()) {
                    val raw = c.getString(idx) ?: continue
                    val normalized = PhoneUtils.normalize(raw)
                    if (normalized.length >= 6) set.add(PhoneUtils.hash(normalized))
                }
            }
            hashSet = set
            lastLoadMs = System.currentTimeMillis()
            synchronized(cache) { cache.clear() }
        } finally {
            loading.set(false)
        }
    }

    fun isContact(phoneHash: String): Boolean {
        val now = System.currentTimeMillis()
        if (hashSet.isEmpty() || now - lastLoadMs > CACHE_TTL_MS) {
            refresh()
        }
        synchronized(cache) {
            cache[phoneHash]?.let { return it }
        }
        val hit = hashSet.contains(phoneHash)
        synchronized(cache) { cache[phoneHash] = hit }
        return hit
    }

    companion object {
        /** 通讯录哈希集合的存活时间，超时后下次查询触发刷新 */
        private const val CACHE_TTL_MS = 10 * 60 * 1000L
    }
}
