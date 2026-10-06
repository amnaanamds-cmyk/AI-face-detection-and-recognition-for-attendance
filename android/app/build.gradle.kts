plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.faceattend.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.faceattend.app"
        minSdk = 24          // Android 7.0+
        targetSdk = 35
        // set by CI from the release tag (v1.2.0 -> 1.2.0) and the build number
        versionCode = (System.getenv("APP_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("APP_VERSION") ?: "1.0.0"
        // the in-app updater checks this repository's latest GitHub release
        // hosted service: the app opens it directly, no QR code needed (empty = ask for the school's own server)
        buildConfigField("String", "CLOUD_URL", "\"${(System.getenv("FACEATTEND_CLOUD_URL") ?: "").trimEnd('/')}\"")
        buildConfigField("String", "UPDATE_REPO", "\"${System.getenv("UPDATE_REPO") ?: "amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance"}\"")
    }

    signingConfigs {
        // One permanent key, so every new APK installs as an update over the previous one.
        // Private key: set ANDROID_KEYSTORE (path) + ANDROID_KEYSTORE_PASSWORD + ANDROID_KEY_ALIAS +
        // ANDROID_KEY_PASSWORD (CI reads them from repository secrets). Otherwise the committed
        // keystore/faceattend-public.jks is used - fine for a demo, NOT for selling (anyone can read it).
        create("release") {
            val private = System.getenv("ANDROID_KEYSTORE")
            if (private != null && file(private).exists()) {
                storeFile = file(private)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            } else {
                storeFile = rootProject.file("keystore/faceattend-public.jks")
                storePassword = "faceattend"
                keyAlias = "faceattend"
                keyPassword = "faceattend"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = false
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("com.google.android.material:material:1.12.0")
    // QR code scanner (Apache-2.0), works without Google Play services
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("com.google.zxing:core:3.4.1")   // reads a QR code from a saved picture (gallery)
}
