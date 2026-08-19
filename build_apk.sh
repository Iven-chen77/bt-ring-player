#!/usr/bin/env bash
# =============================================================================
# 蓝牙响铃播放器 APK 一键构建脚本
# 运行环境：WSL2 (Ubuntu) 或 原生 Linux (Ubuntu 20.04+)
# 作用：自动准备环境 → 执行 buildozer → 校验 APK 产物
# 用法：
#   cd 项目目录
#   chmod +x build_apk.sh
#   ./build_apk.sh           # 默认打 debug 包（推荐首次）
#   ./build_apk.sh release   # 打 release 包（需签名，失败自动提醒）
# =============================================================================

set -o pipefail

# -------- 颜色定义 --------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err()  { echo -e "${RED}[ERR]${NC}  $1"; }
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }

# -------- Step 0: 切到项目根目录 --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || { log_err "无法进入项目目录: $SCRIPT_DIR"; exit 1; }
log_info "项目目录: $SCRIPT_DIR"

# -------- Step 1: 系统检查（Windows 原生不能跑 buildozer）--------
UNAME_S="$(uname -s 2>/dev/null || echo Windows)"
if [[ "$UNAME_S" != "Linux" ]]; then
    log_err "检测到系统: $UNAME_S（非 Linux）"
    log_err "Buildozer 不能在 Windows 原生 PowerShell/cmd 下运行！"
    log_err "请在 WSL2 (Ubuntu) 中执行："
    log_err "  1. Win+R → 输入 wsl 打开 Ubuntu 终端"
    log_err "  2. cd /mnt/d/Iven_work/test/pychar（对应你的 Windows 路径）"
    log_err "  3. bash build_apk.sh"
    exit 2
fi

# 检测是否在 WSL 里（提供提示）
if grep -qi microsoft /proc/version 2>/dev/null; then
    log_info "运行环境: WSL2"
else
    log_info "运行环境: 原生 Linux"
fi

# -------- Step 2: 目录迁移（经验323901：避免 $HOME/.buildozer 只读/受限）--------
# 将 Buildozer 全局缓存、pip 缓存全部移到项目下的 .buildozer_cache/，
# 这样所有构建产物都在当前项目盘，不污染系统 HOME，也不会被权限卡住。
CACHE_ROOT="$SCRIPT_DIR/.buildozer_cache"
HOME_REDIR="$CACHE_ROOT/home"
mkdir -p "$HOME_REDIR/.cache/pip"
mkdir -p "$HOME_REDIR/.gradle"

# 关键：导出 HOME 给 buildozer 用（它会在 ~/.buildozer 放全局 SDK/NDK）
export HOME="$HOME_REDIR"
export PIP_CACHE_DIR="$HOME_REDIR/.cache/pip"
export GRADLE_USER_HOME="$HOME_REDIR/.gradle"

log_info "Buildozer HOME: $HOME（缓存全在项目目录下，方便清理）"
log_info "PyPI 镜像: 清华 (https://pypi.tuna.tsinghua.edu.cn/simple)"

# -------- Step 3: 非交互 & 镜像环境变量 --------
export BUILDOZER_INTERACTIVE=0          # 不询问 y/n
export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PIP_TRUSTED_HOST="pypi.tuna.tsinghua.edu.cn"

# -------- Step 4: 基础依赖自检（缺失时打印安装命令，不自动 sudo）--------
MISSING=()
for cmd in python3 pip git zip unzip tar java; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING+=("$cmd")
    fi
done

# Java 额外校验：必须是 JDK（不是 JRE）且 >=11
JAVA_OK=0
if command -v java &>/dev/null; then
    if command -v javac &>/dev/null; then
        JAVA_VER="$(java -version 2>&1 | head -n1 | grep -oE '"[0-9]+\.' | tr -d '".' | head -c 2)"
        if [[ "$JAVA_VER" -ge 11 ]]; then
            JAVA_OK=1
        fi
    fi
fi
[[ "$JAVA_OK" -ne 1 ]] && MISSING+=("openjdk-17-jdk (>=11)")

# buildozer 自检
if ! python3 -m buildozer --version &>/dev/null; then
    MISSING+=("buildozer (pip)")
fi
# cython 自检（p4a 必需）
if ! python3 -c "import Cython" &>/dev/null; then
    MISSING+=("cython (pip)")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    log_warn "检测到缺失 ${#MISSING[@]} 个依赖，请先执行安装命令（复制到 WSL Ubuntu 终端执行）："
    cat <<EOF

