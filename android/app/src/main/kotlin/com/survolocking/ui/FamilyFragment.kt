package com.survolocking.ui

import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.survolocking.R
import com.survolocking.data.RuleDatabase
import com.survolocking.databinding.FragmentFamilyBinding
import com.survolocking.net.ApiClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * 家庭页（卡片化 UI）。
 *
 * 修复点：
 * 1) 邀请闭环：服务端补了 GET /api/family/invitations，这里拉取并渲染
 *    「收到的邀请」卡片，点接受/拒绝即可完成闭环（此前受邀方拿不到
 *    invitation_id，邀请永远卡在待处理）；
 * 2) 「我」的识别：服务端成员项返回 user_id / phone_masked / is_creator，
 *    此前客户端却读不存在的 blocked_count / is_self 字段导致全部显示 0，
 *    现在按 user_id 与本地缓存比对；
 * 3) 昵称兜底：服务端已兜底「用户+手机后4位」，客户端再兜一层「成员」。
 */
class FamilyFragment : Fragment() {

    private var _binding: FragmentFamilyBinding? = null
    private val binding get() = _binding!!
    private lateinit var api: ApiClient
    private lateinit var db: RuleDatabase

    private var currentFamilyId: Long? = null
    private var refreshJob: Job? = null

    data class Member(
        val userId: Long,
        val name: String,
        val phoneMasked: String,
        val isSelf: Boolean,
        val isCreator: Boolean
    )

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        _binding = FragmentFamilyBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        api = ApiClient(requireContext())
        db = RuleDatabase.getInstance(requireContext())

        binding.rvMembers.layoutManager = LinearLayoutManager(requireContext())

        binding.btnCreateFamily.pressBounce()
        binding.btnInvite.pressBounce()
        binding.btnLeave.pressBounce()
        binding.btnFamilyLogin.pressBounce()

        binding.btnCreateFamily.setOnClickListener { createFamily() }
        binding.btnInvite.setOnClickListener { inviteMember() }
        binding.btnLeave.setOnClickListener { confirmLeave() }
        binding.btnFamilyLogin.setOnClickListener { openLogin() }

