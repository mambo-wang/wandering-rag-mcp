#!/usr/bin/env bash
# ============================================================
# install.sh — 在内网 Linux VM 上运行，离线安装一切
# ============================================================
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${1:-$HOME/wandering-rag-mcp}"

echo "========================================"
echo " wandering-rag-mcp 离线安装器"
echo " 安装目录: $INSTALL_DIR"
echo "========================================"

# ----------------------------------------------------------
# 1. 安装 Miniconda (到安装目录)
# ----------------------------------------------------------
echo ""
echo "[1/4] 安装 Miniconda ..."
CONDA_DIR="$INSTALL_DIR/miniconda"
if [ -d "$CONDA_DIR" ]; then
    echo "  Miniconda 已存在，跳过安装。"
else
    bash "$BUNDLE_DIR/miniconda.sh" -b -p "$CONDA_DIR" -f
    echo "  -> Miniconda 安装完成"
fi
export PATH="$CONDA_DIR/bin:$PATH"

# ----------------------------------------------------------
# 2. 创建 Python 环境并安装依赖
# ----------------------------------------------------------
echo ""
echo "[2/4] 安装 Python 依赖包 ..."
ENV_DIR="$INSTALL_DIR/venv"

if [ -d "$ENV_DIR" ]; then
    echo "  虚拟环境已存在，跳过创建。"
else
    python3 -m venv "$ENV_DIR" 2>/dev/null || "$CONDA_DIR/bin/python" -m venv "$ENV_DIR"
    echo "  -> 虚拟环境创建完成"
fi

source "$ENV_DIR/bin/activate"

# 检查是否有预下载的 wheels
WHEEL_COUNT=$(find "$BUNDLE_DIR/wheels" -name "*.whl" -o -name "*.tar.gz" 2>/dev/null | wc -l)

if [ "$WHEEL_COUNT" -gt 0 ]; then
    # 离线模式：从本地 wheels 目录安装
    echo "  检测到 $WHEEL_COUNT 个本地包，执行离线安装 ..."
    pip install --no-index --find-links "$BUNDLE_DIR/wheels" \
        mcp zvec sentence-transformers "markitdown[all]" python-multipart 2>&1 | tail -10
else
    # 在线模式：从 requirements.txt 安装（Windows 打包的情况）
    echo "  本地 wheels 为空，从 PyPI 在线安装依赖 ..."
    pip install --upgrade pip -q
    pip install -r "$BUNDLE_DIR/requirements.txt" 2>&1 | tail -10
fi

echo "  -> 依赖安装完成"

# ----------------------------------------------------------
# 3. 复制模型文件
# ----------------------------------------------------------
echo ""
echo "[3/4] 部署模型文件 ..."
MODELS_DIR="$INSTALL_DIR/models"
mkdir -p "$MODELS_DIR"

cp -r "$BUNDLE_DIR/models/Qwen3-Embedding-0.6B" "$MODELS_DIR/" 2>/dev/null && \
    echo "  -> Qwen3-Embedding-0.6B 已部署" || \
    echo "  [跳过] Qwen3-Embedding-0.6B 目录不存在"

cp -r "$BUNDLE_DIR/models/bge-reranker-v2-m3" "$MODELS_DIR/" 2>/dev/null && \
    echo "  -> bge-reranker-v2-m3 已部署" || \
    echo "  [跳过] bge-reranker-v2-m3 目录不存在"

# ----------------------------------------------------------
# 4. 部署项目源码
# ----------------------------------------------------------
echo ""
echo "[4/4] 部署项目源码 ..."
mkdir -p "$INSTALL_DIR/src"
cp -r "$BUNDLE_DIR/source/"* "$INSTALL_DIR/src/"
echo "  -> 源码已部署到 $INSTALL_DIR/src/"

# ----------------------------------------------------------
# 生成环境配置脚本
# ----------------------------------------------------------
echo ""
echo "========================================"
echo " 安装完成！"
echo "========================================"

# 生成 env 文件
cat > "$INSTALL_DIR/env.sh" <<ENVEOF
# wandering-rag-mcp 环境配置
# source $INSTALL_DIR/env.sh 即可激活环境

export PATH="$CONDA_DIR/bin:\$PATH"
source "$ENV_DIR/bin/activate"

# 指向本地模型目录（离线模式必须）
export RAG_EMBEDDING_MODEL="$MODELS_DIR/Qwen3-Embedding-0.6B"
export RAG_RERANKER_MODEL="$MODELS_DIR/bge-reranker-v2-m3"
export RAG_DATA_DIR="$INSTALL_DIR/data"

# 强制离线模式，禁止任何网络请求
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
ENVEOF

echo ""
echo " 安装目录结构:"
echo "  $INSTALL_DIR/"
echo "  ├── miniconda/          # Python 运行时"
echo "  ├── venv/               # Python 虚拟环境"
echo "  ├── models/             # 模型权重文件"
echo "  │   ├── Qwen3-Embedding-0.6B/"
echo "  │   └── bge-reranker-v2-m3/"
echo "  ├── src/                # 项目源码"
echo "  │   ├── server.py"
echo "  │   ├── pyproject.toml"
echo "  │   ├── core/"
echo "  │   └── api/            # REST API"
echo "  ├── data/               # 向量数据（运行时生成）"
echo "  └── env.sh              # 环境配置脚本"
echo ""
echo " 启动方式:"
echo "   source $INSTALL_DIR/env.sh"
echo "   cd $INSTALL_DIR/src"
echo "   python server.py                    # stdio 模式"
echo "   python server.py --mode sse         # SSE 模式"
echo "   python server.py --mode streamable-http  # Streamable HTTP 模式"
echo ""
echo " 或直接运行: bash $INSTALL_DIR/../bundle/run.sh"
