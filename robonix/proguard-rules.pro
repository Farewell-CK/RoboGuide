# ProGuard rules for Robonix Android Client

# gRPC
-keep class io.grpc.** { *; }
-dontwarn io.grpc.**
-keep class com.google.protobuf.** { *; }
-dontwarn com.google.protobuf.**

# OkHttp
-keep class okhttp3.** { *; }
-dontwarn okhttp3.**
-keep class okio.** { *; }
-dontwarn okio.**

# Generated proto classes
-keep class robonix.** { *; }

# SnakeYAML (soma robot description) — references java.beans, absent on Android
-dontwarn java.beans.**
-keep class org.yaml.snakeyaml.** { *; }

# Hilt
-keep class dagger.hilt.** { *; }
-keep class javax.inject.** { *; }

# Coroutines
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}

# Keep data models for reflection
-keep class com.robonix.client.data.model.** { *; }

# Keep Room entities
-keep class * extends androidx.room.RoomDatabase
-dontwarn androidx.room.**

# General
-keepattributes Signature
-keepattributes *Annotation*
-keepattributes EnclosingMethod
-keepattributes InnerClasses
