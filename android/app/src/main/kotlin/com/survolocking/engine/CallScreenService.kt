package com.survolocking.engine

import android.os.Build
import android.telecom.Call
import android.telecom.CallScreeningService
import com.survolocking.data.RuleDatabase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

/**
 * 来电拦截服务。
 *
 * 用户必须在「设置 → 应用 → 默认应用 → 来电识别与骚扰拦截」中
 * 将本应用设为默认拦截应用后，本服务才会被系统调用。
 *
 * 系统要求：onScreenCall 必须在 5 秒内返回，超时将自动放行。
 * 本服务内部通过 DecisionEngine 控制在 200ms 内完成决策。
 */
class CallScreenService : CallScreeningService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val engine by lazy { DecisionEngine(this) }
    private val db by lazy { RuleDatabase.getInstance(this) }

    override fun onScreenCall(callDetails: Call.Details) {
        val rawNumber = callDetails.handle?.schemeSpecificPart
        if (rawNumber.isNullOrBlank()) {
            // 未知号码（如隐藏号码）不做处理，交给系统
            respondToCall(callDetails, allowResponse())
            return
        }

        // runBlocking 是有意为之：系统回调需要同步返回决策。
        // DecisionEngine 内部严格限时 200ms，不会造成 ANR。
        val decision = runCatching {
            runBlocking { engine.decide(rawNumber) }
        }.getOrElse { e ->
            // 决策链路任何异常都必须放行，绝不能让用户接不到电话
            android.util.Log.e(TAG, "决策异常，安全放行", e)
            DecisionEngine.Decision(
                DecisionEngine.Action.ALLOW, 4, DecisionEngine.Source.FALLBACK,
                "决策异常，安全放行", 0
            )
        }

        android.util.Log.d(
            TAG, "决策 ${decision.action} 层=${decision.layer} 耗时=${decision.costMs}ms 原因=${decision.reason}"
        )

        respondToCall(callDetails, buildResponse(decision))

        // 异步记录行为与日志队列，不占用决策时间
        val hash = PhoneUtils.hashOf(rawNumber)
        scope.launch {
            val actionCode = when (decision.action) {
                DecisionEngine.Action.BLOCK -> ACTION_BLOCK
                else -> ACTION_ALLOW
            }
            db.recordBehavior(hash, null, actionCode)
            db.enqueueUpload(
                hash = hash,
                callTime = System.currentTimeMillis(),
                ringDuration = null,
                action = actionCode,
                decisionSource = decision.source.code
            )
            // 未拦截的通话结束后交由用户标记，反哺分析与家庭共享库
            if (decision.action != DecisionEngine.Action.BLOCK) {
                db.addPendingMark(hash, null, actionCode)
            }
        }
    }

    private fun buildResponse(decision: DecisionEngine.Decision): CallResponse =
        when (decision.action) {
            DecisionEngine.Action.BLOCK -> CallResponse.Builder()
                .setDisallowCall(true)
                .setRejectCall(true)
                .setSkipCallLog(false) // 保留通话记录，用户可事后核对是否被误拦
                .setSkipNotification(false)
                .build()

            DecisionEngine.Action.SILENCE -> CallResponse.Builder()
                .setDisallowCall(false)
                .apply {
                    // setSilenceCall 自 API 33 起可用
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        setSilenceCall(true)
                    }
                }
                .build()

            DecisionEngine.Action.WARN -> CallResponse.Builder()
                .setDisallowCall(false)
                .build()

            DecisionEngine.Action.ALLOW -> allowResponse()
        }

    private fun allowResponse(): CallResponse =
        CallResponse.Builder().setDisallowCall(false).build()

    companion object {
        private const val TAG = "CallScreenService"

        /** 与服务端 intercept_logs.action 保持一致：1拦截 2放行 3用户拒接 */
        const val ACTION_BLOCK = 1
        const val ACTION_ALLOW = 2
    }
}
