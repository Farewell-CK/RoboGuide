#!/usr/bin/env python3
"""Prepare disposable Flutter Android files; never edit the installed SDK/cache."""
from pathlib import Path
import os
import re

root = Path(__file__).resolve().parents[1]
android = root / 'navi_indoor/.android'
wrapper = android / 'gradle/wrapper/gradle-wrapper.properties'
if not wrapper.is_file():
    raise SystemExit('Run flutter pub get in navi_indoor first.')
# Flutter 3.35.7 selects Gradle 8.11.1; the bin archive is sufficient for AARs.
text = wrapper.read_text()
text = text.replace('gradle-8.11.1-all.zip', 'gradle-8.11.1-bin.zip')
wrapper.write_text(text)
build_file = android / 'build.gradle'
text = build_file.read_text()
if 'NAVI_SKIP_PLUGIN_JAVADOC' not in text:
    # AAR runtime/source artifacts are needed; networked Dokka generation in
    # transitive plugins is unnecessary and fails for jni_flutter on AGP 8.9.1.
    text += '''
// NAVI_SKIP_PLUGIN_JAVADOC: preserve runtime AARs; omit generated API docs.
allprojects {
    tasks.configureEach {
        if (name.startsWith('javaDoc') && name.endsWith('Generation')) {
            enabled = false
        }
    }
}
'''
    build_file.write_text(text)
if os.environ.get('NAVI_MAVEN_MIRROR') == 'aliyun':
    mirrors = '''// NAVI_BUILD_MIRRORS
        maven { url 'https://maven.aliyun.com/repository/google' }
        maven { url 'https://maven.aliyun.com/repository/public' }
        maven { url 'https://maven.aliyun.com/repository/gradle-plugin' }
        '''
    for name in ['settings.gradle', 'build.gradle']:
        path = android / name
        text = path.read_text()
        if 'NAVI_BUILD_MIRRORS' not in text:
            text = re.sub(r'(repositories\s*\{\s*)', lambda m: m[1] + mirrors, text)
            path.write_text(text)
    print('Prepared generated Android repositories using Aliyun mirrors.')
