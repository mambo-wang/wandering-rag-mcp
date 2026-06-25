#!/usr/bin/env bash
# ============================================================
# prepare.sh — 在有网络的机器上运行，打包所有离线部署所需文件
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
wget -q --show-progress -O "$BUNDLE_DIR/miniconda.sh" "$MINICONDA_URL"
echo "  -> miniconda.sh ($(du -h "$BUNDLE_DIR/miniconda.sh" | cut -f1))"

# ----------------------------------------------------------
# 2. 下载 pip wheel 包 (含所有传递依赖)
# ----------------------------------------------------------
echo ""
echo "[2/5] 下载 pip 依赖包 (wheels, linux-$ARCH) ..."
pip download \
    --platform "manylinux2014_$ARCH" \
    --platform "manylinux_2_17_$ARCH" \
    --platform "linux_$ARCH" \
    --python-version 311 \
    --implementation cp \
    --abi cp311 \
    --only-binary=:all: \
    -d "$BUNDLE_DIR/wheels" \
    -r <(cat <<'DEPS'
mcp>=1.0
zvec>=0.5.0
sentence-transformers>=3.0
markitdown[all]>=0.1
python-multipart>=0.0.6
DEPS
) 2>&1 | tail -5

# 部分包没有预编译 wheel（纯 Python 包），需要额外下载 sdist
pip download \
    --no-deps \
    --no-binary=:all: \
    -d "$BUNDLE_DIR/wheels" \
    -r <(cat <<'DEPS'
mcp>=1.0
zvec>=0.5.0
sentence-transformers>=3.0
markitdown[all]>=0.1
python-multipart>=0.0.6
DEPS
) 2>&1 | tail -5

echo "  -> wheels/ ($(du -sh "$BUNDLE_DIR/wheels" | cut -f1), $(ls "$BUNDLE_DIR/wheels" | wc -l) packages)"

# ----------------------------------------------------------
# 3. 下载嵌入模型 (Qwen3-Embedding-0.6B)
# ----------------------------------------------------------
echo ""
echo "[3/5] 下载嵌入模型 Qwen/Qwen3-Embedding-0.6B (~1.2GB) ..."
pip install -q huggingface_hub[cli] 2>/dev/null || true
huggingface-cli download Qwen/Qwen3-Embedding-0.6B \
    --local-dir "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" \
    --local-dir-use-symlinks False
echo "  -> models/Qwen3-Embedding-0.6B/ ($(du -sh "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" | cut -f1))"

# ----------------------------------------------------------
# 4. 下载 Reranker 模型 (bge-reranker-v2-m3)
# ----------------------------------------------------------
echo ""
echo "[4/5] 下载 Reranker 模型 BAAI/bge-reranker-v2-m3 (~1.1GB) ..."
huggingface-cli download BAAI/bge-reranker-v2-m3 \
    --local-dir "$BUNDLE_DIR/models/bge-reranker-v2-m3" \
    --local-dir-use-symlinks False
echo "  -> models/bge-reranker-v2-m3/ ($(du -sh "$BUNDLE_DIR/models/bge-reranker-v2-m3" | cut -f1))"

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
