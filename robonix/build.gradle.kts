// Hilt's own Gradle plugin + KSP annotation processor are intentionally NOT applied here.
// The Hilt compiler enforces that any @HiltAndroidApp Application class live in a
// `com.android.application` module (see RobonixApp.kt) - since this module is now a
// `com.android.library` (GuideAssistant's own MyApplication stays the real Application, wired
// through Koin instead, see module/RobotModule.kt in :app), running that processor here would
// hard-fail the build. All of robonix's Hilt/Compose/ViewModel source is kept as-is (nothing
// deleted or rewritten) - the Hilt annotations just sit inert without their codegen, since only a
// handful of plain constructor classes (GrpcChannelProvider/AtlasClient/LiaisonClient/ChatRepository)
// are actually used by :app right now.
plugins {
    id("com.android.library")
    id("kotlin-android")
    id("com.google.protobuf")
}

android {
    namespace = "com.robonix.client"
    compileSdk = 34

    defaultConfig {
        minSdk = 21
        targetSdk = 34
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    composeOptions {
        // Root project pins Kotlin at 1.9.24 (see root build.gradle); 1.5.14 is the compose
        // compiler release matched to that Kotlin version (robonix itself used to target
        // Kotlin 1.9.22 + compiler 1.5.8).
        kotlinCompilerExtensionVersion = "1.5.14"
    }
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:3.25.3"
    }
    plugins {
        create("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:1.61.1"
        }
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                create("java")
            }
            task.plugins {
                create("grpc")
            }
        }
    }
}

dependencies {
    // Compose BOM
    val composeBom = platform("androidx.compose:compose-bom:2024.02.00")
    implementation(composeBom)

    // Compose UI
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    // Activity & Lifecycle
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    // Navigation
    implementation("androidx.navigation:navigation-compose:2.7.7")

    // Hilt DI annotations/API only - no processor applied, see plugins{} comment above
    implementation("com.google.dagger:hilt-android:2.50")
    implementation("androidx.hilt:hilt-navigation-compose:1.1.0")

    // gRPC & Protobuf (full, not lite - lite causes runtime crashes)
    implementation("io.grpc:grpc-okhttp:1.61.1")
    implementation("io.grpc:grpc-protobuf:1.61.1")
    implementation("io.grpc:grpc-stub:1.61.1")
    implementation("com.google.protobuf:protobuf-java:3.25.3")
    compileOnly("org.apache.tomcat:annotations-api:6.0.53")

    // OkHttp (WebSocket)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // DataStore
    implementation("androidx.datastore:datastore-preferences:1.0.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // YAML (Soma robot description)
    implementation("org.yaml:snakeyaml:2.2")

    // Core
    implementation("androidx.core:core-ktx:1.12.0")

    // Testing
    testImplementation("junit:junit:4.13.2")
    // org.json for mapper golden tests (android.jar's org.json is stubbed in JVM tests)
    testImplementation("org.json:json:20231013")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
