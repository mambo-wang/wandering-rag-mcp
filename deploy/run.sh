#!/usr/bin/env bash
# ============================================================
# run.sh — 在内网 VM 上启动 wandering-rag-mcp 服务
# ============================================================
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${1:-$HOME/wandering-rag-mcp}"

# 加载环境配置
if [ -f "$INSTALL_DIR/env.sh" ]; then
    source "$INSTALL_DIR/env.sh"
else
    echo "错误: 未找到 $INSTALL_DIR/env.sh"
    echo "请先运行 install.sh 进行安装。"
    exit 1
fi

cd "$INSTALL_DIR/src"

# 默认 stdio 模式，可传参覆盖: bash run.sh /path/to/install sse
MODE="${2:-stdio}"

echo "========================================"
echo " wandering-rag-mcp"
echo " 模式: $MODE"
echo " 嵌入模型: $RAG_EMBEDDING_MODEL"
echo " Reranker: $RAG_RERANKER_MODEL"
echo " 数据目录: $RAG_DATA_DIR"
echo "========================================"

case "$MODE" in
    stdio)
        python server.py --mode stdio
        ;;
    sse)
        HOST="${RAG_MCP_HOST:-127.0.0.1}"
        PORT="${RAG_MCP_PORT:-8000}"
        echo " 地址: http://$HOST:$PORT/sse"
        python server.py --mode sse --host "$HOST" --port "$PORT"
        ;;
    streamable-http|http)
        HOST="${RAG_MCP_HOST:-127.0.0.1}"
        PORT="${RAG_MCP_PORT:-8000}"
        echo " 地址: http://$HOST:$PORT/mcp"
        python server.py --mode streamable-http --host "$HOST" --port "$PORT"
        ;;
    *)
        echo "未知模式: $MODE (可选: stdio, sse, streamable-http)"
        exit 1
        ;;
esac
