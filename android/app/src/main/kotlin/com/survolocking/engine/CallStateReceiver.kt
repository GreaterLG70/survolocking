package com.survolocking.engine

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import android.util.Log
import com.survolocking.data.Constants
import com.survolocking.data.RuleDatabase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 通话状态监听。
 *
 * 目的：采集「响铃时长」这一关键特征，供第3层微行为分析使用。
 * 典型骚扰特征为「响铃≤3秒且对方主动挂断」，因此必须精确记录
 * 从 RINGING 到 IDLE(未接) 的时长。
 *
 * 注意：自 Android 9 起，从广播中读取来电号码需要 READ_CALL_LOG 权限。
 */
class CallStateReceiver : BroadcastReceiver() {

    @Suppress("DEPRECATION")
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return

        val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE)
        val number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)

        val pending = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            try {
                handle(context, state, number)
            } finally {
                pending.finish()
            }
        }
    }

    private fun handle(context: Context, state: String?, number: String?) {
        val tracker = CallStateTracker.getInstance(context)
        when (state) {
            TelephonyManager.EXTRA_STATE_RINGING -> {
                if (!number.isNullOrBlank()) tracker.onRinging(number)
            }

            TelephonyManager.EXTRA_STATE_OFFHOOK -> {
                // 用户接通
                tracker.onAnswered()
            }

            TelephonyManager.EXTRA_STATE_IDLE -> {
                // 通话结束：可能是对方挂断、用户拒接或正常结束
                tracker.onIdle(context)
            }
        }
    }

    /**
     * 通话状态追踪器：单例，跨广播持有振铃起始时间。
     */
    class CallStateTracker private constructor(private val context: Context) {

        @Volatile
        private var ringingNumber: String? = null

        @Volatile
        private var ringStartMs: Long = 0

        @Volatile
        private var answered: Boolean = false

        private val db = RuleDatabase.getInstance(context)

        fun onRinging(number: String) {
            ringingNumber = number
            ringStartMs = System.currentTimeMillis()
            answered = false
            Log.d(TAG, "来电振铃开始 number=${number.take(4)}****")
        }

        fun onAnswered() {
            if (ringingNumber == null) return
            answered = true
            val ringSeconds = ((System.currentTimeMillis() - ringStartMs) / 1000).toInt()
            val hash = PhoneUtils.hashOf(ringingNumber!!)
            db.recordBehavior(hash, ringSeconds, ACTION_ANSWERED)
            Log.d(TAG, "用户接通，响铃 ${ringSeconds}s")
        }

        fun onIdle(ctx: Context) {
            val number = ringingNumber ?: return
            val ringSeconds = ((System.currentTimeMillis() - ringStartMs) / 1000).toInt()
            val hash = PhoneUtils.hashOf(number)

            if (!answered) {
                // 未接通即结束：对方主动挂断或用户拒接
                db.recordBehavior(hash, ringSeconds, ACTION_MISSED)
                db.addPendingMark(hash, ringSeconds, ACTION_MISSED)

                // 响铃超过阈值仍未接听，可能是重要来电，提示回拨
                if (ringSeconds >= Constants.RING_IMPORTANT_MIN_SEC) {
                    NotificationHelper.showMissedCallHint(ctx, number, ringSeconds)
                }
                Log.d(TAG, "未接通，响铃 ${ringSeconds}s")
            }

            ringingNumber = null
            answered = false
        }

        companion object {
            private const val TAG = "CallStateTracker"
            const val ACTION_ANSWERED = 2
            const val ACTION_MISSED = 3

            @Volatile
            private var INSTANCE: CallStateTracker? = null

            fun getInstance(context: Context): CallStateTracker =
                INSTANCE ?: synchronized(this) {
                    INSTANCE ?: CallStateTracker(context.applicationContext).also { INSTANCE = it }
                }
        }
    }

    companion object {
        private const val TAG = "CallStateReceiver"
    }
}
