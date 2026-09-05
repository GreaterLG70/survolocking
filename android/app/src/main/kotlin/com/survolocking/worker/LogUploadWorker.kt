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
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/**
 * 拦截日志批量上传。
 *
 * 设计为仅在连接可用时上传，且默认配合 Wi-Fi 约束以节省用户流量。
 * 上传成功后才从本地队列移除，失败保留，避免数据丢失。
 */
class LogUploadWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val api = ApiClient(applicationContext)
        if (!api.isLoggedIn) return Result.success()

        val db = RuleDatabase.getInstance(applicationContext)
        val batch = db.takeUploadBatch(BATCH_SIZE)
        if (batch.isEmpty()) return Result.success()

        val items = batch.map {
            ApiClient.UploadLogItem(
                phoneHash = it.phoneHash,
                callTimeIso = isoFormat(it.callTime),
                ringDuration = it.ringDuration,
                action = it.action,
                decisionSource = it.decisionSource
            )
        }

        return try {
            api.uploadLogs(items)
            db.deleteUploaded(batch.map { it.id })
            // 队列可能很长，若本批填满则继续处理下一批
            if (batch.size == BATCH_SIZE && db.pendingUploadCount() > 0) {
                Result.retry()
            } else {
                Result.success()
            }
        } catch (e: IOException) {
            Result.retry()
        } catch (e: Exception) {
            Result.success()
        }
    }

    private fun isoFormat(millis: Long): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
            timeZone = TimeZone.getDefault()
        }.format(Date(millis))

    companion object {
        private const val BATCH_SIZE = 200
        private const val WORK_NAME = "survolocking_log_upload"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED) // 仅 Wi-Fi，不消耗蜂窝流量
                .setRequiresBatteryNotLow(true)
                .build()
            val request = PeriodicWorkRequestBuilder<LogUploadWorker>(
                Constants.LOG_UPLOAD_INTERVAL_HOURS, TimeUnit.HOURS
            ).setConstraints(constraints).build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request
            )
        }
    }
}
