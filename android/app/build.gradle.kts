plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.megumiss.nkas"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.megumiss.nkas.mobile"
        minSdk = 30
        targetSdk = 35
        versionCode = 5
        versionName = "0.2.4"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

dependencies {
    implementation("androidx.drawerlayout:drawerlayout:1.2.0")
}
