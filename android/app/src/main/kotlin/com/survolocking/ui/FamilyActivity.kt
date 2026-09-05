package com.survolocking.ui

import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.survolocking.databinding.ActivityFamilyBinding
import com.survolocking.net.ApiClient
import com.survolocking.R
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * 家庭组界面：创建/邀请/加入/成员管理。
 *
 * 数据共享范围严格限制在家庭组内，退出即停止共享。
 */
class FamilyActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFamilyBinding
    private lateinit var api: ApiClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFamilyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        api = ApiClient(this)

        if (!api.isLoggedIn) {
            Toast.makeText(this, R.string.login_required, Toast.LENGTH_LONG).show()
            finish()
            return
        }

        binding.btnCreateFamily.setOnClickListener { createFamily() }
        binding.btnInvite.setOnClickListener { inviteMember() }
        binding.btnLeave.setOnClickListener { leaveFamily() }
        binding.btnRefresh.setOnClickListener { loadMembers() }

        loadMembers()
    }

    private fun createFamily() {
        val name = binding.etFamilyName.text.toString().trim()
        if (name.isBlank()) {
            Toast.makeText(this, R.string.family_name_required, Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            val result = runCatching { api.createFamily(name) }
            if (result.isSuccess) {
                Toast.makeText(this@FamilyActivity, R.string.family_created, Toast.LENGTH_SHORT).show()
                loadMembers()
            } else {
                Toast.makeText(
                    this@FamilyActivity,
                    result.exceptionOrNull()?.message ?: getString(R.string.operation_failed),
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun inviteMember() {
        val phone = binding.etInvitePhone.text.toString().trim()
        val familyId = currentFamilyId
        if (phone.length < 6) {
            Toast.makeText(this, R.string.phone_invalid, Toast.LENGTH_SHORT).show()
            return
        }
        if (familyId == null) {
            Toast.makeText(this, R.string.family_required, Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            val result = runCatching { api.inviteMember(familyId, phone) }
            Toast.makeText(
                this@FamilyActivity,
                getString(if (result.isSuccess) R.string.invitation_sent else R.string.operation_failed),
                Toast.LENGTH_SHORT
            ).show()
        }
    }

    private fun leaveFamily() {
        lifecycleScope.launch {
            // 退出接口为 POST /api/family/leave
            val result = runCatching {
                api.leaveFamily()
            }
            if (result.isSuccess) {
                Toast.makeText(this@FamilyActivity, R.string.left_family, Toast.LENGTH_SHORT).show()
                finish()
            } else {
                Toast.makeText(this@FamilyActivity, R.string.operation_failed, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private var currentFamilyId: Long? = null

    private fun loadMembers() {
        lifecycleScope.launch {
            val data = runCatching { api.familyMembers() }.getOrNull()
            if (data == null) {
                binding.tvMembers.text = getString(R.string.no_family)
                binding.memberCard.visibility = View.GONE
                currentFamilyId = null
                return@launch
            }

            val familyId = data.optLong("family_id", -1L)
            currentFamilyId = if (familyId > 0) familyId else null

            if (familyId <= 0) {
                binding.tvMembers.text = getString(R.string.no_family)
                binding.memberCard.visibility = View.GONE
                return@launch
            }

            binding.memberCard.visibility = View.VISIBLE
            binding.tvFamilyName.text = data.optString("family_name", "")

            val arr = data.optJSONArray("members")
            val sb = StringBuilder()
            if (arr != null) {
                for (i in 0 until arr.length()) {
                    val m: JSONObject = arr.getJSONObject(i)
                    val name = m.optString("nickname").ifBlank { m.optString("phone_masked") }
                    val creator = if (m.optBoolean("is_creator")) "（创建者）" else ""
                    sb.append("• ").append(name).append(creator).append('\n')
                }
            }
            binding.tvMembers.text = sb.toString().ifBlank { getString(R.string.no_members) }
        }
    }
}
