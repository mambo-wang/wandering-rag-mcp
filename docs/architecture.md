# 技术架构文档

[返回 README](../README_CN.md)

## 1. 系统概览

wandering-rag-mcp 是一个基于 MCP（Model Context Protocol）协议的本地 RAG 知识库服务器。它的职责边界很清晰：**只负责文档的索引和检索**，不参与大模型生成。生成环节由 MCP 客户端（QoderWork、Claude Desktop 等）自带的大模型完成。

这种设计带来两个好处：一是服务器本身不需要配置任何 LLM API Key，零成本运行；二是可以搭配任意客户端的大模型使用，不绑定特定供应商。

## 2. 技术栈

### 2.1 向量数据库：zvec

[zvec](https://github.com/alibaba/zvec) 是阿里巴巴开源的嵌入式向量数据库（Apache 2.0），定位类似"向量数据库中的 SQLite"。

选择 zvec 的核心理由：

- **嵌入式架构**：`pip install zvec` 即可使用，无需 Docker、无需独立服务进程，与 MCP Server 同进程运行，零网络开销
- **WAL 持久化**：基于预写日志（Write-Ahead Logging）保证崩溃安全，数据写入本地磁盘文件
- **HNSW 索引**：默认使用 HNSW（Hierarchical Navigable Small World）索引，在召回率和查询速度之间取得良好平衡
- **标量字段**：支持 STRING、INT64 等标量字段与向量共存，元数据不需要额外的 JSON 文件

本项目使用的 zvec API：

```python
# 创建/打开 Collection
zvec.create_and_open(path, schema)   # 新建
zvec.open(path)                      # 打开已有

# Schema 定义
CollectionSchema(
    name="collection_name",
    fields=[
        FieldSchema("text", DataType.STRING),
        FieldSchema("source", DataType.STRING),
        FieldSchema("chunk_index", DataType.INT64),
    ],
    vectors=VectorSchema("embedding", DataType.VECTOR_FP32, 1024),
)

# CRUD
collection.insert(DocList([Doc(id=..., vectors={...}, fields={...})]))
collection.query(Query("embedding", vector=query_vec), topk=5)
collection.delete([id1, id2, ...])
collection.fetch([id1])
```

### 2.2 嵌入模型：Qwen3-Embedding-0.6B

[Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 是通义千问团队发布的轻量级嵌入模型。

| 属性 | 值 |
|---|---|
| 参数量 | 0.6B |
| 输出维度 | 1024（可配置 32-1024） |
| 最大上下文 | 32,768 tokens |
| 语言支持 | 100+ 语言，中英文效果优秀 |
| 模型大小 | ~1.2 GB |
| 许可证 | Apache 2.0 |

通过 `sentence-transformers` 库加载，首次运行时从 HuggingFace 下载并缓存到 `~/.cache/huggingface/`，后续完全离线运行。对于中国用户，代码中默认设置了 `HF_ENDPOINT=https://hf-mirror.com` 镜像加速。

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
embeddings = model.encode(texts, normalize_embeddings=True)
# 输出: numpy array, shape (N, 1024), L2 归一化
```

模型通过 `EmbeddingService` 单例封装，采用懒加载策略——MCP Server 启动时不加载模型，首次调用 `search` 或 `ingest` 时才初始化（约 15 秒），避免冷启动延迟。

### 2.3 MCP 框架：FastMCP

使用官方 `mcp` Python SDK（v1.28.0）中的 `FastMCP` 类构建 MCP Server。工具通过 `@mcp.tool()` 装饰器注册，函数的类型注解自动生成 JSON Schema，docstring 成为工具描述。

支持三种传输模式：

| 模式 | 端点 | 适用场景 |
|---|---|---|
| `stdio` | 标准输入/输出 | 本地客户端（QoderWork、Claude Desktop） |
| `sse` | `GET /sse` + `POST /messages/` | 旧版远程客户端 |
| `streamable-http` | `POST /mcp` | 新版远程客户端（推荐） |

### 2.4 文档转换：markitdown

[markitdown](https://github.com/microsoft/markitdown)（微软开源）用于将二进制文档转换为 Markdown 文本。使用 `[all]` 安装以获取全部格式转换器。

| 格式 | 转换器 | 说明 |
|---|---|---|
| PDF | PdfConverter | 基于 pdfminer |
| DOCX | DocxConverter | 基于 python-docx |
| PPTX | PptxConverter | 基于 python-pptx |
| XLSX | XlsxConverter | 基于 openpyxl |

### 2.5 Reranker：bge-reranker-v2-m3

[bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) 是 BAAI 开源的 Cross-Encoder 重排序模型，支持中英文等 100+ 语言。

与 Bi-Encoder（Qwen3-Embedding）的区别：

| | Bi-Encoder (Embedding) | Cross-Encoder (Reranker) |
|---|---|---|
| 输入 | 单独编码 query 和 document | 拼接 (query, document) 联合编码 |
| 速度 | 快（可预计算向量） | 慢（每对都要推理） |
| 精度 | 中等 | 高 |
| 适用阶段 | 粗筛（从海量候选中快速召回） | 精排（对少量候选重新打分） |

本项目采用**两阶段检索**策略：先用 Bi-Encoder 从 zvec 召回较多候选（如 20 条），再用 Cross-Encoder 精排取 top-5。Reranker 为可选功能，默认关闭，可通过 `search` 工具的 `rerank=true` 参数启用。

### 2.6 REST API：Starlette

在 SSE 和 Streamable HTTP 模式下，服务器同时暴露 REST API（`/api/`），供 Web 前端通过 HTTP 管理文档。REST API 和 MCP 共享同一进程、同一端口、同一份向量数据。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查 |
| `/api/collections` | GET | 列出知识库 |
| `/api/collections/{name}/documents` | GET | 列出文档 |
| `/api/collections/{name}/documents` | POST | 上传文件（multipart） |
| `/api/collections/{name}/documents` | DELETE | 删除文档 |
| `/api/collections/{name}/search` | POST | 语义搜索 |

REST API 和 MCP 工具调用同一个业务逻辑层（`core/service.py`），确保两套接口的行为完全一致。CORS 通过 `RAG_CORS_ORIGINS` 环境变量配置，默认允许所有来源。

依赖 `python-multipart` 包解析 multipart/form-data 文件上传。starlette 和 uvicorn 作为 `mcp` 包的传递依赖，无需额外安装。

## 3. 系统架构

```mermaid
flowchart TB
    subgraph Clients["客户端"]
        direction LR
        subgraph MCPClient["MCP Client (QoderWork / Claude Desktop)"]
            C1["用户提问"] --> C2["调用 search 工具检索"]
            C2 --> C3["将检索结果作为 context"]
            C3 --> C4["大模型生成回答"]
        end
        subgraph WebClient["Web 前端 (CodingHub 等)"]
            W1["上传/管理文档"] --> W2["调用 REST API"]
            W2 --> W3["展示搜索结果"]
        end
    end

    MCPClient <-->|"JSON-RPC (stdio / SSE / Streamable HTTP)"| Entry
    WebClient -->|"HTTP (JSON / multipart)"| APIRoutes

    subgraph Server["RAG MCP Server"]
        subgraph Entry["server.py (入口层)"]
            E1["@mcp.tool()<br/>search · ingest_file · ingest_directory<br/>list_collections · list_documents · delete_document"]
        end

        subgraph APIRoutes["api/app.py (REST API)"]
            R1["POST /api/collections/{name}/documents<br/>GET /api/collections<br/>POST /api/collections/{name}/search<br/>..."]
        end

        Entry --> Svc
        APIRoutes --> Svc

        subgraph Svc["core/service.py (业务逻辑层)"]
            SV1["search() · ingest_file() · ingest_content()<br/>delete_document() · list_collections() · list_documents()"]
        end

        Svc --> Ingest & Search

        subgraph Ingest["导入流水线"]
            I1["文件读取"] --> I2{"格式分流"}
            I2 -->|"纯文本"| I3a["UTF-8 直接读取"]
            I2 -->|"二进制"| I3b["markitdown 转换"]
            I3a --> I4["文本分块"]
            I3b --> I4
            I4 --> I5["批量嵌入"] --> I6["zvec 写入"]
        end

        subgraph Search["检索流水线"]
            S1["查询文本"] --> S2["嵌入编码"]
            S2 --> S3["zvec ANN 搜索<br/>(召回较多候选)"]
            S3 --> S4{"rerank?"}
            S4 -->|"否"| S5a["返回 top-k 结果"]
            S4 -->|"是"| S5b["Cross-Encoder 精排"]
            S5b --> S5c["返回 top-k 结果"]
        end

        Ingest & Search --> Core

        subgraph Core["core/ 核心模块"]
            direction LR
            M1["chunker.py<br/>递归文本分块"]
            M2["embeddings.py<br/>嵌入模型封装"]
            M3["reranker.py<br/>Reranker 封装"]
            M4["vector_store.py<br/>zvec 封装"]
        end

        Core --> Infra

        subgraph Infra["底层依赖"]
            direction LR
            F1["sentence-transformers<br/>Qwen3-Embedding-0.6B (1024-dim)"]
            F2["zvec<br/>./data/{collection}/ (磁盘存储)"]
        end
    end

    style Clients fill:#e8f4f8,stroke:#2196F3
    style MCPClient fill:#e8f4f8,stroke:#2196F3
    style WebClient fill:#e0f2f1,stroke:#009688
    style Server fill:#f5f5f5,stroke:#333
    style Entry fill:#fff3e0,stroke:#FF9800
    style APIRoutes fill:#e0f7fa,stroke:#00BCD4
    style Svc fill:#fff9c4,stroke:#FBC02D
    style Ingest fill:#e8f5e9,stroke:#4CAF50
    style Search fill:#e3f2fd,stroke:#2196F3
    style Core fill:#fce4ec,stroke:#E91E63
    style Infra fill:#f3e5f5,stroke:#9C27B0
```

## 4. 核心模块详解

### 4.1 文本分块器 (`core/chunker.py`)

采用递归字符分块策略，按语义边界优先级逐级切分：

```
段落（\n\n）→ 行（\n）→ 句子（。！？.!?）→ 字符
```

**分块参数**：默认 `chunk_size=500` 字符，`chunk_overlap=50` 字符。

**分块流程**：

1. 如果文本长度 ≤ chunk_size，直接作为一个块返回
2. 按段落（双换行）拆分，将小段落合并到 chunk_size 以内
3. 如果单段落超过 chunk_size，降级到按行拆分
4. 如果单行仍然超长，降级到按句子拆分（支持中英文标点）
5. 最终兜底：按字符硬切分，带 overlap

**重叠策略**：每个新块会从前一个块的尾部携带 chunk_overlap 个字符，保证跨块语义连续性。

**文档 ID**：使用文件绝对路径的 SHA256 哈希前 16 位，确保同一文件的多次导入具有稳定的 ID，支持幂等操作。

```python
# Chunk 数据结构
@dataclass
class Chunk:
    text: str          # 文本内容
    source: str        # 来源文件路径
    chunk_index: int   # 块序号
    doc_id: str        # SHA256(filepath)[:16]
```

### 4.2 嵌入服务 (`core/embeddings.py`)

单例模式的 `EmbeddingService`，封装 sentence-transformers：

- **懒加载**：首次调用 `encode()` 或 `encode_query()` 时才加载模型，避免 MCP Server 冷启动过慢
- **维度自动检测**：加载后通过编码测试文本自动检测输出维度，兼容不同模型
- **归一化输出**：所有向量经 L2 归一化，配合 zvec 的 IP（内积）度量等价于余弦相似度
- **模型可替换**：通过 `RAG_EMBEDDING_MODEL` 环境变量切换模型

### 4.3 向量存储 (`core/vector_store.py`)

`VectorStore` 类封装 zvec 的全部操作：

**Collection 管理**：
- 每个 Collection 对应 `data/{name}/` 目录
- 首次访问自动创建（`create_and_open`），后续自动打开（`open`）
- 内存中缓存已打开的 Collection 实例，避免重复 IO

**zvec Schema**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `embedding` | VECTOR_FP32 (1024) | 文本嵌入向量 |
| `text` | STRING | 文本块原文 |
| `source` | STRING | 来源文件路径 |
| `chunk_index` | INT64 | 块在源文件中的序号 |

**文档注册表**：每个 Collection 目录下维护一个 `_registry.json` 文件，记录已导入文档的路径和块数量，供 `list_documents` 使用。

**删除策略**：通过 `{doc_id}_{0..N}` 模式逐一 fetch 检查存在性，收集到所有属于该文档的 chunk ID 后批量删除。

### 4.4 Reranker 服务 (`core/reranker.py`)

单例模式的 `RerankerService`，封装 sentence-transformers 的 CrossEncoder：

- **懒加载**：首次调用 `rerank()` 时才加载模型，与 EmbeddingService 独立
- **可选启用**：search 工具的 `rerank` 参数控制，默认关闭以保持检索速度
- **两阶段检索**：启用时先从 zvec 召回 `max(top_k * 3, 20)` 条候选，再由 Cross-Encoder 对 (query, text) 对逐一打分，按新分数排序后返回 top-k
- **模型可替换**：通过 `RAG_RERANKER_MODEL` 环境变量切换模型

```python
# Reranker 调用流程
reranker = RerankerService()
reranked = reranker.rerank(query="如何配置 MCP", candidates=results, top_n=5)
# reranked 中每条结果新增 rerank_score 字段
```

### 4.5 业务逻辑层 (`core/service.py`)

`service.py` 是 MCP 工具和 REST API 共享的业务逻辑层。它拥有 `VectorStore` 单例，暴露返回结构化数据（dict）的操作函数。

**设计动机**：MCP 工具函数需要返回格式化字符串（供 LLM 阅读），而 REST API 需要返回 JSON。将公共逻辑提取到 service 层后，两套接口只需各自做最后一步格式化，避免代码重复。

**核心函数**：

| 函数 | 说明 | 返回类型 |
|---|---|---|
| `get_store()` | 获取 VectorStore 单例 | `VectorStore` |
| `read_file_content(filepath)` | 读取文件内容（文本/二进制） | `(content, error)` |
| `ingest_file(filepath, collection, chunk_size)` | 导入文件 | `{"status", "filepath", "chunks"}` |
| `ingest_content(content, filename, collection, chunk_size)` | 导入内容（文件上传场景） | `{"status", "filename", "chunks"}` |
| `delete_document(filepath, collection)` | 删除文档 | `{"status", "filepath", "deleted"}` |
| `list_collections()` | 列出知识库 | `[{"name", "doc_count"}]` |
| `list_documents(collection)` | 列出文档 | `[{"source", "chunk_count"}]` |
| `search(query, top_k, collection, rerank)` | 语义搜索 | `[{"id", "score", "text", "source"}]` |

`ingest_content()` 是专门为 REST API 文件上传场景设计的——接收已读取的文本内容和文件名，将文件保存到 `data/{collection}/uploads/` 下作为虚拟路径。

## 5. 数据流

### 5.1 导入流程

```mermaid
flowchart TD
    A["输入文件 (任意格式)"] --> B{"文件格式"}
    B -->|"纯文本 (.md, .txt, .py, ...)"| C1["UTF-8 直接读取"]
    B -->|"二进制 (.pdf, .docx, .pptx, .xlsx)"| C2["markitdown 转换为 Markdown"]
    C1 --> D["原始文本"]
    C2 --> D
    D --> E["chunker.py: 递归分块<br/>(500 字符/块, 50 字符重叠)"]
    E --> F["Chunk 列表"]
    F --> G["embeddings.py: 批量编码<br/>(Qwen3-Embedding-0.6B → 1024 维归一化向量)"]
    G --> H["(Chunk, Vector) 对"]
    H --> I["vector_store.py:<br/>zvec.insert() + 写入 _registry.json"]
    I --> J[("data/{collection}/<br/>zvec 持久化文件")]
```

### 5.2 检索流程

```mermaid
flowchart TD
    A["用户查询 (自然语言)"] --> B["embeddings.py: encode_query()<br/>→ 1024 维归一化向量"]
    B --> C["查询向量"]
    C --> D["vector_store.py:<br/>zvec.query(Query('embedding', vector=...), topk=max(k*3,20))"]
    D --> E["候选结果集<br/>(按向量相似度排序)"]
    E --> F{"rerank?"}
    F -->|"否"| H["取 top-k，格式化结果<br/>(含来源引用和相似度分数)"]
    F -->|"是"| G["reranker.py: CrossEncoder 打分<br/>(query, text) 逐对推理"]
    G --> G2["按 rerank_score 降序排序"]
    G2 --> H
    H --> I["返回给 MCP 客户端"]
    I --> J["客户端大模型基于检索结果生成回答"]
```

## 6. 存储结构

```
data/
├── default/                          # "default" Collection
│   ├── _registry.json                # 文档注册表
│   ├── (zvec 内部文件)               # WAL、索引、向量数据
│   └── ...
├── project-a/                        # 另一个 Collection
│   ├── _registry.json
│   └── ...
└── project-b/
    └── ...
```

**_registry.json 示例**：

```json
{
  "D:\\docs\\api-guide.md": {
    "chunk_count": 12,
    "doc_id": "a1b2c3d4e5f6g7h8"
  },
  "D:\\docs\\architecture.pdf": {
    "chunk_count": 8,
    "doc_id": "i9j0k1l2m3n4o5p6"
  }
}
```

## 7. 部署模式

### 7.1 本地 stdio 模式

最简单的部署方式，MCP Server 作为子进程由客户端启动，通过标准输入/输出通信。

```json
{
  "mcpServers": {
    "wandering-rag-mcp": {
      "command": "python",
      "args": ["/path/to/wandering-rag-mcp/server.py"]
    }
  }
}
```

特点：零网络配置、自动生命周期管理、仅单机使用。

### 7.2 远程 SSE 模式

启动 HTTP 服务器，客户端通过 SSE 长连接 + HTTP POST 双通道通信。同时自动暴露 REST API 供 Web 前端调用。

```bash
# 默认同时启用 MCP + REST API
python server.py --mode sse --host 0.0.0.0 --port 8000

# 仅 MCP，禁用 REST API
python server.py --mode sse --host 0.0.0.0 --port 8000 --no-api
```

Nginx 反代配置要点：

```nginx
location /sse {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;          # SSE 必须关闭缓冲
    proxy_read_timeout 86400s;    # 长连接超时
}
location /messages/ {
    proxy_pass http://127.0.0.1:8000;
}
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    client_max_body_size 100m;    # 文件上传大小限制
}
```

### 7.3 远程 Streamable HTTP 模式

单一端点，所有通信走 POST，是 MCP 推荐的新方案。同时自动暴露 REST API。

```bash
# 默认同时启用 MCP + REST API
python server.py --mode streamable-http --host 0.0.0.0 --port 8000

# 仅 MCP，禁用 REST API
python server.py --mode streamable-http --host 0.0.0.0 --port 8000 --no-api
```

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;
}
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    client_max_body_size 100m;
}
```

## 8. 依赖关系

```
wandering-rag-mcp
├── mcp >= 1.0                    # MCP 协议 SDK
│   ├── starlette                  # ASGI 框架 (SSE/HTTP 模式 + REST API)
│   ├── uvicorn                    # ASGI 服务器
│   └── sse-starlette              # SSE 支持
├── zvec >= 0.5.0                 # 嵌入式向量数据库
├── sentence-transformers >= 3.0  # 嵌入模型运行时
│   ├── torch                      # PyTorch
│   └── transformers               # HuggingFace Transformers
├── markitdown[all] >= 0.1        # 文档格式转换
│   ├── python-docx                # DOCX 解析
│   ├── python-pptx                # PPTX 解析
│   ├── openpyxl                   # XLSX 解析
│   └── pdfminer.six               # PDF 解析
└── python-multipart >= 0.0.6     # REST API 文件上传解析
```

## 9. 设计决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 向量库 | zvec（嵌入式） | 零运维，与 MCP Server 同进程，适合个人/小团队场景 |
| 嵌入模型 | Qwen3-Embedding-0.6B | 0.6B 足够小可在 CPU 运行，32K 上下文减少分块损失，中英文双语 |
| 分块策略 | 递归字符分块 | 纯文本场景足够，无额外依赖；语义级分块（如按 section）可作为后续优化 |
| 元数据存储 | zvec 标量字段 + _registry.json | 标量字段存储每条记录的元数据，registry 提供按文档维度的索引 |
| LLM | 不内置 | MCP Server 只做检索，生成由客户端承担，避免绑定特定 LLM 供应商 |
| 文档格式 | markitdown 统一转换 | 一套转换管线覆盖 PDF/DOCX/PPTX/XLSX，维护成本低 |
| 模型加载 | 懒加载 | 避免 Server 冷启动时加载 ~1.2GB 模型导致的延迟 |
| Reranker | 可选、默认关闭 | 兼顾速度和精度；Cross-Encoder 推理较慢，不适合作为默认行为 |
| Reranker 模型 | bge-reranker-v2-m3 | 100+ 语言、中英文双语、与 Qwen3-Embedding 搭配效果良好 |
| REST API | 同进程同端口 | 无需额外进程/端口，共享向量数据，简化部署；Web 前端和 MCP 客户端可独立使用 |
| REST 框架 | starlette（直接） | 已是 mcp 的传递依赖，零额外依赖；FastAPI 过重且引入 pydantic 等额外依赖 |
| 业务逻辑层 | core/service.py | MCP 和 REST 共享逻辑，避免代码重复；各自只做格式化（字符串 vs JSON） |
