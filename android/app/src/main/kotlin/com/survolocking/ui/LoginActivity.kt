package com.survolocking.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.survolocking.R
import com.survolocking.databinding.ActivityLoginHostBinding

/**
 * 登录 / 注册的宿主 Activity（独立全屏页，小黑盒风格）。
 *
 * 两个入口：
 *   - Splash 未登录时直达此页 → 登录成功后进入 MainActivity；
 *   - 「我的」页点登录按钮复用此页 → 登录成功后返回上层（finish）。
 *
 * 通过 intent extra "to_main" 区分：true 时登录成功跳 MainActivity，否则 finish。
 */
class LoginActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityLoginHostBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Splash 跳过来时要求登录后进入主程序
        val toMain = intent.getBooleanExtra(EXTRA_TO_MAIN, false)

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .add(
                    R.id.login_host,
                    LoginFragment().also {
                        it.onLoggedIn = {
                            if (toMain) {
                                startActivity(Intent(this, MainActivity::class.java))
                                finish()
                            } else {
                                finish()
                            }
                        }
                    },
                    "login"
                )
                .commitNow()
        }
    }

    companion object {
        const val EXTRA_TO_MAIN = "to_main"
    }
}
