package com.survolocking.ui

import android.view.MotionEvent
import android.view.View
import android.view.animation.AnticipateOvershootInterpolator

/**
 * 微动效：按压时轻微回缩，松手时带弹性回弹（拟物反馈）。
 * 返回 false，不拦截点击事件，按钮的 ripple 与 onClick 仍正常。
 */
fun View.pressBounce() {
    setOnTouchListener { v, event ->
        when (event.action) {
            MotionEvent.ACTION_DOWN ->
                v.animate().scaleX(0.96f).scaleY(0.96f).setDuration(90).start()
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                v.animate().scaleX(1f).scaleY(1f)
                    .setInterpolator(AnticipateOvershootInterpolator()).setDuration(180).start()
        }
        false
    }
}
