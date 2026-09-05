package com.survolocking.engine

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.survolocking.data.Constants
import com.survolocking.data.RuleDatabase
import com.survolocking.net.ApiClient
import com.survolocking.ui.MarkActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 通知内「骚扰 / 客户」快捷标记按钮的接收器。
 *
 * 用户直接在通知上完成标记，无需打开界面，提升标记率。
 */
class MarkReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val phoneHash = intent.getStringExtra(MarkActivity.EXTRA_PHONE_HASH) ?: return
        val userMark = intent.getIntExtra(MarkActivity.EXTRA_USER_MARK, Constants.MARK_UNKNOWN)

        val pending = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val db = RuleDatabase.getInstance(context)
        val api = ApiClient(context)

        scope.launch {
            try {
                when (userMark) {
                    Constants.MARK_SPAM -> db.addBlacklist(phoneHash, Constants.SOURCE_USER)
                    Constants.MARK_CUSTOMER -> db.addWhitelist(phoneHash, Constants.SOURCE_USER)
                }
                if (api.isLoggedIn) {
                    runCatching { api.markCall(phoneHash, userMark) }
                }
            } finally {
                pending.finish()
            }
        }
    }
}
