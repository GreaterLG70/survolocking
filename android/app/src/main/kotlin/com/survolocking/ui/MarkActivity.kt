package com.survolocking.ui

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.survolocking.data.Constants
import com.survolocking.data.RuleDatabase
import com.survolocking.databinding.ActivityMarkBinding
import com.survolocking.net.ApiClient
import com.survolocking.R
import kotlinx.coroutines.launch

/**
 * 通话后标记界面。
 *
 * 用户标记是 DeepSeek 分析与家庭聚合的核心输入，
 * 因此这个界面要尽可能轻：三个按钮，一次点击完成。
 */
class MarkActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMarkBinding
    private lateinit var api: ApiClient
    private lateinit var db: RuleDatabase

    private var phoneHash: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMarkBinding.inflate(layoutInflater)
        setContentView(binding.root)

        api = ApiClient(this)
        db = RuleDatabase.getInstance(this)

        phoneHash = intent.getStringExtra(EXTRA_PHONE_HASH).orEmpty()
        val ringSeconds = intent.getIntExtra(EXTRA_RING_SECONDS, -1)

        if (phoneHash.isBlank()) {
            finish()
            return
        }

        if (ringSeconds >= 0) {
            binding.tvRingInfo.text = getString(R.string.mark_ring_info, ringSeconds)
        }

        binding.btnMarkSpam.setOnClickListener { submit(Constants.MARK_SPAM) }
        binding.btnMarkCustomer.setOnClickListener { submit(Constants.MARK_CUSTOMER) }
        binding.btnMarkUnknown.setOnClickListener { submit(Constants.MARK_UNKNOWN) }
    }

    private fun submit(userMark: Int) {
        lifecycleScope.launch {
            when (userMark) {
                Constants.MARK_SPAM -> db.addBlacklist(phoneHash, Constants.SOURCE_USER)
                Constants.MARK_CUSTOMER -> db.addWhitelist(phoneHash, Constants.SOURCE_USER)
            }
            if (api.isLoggedIn) {
                runCatching { api.markCall(phoneHash, userMark) }
            }
            val msg = when (userMark) {
                Constants.MARK_SPAM -> R.string.marked_spam
                Constants.MARK_CUSTOMER -> R.string.marked_customer
                else -> R.string.marked_unknown
            }
            Toast.makeText(this@MarkActivity, msg, Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    companion object {
        const val EXTRA_PHONE_HASH = "extra_phone_hash"
        const val EXTRA_RING_SECONDS = "extra_ring_seconds"
        const val EXTRA_USER_MARK = "extra_user_mark"
    }
}
