package com.survolocking.ui

import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.survolocking.R
import com.survolocking.databinding.FragmentProfileBinding
import com.survolocking.net.ApiClient
import kotlinx.coroutines.launch

/**
 * 个人中心（小黑盒风格卡片化）。
 *
 * 修复点：
 * 1) 昵称为 null：服务端 /me 已兜底「用户+手机后4位」，这里再叠一层本地兜底
 *    （“守护者”），任何情况下都不会把 null 渲染出来；
 * 2) UI 粗糙：改为 用户大卡片（头像+昵称+UID+双数据标签）+ 设置项 + 一言 + 关于；
 * 3) 新增修改昵称入口（PUT /api/auth/profile）。
 */
class ProfileFragment : Fragment() {

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private lateinit var api: ApiClient

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        _binding = FragmentProfileBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        api = ApiClient(requireContext())

        binding.btnLogin.pressBounce()
        binding.btnLogout.pressBounce()
        binding.itemEditNickname.pressBounce()

        binding.btnLogin.setOnClickListener { openLogin() }
        binding.btnLogout.setOnClickListener {
            api.logout()
            refresh()
        }
        binding.itemEditNickname.setOnClickListener {
            if (api.isLoggedIn) showEditNicknameDialog() else openLogin()
        }

        refresh()
        loadHitokoto()
    }

    override fun onResume() {
        super.onResume()
        if (::api.isInitialized) {
            // 从登录页回来时拉一次最新资料（昵称可能已变化）
            if (api.isLoggedIn) {
                lifecycleScope.launch { runCatching { api.refreshProfile() } }
            }
            refresh()
        }
    }

    private fun openLogin() {
        startActivity(android.content.Intent(requireContext(), LoginActivity::class.java))
    }

    fun refresh() {
        if (api.isLoggedIn) {
            binding.btnLogin.visibility = View.GONE
            binding.btnLogout.visibility = View.VISIBLE
            // 本地兜底：即便缓存昵称意外为空也绝不显示 null
            val name = api.nickname?.takeIf { it.isNotBlank() }
                ?: getString(R.string.profile_default_name)
            binding.tvNickname.text = name
            binding.tvAvatarInitial.text = name.firstOrNull()?.toString()?.uppercase() ?: "守"
            binding.tvUid.text = getString(R.string.profile_uid) + " " + api.userId
            binding.tvUsage.text = api.usageDays.toString()
            binding.tvGuard.text = api.installDays.toString()
        } else {
            binding.btnLogin.visibility = View.VISIBLE
            binding.btnLogout.visibility = View.GONE
            binding.tvNickname.text = getString(R.string.profile_not_logged)
            binding.tvAvatarInitial.text = "?"
            binding.tvUid.text = getString(R.string.profile_uid)
            binding.tvUsage.text = "--"
            binding.tvGuard.text = "--"
        }
    }

    /** 修改昵称：AlertDialog 输入 → PUT /api/auth/profile → 本地缓存刷新 */
    private fun showEditNicknameDialog() {
        val input = EditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            hint = getString(R.string.profile_nickname_hint)
            setText(api.nickname.orEmpty())
            setSelection(text.length)
        }
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.profile_edit_nickname)
            .setView(input)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val name = input.text.toString().trim()
                if (name.isEmpty()) {
                    Toast.makeText(requireContext(), R.string.family_name_required, Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    val ok = runCatching { api.updateProfile(nickname = name) }.getOrDefault(false)
                    if (ok) {
                        Toast.makeText(requireContext(), R.string.profile_nickname_saved, Toast.LENGTH_SHORT).show()
                        refresh()
                    } else {
                        Toast.makeText(requireContext(), R.string.operation_failed, Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun loadHitokoto() {
        lifecycleScope.launch {
            val text = api.hitokoto() ?: getString(R.string.hitokoto_loading)
            binding.tvHitokoto.text = text
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
