# 内网离线部署指南

本文档说明如何在**无网络、无 Python/pip** 的 Linux 虚拟机上部署 wandering-rag-mcp。

## 前提条件

| 项目 | 要求 |
|---|---|
| 有网机器 | 已安装 Python 3.10+、pip、wget |
| 内网 VM | Linux x86_64 或 aarch64，有 bash |
| 传输方式 | U 盘、SCP、或任何文件传输工具 |
| 预估包大小 | ~3GB（含两个模型权重） |

## 步骤一：有网机器上打包

在有网络的机器上运行 `prepare.sh`，它会自动下载 Miniconda、所有 pip 依赖、模型权重，并打包为单个 tar.gz 文件。

```bash
cd wandering-rag-mcp/deploy

# x86_64 架构（大多数服务器）
bash prepare.sh x86_64

# ARM 架构（如华为鲲鹏、苹果 Silicon 的 Linux VM）
bash prepare.sh aarch64
```

完成后会生成 `deploy/wandering-rag-mcp-offline.tar.gz`（约 3GB）。

## 步骤二：传输到内网 VM

将以下文件拷贝到内网 VM 的任意目录（如 `~/tmp/`）：

```
wandering-rag-mcp-offline.tar.gz
```

## 步骤三：内网 VM 上安装

```bash
# 解压
cd ~/tmp
tar xzf wandering-rag-mcp-offline.tar.gz

# 安装（默认安装到 ~/wandering-rag-mcp）
cd bundle
bash install.sh

# 或指定安装目录
bash install.sh /opt/wandering-rag-mcp
```

安装过程：
1. 解压 Miniconda 到安装目录（自带 Python 3.11）
2. 创建虚拟环境，从本地 wheels 安装所有依赖
3. 部署模型文件到 `models/` 目录
4. 部署项目源码到 `src/` 目录
5. 生成 `env.sh` 环境配置脚本

## 步骤四：启动服务

```bash
# stdio 模式（默认）
bash run.sh

# SSE 模式
bash run.sh ~/wandering-rag-mcp sse

# Streamable HTTP 模式
bash run.sh ~/wandering-rag-mcp http
```

或手动激活环境后启动：

```bash
source ~/wandering-rag-mcp/env.sh
cd ~/wandering-rag-mcp/src

# stdio 模式
python server.py

# SSE 模式
python server.py --mode sse --port 8000

# Streamable HTTP 模式
python server.py --mode streamable-http --host 0.0.0.0 --port 8000
```

## 安装目录结构

```
~/wandering-rag-mcp/
├── miniconda/              # Python 运行时（自包含）
├── venv/                   # Python 虚拟环境
├── models/                 # 模型权重（离线加载）
│   ├── Qwen3-Embedding-0.6B/
│   └── bge-reranker-v2-m3/
├── src/                    # 项目源码
│   ├── server.py
│   ├── pyproject.toml
│   └── core/
├── data/                   # 向量数据（运行时自动创建）
└── env.sh                  # 环境配置脚本
```

## 环境变量

`env.sh` 已自动配置以下变量，无需手动设置：

| 变量 | 值 | 说明 |
|---|---|---|
| `RAG_EMBEDDING_MODEL` | `<安装目录>/models/Qwen3-Embedding-0.6B` | 本地嵌入模型路径 |
| `RAG_RERANKER_MODEL` | `<安装目录>/models/bge-reranker-v2-m3` | 本地 Reranker 路径 |
| `RAG_DATA_DIR` | `<安装目录>/data` | 向量数据存储 |
| `TRANSFORMERS_OFFLINE` | `1` | 禁止模型联网 |
| `HF_HUB_OFFLINE` | `1` | 禁止 HuggingFace Hub 联网 |

如需额外配置，编辑 `env.sh` 即可。

## 常见问题

**Q: 安装时报 `Permission denied`？**
A: 确保脚本有执行权限：`chmod +x install.sh run.sh`

**Q: 运行时报 `ModuleNotFoundError`？**
A: 确保先执行 `source env.sh` 激活虚拟环境。

**Q: 模型加载失败？**
A: 检查 `models/` 目录下是否有完整的模型文件（至少包含 `config.json` 和 `.safetensors` 文件）。

**Q: 只想用 Reranker，不下载模型？**
A: 在 `prepare.sh` 中注释掉第 4 步（Reranker 模型下载），并在 `env.sh` 中将 `RAG_RERANKER_MODEL` 改为空值。Reranker 功能为可选，默认关闭。

**Q: 如何更新项目代码？**
A: 只需将新的 `src/` 目录覆盖到安装目录，模型和环境不需要重新安装。
