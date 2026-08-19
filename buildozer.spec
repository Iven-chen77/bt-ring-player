[app]

# (str) 应用标题 (显示在手机桌面和启动器)
title = 蓝牙响铃播放器

# (str) 包名 - 必须唯一，建议使用反向域名格式
package.name = btringplayer

# (str) 包的域名前缀
package.domain = org.example.btring

# (str) 源代码目录（入口文件所在目录）
source.dir = .

# (list) 源代码目录中要包含的文件（空 = 全部）；建议显式列出以便排除缓存
# 重要：必须包含 ttc/ttf/otf，否则中文字体文件 MSYH.ttc 不会被打进 APK！
source.include_exts = py,png,jpg,kv,atlas,json,mp3,wav,ogg,ttc,ttf,otf,aac,flac,m4a

# (list) 要排除的文件/目录模式
source.exclude_patterns = tests,bin,__pycache__,*.pyc,.git,.buildozer,venv,env,build,dist

# (str) 应用版本号（version_name）
version = 1.0

# (list) 应用需求 - Python 库
# 注意：kivymd 1.2.0 需要配合 kivy>=2.2.0
# 注意：sdl2_* 这些是 p4a 内置 recipe 名，不要放在 requirements 里（会报错），kivy 会自动依赖
requirements = python3,kivy==2.3.0,kivymd==1.2.0,pillow,plyer,android

# (str) 预设的 Android API 级别
# 33 = Android 13，34 = Android 14（推荐 33，兼容性更稳）
android.api = 33

# (int) 最低支持的 Android API 级别 (Android 7.0+)
android.minapi = 24

# (int) Android SDK build-tools 版本（和 NDK/SDK 配套）
android.buildtools = 33.0.3

# (str) Android NDK 版本（必须和 SDK/API 级别配套，API33 → NDK r25b）
android.ndk = 25b

# (str) 支持的 CPU 架构；多个用空格分隔
# armeabi-v7a = 32位 ARM 兼容最广；arm64-v8a = 64位 ARM（主流）；x86_64 = 模拟器
android.storage_dir = .buildozer/android/platform/build

# (str) 入口模块名 (.py 文件名去掉后缀)
# 即 main.py 启动文件
entrypoint = main.py

# (bool) 允许应用写入 SD 卡 / 共享存储
android.allow_backup = True

# (list) Android 权限 - 蓝牙、位置、存储、通知
# 说明：
#   BLUETOOTH_SCAN / BLUETOOTH_CONNECT = Android 12+ 必须
#   ACCESS_FINE_LOCATION = 扫描蓝牙经典设备需要（Android 11 及以下）
#   READ_MEDIA_AUDIO = Android 13+ 读取音频
#   WRITE_EXTERNAL_STORAGE / READ_EXTERNAL_STORAGE = Android 12 及以下
android.permissions = BLUETOOTH, \
    BLUETOOTH_ADMIN, \
    BLUETOOTH_SCAN, \
    BLUETOOTH_CONNECT, \
    BLUETOOTH_ADVERTISE, \
    ACCESS_FINE_LOCATION, \
    ACCESS_COARSE_LOCATION, \
    WRITE_EXTERNAL_STORAGE, \
    READ_EXTERNAL_STORAGE, \
    READ_MEDIA_AUDIO, \
    INTERNET, \
    VIBRATE, \
    WAKE_LOCK, \
    FOREGROUND_SERVICE

# (str) 应用图标路径 (放一张 512x512 的 png 到项目根目录并改这里)
# icon.filename = %(source.dir)s/data/icon.png

# (str) 应用启动闪屏
# presplash.filename = %(source.dir)s/data/presplash.png

# (bool) 启动屏幕背景色 (ARGB, FF=不透明)
presplash.color = #FF1976D2

# (str) 屏幕方向: landscape(横屏), portrait(竖屏), all, sensor
orientation = portrait

# (bool) 是否全屏 (隐藏状态栏)
fullscreen = 0

# (str) 打包类型: debug / release
# 建议先打 debug，能跑通再打 release（release 需签名）
android.release_artifact = apk

# (bool) 启用 AndroidX (Android 新支持库，KivyMD 需要)
android.useAndroidX = True
android.enableJetifier = True

# (list) Android 服务: 留空
# services =

# (list) meta-data
# android.meta_data =

# (list) manifest 中要添加的 activity intent-filter 标签内容（可选）
# 这里添加蓝牙相关的 action 可选
android.manifest_placeholders =

# (bool) 每次构建前自动执行 buildozer android clean
# 建议首次构建设为 0，遇到依赖问题再手动 clean
# android.apptheme = @android:style/Theme.Material.Light.NoActionBar

# ---------------------
# 国内镜像配置 (关键!)
# ---------------------
# 使用阿里云 PyPI 镜像加速 Python 包下载
pypi.mirror = https://pypi.tuna.tsinghua.edu.cn/simple

# 阿里云 Maven 镜像 - 替代默认的 Google / MavenCentral
# 解决 Gradle 依赖下载慢或失败的问题
android.maven_repos = \
    https://maven.aliyun.com/repository/google,\
    https://maven.aliyun.com/repository/public,\
    https://maven.aliyun.com/repository/gradle-plugin

# ---------------------
# 构建性能
# ---------------------
# (int) Gradle 并行构建线程数
# android.gradle_daemon = True

# (str) 额外的 Gradle JVM 参数
android.gradle_jvm_args = -Xmx2048m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8

# (str) 自定义 Gradle 属性 (国内镜像)
android.gradle_properties = \
    org.gradle.daemon=true,\
    org.gradle.parallel=true,\
    org.gradle.caching=true,\
    org.gradle.jvmargs=-Xmx2048m

# ---------------------
# 日志 / 调试
# ---------------------
# (str) logcat 过滤标签 (方便调试 adb logcat)
android.logcat_filters = *:S python:D

# (bool) 允许 APK 调试 (debug 构建自动启用)
android.debuggable = True

[buildozer]

# (int) 日志级别 (1=只错误, 2=警告+错误, 3=默认, 4=详细)
log_level = 3

# (int) 构建前若命令失败是否警告 (1=是)
warn_on_root = 1

# (str) 构建缓存目录，首次构建会下载 SDK/NDK 等
build_dir = .buildozer

# (str) 输出 APK 目录
bin_dir = bin
