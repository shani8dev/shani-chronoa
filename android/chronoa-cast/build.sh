#!/bin/bash
# Build Chronoa Cast without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.
# JAVA_HOME and ANDROID_SDK must point at a JDK 17 and an SDK with platforms;android-29
# and build-tools;34.0.0 (sdkmanager installs both).
set -euo pipefail
cd "$(dirname "$0")"
: "${JAVA_HOME:?set JAVA_HOME}" "${ANDROID_SDK:?set ANDROID_SDK}"
export PATH="$JAVA_HOME/bin:$PATH"   # d8 and apksigner run java from PATH
BT="$ANDROID_SDK/build-tools/34.0.0"; JAR="$ANDROID_SDK/platforms/android-29/android.jar"
OUT=build; rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/dex"
"$BT/aapt2" link -I "$JAR" --manifest AndroidManifest.xml -o "$OUT/base.apk"
"$JAVA_HOME/bin/javac" --release 8 -classpath "$JAR" -d "$OUT/classes" $(find src -name '*.java') 2>&1 | grep -v "bootstrap class path\|warning: \[options\]" || true
"$BT/d8" --release --min-api 29 --lib "$JAR" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && zip -q "../unsigned.apk" classes.dex)
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
KS="${KEYSTORE:-$OUT/debug.keystore}"
[ -f "$KS" ] || "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KS" -storepass android -keypass android \
    -alias chronoa -keyalg RSA -keysize 2048 -validity 3650 -dname "CN=Chronoa Cast" >/dev/null 2>&1
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android --out "$OUT/chronoa-cast.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT/chronoa-cast.apk" && ls -l "$OUT/chronoa-cast.apk"
