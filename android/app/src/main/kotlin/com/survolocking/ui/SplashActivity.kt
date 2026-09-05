package com.survolocking.ui

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.OvershootInterpolator
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.survolocking.databinding.ActivitySplashBinding
import com.survolocking.net.ApiClient

/**
 * 启动闪屏页。
 *
 * 模拟小黑盒的「品牌印记开场」：1.5s 内把 logo 从 0.78 缩放 + 透明度 0→1，
 * 副标题在 logo 落定后淡入，loading 圆点随后开始转。
 *
 * 动画结束后根据登录态决定去向：
 *   - 已登录 → MainActivity
 *   - 未登录 → MainActivity（MainActivity 自己会展示登录门禁全屏覆盖）
 *
 * 真实闪屏时长由 AnimatorSet 时长决定；额外加 200ms 缓冲，确保视觉落定。
 */
class SplashActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySplashBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySplashBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 沉浸式：把状态栏/导航栏藏掉，画面从顶到底都是 splash 配色，避免黑边
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, binding.root).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        // 旧设备兼容：保险地把内容视图下沉
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility =
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                    View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                    View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                    View.SYSTEM_UI_FLAG_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }

        playLogoAnimation()
    }

    private fun playLogoAnimation() {
        val logo = binding.ivLogo
        val subtitle = binding.tvSubtitle
        val loading = binding.pbLoading
        val footer = binding.tvFooter

        // 初始状态：logo 略小 + 完全透明；其他元素先藏
        logo.alpha = 0f
        logo.scaleX = 0.78f
        logo.scaleY = 0.78f
        subtitle.alpha = 0f
        loading.alpha = 0f
        footer.alpha = 0f

        val logoFade = ObjectAnimator.ofFloat(logo, View.ALPHA, 0f, 1f).apply {
            duration = 520
            interpolator = AccelerateDecelerateInterpolator()
        }
        val logoScaleX = ObjectAnimator.ofFloat(logo, View.SCALE_X, 0.78f, 1.0f).apply {
            duration = 720
            interpolator = OvershootInterpolator(1.2f)
        }
        val logoScaleY = ObjectAnimator.ofFloat(logo, View.SCALE_Y, 0.78f, 1.0f).apply {
            duration = 720
            interpolator = OvershootInterpolator(1.2f)
        }
        val subtitleFade = ObjectAnimator.ofFloat(subtitle, View.ALPHA, 0f, 1f).apply {
            duration = 380
            startDelay = 280
        }
        val loadingFade = ObjectAnimator.ofFloat(loading, View.ALPHA, 0f, 1f).apply {
            duration = 240
            startDelay = 240
        }
        val footerFade = ObjectAnimator.ofFloat(footer, View.ALPHA, 0f, 0.55f).apply {
            duration = 320
            startDelay = 320
        }

        val set = AnimatorSet().apply {
            playTogether(logoFade, logoScaleX, logoScaleY, subtitleFade, loadingFade, footerFade)
        }
        set.start()

        // 动画时长 ~1.04s，缓冲 250ms 后跳转
        val totalMs = (set.totalDuration + 250).toLong()
        Handler(Looper.getMainLooper()).postDelayed({ goNext() }, totalMs)
    }

    private fun goNext() {
        // 已登录 → 直接进主程序；未登录 → 独立登录页（账号/密码切换）
        val api = ApiClient(this)
        if (api.isLoggedIn) {
            startActivity(Intent(this, MainActivity::class.java))
        } else {
            startActivity(
                Intent(this, LoginActivity::class.java)
                    .putExtra(LoginActivity.EXTRA_TO_MAIN, true)
            )
        }
        overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
        finish()
    }
}
