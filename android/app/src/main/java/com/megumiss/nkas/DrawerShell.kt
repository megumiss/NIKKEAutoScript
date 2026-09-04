package com.megumiss.nkas

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.drawerlayout.widget.DrawerLayout

object DrawerShell {
    data class Built(val root: DrawerLayout, val content: FrameLayout)

    fun build(activity: Activity, selected: String, onNavigate: (String) -> Unit): Built {
        val drawer = DrawerLayout(activity)
        drawer.setBackgroundColor(Color.rgb(248, 250, 252))
        val main = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.rgb(248, 250, 252)) }
        val toolbar = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setPadding(dp(activity, 8), 0, dp(activity, 16), 0); setBackgroundColor(Color.rgb(255, 255, 255)) }
        val menu = ImageButton(activity).apply { setImageResource(android.R.drawable.ic_menu_sort_by_size); setColorFilter(Color.rgb(26, 112, 170)); setBackgroundColor(Color.TRANSPARENT); contentDescription = "打开导航"; setOnClickListener { drawer.openDrawer(Gravity.LEFT) } }
        toolbar.addView(menu, LinearLayout.LayoutParams(dp(activity, 48), dp(activity, 48)))
        toolbar.addView(TextView(activity).apply { text = "NKAS Mobile"; textSize = 18f; setTextColor(Color.rgb(25, 35, 48)); setTypeface(Typeface.DEFAULT, Typeface.BOLD) }, LinearLayout.LayoutParams(0, -2, 1f))
        val content = FrameLayout(activity)
        main.addView(toolbar, LinearLayout.LayoutParams(-1, dp(activity, 56)))
        main.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        drawer.addView(main, DrawerLayout.LayoutParams(-1, -1))

        val panel = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(activity, 16), dp(activity, 28), dp(activity, 16), dp(activity, 16)); setBackgroundColor(Color.rgb(255, 255, 255)) }
        panel.addView(TextView(activity).apply { text = "NKAS\nMobile"; textSize = 22f; setTextColor(Color.rgb(25, 35, 48)); setTypeface(Typeface.DEFAULT, Typeface.BOLD); setPadding(dp(activity, 8), 0, 0, dp(activity, 24)) })
        listOf("初始化" to "setup", "NKAS UI" to "ui", "设置" to "settings", "关于" to "about").forEach { (label, key) ->
            val item = TextView(activity).apply { text = label; textSize = 16f; setTextColor(if (selected == key) Color.rgb(26, 112, 170) else Color.rgb(92, 105, 120)); setPadding(dp(activity, 14), dp(activity, 15), 0, dp(activity, 15)); setOnClickListener { drawer.closeDrawer(Gravity.LEFT); onNavigate(key) } }
            panel.addView(item, LinearLayout.LayoutParams(-1, -2))
        }
        drawer.addDrawerListener(object : DrawerLayout.SimpleDrawerListener() {})
        val params = DrawerLayout.LayoutParams(dp(activity, 280), -1).apply { gravity = Gravity.LEFT }
        drawer.addView(panel, params)
        return Built(drawer, content)
    }

    private fun dp(activity: Activity, value: Int) = (value * activity.resources.displayMetrics.density).toInt()
}
