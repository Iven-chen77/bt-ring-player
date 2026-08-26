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

def _resolve_default_music_dir():
    """跨平台默认音乐目录：
    - 桌面: APP_FOLDER/music（随项目走）
    - Android: 公共外部存储的 Music/ 目录（用户容易把 mp3 放进去），
               不可用时 fallback 到 APP_FOLDER/music
    """
    try:
        from kivy.utils import platform
    except Exception:
        platform = None
    if platform == "android":
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
        # 兜底下载目录
        try:
            from plyer import storagepath
            d = storagepath.get_downloads_dir()
            if d:
                try:
                    os.makedirs(d, exist_ok=True)
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
            from android.permissions import request_permissions, Permission
            # 动态申请权限
            perms = [
                Permission.BLUETOOTH,
                Permission.BLUETOOTH_ADMIN,
                Permission.BLUETOOTH_CONNECT,
                Permission.BLUETOOTH_SCAN,
                Permission.ACCESS_FINE_LOCATION,
                Permission.ACCESS_COARSE_LOCATION,
            ]
            try:
                request_permissions(perms)
            except Exception:
                pass

            BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
            self._bt_adapter = BluetoothAdapter.getDefaultAdapter()
            self._BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")
            self._UUID = autoclass("java.util.UUID")
            # SPP 标准 UUID
            self._spp_uuid = self._UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
            self._cast = cast
        except Exception as e:
            print(f"[BT] Android init error: {e}")

    # -------- 扫描已配对设备 --------
    def get_paired_devices(self):
        if platform == "android" and hasattr(self, "_bt_adapter") and self._bt_adapter:
            try:
                bonded = self._bt_adapter.getBondedDevices().toArray()
                self.paired_devices = []
                for dev in bonded:
                    self.paired_devices.append({
                        "name": dev.getName() or "(未知设备)",
                        "address": dev.getAddress(),
                    })
            except Exception as e:
                print(f"[BT] get_paired error: {e}")
        return self.paired_devices

    # -------- 开启蓝牙 --------
    def enable_bt(self):
        if platform == "android" and hasattr(self, "_bt_adapter"):
            try:
                if not self._bt_adapter.isEnabled():
                    self._bt_adapter.enable()
                    time.sleep(1.5)
            except Exception as e:
                print(f"[BT] enable error: {e}")

    # -------- 扫描附近设备（带回调） --------
    def start_scan(self, callback):
        """开始扫描，每次发现调用 callback(device_dict)"""
        self.on_device_found = callback
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
        # Android 原生扫描
        try:
            self.enable_bt()
            from jnius import autoclass, PythonJavaClass, java_method
            Intent = autoclass("android.content.Intent")
            IntentFilter = autoclass("android.content.IntentFilter")
            Context = autoclass("android.content.Context")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity

            # 广播接收器
            class ScanReceiver(PythonJavaClass):
                __javainterfaces__ = ["android/content/BroadcastReceiver"]

                def __init__(self, outer):
                    self.outer = outer
                    super().__init__()

                @java_method("(Landroid/content/Context;Landroid/content/Intent;)V")
                def onReceive(self, context, intent):
                    action = intent.getAction()
                    if action == "android.bluetooth.device.action.FOUND":
                        dev = intent.getParcelableExtra("android.bluetooth.device.extra.DEVICE")
                        rssi = intent.getShortExtra("android.bluetooth.device.extra.RSSI", 0)
                        name = dev.getName()
                        addr = dev.getAddress()
                        if not name:
                            name = "(未命名)"
                        info = {"name": name, "address": addr, "rssi": int(rssi)}
                        Clock.schedule_once(lambda dt, info=info: self.outer._on_found(info), 0)

            self._scan_receiver = ScanReceiver(self)
            flt = IntentFilter("android.bluetooth.device.action.FOUND")
            activity.registerReceiver(self._scan_receiver, flt)
            self._bt_adapter.startDiscovery()
            # 12秒后自动停止扫描
            def _stop():
                time.sleep(12)
                try:
                    self._bt_adapter.cancelDiscovery()
                    activity.unregisterReceiver(self._scan_receiver)
                except Exception:
                    pass
            threading.Thread(target=_stop, daemon=True).start()
        except Exception as e:
            print(f"[BT] scan error: {e}")

    @mainthread
    def _on_found(self, info):
        # 去重
        for d in self.scanned_devices:
            if d["address"] == info["address"]:
                return
        self.scanned_devices.append(info)
        if self.on_device_found:
            self.on_device_found(info)

    # -------- 连接设备（SPP） --------
    def connect(self, device, on_success=None, on_error=None):
        self.on_connected = on_success
        self._on_connect_error = on_error
        self.connected_device = device
        if platform != "android":
            # 桌面模拟：1秒后连接成功
            def _sim():
                time.sleep(1)
                self.connected = True
                Clock.schedule_once(lambda dt: on_success and on_success(device), 0)
            threading.Thread(target=_sim, daemon=True).start()
            return
        threading.Thread(target=self._connect_thread, args=(device,), daemon=True).start()

    def _connect_thread(self, device):
        try:
            self.enable_bt()
            addr = device["address"]
            dev = self._bt_adapter.getRemoteDevice(addr)
            # 若未配对，先发起配对
            if dev.getBondState() != self._BluetoothDevice.BOND_BONDED:
                dev.createBond()
                for _ in range(20):  # 最多等10秒配对
                    time.sleep(0.5)
                    if dev.getBondState() == self._BluetoothDevice.BOND_BONDED:
                        break
            self._bt_adapter.cancelDiscovery()
            sock = dev.createRfcommSocketToServiceRecord(self._spp_uuid)
            sock.connect()
            self._socket = sock
            self.connected = True
            Clock.schedule_once(lambda dt: self.on_connected and self.on_connected(device), 0)
            # 启动接收监听线程
            threading.Thread(target=self._recv_loop, daemon=True).start()
        except Exception as e:
            print(f"[BT] connect error: {e}")
            self.connected = False
            Clock.schedule_once(lambda dt: self._on_connect_error and self._on_connect_error(str(e)), 0)

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
            content.bind(on_connect_requested=self._on_connect_from_dialog)
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
        if self.bt_dialog:
            self.bt_dialog.dismiss()
            self.bt_dialog = None
        self.status_text = f"连接中: {device['name']}..."
        BT.connect(
            device,
            on_success=self._on_connected,
            on_error=lambda e: self._on_connect_error(e, device),
        )

    def _on_connect_error(self, err, device):
        self.status_text = f"连接失败: {device['name']}"
        App.get_running_app().toast(f"连接失败: {err}", error=True)


