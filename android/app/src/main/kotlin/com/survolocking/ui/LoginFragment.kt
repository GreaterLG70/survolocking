package com.survolocking.ui

import android.os.Bundle
import android.os.CountDownTimer
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.tabs.TabLayout
import com.survolocking.R
import com.survolocking.databinding.FragmentLoginBinding
import com.survolocking.net.ApiClient
import kotlinx.coroutines.launch
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * 全屏登录页（仿小黑盒风格）。
 *
 * 两种登录方式（Tab 切换，互不掺杂）：
 *   - 验证码登录：手机号 + 图形验证码（防短信刷量）+ 短信验证码。
 *     新手机号验证通过后由服务端隐式注册——绝不调 register 接口，
 *     从根上消除「已注册号填密码 → 1004」的死局。
 *   - 密码登录：手机号 + 密码，无需任何验证码（小黑盒/主流 App 同款体验）。
 *
 * 与服务端的关键交互：
 *   1) 1005（限频）：服务端在 data.retry_after 里返回剩余秒数，
 *      这里据此起倒计时，按钮文字改为 "N 秒后可重发"。
 *   2) 开发服务器返回 dev_code 时直接回显给用户。
 */
class LoginFragment : Fragment() {

    private var _binding: FragmentLoginBinding? = null
    private val binding get() = _binding!!

    var onLoggedIn: (() -> Unit)? = null

