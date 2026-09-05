package com.survolocking

import android.app.Application
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.appcompat.app.AppCompatDelegate
import com.survolocking.engine.CallStateMonitorService
import com.survolocking.engine.NotificationHelper
import com.survolocking.net.ApiClient
import com.survolocking.worker.LogUploadWorker
import com.survolocking.worker.RuleSyncWorker

/**
 * 应用入口。
 *
 * 启动顺序：
 * 1) 建立通知渠道（Android 8+ 必需，否则通知不显示）；
 * 2) 拉起常驻前台服务，提高来电拦截的进程存活率；
 * 3) 注册周期任务：规则同步、日志上传。
 */
class SurvolockingApp : Application() {

    override fun onCreate() {
        super.onCreate()

        // 强制暗色模式，承载磨砂玻璃视觉
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)

        // 记录首次启动时间，用于"已守护天数"统计
        ApiClient(this).ensureFirstLaunchRecorded()

        NotificationHelper.createChannels(this)

        runCatching { startMonitorService() }
            .onFailure { Log.w(TAG, "常驻服务启动失败", it) }

        RuleSyncWorker.schedule(this)
        LogUploadWorker.schedule(this)

        Log.i(TAG, "Survolocking 初始化完成")
    }

    private fun startMonitorService() {
        val intent = Intent(this, CallStateMonitorService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    companion object {
        private const val TAG = "SurvolockingApp"
    }
}
