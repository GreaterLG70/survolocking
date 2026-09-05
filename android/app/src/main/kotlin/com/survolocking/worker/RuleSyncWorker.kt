package com.survolocking.worker

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.survolocking.data.Constants
import com.survolocking.data.RuleDatabase
import com.survolocking.net.ApiClient
import java.util.concurrent.TimeUnit

/**
 * 规则包增量同步。
 *
 * 仅在已登录时工作；同步到的号段规则写入 graylist，
 * 号码级黑白名单分别写入 blacklist / whitelist。
 */
class RuleSyncWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val api = ApiClient(applicationContext)
        if (!api.isLoggedIn) return Result.success()

        val db = RuleDatabase.getInstance(applicationContext)
        val prefs = applicationContext.getSharedPreferences(
            Constants.PREF_NAME, Context.MODE_PRIVATE
        )
        val since = prefs.getInt(Constants.KEY_RULE_VERSION, 0)

        return try {
            val result = api.syncRules(since)

            // 号段/前缀规则：全量替换，保证与服务端一致
            val grayRules = result.rules
                .filter { it.ruleType == 2 }
                .map {
                    RuleDatabase.GrayRule(
                        prefix = it.pattern,
                        patternType = it.ruleType,
                        confidence = it.confidence,
                        version = it.version
                    )
                }
            if (grayRules.isNotEmpty()) db.replaceGrayRules(grayRules)

            // 号码级规则：hash:xxx 形式
            result.rules.filter { it.ruleType == 1 && it.pattern.startsWith("hash:") }
                .forEach { db.addBlacklist(it.pattern.removePrefix("hash:"), it.source) }

            result.rules.filter { it.ruleType == 4 && it.pattern.startsWith("hash:") }
                .forEach { db.addWhitelist(it.pattern.removePrefix("hash:"), it.source) }

            prefs.edit().putInt(Constants.KEY_RULE_VERSION, result.version).apply()
            Result.success()
        } catch (e: Exception) {
            // 网络类失败重试，其它错误直接放弃本次同步
            if (e is java.io.IOException) Result.retry() else Result.success()
        }
    }

    companion object {
        private const val WORK_NAME = "survolocking_rule_sync"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val request = PeriodicWorkRequestBuilder<RuleSyncWorker>(
                Constants.RULE_SYNC_INTERVAL_HOURS, TimeUnit.HOURS
            ).setConstraints(constraints).build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request
            )
        }
    }
}
