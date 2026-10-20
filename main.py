# -*- coding: utf-8 -*-
"""
蓝牙响铃 + 本地音乐播放器 App
技术栈: KivyMD + Kivy + plyer/jnius (Android原生API)
"""

import os
import sys
import traceback
import threading
import time
import json
from os.path import join, exists, dirname, abspath

# 把 Kivy 配置/日志目录固定在项目内，避免写入用户目录权限问题
# （必须在导入 kivy 之前设置）
def _resolve_app_folder():
    """跨平台返回 App 可写的基准目录。
    - Windows/Linux/Mac: main.py 所在目录（开发/打包均可写）
    - Android: 优先用 p4a 注入的 ANDROID_PRIVATE（真实物理目录，可写），
               因为 __file__ 通常指向 zip 包内的虚拟路径，是只读/不可写入的。
    """
    base = dirname(abspath(__file__))
    # Android 上 base 可能是 _applibs.zip 内路径（不可写），换成 ANDROID_PRIVATE
    try:
        from kivy.utils import platform
    except Exception:
        platform = None
    if platform == "android":
        priv = os.environ.get("ANDROID_PRIVATE") or ""
        try:
            if priv and exists(priv):
                # 确保目录可写，顺便做个小测试
                test_fn = join(priv, ".write_test_" + str(os.getpid()))
                try:
                    with open(test_fn, "w") as f:
                        f.write("ok")
                    os.remove(test_fn)
                    return priv
                except Exception:
                    pass
        except Exception:
            pass
        # 兜底：android.storage 模块
        try:
            from android.storage import app_storage_path
            return app_storage_path()
        except Exception:
            pass
    return base

APP_FOLDER = _resolve_app_folder()
os.environ.setdefault("KIVY_HOME", join(APP_FOLDER, ".kivy"))
CRASH_LOG = join(APP_FOLDER, "crash.log")
BT_DEBUG_LOG = join(APP_FOLDER, "bt_debug.log")

def _resolve_default_music_dir():
    """跨平台默认音乐目录：
    - 桌面: APP_FOLDER/music（随项目走）
    - Android: 优先用 app 私有存储目录（不需要存储权限），用户可通过「选择目录」按钮选公共目录
    """
    try:
        from kivy.utils import platform
    except Exception:
        platform = None
    if platform == "android":
        # Android 13+ Scoped Storage: 优先用 app 私有目录（不需要权限）
        try:
            from android.storage import app_storage_path
            d = join(app_storage_path(), "music")
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass
        # 兼容旧版 Android
        try:
            from android.storage import primary_external_storage_path
            ext = primary_external_storage_path()
            d = join(ext, "Music")
            try:
                os.makedirs(d, exist_ok=True)
                test_fn = join(d, ".write_test_" + str(os.getpid()))
                with open(test_fn, "w") as f:
                    f.write("ok")
                os.remove(test_fn)
                return d
            except Exception:
                pass
        except Exception:
            pass
    d = join(APP_FOLDER, "music")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d

# ===== 全局崩溃日志 hook（闪退时写入 crash.log，避免"点击闪退毫无痕迹"）=====
def _write_crash(title, exc_type, exc_value, tb):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n")
            traceback.print_exception(exc_type, exc_value, tb, file=f)
    except Exception:
        pass
    # 同时打印到控制台方便现场看
    traceback.print_exception(exc_type, exc_value, tb)

def _global_except_hook(exc_type, exc_value, tb):
    _write_crash("UNHANDLED EXCEPTION", exc_type, exc_value, tb)
def _thread_except_hook(args):
    _write_crash(f"THREAD[{getattr(args.thread, 'name', '?')}] EXCEPTION",
                 args.exc_type, args.exc_value, args.exc_traceback)

sys.excepthook = _global_except_hook
if hasattr(threading, "excepthook"):
    threading.excepthook = _thread_except_hook

def jni_detail(e):
    """把 jnius 抛出的 JavaException 还原成可读信息。
    jnius 很多情况下 str(e) 只有一句 "JVM exception occurred"，真正的类名和信息
    藏在 e.classname / e.innermessage / e.stacktrace 里，必须单独取出来，
    否则界面上永远只看到"JVM exception occurred"，无从定位。"""
    parts = []
    cls = getattr(e, "classname", None)
    inner = getattr(e, "innermessage", None)
    if cls:
        parts.append(str(cls))
    if inner:
        parts.append(str(inner))
    if not parts:
        parts.append(str(e) or e.__class__.__name__)
    st = getattr(e, "stacktrace", None)
    if st:
        try:
            frames = [str(x).strip() for x in list(st)[:4]]
            if frames:
                parts.append("[Java] " + " <- ".join(frames))
        except Exception:
            pass
    return " ".join(parts)

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.properties import (
    StringProperty, BooleanProperty, ListProperty, NumericProperty, ObjectProperty
)
from kivy.clock import Clock, mainthread
from kivy.core.audio import SoundLoader
from kivy.uix.popup import Popup
from kivy.utils import platform
from kivy.core.text import LabelBase
from kivy.config import Config


APP_FONT_NAME = "AppCJKFont"   # 全局中文本字体注册名（不与系统Roboto冲突）
APP_FONT_PATH = None            # 实际字体文件绝对路径（运行时探测）


