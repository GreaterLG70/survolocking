package com.survolocking.ui

import android.Manifest
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.survolocking.R
import com.survolocking.data.RuleDatabase
import com.survolocking.databinding.FragmentDataBinding
import com.survolocking.engine.DecisionEngine
import com.survolocking.net.ApiClient
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class DataFragment : Fragment() {

    private var _binding: FragmentDataBinding? = null
    private val binding get() = _binding!!
    private lateinit var api: ApiClient
    private lateinit var db: RuleDatabase
    private lateinit var engine: DecisionEngine

    private val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.CHINA)

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        if (results[Manifest.permission.READ_CONTACTS] == true) {
            lifecycleScope.launch { engine.refreshAll() }
        }
        refreshRole()
    }

    private val roleLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { refreshRole() }

    /**
     * 电池优化白名单页：用户从系统弹窗回来后回调；无论结果如何都刷新卡片状态。
     * 部分 OEM（华为/小米/OPPO）会拦截 ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS，
     * 这种情况下回退到系统电池设置总览，让用户手动找开关。
     */
    private val batteryLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { refreshBattery() }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        _binding = FragmentDataBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        api = ApiClient(requireContext())
        db = RuleDatabase.getInstance(requireContext())
        engine = DecisionEngine(requireContext())

        binding.rvBlocked.layoutManager = LinearLayoutManager(requireContext())

        binding.btnSetDefault.setOnClickListener { requestRole() }
        binding.btnGrant.setOnClickListener {
            permissionLauncher.launch(
                arrayOf(
                    Manifest.permission.READ_CONTACTS,
                    Manifest.permission.READ_PHONE_STATE,
                    Manifest.permission.READ_CALL_LOG,
                    Manifest.permission.POST_NOTIFICATIONS
                )
            )
        }
        binding.btnBattery.setOnClickListener { requestIgnoreBatteryOptimizations() }

        // 黑白名单卡片：点开查看明细（号码以脱敏哈希展示）
        binding.cardBlack.setOnClickListener { showListDialog(isBlack = true) }
        binding.cardWhite.setOnClickListener { showListDialog(isBlack = false) }

        refreshRole()
        refreshBattery()
        refresh()
    }

    /**
     * 黑/白名单明细弹窗。
     * 号码在端侧即哈希化存储（隐私设计），这里展示哈希前 8 位 +
     * 被标记次数（黑）/ 来源与有效期（白）。
     */
    private fun showListDialog(isBlack: Boolean) {
        if (isBlack) {
            val list = db.listBlacklist()
            val items = list.map {
                "•••• ${it.phoneHash.take(8)}　·  ${getString(R.string.blacklist_mark_count, it.markCount)}　·  ${fmt.format(Date(it.createdAt))}"
            }.toTypedArray()
            androidx.appcompat.app.AlertDialog.Builder(requireContext())
                .setTitle(getString(R.string.blacklist_dialog_title, list.size))
                .setMessage(
                    (items.joinToString("\n").ifEmpty { getString(R.string.list_empty_hint) }) +
                        "\n\n" + getString(R.string.hash_privacy_note)
                )
                .setPositiveButton(android.R.string.ok, null)
                .show()
        } else {
            val list = db.listWhitelist()
            val items = list.map {
                val expire = it.expireAt?.let { e -> fmt.format(Date(e)) }
                    ?: getString(R.string.whitelist_forever)
                "•••• ${it.phoneHash.take(8)}　·  $expire"
            }.toTypedArray()
            androidx.appcompat.app.AlertDialog.Builder(requireContext())
                .setTitle(getString(R.string.whitelist_dialog_title, list.size))
                .setMessage(
                    (items.joinToString("\n").ifEmpty { getString(R.string.list_empty_hint) }) +
                        "\n\n" + getString(R.string.hash_privacy_note)
                )
                .setPositiveButton(android.R.string.ok, null)
                .show()
        }
    }

    override fun onResume() {
        super.onResume()
        if (::db.isInitialized) {
            refreshRole()
            refreshBattery()
            refresh()
        }
    }

    private fun isRoleHeld(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false
        val rm = requireContext().getSystemService(RoleManager::class.java) ?: return false
        return rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
    }

    private fun requestRole() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val rm = requireContext().getSystemService(RoleManager::class.java)
        val intent = rm?.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)
        if (intent != null) roleLauncher.launch(intent)
    }

    /**
     * 是否已被加入电池优化白名单。Android M+ 才有此 API。
     */
    private fun isIgnoringBatteryOptimizations(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        val pm = requireContext().getSystemService(Context.POWER_SERVICE) as? PowerManager
            ?: return false
        return pm.isIgnoringBatteryOptimizations(requireContext().packageName)
    }

    /**
     * 触发系统弹窗；老设备 / 被 OEM 拦截时回退到电池设置总览。
     */
    private fun requestIgnoreBatteryOptimizations() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            // 老设备没有电池优化概念，视为已开启
            return
        }
        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
            data = Uri.parse("package:${requireContext().packageName}")
        }
        val canResolve = intent.resolveActivity(requireContext().packageManager) != null
        if (canResolve) {
            try {
                batteryLauncher.launch(intent)
                return
            } catch (_: Exception) {
                // 部分 ROM 拒收，回退
            }
        }
        // 兜底：跳到「电池」总览，让用户自己找开关
        try {
            batteryLauncher.launch(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        } catch (_: Exception) {
            // 最后兜底：跳应用详情页
            batteryLauncher.launch(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:${requireContext().packageName}")
                }
            )
        }
    }

    private fun refreshBattery() {
        val granted = isIgnoringBatteryOptimizations()
        binding.cardBattery.visibility = View.VISIBLE
        if (granted) {
            binding.tvBatteryStatus.text = getString(R.string.battery_granted)
            binding.tvBatteryStatus.setTextColor(
                ContextCompat.getColor(requireContext(), R.color.success)
            )
            binding.btnBattery.visibility = View.GONE
        } else {
            binding.tvBatteryStatus.text = getString(R.string.battery_card_desc)
            binding.tvBatteryStatus.setTextColor(
                ContextCompat.getColor(requireContext(), R.color.text_secondary)
            )
            binding.btnBattery.visibility = View.VISIBLE
        }
    }

    private fun refreshRole() {
        val active = isRoleHeld()
        val contacts = ContextCompat.checkSelfPermission(
            requireContext(), Manifest.permission.READ_CONTACTS
        ) == PackageManager.PERMISSION_GRANTED

        binding.cardRole.visibility = if (active && contacts) View.GONE else View.VISIBLE
        binding.btnSetDefault.visibility = if (active) View.GONE else View.VISIBLE
        binding.btnGrant.visibility = if (contacts) View.GONE else View.VISIBLE

        binding.tvRoleStatus.text = when {
            !active -> getString(R.string.home_role_inactive)
            !contacts -> getString(R.string.contacts_denied)
            else -> getString(R.string.home_role_active)
        }
        binding.tvRoleStatus.setTextColor(
            ContextCompat.getColor(
                requireContext(),
                if (active && contacts) R.color.success else R.color.warning
            )
        )
    }

    private fun refresh() {
        binding.tvGuardDays.text = db.guardDays().toString()
        binding.tvBlocked.text = db.blockedCount().toString()
        binding.tvPending.text = db.pendingUploadCount().toString()
        binding.tvBlack.text = db.blacklistCount().toString()
        binding.tvWhite.text = db.whitelistCount().toString()

        val list = db.recentBlocked(30)
        if (list.isEmpty()) {
            binding.rvBlocked.visibility = View.GONE
            binding.tvBlockedEmpty.visibility = View.VISIBLE
        } else {
            binding.rvBlocked.visibility = View.VISIBLE
            binding.tvBlockedEmpty.visibility = View.GONE
            binding.rvBlocked.adapter = BlockedAdapter(list.map { fmt.format(Date(it)) })
        }
    }

    private class BlockedAdapter(private val items: List<String>) :
        RecyclerView.Adapter<BlockedAdapter.VH>() {
        class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
            val tv: TextView = itemView.findViewById(R.id.tv_time)
        }
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_blocked, parent, false)
            return VH(view)
        }
        override fun getItemCount() = items.size
        override fun onBindViewHolder(holder: VH, position: Int) {
            holder.tv.text = items[position]
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
