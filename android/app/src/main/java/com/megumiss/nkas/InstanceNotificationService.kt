package com.megumiss.nkas

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.widget.RemoteViews
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class InstanceNotificationService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var notifications: NotificationManager
    private val notifiedIds = mutableMapOf<String, Int>()

    private val refreshTask = object : Runnable {
        override fun run() {
            executor.execute {
                val snapshot = fetchInstances()
                handler.post { publish(snapshot) }
            }
            handler.postDelayed(this, REFRESH_INTERVAL_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        notifications = getSystemService(NotificationManager::class.java)
        createChannel()
        startForegroundCompat(buildSummary(emptyList(), false))
        handler.post(refreshTask)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CONTROL) {
            val name = intent.getStringExtra(EXTRA_NAME).orEmpty()
            val operation = intent.getStringExtra(EXTRA_OPERATION).orEmpty()
            if (name.isNotBlank() && operation in CONTROL_OPERATIONS) {
                executor.execute {
                    postControl(name, operation)
                    val snapshot = fetchInstances()
                    handler.post { publish(snapshot) }
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "NKAS 实例状态", NotificationManager.IMPORTANCE_LOW).apply {
                description = "显示 NKAS 实例状态并提供启动和停止操作"
                setShowBadge(false)
            },
        )
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(SUMMARY_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(SUMMARY_ID, notification)
        }
    }

    private fun publish(instances: List<InstanceSnapshot>?) {
        if (instances == null) {
            notifications.notify(SUMMARY_ID, buildSummary(emptyList(), false))
            notifiedIds.values.forEach { notifications.cancel(it) }
            notifiedIds.clear()
            return
        }

        val currentNames = instances.map { it.name }.toSet()
        notifiedIds.filterKeys { it !in currentNames }.values.forEach { notifications.cancel(it) }
        notifiedIds.keys.removeIf { it !in currentNames }

        instances.forEach { instance ->
            val id = notifiedIds.getOrPut(instance.name) { notificationId(instance.name) }
            notifications.notify(id, buildInstanceNotification(instance))
        }
        notifications.notify(SUMMARY_ID, buildSummary(instances, true))
    }

    private fun buildSummary(instances: List<InstanceSnapshot>, serviceReady: Boolean): Notification {
        val running = instances.count { it.state == 1 }
        val summaryText = when {
            !serviceReady -> "正在等待 NKAS Web UI 服务"
            instances.isEmpty() -> "未发现实例"
            else -> "${instances.size} 个实例，${running} 个运行中"
        }
        val style = Notification.InboxStyle()
        if (instances.isEmpty()) {
            style.addLine(summaryText)
        } else {
            instances.forEach { style.addLine("${it.name}：${it.statusText()}") }
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("NKAS 实例")
            .setContentText(summaryText)
            .setStyle(style)
            .setGroup(GROUP_KEY)
            .setGroupSummary(true)
            .setContentIntent(openAppIntent())
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .build()
    }

    private fun buildInstanceNotification(instance: InstanceSnapshot): Notification {
        val operation = if (instance.state == 1) "stop" else "start"
        val actionLabel = if (operation == "stop") "停止 ${instance.name}" else "启动 ${instance.name}"
        val remoteViews = RemoteViews(packageName, R.layout.notification_instance).apply {
            setImageViewResource(R.id.notification_instance_logo, R.drawable.ic_launcher_foreground)
            setTextViewText(R.id.notification_instance_name, instance.name)
            setTextViewText(R.id.notification_instance_status, instance.statusText())
            setImageViewResource(
                R.id.notification_instance_action,
                if (operation == "stop") R.drawable.ic_notification_stop else R.drawable.ic_notification_start,
            )
            setContentDescription(R.id.notification_instance_action, actionLabel)
            setOnClickPendingIntent(R.id.notification_instance_action, controlIntent(instance.name, operation))
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(instance.name)
            .setContentText(instance.statusText())
            .setGroup(GROUP_KEY)
            .setContentIntent(openAppIntent())
            .setCustomContentView(remoteViews)
            .setCustomBigContentView(remoteViews)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .build()
    }

    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this,
        OPEN_APP_REQUEST,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun controlIntent(name: String, operation: String): PendingIntent = PendingIntent.getService(
        this,
        "$name:$operation".hashCode(),
        Intent(this, InstanceNotificationService::class.java).apply {
            action = ACTION_CONTROL
            putExtra(EXTRA_NAME, name)
            putExtra(EXTRA_OPERATION, operation)
        },
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun fetchInstances(): List<InstanceSnapshot>? = runCatching {
        val connection = (URL(INSTANCES_URL).openConnection() as HttpURLConnection).apply {
            connectTimeout = 2500
            readTimeout = 2500
            requestMethod = "GET"
        }
        connection.inputStream.bufferedReader().use { reader ->
            val array = JSONArray(reader.readText())
            buildList(array.length()) {
                for (index in 0 until array.length()) {
                    val item = array.getJSONObject(index)
                    add(InstanceSnapshot(item.optString("name"), item.optInt("state"), item.optString("current_task")))
                }
            }
        }.also { connection.disconnect() }
    }.getOrNull()

    private fun postControl(name: String, operation: String) {
        runCatching {
            val encodedName = Uri.encode(name)
            val connection = (URL("$BASE_URL/api/$encodedName/$operation").openConnection() as HttpURLConnection).apply {
                connectTimeout = 2500
                readTimeout = 2500
                requestMethod = "POST"
                doOutput = true
                outputStream.use { }
            }
            connection.inputStream.close()
            connection.disconnect()
        }
    }

    private data class InstanceSnapshot(val name: String, val state: Int, val currentTask: String) {
        fun statusText(): String = when {
            state == 1 && currentTask.isNotBlank() -> "运行中 · $currentTask"
            state == 1 -> "空闲"
            state == 4 -> "更新中"
            state == 3 -> "异常停止"
            else -> "已停止"
        }
    }

    companion object {
        private const val CHANNEL_ID = "nkas_instances"
        private const val GROUP_KEY = "nkas_instance_group"
        private const val SUMMARY_ID = 100
        private const val OPEN_APP_REQUEST = 101
        private const val REFRESH_INTERVAL_MS = 5000L
        private const val BASE_URL = "http://127.0.0.1:12271"
        private const val INSTANCES_URL = "$BASE_URL/api/instances"
        private const val ACTION_CONTROL = "com.megumiss.nkas.mobile.INSTANCE_CONTROL"
        private const val EXTRA_NAME = "instance_name"
        private const val EXTRA_OPERATION = "instance_operation"
        private val CONTROL_OPERATIONS = setOf("start", "stop")

    fun start(context: Context) {
            val intent = Intent(context, InstanceNotificationService::class.java)
            runCatching {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
                else context.startService(intent)
            }
        }

        private fun notificationId(name: String): Int = 1000 + (name.hashCode() and 0x3fffffff)
    }
}
