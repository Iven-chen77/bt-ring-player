package org.btring;

import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import java.util.ArrayList;
import java.util.List;

/**
 * 蓝牙 Java 侧桥接类。
 *
 * 为什么必须写在 Java 侧（两个根本原因，都已在真机日志中验证）：
 * 1) 扫描回调：jnius 的 PythonJavaClass 底层用 java.lang.reflect.Proxy，只能代理「接口」。
 *    android.content.BroadcastReceiver 和 android.bluetooth.le.ScanCallback 都是「抽象类」，
 *    Python 侧实例化会抛 java.lang.IllegalArgumentException: ... is not an interface。
 * 2) ScanSettings：在 Python 侧用 ScanSettings.Builder().build() 经 jnius 传回 Java 时
 *    会变成 null，startScan 直接抛 IllegalArgumentException: settings is null。
 *    在 Java 里构造则完全没有这个问题。
 * 3) GATT 连接回调 BluetoothGattCallback 同样是抽象类，也只能在 Java 侧实现。
 *
 * 结果统一存放在 Java 静态字段里，Python 侧定时轮询取走，避免跨语言回调注册的坑。
 */
public class BtBridge {

    // ==================== 扫描部分 ====================

    private static final List<String> CLASSIC = new ArrayList<String>();
    private static final List<String> BLE = new ArrayList<String>();

    /** BLE 扫描失败错误码，0 表示无错误（取走即清零） */
    private static volatile int bleError = 0;
    /** 最近一次扫描启动失败的原因（取走即清零） */
    private static volatile String scanStartError = "";

