package org.btring;

import android.bluetooth.BluetoothDevice;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import java.util.ArrayList;
import java.util.List;

/**
 * 经典蓝牙 / BLE 扫描的 Java 侧桥接类。
 *
 * 为什么必须写在 Java 侧：
 * jnius 的 PythonJavaClass 底层用 java.lang.reflect.Proxy 生成代理，而 Proxy 只能代理
 * 「接口」。android.content.BroadcastReceiver 和 android.bluetooth.le.ScanCallback
 * 都是「抽象类」，用 PythonJavaClass 会直接抛：
 *   java.lang.IllegalArgumentException: android.content.BroadcastReceiver is not an interface
 * 所以扫描回调只能在 Java 侧实现。这里把扫描结果存进静态列表，Python 侧定时轮询取走，
 * 避免任何 Python <-> Java 回调注册的复杂度。
 *
 * 结果字符串格式统一为：name|address|rssi
 * （name 可能含空格等，用 '|' 分隔，Python 侧按第一个/第二个 '|' 切分即可）
 */
public class BtBridge {

    private static final List<String> CLASSIC = new ArrayList<String>();
    private static final List<String> BLE = new ArrayList<String>();

    /** BLE 扫描失败错误码，0 表示无错误（取走即清零） */
    private static volatile int bleError = 0;

    /** 经典蓝牙发现接收器。
     *  注意：ACTION_FOUND 是系统广播，Android 14 注册时用两参版本即可（纯系统广播）。 */
    public static class ClassicReceiver extends BroadcastReceiver {
        @Override
        public void onReceive(Context context, Intent intent) {
            try {
                if (intent == null) {
                    return;
                }
                if (!BluetoothDevice.ACTION_FOUND.equals(intent.getAction())) {
                    return;
                }
                BluetoothDevice dev =
                        (BluetoothDevice) intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                if (dev == null) {
                    return;
                }
                String name = safeName(dev);
                String addr = safeAddress(dev);
                int rssi = intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, (short) -999);
                synchronized (CLASSIC) {
                    CLASSIC.add(name + "|" + addr + "|" + rssi);
                }
            } catch (Throwable t) {
                // 绝不在广播回调里往上抛异常，否则可能直接 FATAL EXCEPTION 杀掉进程
            }
        }
    }

    /** BLE 扫描回调 */
    public static class BleCallback extends ScanCallback {
        @Override
        public void onScanResult(int callbackType, ScanResult result) {
            collect(result);
        }

        @Override
        public void onBatchScanResults(List<ScanResult> results) {
            if (results == null) {
                return;
            }
            for (ScanResult r : results) {
                collect(r);
            }
        }

        @Override
        public void onScanFailed(int errorCode) {
            bleError = errorCode;
        }

        private void collect(ScanResult result) {
            try {
                if (result == null) {
                    return;
                }
                BluetoothDevice dev = result.getDevice();
                if (dev == null) {
                    return;
                }
                synchronized (BLE) {
                    BLE.add(safeName(dev) + "|" + safeAddress(dev) + "|" + result.getRssi());
                }
            } catch (Throwable t) {
            }
        }
    }

    private static String safeName(BluetoothDevice dev) {
        try {
            String n = dev.getName();
            if (n != null && n.length() > 0) {
                return n;
            }
        } catch (Throwable t) {
            // SecurityException: 缺 BLUETOOTH_CONNECT
        }
        return "(未命名)";
    }

    private static String safeAddress(BluetoothDevice dev) {
        try {
            String a = dev.getAddress();
            if (a != null && a.length() > 0) {
                return a;
            }
        } catch (Throwable t) {
        }
        return "(unknown)";
    }

    /** 取走并清空已收集的结果数组，元素格式 name|address|rssi */
    public static String[] drain() {
        List<String> out = new ArrayList<String>();
        synchronized (BLE) {
            out.addAll(BLE);
            BLE.clear();
        }
        synchronized (CLASSIC) {
            out.addAll(CLASSIC);
            CLASSIC.clear();
        }
        return out.toArray(new String[0]);
    }

    /** 取走并清零 BLE 错误码，0 表示没有错误 */
    public static int drainError() {
        int c = bleError;
        bleError = 0;
        return c;
    }

    /** 新一轮扫描前清空累积结果 */
    public static void reset() {
        synchronized (CLASSIC) {
            CLASSIC.clear();
        }
        synchronized (BLE) {
            BLE.clear();
        }
        bleError = 0;
    }
}