    private var countDown: CountDownTimer? = null

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        _binding = FragmentLoginBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.tabs.addTab(binding.tabs.newTab().setText(R.string.login_tab_code))
        binding.tabs.addTab(binding.tabs.newTab().setText(R.string.login_tab_password))
        binding.tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab?) {
                when (tab?.position) {
                    0 -> showGroup(binding.groupCode)
                    1 -> showGroup(binding.groupPwd)
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab?) {}
            override fun onTabReselected(tab: TabLayout.Tab?) {}
        })

        binding.captcha.setOnClickListener { binding.captcha.refresh() }

        binding.btnSendCode.setOnClickListener { onSendCode() }
        binding.btnLoginCode.setOnClickListener { onCodeLogin() }
        binding.btnLoginPwd.setOnClickListener { onPasswordLogin() }

        binding.btnSendCode.pressBounce()
        binding.btnLoginCode.pressBounce()
        binding.btnLoginPwd.pressBounce()
    }

    private fun showGroup(target: View) {
        binding.groupCode.visibility = if (target === binding.groupCode) View.VISIBLE else View.GONE
        binding.groupPwd.visibility = if (target === binding.groupPwd) View.VISIBLE else View.GONE
    }

    /** 图形验证码校验：仅验证码登录 Tab 使用（防短信刷量） */
    private fun captchaOk(): Boolean {
        val input = binding.etCaptcha.text.toString().trim().uppercase()
        if (input.isEmpty()) {
            toast(R.string.captcha_required)
            return false
        }
        if (input != binding.captcha.code.uppercase()) {
            toast(R.string.captcha_wrong)
            binding.captcha.refresh()
            binding.etCaptcha.text?.clear()
            return false
        }
        return true
    }

    private fun activePhone(): String = binding.etPhone.text.toString().trim()

    private fun onSendCode() {
        if (!captchaOk()) return
        val phone = activePhone()
        if (phone.length < 6) {
            toast(R.string.phone_invalid)
            return
        }
        lifecycleScope.launch {
            binding.btnSendCode.isEnabled = false
            try {
                val dev = ApiClient(requireContext()).sendCode(phone)
                if (dev != null) {
                    // 开发服务器：把验证码直接展示给用户
                    binding.tvDevCode.visibility = View.VISIBLE
                    binding.tvDevCode.text = getString(R.string.dev_code_hint, dev)
                    toast(getString(R.string.dev_code_toast, dev))
                } else {
                    toast(R.string.send_code_success)
                }
                binding.tvRateHint.visibility = View.GONE
                startCountdown(defaultSeconds = 60)
            } catch (e: ApiClient.ApiException) {
                // 服务端业务错误：1005 显示剩余倒计时，其他显示原话
                handleSendCodeError(e)
            } catch (e: Exception) {
                binding.btnSendCode.isEnabled = true
                toast(errorMessage(e, R.string.login_no_network))
                Log.e(TAG, "sendCode failed", e)
            }
        }
    }

    /**
     * 处理 send-code 错误：1005 限频 → 用服务端 retry_after 同步倒计时；
     * 其他错误复用统一文案。
     */
    private fun handleSendCodeError(e: ApiClient.ApiException) {
        if (e.code == 1005) {
            val retryAfter = e.retryAfterSeconds
            if (retryAfter > 0) {
                binding.tvRateHint.visibility = View.VISIBLE
                binding.tvRateHint.text = getString(R.string.login_retry_hint, retryAfter)
                startCountdown(defaultSeconds = retryAfter)
                return
            }
        }
        binding.btnSendCode.isEnabled = true
        toast(e.message ?: getString(R.string.send_code_failed))
    }

    private fun startCountdown(defaultSeconds: Int) {
        countDown?.cancel()
        val ms = defaultSeconds.coerceAtLeast(1) * 1000L
        countDown = object : CountDownTimer(ms, 1000) {
            override fun onTick(millis: Long) {
                val left = (millis / 1000).toInt().coerceAtLeast(1)
                binding.btnSendCode.text = getString(R.string.countdown_left, left)
                binding.btnSendCode.isEnabled = false
            }
            override fun onFinish() {
                binding.btnSendCode.isEnabled = true
                binding.btnSendCode.setText(R.string.login_send_code)
                binding.tvRateHint.visibility = View.GONE
            }
        }.also { it.start() }
    }

    /** 验证码登录：统一走 /login（新号由服务端隐式注册），不再分流到 register */
    private fun onCodeLogin() {
        if (!captchaOk()) return
        val phone = activePhone()
        val code = binding.etCode.text.toString().trim()
        if (phone.length < 6 || code.length < 4) {
            toast(R.string.input_incomplete)
            return
        }
        lifecycleScope.launch {
            try {
                ApiClient(requireContext()).login(phone, code)
                toast(R.string.login_success)
                onLoggedIn?.invoke()
            } catch (e: Exception) {
                toast(errorMessage(e, R.string.login_failed))
                Log.e(TAG, "codeLogin failed", e)
            }
        }
    }

    /** 密码登录：手机号 + 密码，无图形验证码 */
    private fun onPasswordLogin() {
        val phone = activePhone()
        val pwd = binding.etPassword.text.toString().trim()
        if (phone.length < 6 || pwd.length < 6) {
            toast(R.string.input_incomplete)
            return
        }
        lifecycleScope.launch {
            try {
                ApiClient(requireContext()).passwordLogin(phone, pwd)
                toast(R.string.login_success)
                onLoggedIn?.invoke()
            } catch (e: Exception) {
                toast(errorMessage(e, R.string.login_failed))
                Log.e(TAG, "pwdLogin failed", e)
            }
        }
    }

    /** 把异常翻译成给用户的友好提示：网络问题单独提示，其余展示后端原话 */
    private fun errorMessage(e: Exception, fallback: Int): String {
        return when (e) {
            is UnknownHostException,
            is SocketTimeoutException,
            is ConnectException -> getString(R.string.network_error)
            is ApiClient.ApiException -> e.message ?: getString(fallback)
            else -> e.message ?: getString(fallback)
        }
    }

    private fun toast(resId: Int) = Toast.makeText(requireContext(), resId, Toast.LENGTH_SHORT).show()
    private fun toast(msg: String) = Toast.makeText(requireContext(), msg, Toast.LENGTH_LONG).show()

    override fun onDestroyView() {
        super.onDestroyView()
        countDown?.cancel()
        _binding = null
    }

    companion object {
        private const val TAG = "LoginFragment"
    }
}