def setup_cjk_font():
    """注册中文本字体。优先使用项目自带 data/fonts/MSYH.ttc（跨平台稳定，随APK打包）。

    跨平台路径说明：
      - Windows/Linux/Mac：入口脚本 main.py 在真实目录，用 dirname(abspath(__file__))。
      - Android（p4a/Buildozer 打包）：
          main.py 通常被压缩进 _applibs.zip，abspath(__file__) 指向 zip 内虚拟路径，
          exists() 会 False；p4a 会注入环境变量 ANDROID_PRIVATE，指向 --private
          目录的真实解压目录（main.py同级的 data/fonts/ 就在这里），用它更稳。
    """
    global APP_FONT_PATH

    # --- Step 1: 拿到"基准目录"（main.py所在目录）---
    if platform == "android":
        # 优先 p4a 注入的 ANDROID_PRIVATE（真实物理目录，存在）
        base_dir = os.environ.get("ANDROID_PRIVATE") or ""
        if not base_dir or not exists(base_dir):
            # 某些旧 p4a 没给 ANDROID_PRIVATE，用 user_data_dir 的父级兜底
            try:
                from android.storage import app_storage_path
                base_dir = app_storage_path()
            except Exception:
                base_dir = dirname(abspath(__file__))
    else:
        base_dir = dirname(abspath(__file__))

    app_font_dir = join(base_dir, "data", "fonts")
    candidates = []

    # --- 最高优先级：项目自带字体（Windows / Android 通用，随 APK 打包）---
    if exists(app_font_dir):
        for name in sorted(os.listdir(app_font_dir)):
            if name.lower().endswith((".ttf", ".ttc", ".otf")):
                candidates.append(join(app_font_dir, name))

    # --- 次优先级：系统字体（项目里没带时兜底）---
    if platform == "win":
        win_fonts = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"
        candidates += [
            join(win_fonts, "msyh.ttc"), join(win_fonts, "msyh.ttf"),
            join(win_fonts, "msyhbd.ttc"), join(win_fonts, "simhei.ttf"),
            join(win_fonts, "simsun.ttc"), join(win_fonts, "simkai.ttf"),
        ]
    elif platform == "android":
        candidates += [
            "/system/fonts/NotoSansCJK-Regular.ttc",
            "/system/fonts/NotoSansSC-Regular.otf",
            "/system/fonts/DroidSansFallback.ttf",
            "/system/fonts/MTLmr3m.ttf",
        ]
    elif platform == "macosx":
        candidates += [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:  # linux
        candidates += [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ]

    def first_exist(paths):
        for p in paths:
            if p and exists(p):
                return p
        return None

    regular = first_exist(candidates)
    if not regular:
        print("[FONT] 未找到任何 CJK 字体文件，中文可能显示为方块。")
        return
    APP_FONT_PATH = regular
    bold = first_exist([p for p in candidates if "bd" in p.lower() or "bold" in p.lower()]) or regular

    # --- 注册一个**唯一名字** AppCJKFont，KV 中所有中文控件显式绑定这个名字 ---
    try:
        LabelBase.register(
            name=APP_FONT_NAME,
            fn_regular=regular, fn_bold=bold,
            fn_italic=regular, fn_bolditalic=bold,
        )
        print(f"[FONT] 注册字体名称: {APP_FONT_NAME}  文件: {regular}")
    except Exception as e:
        # 名字重复注册（二次运行），直接跳过错不影响
        print(f"[FONT] 名字已注册或失败（忽略）: {e}")

    # --- 【经验 487559：不再重注册"Roboto"同名字体】
    # 重注册同名 Roboto 会破坏 KivyMD 内部控件（含图标字体、不同font_style映射），
    # 造成"图标变方框/组件字体变化"。改为通过 theme_cls.font_styles 做定点映射。
    #
    # --- 正确兜底：设置 Kivy 原生默认字体（仅影响 Label/Button 等非 KivyMD 原生控件）---
    try:
        Config.set("kivy", "default_font", [
            APP_FONT_NAME, regular, regular, regular, regular,
        ])
    except Exception:
        pass


setup_cjk_font()


from kivymd.app import MDApp
from kivymd.uix.button import MDRaisedButton, MDFlatButton, MDFloatingActionButton, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.list import OneLineListItem, TwoLineListItem, MDList
from kivymd.uix.dialog import MDDialog
from kivymd.uix.snackbar import MDSnackbar
from kivymd.uix.card import MDCard
from kivymd.uix.toolbar import MDTopAppBar
from kivymd.uix.taptargetview import MDTapTargetView
from kivymd.uix.progressbar import MDProgressBar
from kivymd.icon_definitions import md_icons

# ================= 常量配置 =================
PREF_FILE = join(APP_FOLDER, "bt_prefs.json")
RING_CMD = bytes([0xAA, 0x55, 0x01, 0x00, 0x01])  # 响铃指令示例帧，可按需修改
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")


# ================= 蓝牙服务层 (跨平台封装) =================

# 蓝牙扫描回调不在这里用 PythonJavaClass 实现。
# 原因：jnius 的 PythonJavaClass 底层是 java.lang.reflect.Proxy，只能代理「接口」，
# 而 BroadcastReceiver / ScanCallback 都是「抽象类」，实例化时会抛
#   java.lang.IllegalArgumentException: android.content.BroadcastReceiver is not an interface
# 因此扫描回调改在 Java 侧实现，见 src/org/btring/BtBridge.java。


# ================= 音频播放后端 =================
# 背景：Kivy 的 SoundLoader 在 Android 上走 SDL2_mixer，p4a 打包后一般不带 MP3 解码器，
# 表现为 SoundLoader.load() 返回 None → 手机上 MP3 打不开、提示"无法播放此格式"。
# 因此 Android 平台改用系统原生 android.media.MediaPlayer：
#   - 原生支持 mp3 / aac / m4a / flac / ogg / wav 等格式
#   - 可直接播放 MediaStore 的 content:// URI，无需先拷贝成临时文件
# 桌面端仍用 SoundLoader，两套实现暴露同一组方法：load/play/pause/stop/release/pos/length

try:
    from jnius import PythonJavaClass, java_method

    class MediaPlayerCompleteListener(PythonJavaClass):
        """MediaPlayer 播放结束回调（模块级定义保证 jnius 稳定）"""
        __javainterfaces__ = ["android/media/MediaPlayer$OnCompletionListener"]

        def __init__(self):
            super().__init__()
            self.outer = None

        @java_method("(Landroid/media/MediaPlayer;)V")
        def onCompletion(self, mp):
            if self.outer is not None:
                self.outer._on_mp_complete()

    class MediaPlayerErrorListener(PythonJavaClass):
        """MediaPlayer 播放出错回调"""
        __javainterfaces__ = ["android/media/MediaPlayer$OnErrorListener"]

        def __init__(self):
            super().__init__()
            self.outer = None

        @java_method("(Landroid/media/MediaPlayer;II)Z")
        def onError(self, mp, what, extra):
            if self.outer is not None:
                self.outer._on_mp_error(what, extra)
            return False
except ImportError:
    MediaPlayerCompleteListener = None
    MediaPlayerErrorListener = None


class KivySoundPlayer:
    """桌面端播放器：基于 Kivy SoundLoader"""

    backend = "kivy"

    def __init__(self, on_complete=None, on_error=None):
        self._sound = None
        self._on_complete = on_complete
        self._on_error = on_error
        self.length = 0

    def load(self, src):
        self.release()
        try:
            sound = SoundLoader.load(src)
        except Exception as e:
            print(f"[Player] SoundLoader error: {e}")
            sound = None
        if not sound:
            return False
        self._sound = sound
        try:
            sound.bind(on_stop=lambda *a: self._on_complete and self._on_complete())
        except Exception:
            pass
        self.length = sound.length or 0
        return True

    def play(self):
        if self._sound:
            self._sound.play()

    def pause(self):
        # SoundLoader 没有 pause，用 stop 近似（位置会回退，仅桌面兜底用）
        if self._sound:
            self._sound.stop()

    def stop(self):
        if self._sound:
            self._sound.stop()

    def release(self):
        if self._sound:
            try:
                self._sound.stop()
                self._sound.unload()
            except Exception:
                pass
        self._sound = None
        self.length = 0

    @property
    def pos(self):
        s = self._sound
        if not s:
            return 0
        try:
            if hasattr(s, "get_pos") and s.get_pos():
                return s.get_pos()
            if hasattr(s, "position"):
                return s.position
        except Exception:
            pass
        return 0


class AndroidMediaPlayer:
    """Android 端播放器：android.media.MediaPlayer

    用系统原生解码器，支持 content:// URI 直接播放（MediaStore 音频），也支持本地文件路径。
    """

    backend = "mediaplayer"

    def __init__(self, on_complete=None, on_error=None):
        self._mp = None
        self._complete_lsn = None
        self._error_lsn = None
        self._on_complete = on_complete
        self._on_error = on_error
        self.length = 0

    # ---- Java 线程回调 → 切回 Kivy 主线程 ----
    def _on_mp_complete(self):
        cb = self._on_complete
        if cb:
            Clock.schedule_once(lambda dt: cb(), 0)

    def _on_mp_error(self, what, extra):
        print(f"[Player] MediaPlayer onError what={what} extra={extra}")
        cb = self._on_error
        if cb:
            Clock.schedule_once(lambda dt: cb(f"播放出错 (what={what}, extra={extra})"), 0)

    def load(self, src):
        self.release()
        mp = None
        try:
            from jnius import autoclass
            MediaPlayer = autoclass("android.media.MediaPlayer")
            mp = MediaPlayer()
            if MediaPlayerCompleteListener is not None:
                self._complete_lsn = MediaPlayerCompleteListener()
                self._complete_lsn.outer = self
                mp.setOnCompletionListener(self._complete_lsn)
                self._error_lsn = MediaPlayerErrorListener()
                self._error_lsn.outer = self
                mp.setOnErrorListener(self._error_lsn)

            if src.startswith("content://"):
                Uri = autoclass("android.net.Uri")
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                activity = PythonActivity.mActivity
                mp.setDataSource(activity, Uri.parse(src))
            else:
                mp.setDataSource(src)

            mp.prepare()
            dur = mp.getDuration()
            self.length = (dur / 1000.0) if dur and dur > 0 else 0
            self._mp = mp
            print(f"[Player] MediaPlayer ready: {src} (length={self.length:.1f}s)")
            return True
        except Exception as e:
            print(f"[Player] MediaPlayer load failed: {e}")
            traceback.print_exc()
            if mp is not None:
                try:
                    mp.release()
                except Exception:
                    pass
            self._mp = None
            return False

    def play(self):
        if self._mp:
            try:
                self._mp.start()
            except Exception as e:
                print(f"[Player] MediaPlayer start error: {e}")

    def pause(self):
        if self._mp:
            try:
                self._mp.pause()
            except Exception:
                pass

    def stop(self):
        if self._mp:
            try:
                self._mp.stop()
            except Exception:
                pass

    def release(self):
        if self._mp:
            try:
                self._mp.reset()
            except Exception:
                pass
            try:
                self._mp.release()
            except Exception:
                pass
        self._mp = None
        self.length = 0

    @property
    def pos(self):
        if not self._mp:
            return 0
        try:
            p = self._mp.getCurrentPosition() / 1000.0
            return p if p > 0 else 0
        except Exception:
            return 0


def create_audio_player(on_complete=None, on_error=None):
    """按平台创建播放器：Android 用原生 MediaPlayer，其余用 SoundLoader"""
    if platform == "android" and MediaPlayerCompleteListener is not None:
        return AndroidMediaPlayer(on_complete=on_complete, on_error=on_error)
    return KivySoundPlayer(on_complete=on_complete, on_error=on_error)


class BluetoothService:
    """蓝牙服务封装：Android走jnius原生API，桌面走模拟模式"""

    def __init__(self):
        self.connected = False
        self.connected_device = None  # {"name":xx, "address":xx}
        self.paired_devices = []      # 已配对设备列表
        self.scanned_devices = []     # 扫描到的新设备
        self.on_device_found = None   # 回调: 扫描到设备
        self.on_connected = None      # 回调: 连接成功
        self.on_disconnected = None   # 回调: 断开
        self._android_bt = None
        self._socket = None
        self._init_platform()

    def _init_platform(self):
        if platform == "android":
            self._init_android()
        else:
            # 桌面/调试模式：模拟一些已配对设备供UI测试
            self.paired_devices = [
                {"name": "My_BT_Device", "address": "00:11:22:33:44:55"},
                {"name": "Headphones-X1", "address": "AA:BB:CC:DD:EE:FF"},
            ]

    def _init_android(self):
        try:
            from jnius import autoclass, cast
            BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
            self._bt_adapter = BluetoothAdapter.getDefaultAdapter()
            self._BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")
            self._UUID = autoclass("java.util.UUID")
            # SPP 标准 UUID
            self._spp_uuid = self._UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
            self._cast = cast
            self._permissions_granted = False
            self.last_error = ""
            self.on_status = None
            # Java 侧扫描桥接类（BroadcastReceiver / ScanCallback 都是抽象类，
            # jnius 的 PythonJavaClass 无法代理，只能在 Java 里实现，见 src/org/btring/BtBridge.java）
            self._BtBridge = autoclass("org.btring.BtBridge")
            self._ClassicReceiver = autoclass("org.btring.BtBridge$ClassicReceiver")
            self._BleCallback = autoclass("org.btring.BtBridge$BleCallback")
            # Android 12(API 31) 起扫描用 BLUETOOTH_SCAN / BLUETOOTH_CONNECT 运行时权限；
            # 更低版本靠 ACCESS_FINE_LOCATION，且必须打开系统定位开关才扫得到设备
            try:
                self._sdk_int = autoclass("android.os.Build$VERSION").SDK_INT
            except Exception:
                self._sdk_int = 0
            print(f"[BT] Android init done, sdk={self._sdk_int}")
        except Exception as e:
            print(f"[BT] Android init error: {e}")
            self._BtBridge = None
            self._ClassicReceiver = None
            self._BleCallback = None

    # -------- 权限 & 开关状态（带诊断信息） --------
    def _required_perms(self):
        """按系统版本返回扫描真正需要的运行时权限"""
        try:
            from android.permissions import Permission
        except Exception:
            return []
        perms = []
        names = ("BLUETOOTH_SCAN", "BLUETOOTH_CONNECT") if getattr(self, "_sdk_int", 0) >= 31 \
            else ("ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION")
        for name in names:
            try:
                perms.append(getattr(Permission, name))
            except Exception:
                pass
        return perms

    def _java_perm_names(self):
        """返回需要检查的 Android 权限全名列表（用系统 API 校验，最可靠）"""
        if getattr(self, "_sdk_int", 0) >= 31:
            return ["android.permission.BLUETOOTH_SCAN",
                    "android.permission.BLUETOOTH_CONNECT"]
        return ["android.permission.ACCESS_FINE_LOCATION"]

    def _has_java_permission(self, perm):
        """直接用 Context.checkSelfPermission 校验权限。
        p4a 的 check_permission 在部分机型/权限上会给出错误结果，
        直接问系统最准；返回 None 表示查询本身失败。"""
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            PackageManager = autoclass("android.content.pm.PackageManager")
            activity = PythonActivity.mActivity
            if activity is None:
                return None
            return activity.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED
        except Exception as e:
            print(f"[BT] checkSelfPermission({perm}) error: {jni_detail(e)}")
            return None

    def permission_report(self):
        """返回 (是否已全部授予, 未授予权限名列表)。
        优先用系统 Java API 校验，失败时回退到 p4a 的 check_permission。"""
        missing = []
        java_ok = True
        for full in self._java_perm_names():
            granted = self._has_java_permission(full)
            if granted is None:
                java_ok = False
            elif not granted:
                missing.append(full.split(".")[-1])
        if java_ok:
            return (len(missing) == 0), missing
        # 回退：p4a 的 check_permission
        try:
            from android.permissions import check_permission
        except Exception:
            return True, []
        missing = []
        for p in self._required_perms():
            try:
                if not check_permission(p):
                    missing.append(str(p).split(".")[-1])
            except Exception:
                pass
        return (len(missing) == 0), missing

    def ensure_permissions(self, on_done, status_cb=None):
        """申请（或重新申请）蓝牙权限，授权成功后回调 on_done()"""
        ok, missing = self.permission_report()
        if ok:
            on_done()
            return
        self._report("等待授权: " + ", ".join(missing), status_cb)
        try:
            from android.permissions import request_permissions

            def _cb(permissions, grants):
                ok2, missing2 = self.permission_report()
                print(f"[BT] 授权结果 ok={ok2} missing={missing2}")
                if ok2:
                    self._report("权限已授予", status_cb)
                    Clock.schedule_once(lambda dt: self._safe_call(on_done, status_cb), 0.2)
                else:
                    self._report("权限被拒绝: " + ", ".join(missing2) + "（请到系统设置里手动允许）", status_cb)
            # 直接传权限全名字符串，避免依赖 p4a 的 Permission 枚举（不同版本可能缺项）
            request_permissions(self._java_perm_names(), _cb)
        except Exception as e:
            print(f"[BT] request_permissions error: {jni_detail(e)}")
            # 申请流程本身失败时不要静默继续，否则后面调用会抛 SecurityException
            self._report(f"权限请求异常: {jni_detail(e)}", status_cb)

    def _safe_call(self, fn, status_cb=None):
        """执行回调并把异常细节回显到界面，避免"点了没反应/直接闪退"无从排查"""
        try:
            fn()
        except Exception as e:
            print(f"[BT] callback error: {e}")
            traceback.print_exc()
            self._report(f"操作失败: {jni_detail(e)}", status_cb)

    def is_bt_enabled(self):
        try:
            return bool(self._bt_adapter and self._bt_adapter.isEnabled())
        except Exception as e:
            print(f"[BT] isEnabled error: {e}")
            return False

    def request_enable_bt(self, status_cb=None):
        """Android 13+ 已禁止 app 静默打开蓝牙（adapter.enable() 会失效），改为弹系统确认框"""
        try:
            from jnius import autoclass
            BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
            Intent = autoclass("android.content.Intent")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            activity.startActivity(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            print("[BT] 已发送 ACTION_REQUEST_ENABLE 请求")
            self._report("请在系统弹窗中允许打开蓝牙", status_cb)
            return True
        except Exception as e:
            print(f"[BT] request enable error: {e}")
            self._report(f"无法打开蓝牙: {e}", status_cb)
            return False

    def location_report(self):
        """定位开关状态：Android 11 及以下必须打开定位才能扫到设备（部分国产 ROM 也要求）"""
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")
            activity = PythonActivity.mActivity
            lm = activity.getSystemService(Context.LOCATION_SERVICE)
            return bool(lm.isLocationEnabled())
        except Exception as e:
            print(f"[BT] location check error: {e}")
            return None

    def request_location(self, status_cb=None):
        """打开系统定位设置页。
        部分国产 ROM（realme/ColorOS 等）即使 Android 12+ 也要求打开系统定位开关，
        经典蓝牙 startDiscovery() 才会返回设备。"""
        try:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            activity.startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
            print("[BT] 已打开定位设置页")
            return True
        except Exception as e:
            print(f"[BT] request location error: {e}")
            return False

    def _report(self, text, status_cb=None):
        """把诊断信息打印到日志、写入 bt_debug.log（便于用户反馈），并回显到界面"""
        print(f"[BT] {text}")
        try:
            with open(BT_DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        except Exception:
            pass
        cb = status_cb or getattr(self, "on_status", None)
        if cb:
            try:
                Clock.schedule_once(lambda dt, t=text: cb(t), 0)
            except Exception:
                pass

    # -------- 扫描已配对设备 --------
    def get_paired_devices(self):
        if platform == "android" and hasattr(self, "_bt_adapter") and self._bt_adapter:
            try:
                bonded = self._bt_adapter.getBondedDevices().toArray()
                self.paired_devices = []
                for dev in bonded:
                    try:
                        self.paired_devices.append({
                            "name": dev.getName() or "(未知设备)",
                            "address": dev.getAddress(),
                        })
                    except Exception as e_dev:
                        print(f"[BT] 读取已配对设备失败: {e_dev}")
            except Exception as e:
                # 最常见原因：BLUETOOTH_CONNECT 未授予
                print(f"[BT] get_paired error: {e}")
                self.last_error = f"读取已配对设备失败: {e}"
        return self.paired_devices

    # -------- 开启蓝牙 --------
    def enable_bt(self):
        """确保蓝牙已打开。Android 13+ 已禁止 app 静默开启蓝牙（adapter.enable() 会失效），
        这里只做检测；未打开时弹系统确认框（见 request_enable_bt）"""
        if platform != "android" or not hasattr(self, "_bt_adapter"):
            return False
        if self.is_bt_enabled():
            return True
        self.request_enable_bt()
        return False

    # -------- 扫描附近设备（带回调） --------
    def start_scan(self, callback, status_cb=None):
        """开始扫描。callback(device_dict) 每发现一个设备调用一次；
        status_cb(text) 把诊断信息（权限/蓝牙开关/扫描进度）回显到界面"""
        self.on_device_found = callback
        self.on_status = status_cb
        self.scanned_devices = []
        if platform != "android":
            # 桌面模拟扫描：陆续上报几个假设备
            fake = [
                {"name": "FindMyTag_01", "address": "12:34:56:78:90:AB"},
                {"name": "BT_Speaker", "address": "11:22:33:44:55:66"},
                {"name": "SmartWatch", "address": "CA:FE:BA:BE:00:01"},
            ]
            def _sim():
                for d in fake:
                    time.sleep(0.8)
                    Clock.schedule_once(lambda dt, d=d: callback(d), 0)
            threading.Thread(target=_sim, daemon=True).start()
            return
        # Android：先在主线程确认权限（未授权则弹授权框，授权成功后再回调继续扫描）
        self._report("检查蓝牙权限...", status_cb)
        self.ensure_permissions(on_done=lambda: self._prepare_scan(callback), status_cb=status_cb)

    def _prepare_scan(self, callback):
        """权限就绪后：检查蓝牙开关 / 定位开关，再真正启动扫描"""
        try:
            if not self._bt_adapter:
                self._report("无蓝牙适配器", self.on_status)
                return
            if not self.is_bt_enabled():
                self._scan_retry = getattr(self, "_scan_retry", 0)
                if self._scan_retry == 0:
                    self.request_enable_bt(status_cb=self.on_status)
                self._scan_retry += 1
                if self._scan_retry <= 5:
                    # 等用户在系统弹窗里点"允许"后再自动重试
                    Clock.schedule_once(lambda dt: self._prepare_scan(callback), 2.0)
                else:
                    self._report("蓝牙未开启，请手动打开蓝牙后点「重新扫描」", self.on_status)
                return
            self._scan_retry = 0
            loc = self.location_report()
            ok, missing = self.permission_report()
            self._report(f"环境检查: sdk={self._sdk_int} 权限={'已授予' if ok else '缺失:' + ','.join(missing)} "
                         f"蓝牙=开 定位={'开' if loc else ('关' if loc is False else '未知')}",
                         self.on_status)
            if loc is False:
                self._report("系统定位未打开，已为你跳转定位设置页（realme 等机型必须开定位才能扫到设备）",
                             self.on_status)
                self.request_location(status_cb=self.on_status)
            self._do_start_scan(callback)
        except Exception as e:
            print(f"[BT] prepare scan error: {e}")
            traceback.print_exc()
            self._report(f"扫描准备失败: {jni_detail(e)}", self.on_status)

    def _cleanup_scan(self):
        """停止上一次扫描：取消轮询、注销旧广播接收器、停 BLE 扫描"""
        ev = getattr(self, "_poll_ev", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:
                pass
            self._poll_ev = None
        try:
            if getattr(self, "_bt_adapter", None):
                self._bt_adapter.cancelDiscovery()
        except Exception:
            pass
        try:
            self._stop_ble_scan()
        except Exception:
            pass
        if getattr(self, "_receiver_registered", False):
            try:
                self._scan_activity.unregisterReceiver(self._scan_receiver)
                print("[BT] 旧 receiver 已注销")
            except Exception as e:
                print(f"[BT] unregister old receiver failed: {jni_detail(e)}")
        self._receiver_registered = False
        self._scan_activity = None
        self._scan_receiver = None

    def _do_start_scan(self, callback):
        """实际执行蓝牙扫描：经典发现 + BLE 扫描双引擎。
        回调由 Java 侧 BtBridge 实现（PythonJavaClass 无法代理抽象类），
        结果存在 Java 静态列表里，Python 侧定时轮询取走。"""
        from jnius import autoclass
        self._cleanup_scan()

        if getattr(self, "_BtBridge", None) is None:
            self._report("扫描失败：Java 桥接类 org.btring.BtBridge 未加载（android.add_src 未生效）",
                         self.on_status)
            return

        # --- 步骤1: 构造 IntentFilter ---
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            flt = autoclass("android.content.IntentFilter")("android.bluetooth.device.action.FOUND")
        except Exception as e:
            self._report(f"扫描失败(创建过滤器): {jni_detail(e)}", self.on_status)
            return
        self._scan_activity = activity

        # --- 步骤2: 创建 + 注册 Java 侧广播接收器 ---
        try:
            self._scan_receiver = self._ClassicReceiver()
        except Exception as e:
            self._report(f"扫描失败(创建接收器): {jni_detail(e)}", self.on_status)
            return
        # 先试不带 flag 的两参版本（注册纯系统广播合法），失败再试带 flag 的三参版本
        try:
            activity.registerReceiver(self._scan_receiver, flt)
            self._receiver_registered = True
            print("[BT] registerReceiver OK (2-arg)")
        except Exception as e1:
            print(f"[BT] registerReceiver 2-arg failed: {jni_detail(e1)}")
            try:
                activity.registerReceiver(self._scan_receiver, flt, 2)
                self._receiver_registered = True
                print("[BT] registerReceiver OK (3-arg flag=2)")
            except Exception as e2:
                self._report(f"扫描失败(注册接收器): 两参={jni_detail(e1)} / 三参={jni_detail(e2)}",
                             self.on_status)

        # --- 步骤3: 清空旧结果 + 开始轮询 ---
        try:
            self._BtBridge.reset()
        except Exception as e:
            print(f"[BT] bridge reset error: {jni_detail(e)}")
        self._report("正在扫描附近设备...", self.on_status)
        self._poll_ev = Clock.schedule_interval(self._poll_scan_results, 0.6)

        # --- 步骤4: 经典蓝牙发现 ---
        try:
            result = self._bt_adapter.startDiscovery()
            print(f"[BT] startDiscovery() returned: {result}")
            if not result:
                self._report("经典蓝牙扫描启动失败，继续尝试 BLE 扫描", self.on_status)
        except Exception as e:
            print(f"[BT] startDiscovery error: {e}")
            self._report(f"经典蓝牙扫描异常: {jni_detail(e)}", self.on_status)

        # --- 步骤5: BLE 扫描（双引擎） ---
        self._start_ble_scan(callback)

        # --- 步骤6: 12 秒后自动停止 ---
        # 用「扫描代数」标记本轮扫描：用户中途点「重新扫描」后，旧的定时器不应再动新一轮的 receiver
        self._scan_gen = getattr(self, "_scan_gen", 0) + 1
        gen = self._scan_gen

        def _stop(dt):
            if gen != getattr(self, "_scan_gen", 0):
                return
            self._poll_scan_results()      # 把最后一批结果收进来
            self._cleanup_scan()
            n = len(self.scanned_devices)
            if n:
                self._report(f"扫描完成，共发现 {n} 个新设备", self.on_status)
            else:
                self._report("扫描完成，未发现新设备（确认对方处于可被发现状态后点「重新扫描」）",
                             self.on_status)
        Clock.schedule_once(_stop, 12)

    def _poll_scan_results(self, dt=None):
        """轮询 Java 侧收集到的扫描结果（0.6 秒一次）。
        结果格式：name|address|rssi"""
        bridge = getattr(self, "_BtBridge", None)
        if bridge is None:
            return
        try:
            for raw in bridge.drain():
                s = str(raw)
                parts = s.split("|")
                if len(parts) < 2:
                    continue
                name = parts[0].strip() or "(未命名)"
                addr = parts[1].strip()
                rssi = parts[2].strip() if len(parts) > 2 else ""
                if not addr or addr == "(unknown)":
                    continue
                self._on_found({"name": name, "address": addr, "rssi": rssi})
        except Exception as e:
            print(f"[BT] poll drain error: {jni_detail(e)}")
        try:
            code = bridge.drainError()
            if code:
                self._report(f"BLE 扫描失败: {self._ble_err_desc(code)}", self.on_status)
        except Exception as e:
            print(f"[BT] poll error code failed: {jni_detail(e)}")

    @staticmethod
    def _ble_err_desc(code):
        return {
            1: "ALREADY_STARTED(扫描已在运行)",
            2: "APP_REGISTRATION_FAILED(应用注册失败)",
            3: "INTERNAL_ERROR(系统内部错误)",
            4: "FEATURE_UNSUPPORTED(不支持BLE扫描)",
            5: "OUT_OF_HARDWARE_RESOURCES(硬件资源不足)",
            6: "SCANNING_TOO_FREQUENTLY(扫描过于频繁)",
        }.get(int(code), f"未知错误({code})")

    def _start_ble_scan(self, callback):
        """BLE 扫描作为经典扫描的补充（很多设备只在 BLE 广播里出现）"""
        scanner = None
        try:
            from jnius import autoclass
            scanner = self._bt_adapter.getBluetoothLeScanner()
            if scanner is None:
                self._report("BLE 扫描不可用（未获取到扫描器）", self.on_status)
                return

            # 创建 ScanSettings: SCAN_MODE_LOW_LATENCY 最快发现设备
            ScanSettings = autoclass("android.bluetooth.le.ScanSettings")
            settings_builder = ScanSettings.Builder()
            settings_builder.setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            settings = settings_builder.build()

            # 使用 Java 侧 BleCallback（ScanCallback 是抽象类，Python 侧无法实现）
            self._ble_callback = self._BleCallback()
            scanner.startScan(None, settings, self._ble_callback)
            self._ble_scanner = scanner
            print("[BT-BLE] BLE scan started with LOW_LATENCY settings")
        except Exception as e:
            print(f"[BT-BLE] Error: {jni_detail(e)}")
            last = e
            # 最后 fallback: 无参 ScanSettings 版本
            try:
                if scanner is not None:
                    if getattr(self, "_ble_callback", None) is None:
                        self._ble_callback = self._BleCallback()
                    scanner.startScan(None, None, self._ble_callback)
                    self._ble_scanner = scanner
                    print("[BT-BLE] BLE scan started (fallback, no settings)")
                    return
            except Exception as e2:
                print(f"[BT-BLE] Fallback also failed: {jni_detail(e2)}")
                last = e2
            self._report(f"BLE 扫描启动失败: {jni_detail(last)}", self.on_status)

    def _stop_ble_scan(self):
        """停止 BLE 扫描"""
        cb = getattr(self, "_ble_callback", None)
        scanner = getattr(self, "_ble_scanner", None)
        if cb is None or scanner is None:
            return
        try:
            scanner.stopScan(cb)
            print("[BT-BLE] BLE scan stopped")
        except Exception as e:
            print(f"[BT-BLE] stop error: {jni_detail(e)}")
        finally:
            self._ble_callback = None
            self._ble_scanner = None

    @mainthread
    def _on_found(self, info):
        # 去重
        for d in self.scanned_devices:
            if d["address"] == info["address"]:
                return
        self.scanned_devices.append(info)
        if self.on_device_found:
            self.on_device_found(info)
        self._report(f"已发现 {len(self.scanned_devices)} 个新设备")

    # -------- 连接设备（SPP） --------
    def connect(self, device, on_success=None, on_error=None):
        self.on_connected = on_success
        self._on_connect_error = on_error
        self.connected_device = device
        if platform != "android":
            def _sim():
                time.sleep(1)
                self.connected = True
                Clock.schedule_once(lambda dt: on_success and on_success(device), 0)
            threading.Thread(target=_sim, daemon=True).start()
            return
        # 连接需要 BLUETOOTH_CONNECT，先确认权限（已授权则直接继续）
        self.ensure_permissions(
            on_done=lambda: Clock.schedule_once(lambda dt: self._connect_step1(device), 0.1)
        )

    def _connect_step1(self, device):
        """Step 1: 检查 adapter + 蓝牙开关 + 获取远程设备"""
        try:
            if not getattr(self, "_bt_adapter", None):
                self._safe_error("蓝牙未初始化")
                return
            # 1) 蓝牙开关：未打开就先弹系统框，等真正打开后再继续（否则后面调用必抛异常）
            try:
                enabled = self.is_bt_enabled()
            except Exception as e:
                self._safe_error(f"[step1-开关] {jni_detail(e)}")
                return
            if not enabled:
                self._connect_retry = getattr(self, "_connect_retry", 0) + 1
                if self._connect_retry == 1:
                    self.request_enable_bt()
                if self._connect_retry <= 5:
                    Clock.schedule_once(lambda dt: self._connect_step1(device), 1.5)
                else:
                    self._connect_retry = 0
                    self._safe_error("蓝牙未开启，请先打开蓝牙再连接")
                return
            self._connect_retry = 0

            addr = device.get("address", "")
            if not addr:
                self._safe_error("设备地址为空")
                return
            # 2) 取远程设备
            print(f"[BT] connect: getting remote device {addr}")
            try:
                dev = self._bt_adapter.getRemoteDevice(addr)
            except Exception as e:
                self._safe_error(f"[step1-获取设备] {jni_detail(e)}")
                return
            if dev is None:
                self._safe_error("无法获取远程设备")
                return
            self._connect_dev = dev
            # 下一步：检查配对状态
            Clock.schedule_once(lambda dt: self._connect_step2(dev), 0.1)
        except Exception as e:
            print(f"[BT] connect step1 error: {e}")
            self._safe_error(f"[step1] {jni_detail(e)}")

    def _connect_step2(self, dev):
        """Step 2: 检查配对状态，如未配对则配对"""
        try:
            bonded = self._BluetoothDevice.BOND_BONDED
            try:
                state = dev.getBondState()
            except Exception as e:
                self._safe_error(f"[step2-读配对状态] {jni_detail(e)}")
                return
            if state != bonded:
                print("[BT] not bonded, creating bond...")
                try:
                    dev.createBond()
                except Exception as e:
                    self._safe_error(f"[step2-配对] {jni_detail(e)}")
                    return
                # 用 Clock 轮询配对状态
                self._bond_wait_count = 0
                Clock.schedule_once(lambda dt: self._connect_wait_bond(dev), 1.0)
            else:
                print("[BT] already bonded, creating socket...")
                Clock.schedule_once(lambda dt: self._connect_step3(dev), 0.1)
        except Exception as e:
            print(f"[BT] connect step2 error: {e}")
            self._safe_error(f"[step2] {jni_detail(e)}")

    def _connect_wait_bond(self, dev):
        """等待配对完成"""
        try:
            self._bond_wait_count += 1
            if dev.getBondState() == self._BluetoothDevice.BOND_BONDED:
                print("[BT] bond completed")
                Clock.schedule_once(lambda dt: self._connect_step3(dev), 0.1)
            elif self._bond_wait_count > 10:
                print("[BT] bond timeout")
                self._safe_error("配对超时")
            else:
                Clock.schedule_once(lambda dt: self._connect_wait_bond(dev), 1.0)
        except Exception as e:
            print(f"[BT] bond wait error: {e}")
            self._safe_error(f"[配对等待] {jni_detail(e)}")

    def _connect_step3(self, dev):
        """Step 3: 创建 socket + 连接。
        注意：BluetoothSocket.connect() 是阻塞调用，必须放到后台线程执行，
        否则主线程被阻塞会触发 ANR 直接闪退。"""
        try:
            self._bt_adapter.cancelDiscovery()
            print("[BT] creating RFCOMM socket...")
            try:
                sock = dev.createRfcommSocketToServiceRecord(self._spp_uuid)
            except Exception as e:
                self._safe_error(f"[step3-建Socket] {jni_detail(e)}")
                return
            if sock is None:
                self._safe_error("无法创建 Socket")
                return
            self._socket = sock

            def _do_connect():
                try:
                    print("[BT] connecting socket (background thread)...")
                    sock.connect()
                    self.connected = True
                    print(f"[BT] connected to {self.connected_device.get('name', '?')}")

                    def _on_ok(dt):
                        if self.on_connected:
                            self.on_connected(self.connected_device)
                        threading.Thread(target=self._recv_loop, daemon=True).start()
                    Clock.schedule_once(_on_ok, 0)
                except Exception as e:
                    print(f"[BT] connect error: {e}")
                    self.connected = False
                    try:
                        sock.close()
                    except Exception:
                        pass
                    self._socket = None
                    detail = jni_detail(e)
                    Clock.schedule_once(lambda dt, d=detail: self._safe_error(f"[step3-连接] {d}"), 0)

            threading.Thread(target=_do_connect, daemon=True).start()
        except Exception as e:
            print(f"[BT] connect step3 error: {e}")
            self.connected = False
            self._safe_error(f"[step3] {jni_detail(e)}")

    def _safe_error(self, msg):
        """安全地调用错误回调（同时写入 bt_debug.log，方便事后排查）"""
        self._report(f"连接失败: {msg}")
        Clock.schedule_once(lambda dt: self._on_connect_error and self._on_connect_error(msg), 0)

    def _recv_loop(self):
        try:
            sock = self._socket
            istream = sock.getInputStream()
            buf = bytearray()
            while self.connected:
                n = istream.read()
                if n < 0:
                    break
                buf.append(n & 0xFF)
        except Exception as e:
            print(f"[BT] recv close: {e}")
        finally:
            self.connected = False
            Clock.schedule_once(lambda dt: self.on_disconnected and self.on_disconnected(), 0)

    def disconnect(self):
        self.connected = False
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        self._socket = None
        self.connected_device = None

    # -------- 发送响铃指令 --------
    def send_ring_cmd(self):
        if not self.connected:
            return False, "蓝牙未连接"
        if platform != "android":
            # 桌面模拟：打印指令
            print(f"[BT][SIM] 发送响铃指令: {RING_CMD.hex()}")
            return True, "已发送(模拟)"
        try:
            ostream = self._socket.getOutputStream()
            ostream.write(RING_CMD)
            ostream.flush()
            return True, "响铃指令已发送"
        except Exception as e:
            return False, f"发送失败: {e}"

    # -------- 自动重连：尝试连接上次保存的设备 --------
    def try_autoconnect(self, on_success, on_fail):
        if not exists(PREF_FILE):
            on_fail and on_fail("无历史设备")
            return
        try:
            with open(PREF_FILE, "r", encoding="utf-8") as f:
                last = json.load(f)
            addr = last.get("address")
            name = last.get("name", "历史设备")
            if not addr:
                on_fail and on_fail("无历史地址")
                return
            # 仅在已配对列表中存在时才自动连接
            paired = self.get_paired_devices()
            match = next((d for d in paired if d["address"] == addr), None)
            if match:
                self.connect(match, on_success=on_success,
                             on_error=lambda e: on_fail and on_fail(e))
            else:
                on_fail and on_fail("历史设备不在已配对列表中")
        except Exception as e:
            on_fail and on_fail(str(e))

    def save_last_device(self, device):
        try:
            with open(PREF_FILE, "w", encoding="utf-8") as f:
                json.dump(device, f, ensure_ascii=False)
        except Exception:
            pass


BT = BluetoothService()


# ================= UI 构建 =================
KV = """
#:import get_color_from_hex kivy.utils.get_color_from_hex
#:import MDRaisedButton kivymd.uix.button.MDRaisedButton
#:import MDIconButton kivymd.uix.button.MDIconButton
#:import MDCard kivymd.uix.card.MDCard

<MainScreen>:
    name: "main"
    MDBoxLayout:
        orientation: "vertical"
        MDTopAppBar:
            title: "蓝牙响铃"
            # 注意：不要设置 font_style，内部会覆写 font_name
            font_name: app.app_font
            left_action_items: [["music", lambda x: app.switch_to_player()]]
            right_action_items: [["bluetooth", lambda x: app.show_bt_status()]]
            md_bg_color: app.theme_cls.primary_color

        FloatLayout:
            # 中心响铃卡
            MDCard:
                size_hint: 0.85, 0.35
                pos_hint: {"center_x": 0.5, "center_y": 0.55}
                radius: [24, ]
                elevation: 4
                padding: "16dp"
                orientation: "vertical"
                MDLabel:
                    text: "设备状态"
                    font_name: app.app_font
                    halign: "center"
                    bold: True
                    font_size: "20sp"
                    theme_text_color: "Primary"
                    size_hint_y: None
                    height: "36dp"
                MDLabel:
                    text: root.status_text
                    font_name: app.app_font
                    markup: True
                    halign: "center"
                    font_size: "16sp"
                    size_hint_y: None
                    height: "28dp"
                MDLabel:
                    text: root.device_name
                    font_name: app.app_font
                    halign: "center"
                    font_size: "12sp"
                    theme_text_color: "Secondary"
                    size_hint_y: None
                    height: "24dp"

            # 底部按键行：响铃 + 蓝牙连接
            MDBoxLayout:
                size_hint: 0.85, None
                height: "72dp"
                spacing: "16dp"
                pos_hint: {"center_x": 0.5, "center_y": 0.18}
                MDRaisedButton:
                    text: "响铃"
                    icon: "bell-ring"
                    font_name: app.app_font
                    font_size: "16sp"
                    size_hint_x: 1
                    md_bg_color: get_color_from_hex("#FF5722")
                    on_release: root.on_ring()
                MDRaisedButton:
                    text: "蓝牙"
                    icon: "bluetooth-connect"
                    font_name: app.app_font
                    font_size: "16sp"
                    size_hint_x: 1
                    on_release: root.open_bt_dialog()

<PlayerScreen>:
    name: "player"
    MDBoxLayout:
        orientation: "vertical"
        MDTopAppBar:
            title: "本地播放器"
            font_name: app.app_font
            left_action_items: [["arrow-left", lambda x: app.switch_to_main()]]
            right_action_items: [["refresh", lambda x: root.refresh_songs()]]
            md_bg_color: app.theme_cls.primary_color

        MDBoxLayout:
            orientation: "vertical"
            padding: "12dp"
            spacing: "8dp"

            # 播放信息
            MDCard:
                size_hint_y: None
                height: "100dp"
                padding: "12dp"
                radius: [16,]
                MDBoxLayout:
                    orientation: "vertical"
                    MDLabel:
                        text: root.current_song or "未选择音乐"
                        font_name: app.app_font
                        bold: True
                        font_size: "18sp"
                        halign: "center"
                        size_hint_y: None
                        height: "32dp"
                    MDLabel:
                        text: root.time_text
                        font_name: app.app_font
                        halign: "center"
                        font_size: "12sp"
                        theme_text_color: "Secondary"
                        size_hint_y: None
                        height: "20dp"
                    MDProgressBar:
                        value: root.progress
                        pos_hint: {"center_y": .5}

            # 控制栏
            MDBoxLayout:
                size_hint_y: None
                height: "56dp"
                spacing: "8dp"
                MDIconButton:
                    icon: "skip-previous"
                    on_release: root.prev_song()
                    user_font_size: "32sp"
                MDIconButton:
                    icon: "play" if not root.is_playing else "pause"
                    on_release: root.toggle_play()
                    user_font_size: "40sp"
                    theme_text_color: "Primary"
                MDIconButton:
                    icon: "skip-next"
                    on_release: root.next_song()
                    user_font_size: "32sp"
                MDIconButton:
                    icon: "folder-music"
                    on_release: root.pick_dir()
                    user_font_size: "28sp"

            # 歌曲列表
            MDLabel:
                text: f"本地音乐 ({len(root.songs)})"
                font_name: app.app_font
                bold: True
                font_size: "15sp"
                size_hint_y: None
                height: "28dp"

            ScrollView:
                MDList:
                    id: song_list
"""


class MainScreen(Screen):
    status_text = StringProperty("等待连接...")
    device_name = StringProperty("—")
    bt_dialog = ObjectProperty(allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bt_dialog = None
        BT.on_disconnected = self._on_disconnected

    def on_pre_enter(self):
        # 进入界面时尝试自动连接
        if not BT.connected:
            self.status_text = "自动连接中..."
            BT.try_autoconnect(
                on_success=self._on_connected,
                on_fail=self._on_autoconnect_fail,
            )

    def _on_connected(self, device):
        self.status_text = "[color=#4CAF50]已连接[/color]"
        self.device_name = f"{device['name']} ({device['address']})"
        BT.save_last_device(device)

    def _on_autoconnect_fail(self, msg):
        self.status_text = "未连接 (点击蓝牙按键连接)"
        self.device_name = "—"

    def _on_disconnected(self):
        self.status_text = "已断开"
        self.device_name = "—"

    def on_ring(self):
        ok, msg = BT.send_ring_cmd()
        if ok:
            App.get_running_app().toast(msg)
        else:
            App.get_running_app().toast(msg, error=True)

    # ================= 蓝牙搜索/配对弹窗 =================
    def open_bt_dialog(self):
        try:
            # 弹窗内容
            content = BTDialogContent()
            close_btn = MDFlatButton(text="关闭", on_release=lambda x: dialog.dismiss())
            try:
                close_btn.font_name = APP_FONT_NAME
            except Exception:
                pass
            dialog = MDDialog(
                title="蓝牙设备",
                type="custom",
                content_cls=content,
                buttons=[close_btn],
            )
            self.bt_dialog = dialog
            content.on_connect_requested = self._on_connect_from_dialog
            content.start_scan()
            dialog.open()
            # MDDialog.open() 后动态创建内部子控件，必须扫一遍
            app = App.get_running_app()
            if hasattr(app, "_recursive_set_font") and hasattr(app, "_safe_cb"):
                sb = app._safe_cb
                rs = app._recursive_set_font
                Clock.schedule_once(sb(lambda: rs(dialog)), 0.05)
                # 扫描结果异步到达，延迟再扫两次确保附近设备列表项出来后字体正确
                Clock.schedule_once(sb(lambda: rs(dialog)), 1.2)
                Clock.schedule_once(sb(lambda: rs(dialog)), 2.5)
        except Exception:
            print("[open_bt_dialog] EXCEPTION:")
            traceback.print_exc()
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as f:
                    f.write(f"\n[{time.strftime('%H:%M:%S')}] open_bt_dialog EXCEPTION\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass
            # 给用户提示
            try:
                App.get_running_app().toast("蓝牙弹窗创建失败，详情见 crash.log")
            except Exception:
                pass

    def _on_connect_from_dialog(self, instance, device):
        # 关键：不要在触摸/点击回调里同步 dismiss 弹窗。
        # 同步移除正在处理本次触摸的控件，会让 Kivy 的触摸事件循环拿到已销毁的对象 → 直接闪退。
        # 改为延迟一帧关闭。
        Clock.schedule_once(lambda dt: self._close_bt_dialog(), 0)
        self.status_text = f"连接中: {device['name']}..."
        BT.connect(
            device,
            on_success=self._on_connected,
            on_error=lambda e: self._on_connect_error(e, device),
        )

    def _close_bt_dialog(self):
        try:
            if self.bt_dialog:
                self.bt_dialog.dismiss()
        except Exception as e:
            print(f"[BT-UI] dismiss error: {e}")
        self.bt_dialog = None

    def _on_connect_error(self, err, device):
        self.status_text = f"连接失败: {device['name']}"
        App.get_running_app().toast(f"连接失败: {err}", error=True)


class BTDialogContent(ScrollView):
    """蓝牙弹窗的二级界面：已配对列表 + 扫描到的设备列表"""
    # 注意：不要用 ObjectProperty + dispatch()。
    # Kivy 的 dispatch() 要求事件已注册（未注册会抛 KeyError: 'on_connect_requested'），
    # 只声明同名 Property 并不等于注册事件。这里用普通属性回调，最稳。
    on_connect_requested = None

    @staticmethod
    def _apply_font(item):
        """给 TwoLineListItem/OneLineListItem 绑定中文字体：主文本 + 次文本都要设置"""
        try:
            item.font_name = APP_FONT_NAME
        except Exception:
            pass
        try:
            item.ids._lbl_primary.font_name = APP_FONT_NAME
        except Exception:
            pass
        try:
            item.ids._lbl_secondary.font_name = APP_FONT_NAME
        except Exception:
            pass

    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint_y = None
        self.height = "420dp"
        self.bar_width = "4dp"
        self._box = MDList(padding="4dp")
        self.add_widget(self._box)
        # 已配对分区
        self._paired_title = TwoLineListItem(
            text="已配对设备",
            secondary_text="直接点击连接",
            divider=None,
        )
        self._apply_font(self._paired_title)
        # 不用 font_style="Subtitle1"（内部会覆写 font_name），改为手动调字号+加粗
        try:
            self._paired_title.ids._lbl_primary.bold = True
            self._paired_title.ids._lbl_primary.font_size = "15sp"
            self._paired_title.ids._lbl_primary.theme_text_color = "Primary"
        except Exception:
            pass
        self._box.add_widget(self._paired_title)
        # 扫描分区
        self._scan_title = TwoLineListItem(
            text="附近设备 (扫描中...)",
            secondary_text="点击进行配对连接",
            divider=None,
        )
        self._apply_font(self._scan_title)
        try:
            self._scan_title.ids._lbl_primary.bold = True
            self._scan_title.ids._lbl_primary.font_size = "15sp"
            self._scan_title.ids._lbl_primary.theme_text_color = "Primary"
        except Exception:
            pass
        self._box.add_widget(self._scan_title)
        # 诊断状态行：权限 / 蓝牙开关 / 定位开关 / 扫描进度，出问题时一眼能看到原因
        self._status_item = TwoLineListItem(
            text="扫描状态",
            secondary_text="准备中...",
            divider=None,
        )
        self._apply_font(self._status_item)
        try:
            self._status_item.ids._lbl_primary.bold = True
            self._status_item.ids._lbl_primary.font_size = "14sp"
            self._status_item.ids._lbl_secondary.font_size = "12sp"
        except Exception:
            pass
        self._box.add_widget(self._status_item)
        # 重新扫描入口（授权或打开蓝牙后可随时重试）
        self._rescan_item = OneLineListItem(
            text="↻ 重新扫描",
            on_release=lambda x: self.start_scan(),
        )
        self._apply_font(self._rescan_item)
        self._box.add_widget(self._rescan_item, index=1)
        # 诊断日志入口（日志在 App 私有目录，文件管理器看不到，必须 App 内查看）
        self._log_item = OneLineListItem(
            text="📄 查看诊断日志",
            on_release=lambda x: self._show_logs(),
        )
        self._apply_font(self._log_item)
        self._box.add_widget(self._log_item, index=1)
        self._found_count = 0
        # 填充已配对
        for d in BT.get_paired_devices():
            self._add_paired(d)

    def _add_paired(self, d):
        item = TwoLineListItem(
            text=f"[已配对] {d['name']}",
            secondary_text=d["address"],
            on_release=lambda x, dev=d: self._emit_connect(dev),
        )
        self._apply_font(item)
        self._box.add_widget(item, index=1)  # 插入到分区标题之后

    def _add_scanned(self, d):
        rssi = d.get("rssi", "")
        extra = f"RSSI: {rssi}  |  {d['address']}" if rssi else d["address"]
        item = TwoLineListItem(
            text=d["name"],
            secondary_text=extra,
            on_release=lambda x, dev=d: self._emit_connect(dev),
        )
        self._apply_font(item)
        # 找到扫描标题位置，追加到其后
        idx = self._box.children.index(self._scan_title)
        self._box.add_widget(item, index=idx)

    def start_scan(self):
        self._found_count = 0
        try:
            self._scan_title.text = "附近设备 (扫描中...)"
        except Exception:
            pass
        BT.start_scan(callback=self._on_found, status_cb=self._on_status)

    def _alive(self):
        """弹窗是否还挂在窗口上。
        弹窗关闭后再往已销毁的控件里写属性/加子控件，会访问已释放的 C 对象 → 闪退。"""
        try:
            return self.get_parent_window() is not None
        except Exception:
            return False

    def _on_status(self, text):
        """把蓝牙扫描的诊断信息显示到状态行"""
        print(f"[BT-UI] {text}")
        if not self._alive():
            return
        try:
            self._status_item.secondary_text = text
        except Exception as e:
            print(f"[BT-UI] set status error: {e}")

    @mainthread
    def _on_found(self, d):
        if not self._alive():
            return
        try:
            self._add_scanned(d)
            self._found_count += 1
            self._scan_title.text = f"附近设备 (已发现 {self._found_count})"
        except Exception as e:
            print(f"[BT-UI] add scanned error: {e}")

    def _emit_connect(self, dev):
        """点击设备项：调用外部注册的回调（普通属性，不走 Kivy dispatch）"""
        try:
            cb = self.on_connect_requested
            if cb is None:
                print("[BT-UI] on_connect_requested 未注册")
                return
            cb(self, dev)
        except Exception as e:
            print("[BT-UI] emit connect EXCEPTION:")
            traceback.print_exc()
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as f:
                    f.write(f"\n[{time.strftime('%H:%M:%S')}] emit_connect EXCEPTION\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass
            try:
                App.get_running_app().toast(f"发起连接失败: {jni_detail(e)}", error=True)
            except Exception:
                pass

    def _show_logs(self):
        """在 App 内查看诊断日志。
        bt_debug.log / crash.log 都写在 App 私有目录（/data/data/包名/files/），
        普通文件管理器无权访问，所以必须在 App 里提供查看入口。"""
        texts = []
        for path, label in ((BT_DEBUG_LOG, "bt_debug.log（蓝牙诊断）"),
                            (CRASH_LOG, "crash.log（崩溃记录）")):
            texts.append(f"===== {label} =====")
            try:
                if exists(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    texts.append(content[-8000:] if len(content) > 8000 else content)
                else:
                    texts.append("(暂无内容)")
            except Exception as e:
                texts.append(f"(读取失败: {e})")
        full = "\n".join(texts)

        scroll = ScrollView(size_hint_y=None, height="380dp", do_scroll_x=False)
        lbl = MDLabel(text=full, size_hint_y=None, valign="top",
                      font_name=APP_FONT_NAME, font_size="12sp")
        scroll.add_widget(lbl)

        def _fit(*a):
            try:
                lbl.text_size = (max(scroll.width - 12, 100), None)
                lbl.texture_update()
                lbl.height = max(lbl.texture_size[1], scroll.height)
            except Exception:
                pass

        copy_btn = MDFlatButton(text="复制全部",
                                on_release=lambda x: self._copy_logs(full))
        close_btn = MDFlatButton(text="关闭", on_release=lambda x: dlg.dismiss())
        for b in (copy_btn, close_btn):
            try:
                b.font_name = APP_FONT_NAME
            except Exception:
                pass
        dlg = MDDialog(title="诊断日志", type="custom",
                       content_cls=scroll, buttons=[copy_btn, close_btn])
        dlg.open()
        Clock.schedule_once(_fit, 0.1)

    def _copy_logs(self, text):
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(text)
            App.get_running_app().toast("日志已复制，可粘贴发送")
        except Exception as e:
            print(f"[BT-UI] copy error: {e}")


# ================= 播放器界面 =================
class PlayerScreen(Screen):
    songs = ListProperty([])          # [(filename, fullpath), ...]
    current_index = NumericProperty(-1)
    current_song = StringProperty("")
    is_playing = BooleanProperty(False)
    progress = NumericProperty(0)
    time_text = StringProperty("00:00 / 00:00")
    music_dir = StringProperty("")

    def __init__(self, **kw):
        super().__init__(**kw)
        self._player = None
        self._progress_ev = None
        self._perm_retry = 0
        Clock.schedule_once(lambda dt: self.refresh_songs(), 0.5)

    # ===== 播放引擎：Android=系统原生 MediaPlayer（支持 mp3），桌面=SoundLoader =====
    def _get_player(self):
        if self._player is None:
            self._player = create_audio_player(
                on_complete=self._on_song_end,
                on_error=self._on_player_error,
            )
            print(f"[Player] 播放引擎: {self._player.backend}")
        return self._player

    def _stop_playback(self):
        self.is_playing = False
        if self._progress_ev:
            self._progress_ev.cancel()
            self._progress_ev = None
        if self._player:
            try:
                self._player.release()
            except Exception:
                pass

    def _on_player_error(self, msg):
        self.is_playing = False
        if self._progress_ev:
            self._progress_ev.cancel()
            self._progress_ev = None
        App.get_running_app().toast(f"播放失败: {msg}", error=True)

    def on_enter(self):
        pass

    def pick_dir(self):
        """选择音乐目录（Android 直接用 MediaStore，桌面手动选择）"""
        if platform == "android":
            # Android: 直接用 MediaStore 查询所有音频
            self.refresh_songs()
            return
        # 桌面默认：使用用户音乐目录或app下music子目录
        home = os.path.expanduser("~")
        candidates = [
            _resolve_default_music_dir(),   # 最高优先级（Android公共音乐目录）
            join(APP_FOLDER, "music"),
            join(home, "Music"),
            join(home, "音乐"),
        ]
        for c in candidates:
            if exists(c):
                self._on_dir_selected([c])
                return
        App.get_running_app().toast("未找到音乐目录，已使用 app/music，可将音频放入后刷新")
        # 创建默认目录
        default_d = _resolve_default_music_dir()
        try:
            os.makedirs(default_d, exist_ok=True)
        except Exception:
            pass
        self.music_dir = default_d
        self.refresh_songs()

    def _on_dir_selected(self, selection):
        if not selection:
            return
        path = selection[0]
        self.music_dir = path
        self.refresh_songs()

    def _scan_android_mediastore(self):
        """Android: 用 MediaStore API 查询所有音频文件"""
        self.ids.song_list.clear_widgets()
        self.songs = []
        try:
            from jnius import autoclass
            Uri = autoclass("android.net.Uri")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()

            # 不依赖 autoclass 解析嵌套类，直接用 URI 字符串
            audio_uri = Uri.parse("content://media/external/audio/media")
            projection = [
                "_id",
                "_display_name",
                "_data",
                "title",
                "artist",
                "duration",
            ]
            cursor = resolver.query(audio_uri, projection, None, None, "title ASC")
            if cursor is None:
                App.get_running_app().toast("无法访问媒体库")
                return

            count = 0
            while cursor.moveToNext():
                try:
                    name = cursor.getString(cursor.getColumnIndexOrThrow("_display_name"))
                except Exception:
                    try:
                        data = cursor.getString(cursor.getColumnIndexOrThrow("_data"))
                        name = data.split("/")[-1] if data else f"audio_{count}"
                    except Exception:
                        name = f"audio_{count}"
                if not name:
                    name = f"audio_{count}"

                # 构建 content URI
                try:
                    id_col = cursor.getColumnIndexOrThrow("_id")
                    item_id = cursor.getString(id_col)
                    content_uri = Uri.withAppendedPath(audio_uri, item_id)
                    uri_str = content_uri.toString()
                except Exception:
                    try:
                        data = cursor.getString(cursor.getColumnIndexOrThrow("_data"))
                        uri_str = data
                    except Exception:
                        continue

                self.songs.append((name, uri_str))
                item = OneLineListItem(
                    text=name,
                    on_release=lambda x, idx=len(self.songs)-1: self.play_at(idx),
                )
                try:
                    item.font_name = APP_FONT_NAME
                except Exception:
                    pass
                try:
                    item.ids._lbl_primary.font_name = APP_FONT_NAME
                except Exception:
                    pass
                self.ids.song_list.add_widget(item)
                count += 1

            cursor.close()
            if count == 0:
                App.get_running_app().toast("未发现音频文件")
            else:
                App.get_running_app().toast(f"找到 {count} 首歌曲")
        except Exception as e:
            print(f"[Player] MediaStore scan error: {e}")
            App.get_running_app().toast(f"扫描失败: {e}", error=True)

    def _ensure_audio_permission(self):
        """Android 13+ 读媒体库需 READ_MEDIA_AUDIO（12 及以下用 READ_EXTERNAL_STORAGE）
        返回 True=权限已就绪，False=刚发起了授权请求"""
        try:
            from android.permissions import check_permission, request_permissions, Permission
        except Exception:
            return True
        perms = []
        for name in ("READ_MEDIA_AUDIO", "READ_EXTERNAL_STORAGE"):
            try:
                perm = getattr(Permission, name)
            except Exception:
                continue
            try:
                if not check_permission(perm):
                    perms.append(perm)
            except Exception:
                pass
        if perms:
            print(f"[Player] 请求媒体权限: {perms}")
            try:
                request_permissions(perms)
            except Exception:
                pass
            return False
        return True

    def refresh_songs(self):
        self.ids.song_list.clear_widgets()
        self.songs = []
        # Android: 直接用 MediaStore API 查询所有音频
        if platform == "android":
            # 没有媒体权限时 MediaStore 查不到任何歌，先申请权限，再自动重试查一次
            if not self._ensure_audio_permission() and self._perm_retry < 2:
                self._perm_retry += 1
                App.get_running_app().toast("请允许「音乐和音频」权限")
                Clock.schedule_once(lambda dt: self.refresh_songs(), 3.5)
                return
            self._scan_android_mediastore()
            return
        base = self.music_dir
        if not base:
            self.pick_dir()
            return
        # 普通文件路径递归扫描
        for root, dirs, files in os.walk(base):
            for f in sorted(files):
                if f.lower().endswith(AUDIO_EXTS):
                    full = join(root, f)
                    self.songs.append((f, full))
                    item = OneLineListItem(
                        text=f,
                        on_release=lambda x, idx=len(self.songs)-1: self.play_at(idx),
                    )
                    try:
                        item.font_name = APP_FONT_NAME
                    except Exception:
                        pass
                    try:
                        item.ids._lbl_primary.font_name = APP_FONT_NAME
                    except Exception:
                        pass
                    self.ids.song_list.add_widget(item)
        if not self.songs:
            App.get_running_app().toast("未发现音频文件")

    def play_at(self, idx):
        if idx < 0 or idx >= len(self.songs):
            return
        self.current_index = idx
        name, src = self.songs[idx]
        self.current_song = name
        self._stop_playback()
        player = self._get_player()
        print(f"[Player] 播放: {name} <- {src}")
        # Android 原生 MediaPlayer 可直接播 content:// URI（无需拷贝临时文件）
        ok = player.load(src)
        if not ok and src.startswith("content://"):
            # 兜底：直接读 content:// 失败时，再拷贝成临时文件重试一次
            tmp = self._saf_to_temp(src)
            if tmp:
                ok = player.load(tmp)
        if not ok:
            App.get_running_app().toast("无法播放此格式", error=True)
            return
        player.play()
        self.is_playing = True
        self.progress = 0
        if self._progress_ev:
            self._progress_ev.cancel()
        self._progress_ev = Clock.schedule_interval(self._update_progress, 0.5)

    def _saf_to_temp(self, content_uri_str):
        """将 content URI 的音频文件复制到 app 临时目录后返回路径"""
        pfd = None
        try:
            from jnius import autoclass
            Uri = autoclass("android.net.Uri")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()

            uri = Uri.parse(content_uri_str)
            pfd = resolver.openFileDescriptor(uri, "r")
            if pfd is None:
                print(f"[Player] openFileDescriptor returned null for {content_uri_str}")
                return None

            # 用 os.read(fd) 读 raw fd — 不接管 fd 所有权，不会双重关闭
            raw_fd = pfd.getFd()
            ext = ".mp3"
            for e in AUDIO_EXTS:
                if content_uri_str.lower().endswith(e[1:]):
                    ext = e
                    break
            tmp_path = join(APP_FOLDER, f"_tmp_audio_{int(time.time()*1000)}{ext}")
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = os.read(raw_fd, 65536)
                    if not chunk:
                        break
                    out.write(chunk)
            print(f"[Player] Copied to: {tmp_path}")
            return tmp_path
        except Exception as e:
            print(f"[Player] content URI to temp error: {e}")
            traceback.print_exc()
            return None
        finally:
            if pfd is not None:
                try:
                    pfd.close()
                except Exception:
                    pass

    def _update_progress(self, dt):
        p = self._player
        if not p:
            return
        dur = p.length or 0
        pos = p.pos or 0
        if dur > 0:
            self.progress = min(100, (pos / dur) * 100)
            self.time_text = f"{self._fmt(pos)} / {self._fmt(dur)}"

    @staticmethod
    def _fmt(sec):
        sec = int(max(0, sec))
        m, s = divmod(sec, 60)
        return f"{m:02d}:{s:02d}"

    def toggle_play(self):
        if self._player is None or self.current_index < 0:
            if self.songs:
                self.play_at(0)
            return
        if self.is_playing:
            # 先置位再暂停：避免 Kivy 后端的 on_stop 回调被误判成"播放结束"而自动切歌
            self.is_playing = False
            self._player.pause()
            if self._progress_ev:
                self._progress_ev.cancel()
                self._progress_ev = None
        else:
            self._player.play()
            self.is_playing = True
            if not self._progress_ev:
                self._progress_ev = Clock.schedule_interval(self._update_progress, 0.5)

    def next_song(self):
        if not self.songs:
            return
        nxt = (self.current_index + 1) % len(self.songs)
        self.play_at(nxt)

    def prev_song(self):
        if not self.songs:
            return
        prv = (self.current_index - 1) % len(self.songs)
        self.play_at(prv)

    def _on_song_end(self, *a):
        if self.is_playing:
            self.next_song()


# ================= 主 App =================
class BTRingApp(MDApp):
    dialog = None
    app_font = StringProperty(APP_FONT_NAME)

    # ============== 字体识别 ==============
    @classmethod
    def _is_icon_widget(cls, widget):
        """判断是否为 KivyMD 图标控件/图标Label（不该改为雅黑字体，否则图标会变□）。
        判断优先级（从强到弱）：
        1) 单字符 Unicode PUA 码位 -> 一定是图标字符（最可靠，不依赖类名/font_name）
        2) 有 icon 属性且为纯图标按钮（无 text）-> 跳过整控件（包括内部 ids）
        3) 类名是 TopAppBar/MDI 专用按钮类 -> 跳过
        4) 类名含 Icon -> 是图标控件
        5) 当前 font_name 是 MDI 图标字体路径/名 -> 是图标Label
        """
        if widget is None:
            return False
        klass_name = widget.__class__.__name__

        # 【强规则 1】单字符落在 Unicode Private Use Area -> 必是图标
        #   Material Design Icons / KivyMD icon glyphs 都用 PUA（U+E000~U+F8FF、U+F0000~U+10FFFF）
        #   中/英/数不可能落在这里，所以此判断 100% 精准零误判
        try:
            txt = getattr(widget, "text", None)
            if isinstance(txt, str) and len(txt) == 1:
                cp = ord(txt)
                if (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0x10FFFF):
                    return True
        except Exception:
            pass

        # 【强规则 2】控件显式有 icon 属性且是"纯图标按钮"（无文字）-> 整控件跳过，避免改内部图标Label
        #   KivyMD 1.x: MDFlatButton/MDRaisedButton/MDIconButton 等都支持 icon 参数
        #   MDTopAppBar action items 渲染出的按钮就是这一类
        try:
            icon_val = getattr(widget, "icon", None)
            if isinstance(icon_val, str) and icon_val.strip():
                text_val = getattr(widget, "text", None)
                if not (isinstance(text_val, str) and text_val.strip()):
                    return True
        except Exception:
            pass

        # 【规则 3】TopAppBar / Toolbar 内部专用按钮类名白名单（含内部合成的按钮）
        if any(x in klass_name for x in ("ToolbarButton", "ActionTopAppBarButton",
                                          "TopAppBarButton", "Toolbar")):
            return True

        # 【规则 4】类名含 Icon
        if any(x in klass_name for x in ("Icon", "IconButton", "MDIcon", "IconItem",
                                          "IconLeftWidget", "IconRightWidget")):
            return True

        # 【规则 5】当前 font_name 指向图标字体（注册名或实际路径名）
        cur_fn = getattr(widget, "font_name", "") or ""
        if any(x in cur_fn.lower() for x in ("icons", "materialdesignicons",
                                             "materialicons", "iconfont")):
            return True
        return False

    @classmethod
    def _safe_set_font(cls, widget):
        """给单个控件设置中文字体——前提：不是图标控件且有font_name属性"""
        if widget is None or cls._is_icon_widget(widget):
            return
        try:
            if hasattr(widget, "font_name"):
                widget.font_name = APP_FONT_NAME
        except Exception:
            pass

    # ============== 递归扫树（终极兜底） ==============
    @classmethod
    def _recursive_set_font(cls, widget, depth=0, max_depth=25):
        if widget is None or depth > max_depth:
            return
        # 仅对 Widget 类实例递归，避免扫到 Window 内部非 Widget 特殊对象导致崩溃
        try:
            from kivy.uix.widget import Widget as _B
            if not isinstance(widget, _B):
                return
        except Exception:
            pass

        # 如果整个 widget 就是一个纯图标控件（如 MDIconButton / TopAppBar 的 action 按钮）
        # 直接整棵跳过——不再递归其内部 ids/children，彻底杜绝误改内部图标 Label
        if cls._is_icon_widget(widget):
            return

        # 1) widget 本身（非图标控件才会走到这里；_safe_set_font 还会再判断一次以防万一）
        cls._safe_set_font(widget)

        # 2) widget.ids 字典（MDListItem 的主次文本Label在这里）
        try:
            the_ids = getattr(widget, "ids", None)
            if the_ids:
                try:
                    pairs = list(the_ids.items())
                except Exception:
                    try:
                        pairs = [(k, the_ids[k]) for k in list(the_ids.keys())]
                    except Exception:
                        pairs = []
                for _k, child in pairs:
                    try:
                        cls._safe_set_font(child)
                    except Exception:
                        pass
        except Exception:
            pass

        # 3) 常见私有 Label 属性（MDTopAppBar._title_label / TwoLineListItem._lbl_primary 等）
        for attr_name in ("_title_label", "_lbl_primary", "_lbl_secondary",
                          "_label", "_text_label", "_title"):
            try:
                sub = getattr(widget, attr_name, None)
                if sub is not None:
                    cls._safe_set_font(sub)
            except Exception:
                pass

        # 4) 递归 children（仅 Widget 实例）
        try:
            from kivy.uix.widget import Widget as _W2
            children = getattr(widget, "children", None) or []
            for c in list(children):
                try:
                    if isinstance(c, _W2):
                        cls._recursive_set_font(c, depth + 1, max_depth)
                except Exception:
                    continue
        except Exception:
            pass

    # ============== 安全 Clock 回调包装器（内部抛错不会让整个 App 闪退） ==============
    @staticmethod
    def _safe_cb(target):
        def _cb(dt):
            try:
                target()
            except Exception:
                print("[SAFE CLOCK] callback exception:")
                traceback.print_exc()
                try:
                    with open(CRASH_LOG, "a", encoding="utf-8") as f:
                        f.write(f"\n[{time.strftime('%H:%M:%S')}] CLOCK CALLBACK EXCEPTION\n")
                        traceback.print_exc(file=f)
                except Exception:
                    pass
        return _cb

    # ============== App 构建 ==============
    def build(self):
        self.theme_cls.primary_palette = "Blue"
        self.theme_cls.theme_style = "Light"

        # ---------- KivyMD 全局中文字体映射（核心·经验487559推荐）----------
        # 调试结果：KivyMD 1.2.0 每个 font_style entry 是 list:
        #   [font_name (str), font_size (int), bool_bold, letter_spacing]
        #   例: H1 = ['RobotoLight', 96, False, -1.5]
        #       Icon = ['Icons', 24, False, 0]
        # 所以我们只改 list[0]（字体名字符串）为已注册的 AppCJKFont，
        # 其他字段不动，Icon 条目保持原封不动。
        pure_text_styles = (
            "H1", "H2", "H3", "H4", "H5", "H6",
            "Subtitle1", "Subtitle2",
            "Body1", "Body2",
            "Button", "Caption", "Overline",
        )
        try:
            fs = getattr(self.theme_cls, "font_styles", None)
            print(f"[FONT-THEME] font_styles 实际类型: {type(fs).__name__}")
            total = 0
            if fs is not None:
                for s in pure_text_styles:
                    try:
                        entry = fs[s]
                    except Exception:
                        entry = None
                    if not isinstance(entry, list) or len(entry) < 1:
                        continue
                    # entry[0] = 字体名；换成我们注册的名字
                    entry[0] = APP_FONT_NAME
                    total += 1
            print(f"[FONT-THEME] 已将 {total}/{len(pure_text_styles)} 种 KivyMD font_style 映射到 CJK 字体")
            # 打印 Icon 条目（确保没改它）
            try:
                icon_entry = fs["Icon"] if fs is not None else None
                if isinstance(icon_entry, list) and len(icon_entry) >= 1:
                    print(f"[FONT-THEME] Icon 字体保持原样: {icon_entry[0]}")
            except Exception:
                pass
        except Exception:
            print("[FONT-THEME] 配置 font_styles 异常（忽略）：")
            traceback.print_exc()

        Builder.load_string(KV)
        self.sm = ScreenManager()
        self.main_screen = MainScreen()
        self.player_screen = PlayerScreen()
        self.sm.add_widget(self.main_screen)
        self.sm.add_widget(self.player_screen)

        # 启动后延迟重扫（兜底覆盖：MDDialog/Toast 动态创建、OverFlowMenu 延迟创建的控件）
        sb = self._safe_cb
        scan_times = (0.15, 0.6, 1.2, 2.0, 3.5, 5.0)  # 覆盖到 5 秒（TopAppBar OverFlowMenu 可能很晚）
        for t in scan_times:
            Clock.schedule_once(sb(lambda: self._recursive_set_font(self.sm)), t)
        return self.sm

    def switch_to_player(self):
        try:
            self.sm.current = "player"
            Clock.schedule_once(self._safe_cb(lambda: self._recursive_set_font(self.sm)), 0.05)
        except Exception:
            traceback.print_exc()

    def switch_to_main(self):
        try:
            self.sm.current = "main"
            Clock.schedule_once(self._safe_cb(lambda: self._recursive_set_font(self.sm)), 0.05)
        except Exception:
            traceback.print_exc()

    def show_bt_status(self):
        try:
            status = "已连接" if BT.connected else "未连接"
            dev = BT.connected_device
            msg = f"状态: {status}\n" + (f"设备: {dev['name']} ({dev['address']})" if dev else "设备: 无")
            close_btn = MDFlatButton(text="好的", on_release=lambda x: self.dialog.dismiss())
            try:
                close_btn.font_name = APP_FONT_NAME
            except Exception:
                pass
            self.dialog = MDDialog(title="蓝牙状态", text=msg, buttons=[close_btn])
            self.dialog.open()
            Clock.schedule_once(self._safe_cb(lambda: self._recursive_set_font(self.dialog)), 0.05)
        except Exception:
            traceback.print_exc()

    def toast(self, msg, error=False):
        """底部临时提示框（定点适配·经验487559）：
        弃用 kivymd.toast.toast()（内部 Toast 挂在 Window 上，扫不到且扫Window会闪退，
        导致 Label 仍用 Roboto 中文变□）；改用 KivyMD 标准 MDSnackbar：
        - KivyMD 1.2.0 调用 open() 显示（不是 show()）
        - 直接往 snack.ids.label_container 替换成一个字体写死的 MDLabel
        - 全程只对 snackbar 实例做操作，不扫 Window，零崩溃风险
        """
        try:
            snack = MDSnackbar(duration=2.5)
            # error=True 用错误色（红），否则主题色
            try:
                snack.md_bg_color = (
                    self.theme_cls.error_color if error
                    else self.theme_cls.primary_color
                )
            except Exception:
                pass
            # MDSnackbar 内部有 ids.label_container（BoxLayout），里面是默认 MDLabel
            # 我们清空原有 label，换成自己写死中文字体的 label → 100% 不乱码
            lbl = MDLabel(
                text=msg,
                font_name=APP_FONT_NAME,          # 直接绑定雅黑，不依赖主题/扫树
                theme_text_color="Custom",
                text_color=(1, 1, 1, 1),          # 白字（蓝/红背景清晰）
                shorten=False,
                size_hint_y=None,
                valign="middle",
            )
            try:
                container = snack.ids.label_container
                try:
                    container.clear_widgets()
                except Exception:
                    pass
                container.add_widget(lbl)
            except Exception:
                # 如果拿不到 label_container，回退到直接加 children
                try:
                    snack.add_widget(lbl)
                except Exception:
                    pass
            # 显示：KivyMD 1.x 用 open()，不是 show()
            snack.open()
            # 最后再定点扫一次 snackbar，保险
            try:
                sb = self._safe_cb
                Clock.schedule_once(sb(lambda: self._recursive_set_font(snack)), 0.02)
            except Exception:
                pass
            return
        except Exception:
            print("[toast] MDSnackbar 创建失败，回退旧 toast：")
            traceback.print_exc()
        # 回退方案：旧 toast（若 MDSnackbar 异常则用它保底）
        try:
            from kivymd.toast import toast
            toast(msg)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    BTRingApp().run()
