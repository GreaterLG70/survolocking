package com.survolocking.engine

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import androidx.lifecycle.LifecycleService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 常驻前台服务。
 *
 * 作用：
 * 1) 保持进程活跃，避免来电到达时因进程被杀而漏拦；
 * 2) 定期刷新通讯录哈希集合与清理过期行为日志。
 *
 * 保活三板斧（针对 OEM 后台清理导致「用一会儿就自动关闭」）：
 * - START_STICKY：进程被杀后系统会择机重建服务；
 * - onTaskRemoved：用户划掉任务时 2 秒后用 AlarmManager 拉起自己，
 *   覆盖「划任务即杀进程」的国产 ROM 行为；
 * - MainActivity.onResume 兜底重启：只要用户回到 App，服务必然复活。
 *
 * CallScreenService 由系统在来电时主动绑定拉起，本服务只是提高其存活率。
 */
class CallStateMonitorService : LifecycleService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        NotificationHelper.createChannels(this)
        startForegroundSafely()
        startMaintenanceLoop()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        // 被系统杀掉后尽快重建，保证前台通知与维护循环不中断
        return START_STICKY
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // 用户从最近任务划掉 App：多数国产 ROM 会顺带杀进程。
        // 用一次性 Alarm 在 2 秒后重新拉起本服务（应用进程也会随之复活）。
        scheduleRestart()
        super.onTaskRemoved(rootIntent)
    }

    private fun scheduleRestart() {
        val am = getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        val pi = PendingIntent.getForegroundService(
            this, 0,
            Intent(this, CallStateMonitorService::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        runCatching {
            am.setExactAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME,
                SystemClock.elapsedRealtime() + 2_000L,
                pi
            )
        }
    }

    private fun startForegroundSafely() {
        val notification = NotificationHelper.foregroundNotification(this)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NotificationHelper.ID_FOREGROUND,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
                )
            } else {
                startForeground(NotificationHelper.ID_FOREGROUND, notification)
            }
        } catch (e: Exception) {
            // 前台服务启动失败（如 Android 14+ 缺 FOREGROUND_SERVICE_PHONE_CALL 权限）：
            // 必须 stopSelf，否则系统会因「startForegroundService 已调用却未 startForeground」
            // 在数秒后抛出 RuntimeException 杀掉进程，表现为「打开一会儿就闪退」。
            // 真正的拦截由 CallScreenService（系统来电时拉起）负责，本服务仅为辅助存活。
            android.util.Log.w(TAG, "前台服务启动失败，停止辅助服务", e)
            stopSelf()
        }
    }

    private fun startMaintenanceLoop() {
        scope.launch {
            val engine = DecisionEngine(this@CallStateMonitorService)
            while (isActive) {
                runCatching { engine.refreshAll() }
                delay(MAINTENANCE_INTERVAL_MS)
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "CallStateMonitorService"
        private const val MAINTENANCE_INTERVAL_MS = 30 * 60 * 1000L
    }
}