class BTDialogContent(ScrollView):
    """蓝牙弹窗的二级界面：已配对列表 + 扫描到的设备列表"""
    on_connect_requested = None  # 触发回调

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
        BT.start_scan(callback=self._on_found)

    @mainthread
    def _on_found(self, d):
        self._add_scanned(d)

    def _emit_connect(self, dev):
        # 分派事件：bind 的回调需要 trigger via __self__
        if self.on_connect_requested:
            self.on_connect_requested(self, dev)


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
        self._sound = None
        self._progress_ev = None
        Clock.schedule_once(lambda dt: self.refresh_songs(), 0.5)

    def on_enter(self):
        pass

    def pick_dir(self):
        """选择音乐目录（Android 走 plyer，桌面提示用户配置）"""
        if platform == "android":
            try:
                from plyer import filechooser
                filechooser.choose_dir(on_selection=self._on_dir_selected)
                return
            except Exception as e:
                App.get_running_app().toast(f"目录选择失败: {e}", error=True)
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
        self.music_dir = selection[0]
        self.refresh_songs()

    def refresh_songs(self):
        self.ids.song_list.clear_widgets()
        self.songs = []
        base = self.music_dir
        if not base:
            self.pick_dir()
            return
        # 递归扫描
        for root, dirs, files in os.walk(base):
            for f in sorted(files):
                if f.lower().endswith(AUDIO_EXTS):
                    full = join(root, f)
                    self.songs.append((f, full))
                    item = OneLineListItem(
                        text=f,
                        on_release=lambda x, idx=len(self.songs)-1: self.play_at(idx),
                    )
                    # 设置中文字体：歌曲文件名可能是中文
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
        name, path = self.songs[idx]
        self.current_song = name
        # 停止旧的
        if self._sound:
            try:
                self._sound.stop()
                self._sound.unload()
            except Exception:
                pass
        self._sound = SoundLoader.load(path)
        if not self._sound:
            App.get_running_app().toast("无法播放此格式", error=True)
            return
        self._sound.bind(on_stop=self._on_song_end)
        self._sound.play()
        self.is_playing = True
        self.progress = 0
        if self._progress_ev:
            self._progress_ev.cancel()
        self._progress_ev = Clock.schedule_interval(self._update_progress, 0.5)

    def _update_progress(self, dt):
        s = self._sound
        if not s:
            return
        dur = s.length or 0
        pos = s.get_pos() if hasattr(s, "get_pos") else 0
        # SoundLoader 的 API 没有统一 get_pos，部分后端支持 length 属性
        if dur > 0:
            # fallback 估算
            if not pos:
                pos = min(self._sound._get_pos() if hasattr(self._sound, "_get_pos") else (self.progress / 100 * dur), dur)
            pct = min(100, (pos / dur) * 100)
            self.progress = pct
            self.time_text = f"{self._fmt(pos)} / {self._fmt(dur)}"

    @staticmethod
    def _fmt(sec):
        sec = int(max(0, sec))
        m, s = divmod(sec, 60)
        return f"{m:02d}:{s:02d}"

    def toggle_play(self):
        if not self._sound and self.songs:
            self.play_at(0)
            return
        if not self._sound:
            return
        if self.is_playing:
            self._sound.stop()
            self.is_playing = False
            if self._progress_ev:
                self._progress_ev.cancel()
        else:
            self._sound.play()
            self.is_playing = True
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
