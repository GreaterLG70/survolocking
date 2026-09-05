# Survolocking 混淆规则

# 保留 CallScreeningService，由系统通过清单文件反射拉起
-keep class com.survolocking.engine.CallScreenService { *; }
-keep class com.survolocking.engine.CallStateReceiver { *; }
-keep class com.survolocking.engine.MarkReceiver { *; }

# WorkManager 需要通过反射实例化 Worker
-keep class com.survolocking.worker.** extends androidx.work.Worker { *; }
-keep class com.survolocking.worker.** extends androidx.work.ListenableWorker { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# 移除日志
-assumenosideeffects class android.util.Log {
    public static boolean isLoggable(java.lang.String, int);
    public static int v(...);
    public static int d(...);
}