-------------------------------------------------------------------------
sudo apt update -y
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip python3-dev \
    autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
    libtinfo5 cmake libffi-dev libssl-dev automake
python3 -m pip install --upgrade pip setuptools wheel cython buildozer plyvel
-------------------------------------------------------------------------
EOF
    log_info "缺失项：${MISSING[*]}"
    read -p "是否仍然继续？（可能构建失败）[y/N]: " ans
    [[ "$ans" != "y" && "$ans" != "Y" ]] && exit 3
fi

# -------- Step 5: 构建参数 debug / release --------
BUILD_MODE="${1:-debug}"
case "$BUILD_MODE" in
    debug|release) ;;
    *)
        log_err "未知构建模式: $BUILD_MODE（请用 debug 或 release）"
        exit 4
        ;;
esac

log_info "开始构建: $BUILD_MODE APK"
log_info "首次构建会自动下载 Android SDK/NDK (~4GB)，请耐心等待..."
echo

# 记录构建开始时间
START_TS=$(date +%s)
BEFORE_APKS=""
if [[ -d bin ]]; then
    BEFORE_APKS=$(find bin -maxdepth 1 -name "*.apk" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -n5)
fi

# -------- Step 6: 执行 buildozer（非交互）--------
log_info ">>> buildozer -v android $BUILD_MODE"
set +e
yes | python3 -m buildozer -v android "$BUILD_MODE" 2>&1 | tee build_apk.log
BUILD_RC=${PIPESTATUS[0]}
set -e
echo

# -------- Step 7: 产物校验（经验323901：不看退出码，看APK时间戳）--------
END_TS=$(date +%s)
COST_MIN=$(( (END_TS - START_TS + 59) / 60 ))
log_info "构建耗时: ${COST_MIN} 分钟，退出码: $BUILD_RC"

NEW_APK=""
if [[ -d bin ]]; then
    # 找到构建开始时间之后修改过的 apk（或找最新的 apk）
    LATEST="$(find bin -maxdepth 1 -name "*.apk" -newer build_apk.log 2>/dev/null | head -n1)"
    if [[ -z "$LATEST" ]]; then
        LATEST="$(find bin -maxdepth 1 -name "*.apk" -printf "%T@ %p\n" 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
    fi
    if [[ -n "$LATEST" ]]; then
        APK_SIZE_MB=$(du -m "$LATEST" | cut -f1)
        NEW_APK="$LATEST"
    fi
fi

if [[ -n "$NEW_APK" && "$APK_SIZE_MB" -gt 20 ]]; then
    log_ok "🎉 构建成功！"
    echo "=========================================================="
    echo "  APK 路径 (WSL):   $NEW_APK"
    echo "  APK 大小:         ${APK_SIZE_MB} MB"
    # 输出对应的 Windows 路径（方便用户去文件夹里找到）
    if grep -qi microsoft /proc/version; then
        WIN_PATH="$(wslpath -w "$NEW_APK")"
        echo "  APK 路径 (Windows):  $WIN_PATH"
        echo "  → 复制上面路径到文件资源管理器地址栏即可打开文件"
    fi
    echo "=========================================================="
    echo ""
    if [[ "$BUILD_MODE" == "debug" ]]; then
        log_info "debug 包安装方法（数据线连接手机，开启 USB 调试）："
        echo "   adb install -r \"$NEW_APK\""
        echo ""
        log_info "或者直接把 apk 传到手机，在手机上点击安装（请允许未知来源）。"
    else
        log_warn "release 包需要签名才能安装，请使用 apksigner/jarsigner 签名。"
    fi
    exit 0
else
    log_err "构建失败！未找到 APK 产物或体积异常（<20MB）。"
    if [[ -n "$NEW_APK" ]]; then
        log_err "最新 APK: $NEW_APK (大小可疑，请检查)"
    fi
    log_err "请查看构建日志: $SCRIPT_DIR/build_apk.log"
    echo ""
    log_info "常见排查命令（在 WSL 执行）："
    echo "  # 看 buildozer 日志最后 200 行："
    echo "  tail -n 200 $SCRIPT_DIR/build_apk.log"
    echo "  # 完全清理重建（解决缓存/gradle 污染）："
    echo "  python3 -m buildozer android clean"
    echo "  rm -rf $SCRIPT_DIR/.buildozer_cache $SCRIPT_DIR/.buildozer"
    echo "  bash $0"
    exit 5
fi
