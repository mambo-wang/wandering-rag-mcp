#!/usr/bin/env bash
# ============================================================
# setup.sh — 在有网络的纯净 Linux 服务器上一键安装
# ============================================================
# 用法:
#   curl -sSL https://raw.githubusercontent.com/mambo-wang/wandering-rag-mcp/main/deploy/setup.sh | bash
#   或: bash setup.sh [安装目录]
# ============================================================
set -euo pipefail

INSTALL_DIR="${1:-$HOME/wandering-rag-mcp}"
REPO_URL="https://github.com/mambo-wang/wandering-rag-mcp.git"

echo "========================================"
echo " wandering-rag-mcp 在线安装器"
echo " 安装目录: $INSTALL_DIR"
echo "========================================"

# ----------------------------------------------------------
# 1. 检查系统依赖
# ----------------------------------------------------------
echo ""
echo "[1/6] 检查系统依赖 ..."

MISSING=()
for cmd in git python3 pip3; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING+=("$cmd")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  缺少以下工具: ${MISSING[*]}"
    echo ""
    echo "  请先安装:"
    echo "    Ubuntu/Debian: sudo apt-get install git python3 python3-pip python3-venv"
    echo "    CentOS/RHEL:   sudo yum install git python3 python3-pip"
    echo "    Fedora:        sudo dnf install git python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PYTHON_VERSION"
echo "  Git: $(git --version)"

# ----------------------------------------------------------
# 2. 克隆仓库
# ----------------------------------------------------------
echo ""
echo "[2/6] 克隆仓库 ..."

if [ -d "$INSTALL_DIR/src" ]; then
    echo "  安装目录已存在，更新代码 ..."
    cd "$INSTALL_DIR/src"
    git pull origin main
else
    mkdir -p "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR/src"
fi

# ----------------------------------------------------------
# 3. 创建虚拟环境
# ----------------------------------------------------------
echo ""
echo "[3/6] 创建 Python 虚拟环境 ..."

ENV_DIR="$INSTALL_DIR/venv"

if [ -d "$ENV_DIR" ]; then
    echo "  虚拟环境已存在，跳过创建。"
else
    python3 -m venv "$ENV_DIR"
    echo "  -> 虚拟环境创建完成"
fi

source "$ENV_DIR/bin/activate"

# ----------------------------------------------------------
# 4. 安装 Python 依赖
# ----------------------------------------------------------
echo ""
echo "[4/6] 安装 Python 依赖（这可能需要几分钟）..."

pip install --upgrade pip -q
pip install -q \
    "mcp>=1.0" \
    "zvec>=0.5.0" \
    "sentence-transformers>=3.0" \
    "markitdown[all]>=0.1" \
    "python-multipart>=0.0.6"

echo "  -> 依赖安装完成"

# ----------------------------------------------------------
# 5. 预下载模型（可选）
# ----------------------------------------------------------
echo ""
echo "[5/6] 预下载嵌入模型（约 1.2GB，首次运行会自动下载）..."

MODELS_DIR="$INSTALL_DIR/models"
mkdir -p "$MODELS_DIR"

if [ -d "$MODELS_DIR/Qwen3-Embedding-0.6B" ] && [ -f "$MODELS_DIR/Qwen3-Embedding-0.6B/config.json" ]; then
    echo "  嵌入模型已存在，跳过下载。"
else
    echo "  正在下载 Qwen/Qwen3-Embedding-0.6B ..."
    pip install -q "huggingface_hub[cli]"
    huggingface-cli download Qwen/Qwen3-Embedding-0.6B \
        --local-dir "$MODELS_DIR/Qwen3-Embedding-0.6B" \
        --local-dir-use-symlinks False
    echo "  -> 嵌入模型下载完成"
fi

