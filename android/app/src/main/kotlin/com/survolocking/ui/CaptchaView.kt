package com.survolocking.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import kotlin.random.Random

/**
 * 轻量自绘图形验证码：4 位字符 + 干扰线 + 随机旋转，点击刷新。
 * 用于发送验证码 / 密码登录前的初级人机校验。
 */
class CaptchaView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private val charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    private val rnd = Random.Default
    private val palette = listOf(
        Color.parseColor("#34D9C4"),
        Color.parseColor("#F5A623"),
        Color.parseColor("#5EEAD4"),
        Color.parseColor("#ECF2F7")
    )

    var code: String = ""
        private set

    init {
        setOnClickListener { refresh() }
        refresh()
    }

    fun refresh() {
        code = (1..4).joinToString("") { charset.random(rnd).toString() }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        canvas.drawColor(Color.parseColor("#121A28"))

        val linePaint = Paint().apply {
            strokeWidth = 2f
            isAntiAlias = true
            alpha = 120
        }
        repeat(5) {
            linePaint.color = palette.random(rnd)
            canvas.drawLine(
                rnd.nextInt(w.toInt()).toFloat(),
                rnd.nextInt(h.toInt()).toFloat(),
                rnd.nextInt(w.toInt()).toFloat(),
                rnd.nextInt(h.toInt()).toFloat(),
                linePaint
            )
        }

        val textPaint = Paint().apply {
            textSize = h * 0.62f
            isAntiAlias = true
            textAlign = Paint.Align.CENTER
            typeface = Typeface.DEFAULT_BOLD
        }
        val step = w / (code.length + 1)
        for (i in code.indices) {
            textPaint.color = palette.random(rnd)
            val x = step * (i + 1)
            val baseY = h * 0.5f + (rnd.nextFloat() - 0.5f) * h * 0.2f
            canvas.save()
            canvas.rotate((rnd.nextFloat() - 0.5f) * 32f, x, baseY)
            canvas.drawText(code[i].toString(), x, baseY + h * 0.22f, textPaint)
            canvas.restore()
        }
    }
}
