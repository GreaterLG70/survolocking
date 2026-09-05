package com.survolocking.engine

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.graphics.drawable.toBitmap
import com.survolocking.R
import com.survolocking.data.Constants
import com.survolocking.ui.MarkActivity

/**
 * 通知辅助类。
 *
 * 采用通知而非直接弹窗：Android 10+ 严格限制后台启动 Activity，
 * 通知是通话结束后引导用户标记的唯一可靠方式。
 */
object NotificationHelper {

    const val CHANNEL_INTERCEPT = "survolocking_intercept"
    const val CHANNEL_MARK = "survolocking_mark"
    const val CHANNEL_SERVICE = "survolocking_service"

    const val ID_FOREGROUND = 1001
    const val ID_MARK = 2001

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)

        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_INTERCEPT, "拦截提醒", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "被拦截来电的记录提醒" }
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_MARK, "通话标记", NotificationManager.IMPORTANCE_DEFAULT
            ).apply { description = "通话结束后标记号码类型" }
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_SERVICE, "拦截服务", NotificationManager.IMPORTANCE_MIN
            ).apply { description = "保持来电拦截服务运行" }
        )
    }

    private fun canNotify(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
    }

    /** 通话结束后引导用户标记号码 */
    fun showMarkPrompt(context: Context, phoneHash: String, ringSeconds: Int?) {
        if (!canNotify(context)) return

        val intent = Intent(context, MarkActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(MarkActivity.EXTRA_PHONE_HASH, phoneHash)
            putExtra(MarkActivity.EXTRA_RING_SECONDS, ringSeconds ?: -1)
        }
        val pi = PendingIntent.getActivity(
            context, phoneHash.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val large = ContextCompat.getDrawable(context, R.drawable.ic_stat_notify)!!.toBitmap(192, 192)
        val notification = NotificationCompat.Builder(context, CHANNEL_MARK)
            .setSmallIcon(R.drawable.ic_stat_notify)
            .setLargeIcon(large)
            .setColor(0xFF2E7D32.toInt())
            .setContentTitle("刚才的通话是？")
            .setContentText("点击标记为骚扰、客户或不确定，帮助提升识别准确率")
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .addAction(
                R.drawable.ic_stat_notify, "骚扰",
                markActionIntent(context, phoneHash, Constants.MARK_SPAM)
            )
            .addAction(
                R.drawable.ic_stat_notify, "客户",
                markActionIntent(context, phoneHash, Constants.MARK_CUSTOMER)
            )
            .build()

        val id = ID_MARK + (phoneHash.hashCode() and 0xFF)
        NotificationManagerCompat.from(context).notify(id, notification)
    }

    private fun markActionIntent(context: Context, phoneHash: String, mark: Int): PendingIntent {
        val intent = Intent(context, MarkReceiver::class.java).apply {
            putExtra(MarkActivity.EXTRA_PHONE_HASH, phoneHash)
            putExtra(MarkActivity.EXTRA_USER_MARK, mark)
        }
        return PendingIntent.getBroadcast(
            context, (phoneHash + mark).hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /** 长时间响铃的未接来电，提示用户回拨 */
    fun showMissedCallHint(context: Context, number: String, ringSeconds: Int) {
        if (!canNotify(context)) return
        val masked = maskNumber(number)
        val notification = NotificationCompat.Builder(context, CHANNEL_MARK)
            .setSmallIcon(R.drawable.ic_stat_notify)
            .setColor(0xFF2E7D32.toInt())
            .setContentTitle("疑似重要来电")
            .setContentText("$masked 响铃 ${ringSeconds}秒未接通，建议回拨确认")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context)
            .notify(ID_MARK + 900, notification)
    }

    /** 前台服务常驻通知。点通知直接回到 App，关闭通知即停服务（带二次确认）。 */
    fun foregroundNotification(context: Context): Notification {
        val openIntent = Intent(context, com.survolocking.ui.MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openPi = PendingIntent.getActivity(
            context, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val large = ContextCompat.getDrawable(context, R.drawable.ic_stat_notify)!!.toBitmap(192, 192)
        return NotificationCompat.Builder(context, CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_stat_notify)
            .setLargeIcon(large)
            .setColor(0xFF2E7D32.toInt())
            .setContentTitle(context.getString(R.string.notif_service_title))
            .setContentText(context.getString(R.string.notif_service_text))
            .setTicker(context.getString(R.string.notif_service_title))
            .setContentIntent(openPi)
            .setOngoing(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    private fun maskNumber(number: String): String {
        val n = PhoneUtils.normalize(number)
        return if (n.length >= 7) "${n.take(3)}****${n.takeLast(4)}" else "未知号码"
    }
}
