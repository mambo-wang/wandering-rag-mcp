#!/usr/bin/env bash
# ============================================================
# prepare.sh — 在有网络的机器上运行，打包所有离线部署所需文件
# 
# 注意：如果要部署到 Linux 服务器，建议在有网络的 Linux 机器上运行此脚本。
# 在 Windows 上打包的 wheel 可能无法直接在 Linux 上使用。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUNDLE_DIR="$SCRIPT_DIR/bundle"
ARCH="${1:-x86_64}"   # x86_64 or aarch64

echo "========================================"
echo " wandering-rag-mcp 离线打包工具"
echo " 目标架构: $ARCH"
echo "========================================"

# 检测当前系统
CURRENT_OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    CURRENT_OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    CURRENT_OS="macos"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    CURRENT_OS="windows"
fi

if [ "$CURRENT_OS" = "windows" ]; then
    echo ""
    echo "⚠️  检测到当前系统为 Windows"
    echo "   在 Windows 上打包的 wheel 可能无法在 Linux 上使用。"
    echo "   建议：在有网络的 Linux 机器上运行此脚本，或使用在线安装方式："
    echo "   curl -sSL https://raw.githubusercontent.com/mambo-wang/wandering-rag-mcp/main/deploy/setup.sh | bash"
    echo ""
    read -p "是否继续打包？(y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消。"
        exit 0
    fi
fi

rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR"/{wheels,models,source}

# ----------------------------------------------------------
# 1. 下载 Miniconda (Linux)
# ----------------------------------------------------------
echo ""
echo "[1/5] 下载 Miniconda (Linux $ARCH) ..."
if [ "$ARCH" = "aarch64" ]; then
    MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh"
else
    MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
fi
curl -L --progress-bar -o "$BUNDLE_DIR/miniconda.sh" "$MINICONDA_URL"
echo "  -> miniconda.sh ($(du -h "$BUNDLE_DIR/miniconda.sh" | cut -f1))"

# ----------------------------------------------------------
# 2. 下载 pip wheel 包 (含所有传递依赖)
# ----------------------------------------------------------
echo ""
echo "[2/5] 处理 pip 依赖包 ..."

# 生成依赖文件
cat > "$BUNDLE_DIR/requirements.txt" <<'DEPS'
mcp>=1.0
zvec>=0.5.0
sentence-transformers>=3.0
markitdown[all]>=0.1
python-multipart>=0.0.6
DEPS

if [ "$CURRENT_OS" = "linux" ]; then
    echo "  当前系统为 Linux，下载 Linux wheels ..."
    pip download \
        -d "$BUNDLE_DIR/wheels" \
        -r "$BUNDLE_DIR/requirements.txt" 2>&1 | tail -15
    echo "  -> wheels/ ($(du -sh "$BUNDLE_DIR/wheels" | cut -f1), $(ls "$BUNDLE_DIR/wheels" | wc -l) packages)"
else
    echo "  当前系统非 Linux (检测到: $CURRENT_OS)"
    echo "  跳过 wheel 下载，生成 requirements.txt 供目标服务器在线安装"
    echo "  目标服务器安装时将执行: pip install -r requirements.txt"
    # 创建空 wheels 目录以保持包结构一致
    mkdir -p "$BUNDLE_DIR/wheels"
    echo "  -> requirements.txt 已生成"
fi

# ----------------------------------------------------------
# 3. 下载嵌入模型 (Qwen3-Embedding-0.6B)
# ----------------------------------------------------------
echo ""
echo "[3/5] 下载嵌入模型 Qwen/Qwen3-Embedding-0.6B (~1.2GB) ..."

# 检测 huggingface 下载工具
if command -v hf &>/dev/null; then
    HF_CMD="hf"
elif command -v huggingface-cli &>/dev/null; then
    HF_CMD="huggingface-cli"
