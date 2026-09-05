package com.survolocking.engine

import com.survolocking.data.Constants
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * 号码处理工具。
 *
 * 端云哈希契约：此处算法必须与服务端 security.hash_phone 完全一致，
 * 即 HMAC-SHA256(activeSalt, normalized)。
 *
 * 盐的来源：服务端在登录 / /me 时下发 PHONE_HASH_SALT，端侧缓存到本地
 * （activeSalt），作为哈希计算的唯一真相源；Constants.PHONE_SALT 仅作为
 * 登录前尚未拿到服务端盐时的兜底默认值。这样无需手动同步三端源码常量，
 * 避免盐不一致导致日志与规则静默失配。
 */
object PhoneUtils {

    private val NON_DIGIT_KEEP_PLUS = Regex("[^0-9+]")

    /** 当前生效的盐，默认取兜底常量，登录后被服务端下发值覆盖 */
    var activeSalt: String = Constants.PHONE_SALT
        private set

    /** 用服务端下发的盐更新，空值忽略 */
    fun setSalt(salt: String) {
        if (salt.isNotBlank()) activeSalt = salt
    }

    /** 归一化：去空格、横线、括号，保留前导 + */
    fun normalize(phone: String): String =
        NON_DIGIT_KEEP_PLUS.replace(phone, "")

    fun hash(normalizedPhone: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(activeSalt.toByteArray(), "HmacSHA256"))
        return mac.doFinal(normalizedPhone.toByteArray()).toHex()
    }

    fun hashOf(rawPhone: String): String = hash(normalize(rawPhone))

    /** 不依赖盐的裸哈希，仅用于本地临时索引，禁止上传 */
    fun sha256(input: String): String =
        MessageDigest.getInstance("SHA-256").digest(input.toByteArray()).toHex()

    private fun ByteArray.toHex(): String =
        joinToString("") { "%02x".format(it) }

    /**
     * 异常号段判定。
     * 境外来电、虚拟运营商号段、高仿号为骚扰电话高发区。
     */
    fun isSuspiciousPrefix(normalized: String): Boolean {
        val n = normalized.removePrefix("+")
        val prefixes = arrayOf(
            "00852", "00853", "00886", // 中国香港/澳门/台湾
            "001", "0060", "0065", "0081", "0082", // 常见境外诈骗来源
            "170", "171", "162", "165", "167", "174", "149" // 虚拟运营商
        )
        return prefixes.any { n.startsWith(it) }
    }
}