# Reranker 模型为可选，默认不下载
if [ "${DOWNLOAD_RERANKER:-false}" = "true" ]; then
    if [ -d "$MODELS_DIR/bge-reranker-v2-m3" ] && [ -f "$MODELS_DIR/bge-reranker-v2-m3/config.json" ]; then
        echo "  Reranker 模型已存在，跳过下载。"
    else
        echo "  正在下载 BAAI/bge-reranker-v2-m3 (~1.1GB) ..."
        huggingface-cli download BAAI/bge-reranker-v2-m3 \
            --local-dir "$MODELS_DIR/bge-reranker-v2-m3" \
            --local-dir-use-symlinks False
        echo "  -> Reranker 模型下载完成"
    fi
else
    echo "  [跳过] Reranker 模型（可选，设置 DOWNLOAD_RERANKER=true 下载）"
fi

# ----------------------------------------------------------
# 6. 生成环境配置和启动脚本
# ----------------------------------------------------------
echo ""
echo "[6/6] 生成配置文件 ..."

# 生成 env.sh
cat > "$INSTALL_DIR/env.sh" <<ENVEOF
# wandering-rag-mcp 环境配置
# source $INSTALL_DIR/env.sh 即可激活环境

source "$ENV_DIR/bin/activate"

# 指向本地模型目录
export RAG_EMBEDDING_MODEL="$MODELS_DIR/Qwen3-Embedding-0.6B"
export RAG_RERANKER_MODEL="$MODELS_DIR/bge-reranker-v2-m3"
export RAG_DATA_DIR="$INSTALL_DIR/data"

# 如需离线模式，取消下面的注释
# export TRANSFORMERS_OFFLINE=1
# export HF_HUB_OFFLINE=1
# export HF_DATASETS_OFFLINE=1
ENVEOF

# 生成 start.sh 便捷启动脚本
cat > "$INSTALL_DIR/start.sh" <<'STARTEOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$INSTALL_DIR/env.sh"
cd "$INSTALL_DIR/src"

MODE="${1:-sse}"
HOST="${RAG_MCP_HOST:-0.0.0.0}"
PORT="${RAG_MCP_PORT:-8000}"

echo "========================================"
echo " wandering-rag-mcp"
echo " 模式: $MODE"
echo " 地址: http://$HOST:$PORT"
echo "========================================"

case "$MODE" in
    stdio)
        python server.py --mode stdio
        ;;
    sse)
        python server.py --mode sse --host "$HOST" --port "$PORT"
        ;;
    http|streamable-http)
        python server.py --mode streamable-http --host "$HOST" --port "$PORT"
        ;;
    *)
        echo "用法: bash start.sh [stdio|sse|http]"
        exit 1
        ;;
esac
STARTEOF
chmod +x "$INSTALL_DIR/start.sh"

echo ""
echo "========================================"
echo " 安装完成！"
echo "========================================"
echo ""
echo " 安装目录结构:"
echo "  $INSTALL_DIR/"
echo "  ├── venv/               # Python 虚拟环境"
echo "  ├── models/             # 模型权重文件"
echo "  │   └── Qwen3-Embedding-0.6B/"
echo "  ├── src/                # 项目源码"
echo "  │   ├── server.py"
echo "  │   ├── core/"
echo "  │   └── api/"
echo "  ├── data/               # 向量数据（运行时生成）"
echo "  ├── env.sh              # 环境配置脚本"
echo "  └── start.sh            # 便捷启动脚本"
echo ""
echo " 启动方式:"
echo "   bash $INSTALL_DIR/start.sh         # SSE 模式（默认）"
echo "   bash $INSTALL_DIR/start.sh stdio   # stdio 模式"
echo "   bash $INSTALL_DIR/start.sh http    # Streamable HTTP 模式"
echo ""
echo " 或手动启动:"
echo "   source $INSTALL_DIR/env.sh"
echo "   cd $INSTALL_DIR/src"
echo "   python server.py --mode sse --host 0.0.0.0 --port 8000"
echo ""
echo " REST API 地址: http://localhost:8000/api/health"