else
    echo "  安装 huggingface_hub ..."
    pip install -q "huggingface_hub[cli]" 2>/dev/null || true
    if command -v hf &>/dev/null; then
        HF_CMD="hf"
    elif command -v huggingface-cli &>/dev/null; then
        HF_CMD="huggingface-cli"
    else
        echo "  ⚠️ 无法安装 huggingface_hub，跳过模型下载"
        echo "  目标服务器需要自行下载模型或使用在线安装"
        HF_CMD=""
    fi
fi

if [ -n "$HF_CMD" ]; then
    $HF_CMD download Qwen/Qwen3-Embedding-0.6B \
        --local-dir "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" \
        --local-dir-use-symlinks False || echo "  ⚠️ 嵌入模型下载失败，目标服务器需在线下载"
    
    if [ -d "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" ] && [ -f "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B/config.json" ]; then
        echo "  -> models/Qwen3-Embedding-0.6B/ ($(du -sh "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" | cut -f1))"
    fi
fi

# ----------------------------------------------------------
# 4. 下载 Reranker 模型 (bge-reranker-v2-m3)
# ----------------------------------------------------------
echo ""
echo "[4/5] 下载 Reranker 模型 BAAI/bge-reranker-v2-m3 (~1.1GB) ..."

if [ -n "$HF_CMD" ]; then
    $HF_CMD download BAAI/bge-reranker-v2-m3 \
        --local-dir "$BUNDLE_DIR/models/bge-reranker-v2-m3" \
        --local-dir-use-symlinks False || echo "  ⚠️ Reranker 模型下载失败（可选，不影响基本功能）"
    
    if [ -d "$BUNDLE_DIR/models/bge-reranker-v2-m3" ] && [ -f "$BUNDLE_DIR/models/bge-reranker-v2-m3/config.json" ]; then
        echo "  -> models/bge-reranker-v2-m3/ ($(du -sh "$BUNDLE_DIR/models/bge-reranker-v2-m3" | cut -f1))"
    fi
else
    echo "  跳过（huggingface 工具不可用）"
fi

# ----------------------------------------------------------
# 5. 复制项目源码
# ----------------------------------------------------------
echo ""
echo "[5/5] 复制项目源码 ..."
cp "$PROJECT_DIR/pyproject.toml" "$BUNDLE_DIR/source/"
cp "$PROJECT_DIR/server.py"      "$BUNDLE_DIR/source/"
cp -r "$PROJECT_DIR/core"        "$BUNDLE_DIR/source/"
cp -r "$PROJECT_DIR/api"         "$BUNDLE_DIR/source/"
echo "  -> source/ (pyproject.toml, server.py, core/, api/)"

# 复制安装和启动脚本
cp "$SCRIPT_DIR/install.sh" "$BUNDLE_DIR/"
cp "$SCRIPT_DIR/run.sh"     "$BUNDLE_DIR/"
chmod +x "$BUNDLE_DIR/install.sh" "$BUNDLE_DIR/run.sh"

# ----------------------------------------------------------
# 6. 打包
# ----------------------------------------------------------
echo ""
echo "========================================"
echo " 打包完成！"
echo "========================================"
TOTAL_SIZE=$(du -sh "$BUNDLE_DIR" | cut -f1)
echo " bundle/ 总大小: $TOTAL_SIZE"
echo ""
echo " 正在生成 tar.gz ..."
tar -czf "$SCRIPT_DIR/wandering-rag-mcp-offline.tar.gz" -C "$SCRIPT_DIR" bundle
ARCHIVE_SIZE=$(du -h "$SCRIPT_DIR/wandering-rag-mcp-offline.tar.gz" | cut -f1)
echo ""
echo " 输出文件: deploy/wandering-rag-mcp-offline.tar.gz ($ARCHIVE_SIZE)"
echo ""
echo " 使用方法:"
echo "   1. 将 wandering-rag-mcp-offline.tar.gz 拷贝到内网 Linux VM"
echo "   2. tar xzf wandering-rag-mcp-offline.tar.gz"
echo "   3. cd bundle && bash install.sh"
echo "   4. bash run.sh"
