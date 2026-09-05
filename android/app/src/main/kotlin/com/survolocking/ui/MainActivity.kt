package com.survolocking.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import com.survolocking.R
import com.survolocking.databinding.ActivityMainBinding
import com.survolocking.net.ApiClient

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val dataFrag = DataFragment()
    private val familyFrag = FamilyFragment()
    private val profileFrag = ProfileFragment()

    /** 互踢弹窗只弹一次：多个并发请求同时 401 时不重复打扰 */
    private var kickDialogShown = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        supportFragmentManager.beginTransaction()
            .add(R.id.fragment_container, dataFrag, "data")
            .add(R.id.fragment_container, familyFrag, "family").hide(familyFrag)
            .add(R.id.fragment_container, profileFrag, "profile").hide(profileFrag)
            .show(dataFrag)
            .commitNow()

        binding.bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_data -> showFragment(dataFrag)
                R.id.nav_family -> showFragment(familyFrag)
                R.id.nav_profile -> showFragment(profileFrag)
                else -> return@setOnItemSelectedListener false
            }
            true
        }
        binding.bottomNav.selectedItemId = R.id.nav_data

        // 单设备登录互踢（参考 QQ）：任意请求命中 401 + X-Kicked 时，
        // ApiClient 已清掉本地登录态，这里只负责 UI——
        // 弹「账号被迫离线」对话框，确认后强制回登录页。
        ApiClient.onKicked = { runOnUiThread { showKickedDialog() } }
    }

    override fun onResume() {
        super.onResume()
        ensureMonitorServiceAlive()
    }

    /**
     * 兜底保活：只要用户回到 App，常驻前台服务必然复活。
     * 服务已在运行时重复 start 只是走一遍 onStartCommand，无副作用。
     */
    private fun ensureMonitorServiceAlive() {
        runCatching {
            val intent = Intent(this, com.survolocking.engine.CallStateMonitorService::class.java)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
        }
    }

    private fun showKickedDialog() {
        if (kickDialogShown || isFinishing || isDestroyed) return
        kickDialogShown = true
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle(R.string.kicked_title)
            .setMessage(R.string.kicked_message)
            .setCancelable(false)
            .setPositiveButton(R.string.kicked_relogin) { _, _ ->
                kickDialogShown = false
                startActivity(
                    Intent(this, LoginActivity::class.java)
                        .putExtra(LoginActivity.EXTRA_TO_MAIN, true)
                )
                finish()
            }
            .show()
    }

    override fun onDestroy() {
        super.onDestroy()
        ApiClient.onKicked = null
    }

    private fun showFragment(frag: Fragment) {
        supportFragmentManager.beginTransaction().apply {
            if (!frag.isAdded) add(R.id.fragment_container, frag, frag.javaClass.simpleName)
            for (f in listOf(dataFrag, familyFrag, profileFrag)) {
                if (f != frag) hide(f)
            }
            show(frag)
        }.commit()
    }
}