        refresh()
    }

    override fun onResume() {
        super.onResume()
        // 进入家庭页时立即刷新一次
        if (::api.isInitialized) refresh()
        // 接着每 7 秒轮询一次，离开页面（onPause）即停。
        // 多人协作场景（家庭成员邀请/接受会触发对方列表变更）下，避免用户手动切回再下拉刷新。
        refreshJob?.cancel()
        refreshJob = viewLifecycleOwner.lifecycleScope.launch {
            while (isActive) {
                delay(FAMILY_REFRESH_INTERVAL_MS)
                if (::api.isInitialized) runCatching { refresh() }
            }
        }
    }

    override fun onPause() {
        refreshJob?.cancel()
        refreshJob = null
        super.onPause()
    }

    private fun openLogin() {
        startActivity(android.content.Intent(requireContext(), LoginActivity::class.java))
    }

    private fun refresh() {
        val logged = api.isLoggedIn
        binding.cardLoginHint.visibility = if (logged) View.GONE else View.VISIBLE
        binding.btnCreateFamily.visibility = if (logged) View.VISIBLE else View.GONE
        binding.btnInvite.visibility = if (logged) View.VISIBLE else View.GONE
        binding.cardInvites.visibility = View.GONE

        if (!logged) {
            binding.tvFamilyTotal.text = "0"
            binding.tvMyBlocked.text = "0"
            binding.tvFamilyName.text = ""
            binding.rvMembers.adapter = null
            binding.tvFamilyEmpty.visibility = View.VISIBLE
            binding.btnLeave.visibility = View.GONE
            return
        }

        lifecycleScope.launch {
            runCatching { loadInvitations() }
            runCatching { loadFamily() }
        }
    }

    /** 拉取我收到的待处理邀请，动态渲染邀请卡片 */
    private suspend fun loadInvitations() {
        val arr = api.myInvitations() ?: return
        if (arr.length() == 0) return
        activity?.runOnUiThread {
            val box = binding.llInvites
            box.removeAllViews()
            for (i in 0 until arr.length()) {
                val inv = arr.optJSONObject(i) ?: continue
                val invId = inv.optLong("invitation_id", -1L)
                if (invId <= 0) continue
                val familyName = inv.optString("family_name", "")
                val inviter = inv.optString("inviter_nickname", "成员")
                val item = layoutInflater.inflate(R.layout.item_invitation, box, false)
                item.findViewById<TextView>(R.id.tv_invite_desc).text =
                    getString(R.string.family_invite_from, inviter, familyName)
                item.findViewById<View>(R.id.btn_accept).setOnClickListener { acceptInvite(invId) }
                item.findViewById<View>(R.id.btn_reject).setOnClickListener { rejectInvite(invId) }
                box.addView(item)
            }
            binding.cardInvites.visibility = View.VISIBLE
        }
    }

    private fun acceptInvite(invitationId: Long) {
        lifecycleScope.launch {
            val ok = runCatching { api.acceptInvitation(invitationId) }.isSuccess
            activity?.runOnUiThread {
                Toast.makeText(
                    requireContext(),
                    if (ok) R.string.family_invite_accepted else R.string.operation_failed,
                    Toast.LENGTH_SHORT
                ).show()
                if (ok) refresh()
            }
        }
    }

    private fun rejectInvite(invitationId: Long) {
        lifecycleScope.launch {
            val ok = runCatching { api.rejectInvitation(invitationId) }.isSuccess
            activity?.runOnUiThread {
                Toast.makeText(
                    requireContext(),
                    if (ok) R.string.family_invite_rejected else R.string.operation_failed,
                    Toast.LENGTH_SHORT
                ).show()
                if (ok) refresh()
            }
        }
    }

    private suspend fun loadFamily() {
        val data: JSONObject = api.familyMembers() ?: return
        val members = data.optJSONArray("members")
        currentFamilyId = data.optLong("family_id", -1L).let { if (it == -1L) null else it }

        val list = mutableListOf<Member>()
        val myId = api.userId
        if (members != null) {
            for (i in 0 until members.length()) {
                val m = members.getJSONObject(i)
                val uid = m.optLong("user_id", -1L)
                list.add(
                    Member(
                        userId = uid,
                        name = m.optString("nickname")
                            .takeIf { it.isNotBlank() && it != "null" }
                            ?: getString(R.string.no_members),
                        phoneMasked = m.optString("phone_masked", ""),
                        isSelf = uid == myId,
                        isCreator = m.optBoolean("is_creator", false)
                    )
                )
            }
        }

        activity?.runOnUiThread {
            val inFamily = currentFamilyId != null
            binding.tvFamilyTotal.text = list.size.toString()
            binding.tvMyBlocked.text = db.blockedCount().toString()
            binding.tvFamilyName.text = data.optString("family_name", "").ifBlank { "" }
            binding.btnLeave.visibility = if (inFamily) View.VISIBLE else View.GONE
            if (list.isEmpty()) {
                binding.tvFamilyEmpty.visibility = View.VISIBLE
                binding.rvMembers.adapter = null
            } else {
                binding.tvFamilyEmpty.visibility = View.GONE
                binding.rvMembers.adapter = MemberAdapter(list)
            }
        }
    }

    private fun createFamily() {
        val input = EditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            hint = getString(R.string.family_name_hint)
        }
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.btn_create_family)
            .setView(input)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val name = input.text.toString().trim()
                if (name.isEmpty()) {
                    Toast.makeText(requireContext(), R.string.family_name_required, Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    val ok = runCatching { api.createFamily(name) }.isSuccess
                    activity?.runOnUiThread {
                        Toast.makeText(requireContext(),
                            if (ok) R.string.family_created else R.string.operation_failed,
                            Toast.LENGTH_SHORT).show()
                        if (ok) refresh()
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun inviteMember() {
        val fid = currentFamilyId
        if (fid == null) {
            Toast.makeText(requireContext(), R.string.family_required, Toast.LENGTH_SHORT).show()
            return
        }
        val input = EditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_PHONE
            hint = getString(R.string.invite_phone_hint)
        }
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.btn_invite)
            .setView(input)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val phone = input.text.toString().trim()
                if (phone.length < 6) {
                    Toast.makeText(requireContext(), R.string.phone_invalid, Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    // 失败原因（未注册 / 已在别组）由服务端 fail() 返回，读异常 message 原话展示
                    val err = runCatching { api.inviteMember(fid, phone) }.exceptionOrNull()
                    activity?.runOnUiThread {
                        if (err == null) {
                            Toast.makeText(requireContext(), R.string.invitation_sent, Toast.LENGTH_SHORT).show()
                        } else {
                            Toast.makeText(
                                requireContext(),
                                (err as? ApiClient.ApiException)?.message
                                    ?: getString(R.string.operation_failed),
                                Toast.LENGTH_LONG
                            ).show()
                        }
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun confirmLeave() {
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.btn_leave)
            .setMessage(R.string.family_leave_confirm)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                lifecycleScope.launch {
                    val ok = runCatching { api.leaveFamily() }.isSuccess
                    activity?.runOnUiThread {
                        Toast.makeText(requireContext(),
                            if (ok) R.string.left_family else R.string.operation_failed,
                            Toast.LENGTH_SHORT).show()
                        if (ok) refresh()
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private class MemberAdapter(private val items: List<Member>) :
        RecyclerView.Adapter<MemberAdapter.VH>() {
        class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
            val name: TextView = itemView.findViewById(R.id.tv_name)
            val tag: TextView = itemView.findViewById(R.id.tv_tag)
            val sub: TextView = itemView.findViewById(R.id.tv_sub)
            val avatarInitial: TextView = itemView.findViewById(R.id.tv_avatar_initial)
        }
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val v = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_member, parent, false)
            return VH(v)
        }
        override fun getItemCount() = items.size
        override fun onBindViewHolder(holder: VH, position: Int) {
            val m = items[position]
            holder.name.text = m.name
            holder.avatarInitial.text = m.name.firstOrNull()?.toString()?.uppercase() ?: "?"
            val ctx = holder.itemView.context
            holder.tag.visibility = View.VISIBLE
            holder.tag.text = when {
                m.isCreator -> ctx.getString(R.string.family_creator_tag)
                m.isSelf -> ctx.getString(R.string.family_member_you)
                else -> ""
            }
            if (holder.tag.text.isNullOrBlank()) holder.tag.visibility = View.GONE
            holder.sub.text = m.phoneMasked
        }
    }

    override fun onDestroyView() {
        refreshJob?.cancel()
        refreshJob = null
        super.onDestroyView()
        _binding = null
    }

    companion object {
        // 家庭页热刷新间隔：7 秒，平衡实时性与电量。
        // 邀请/接受/退出都会立刻触发服务端状态变化，间隔太长体验差，太短浪费电。
        private const val FAMILY_REFRESH_INTERVAL_MS = 7_000L
    }
}