    private static final BleCallback BLE_CALLBACK = new BleCallback();

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
                int rssi = intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, (short) -999);
                synchronized (CLASSIC) {
                    CLASSIC.add(safeName(dev) + "|" + safeAddress(dev) + "|" + rssi);
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

    /** 在 Java 侧构造 ScanSettings 并启动 BLE 扫描。
     *  返回空字符串表示成功，否则返回失败原因。 */
    public static String startBleScan(BluetoothLeScanner scanner) {
        if (scanner == null) {
            return "扫描器为 null";
        }
        try {
            ScanSettings.Builder builder = new ScanSettings.Builder();
            builder.setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY);
            try {
                // 低版本没有 setCallbackType，单独 try 避免影响整体
                builder.setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES);
            } catch (Throwable ignored) {
            }
            ScanSettings settings = builder.build();
            scanner.startScan(null, settings, BLE_CALLBACK);
            return "";
        } catch (Throwable t) {
            String m = t.getClass().getSimpleName() + ": " + t.getMessage();
            scanStartError = m;
            return m;
        }
    }

    public static void stopBleScan(BluetoothLeScanner scanner) {
        try {
            if (scanner != null) {
                scanner.stopScan(BLE_CALLBACK);
            }
        } catch (Throwable t) {
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

    /** 取走并清空已收集的扫描结果，元素格式 name|address|rssi */
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

    public static String drainScanStartError() {
        String s = scanStartError;
        scanStartError = "";
        return s;
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
        scanStartError = "";
    }

    /** 设备类型：1=经典 2=BLE 3=双模 0=未知 */
    public static int deviceType(BluetoothDevice dev) {
        try {
            return dev.getType();
        } catch (Throwable t) {
            return 0;
        }
    }

    // ==================== GATT 连接部分 ====================

    public static final int GATT_IDLE = 0;
    public static final int GATT_CONNECTING = 1;
    public static final int GATT_CONNECTED = 2;
    public static final int GATT_DISCOVERED = 3;
    public static final int GATT_FAILED = 4;

    private static volatile int gattState = GATT_IDLE;
    private static volatile String gattMsg = "";
    private static BluetoothGatt gatt = null;
    private static BluetoothGattCharacteristic writeChar = null;

    private static final List<String> GATT_CHARS = new ArrayList<String>();

    private static final BluetoothGattCallback GATT_CB = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            try {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    gattState = GATT_CONNECTED;
                    gattMsg = "GATT 已连接，正在发现服务...";
                    g.discoverServices();
                } else {
                    gattState = GATT_FAILED;
                    gattMsg = "GATT 连接断开 newState=" + newState + " status=" + status;
                    try {
                        g.close();
                    } catch (Throwable t) {
                    }
                }
            } catch (Throwable t) {
                gattState = GATT_FAILED;
                gattMsg = "GATT 状态回调异常: " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            try {
                synchronized (GATT_CHARS) {
                    GATT_CHARS.clear();
                    writeChar = null;
                    for (BluetoothGattService s : g.getServices()) {
                        for (BluetoothGattCharacteristic c : s.getCharacteristics()) {
                            int props = c.getProperties();
                            GATT_CHARS.add(s.getUuid().toString() + "|" + c.getUuid().toString()
                                    + "|props=" + props);
                            if (writeChar == null
                                    && ((props & BluetoothGattCharacteristic.PROPERTY_WRITE) != 0
                                    || (props & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0)) {
                                writeChar = c;
                            }
                        }
                    }
                }
                gattState = GATT_DISCOVERED;
                gattMsg = "服务发现完成，共 " + GATT_CHARS.size() + " 个特征";
            } catch (Throwable t) {
                gattState = GATT_DISCOVERED;
                gattMsg = "服务发现回调异常: " + t.getClass().getSimpleName();
            }
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int status) {
            gattMsg = "写入 " + c.getUuid() + " status=" + status;
        }
    };

    /** 建立 GATT 连接（BLE 设备唯一可行的连接方式） */
    public static String gattConnect(Context ctx, BluetoothDevice dev) {
        if (ctx == null) {
            return "Context 为 null";
        }
        if (dev == null) {
            return "device 为 null";
        }
        try {
            synchronized (GATT_CHARS) {
                GATT_CHARS.clear();
                writeChar = null;
            }
            gattState = GATT_CONNECTING;
            gattMsg = "正在建立 GATT 连接...";
            if (gatt != null) {
                try {
                    gatt.close();
                } catch (Throwable t) {
                }
                gatt = null;
            }
            gatt = dev.connectGatt(ctx, false, GATT_CB);
            if (gatt == null) {
                gattState = GATT_FAILED;
                gattMsg = "connectGatt 返回 null";
                return gattMsg;
            }
            return "";
        } catch (Throwable t) {
            gattState = GATT_FAILED;
            String m = t.getClass().getSimpleName() + ": " + t.getMessage();
            gattMsg = m;
            return m;
        }
    }

    public static int gattState() {
        return gattState;
    }

    public static String gattMessage() {
        return gattMsg == null ? "" : gattMsg;
    }

    public static String[] gattChars() {
        synchronized (GATT_CHARS) {
            return GATT_CHARS.toArray(new String[0]);
        }
    }

    /** 向第一个可写特征写数据。返回空字符串表示成功。 */
    public static String gattWrite(byte[] data) {
        if (gatt == null) {
            return "GATT 未连接";
        }
        if (writeChar == null) {
            return "没有找到可写特征";
        }
        try {
            writeChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            writeChar.setValue(data);
            boolean ok = gatt.writeCharacteristic(writeChar);
            return ok ? "" : "writeCharacteristic 返回 false";
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    /** 向指定 UUID 的特征写数据。返回空字符串表示成功。 */
    public static String gattWriteTo(String charUuid, byte[] data) {
        if (gatt == null) {
            return "GATT 未连接";
        }
        try {
            for (BluetoothGattService s : gatt.getServices()) {
                for (BluetoothGattCharacteristic c : s.getCharacteristics()) {
                    if (c.getUuid().toString().equalsIgnoreCase(charUuid)) {
                        c.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
                        c.setValue(data);
                        return gatt.writeCharacteristic(c) ? "" : "writeCharacteristic 返回 false";
                    }
                }
            }
            return "未找到特征 " + charUuid;
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    public static void gattDisconnect() {
        try {
            if (gatt != null) {
                gatt.disconnect();
                gatt.close();
            }
        } catch (Throwable t) {
        }
        gatt = null;
        writeChar = null;
        gattState = GATT_IDLE;
        gattMsg = "";
    }
}
