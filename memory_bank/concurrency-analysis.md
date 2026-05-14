# DBT 平台并发性能评估与优化方案

> 评估日期：2026-05-14

---

## 1. 当前架构概况

| 组件 | 配置 | 并发模型 |
|------|------|----------|
| Gunicorn | 3 workers, sync, timeout=120s | 每 worker 同时处理 1 个请求 |
| Celery Worker | 1 进程, concurrency=2 | 最多 2 个异步任务并行（仅图片自动生成 + 文档处理） |
| MongoDB | pymongo 默认连接池 (maxPoolSize=100) | 连接池充足 |
| Redis | 单实例 (Redis 7) | 充足 |
| Qdrant | 单实例 | 充足 |
| LLM (MiniMax M2.7) | `requests.post()` 无连接复用, 60s 超时 | 同步阻塞, 每次新建 TCP 连接 |
| TTS (Volcengine) | Web 同步, `requests.post()` 流式 | 在 Gunicorn worker 内阻塞, 5-15s/次 |
| ASR (Volcengine) | Web 同步, 提交+轮询 | 在 Gunicorn worker 内阻塞, 1-6s/次 |
| Image (MiniMax) | Web 同步 + Celery 异步（两条路径） | 手动生成阻塞 worker 10-120s；教学中的自动生成走 Celery |
| Embedding (BGE-M3) | 进程级单例, 懒加载+启动预热的混合模式 | CPU 密集, ~100-500ms/次 |

### 关键请求链路（教学聊天 — 全部在 Gunicorn Web Worker 内同步执行）

```
用户消息 → Nginx → Gunicorn Worker (阻塞，直到 LLM 完成)
  → MongoDB 查询 Session
  → 关键词风险检测 (内存正则)
  → RAG 检索 (MongoDB text + Qdrant 向量 + Redis 缓存)
  → BGE-M3 Embedding 推理 (CPU 密集, 100-500ms)
  → MiniMax LLM API (同步 HTTP, 15-60s)
  → MongoDB 写入回复
  → 可选: 触发 Celery 异步图片自动生成（教学中自动触发走 Celery，不阻塞）
  → 返回 HTML 响应 / SSE 流式输出（流结束时才释放 worker）

同一 worker 还可能同时被调用来处理：
  → TTS 语音合成 (Volcengine 流式 HTTP, 5-15s, 在 web worker 内阻塞)
  → 手动图片生成 (MiniMax HTTP, 10-120s, 在 web worker 内阻塞)
  → ASR 语音识别 (Volcengine 提交+轮询, 1-6s, 在 web worker 内阻塞)
```

**关键事实**：TTS、ASR、手动图片生成均未进入 Celery，直接在 Gunicorn worker 中同步调用外部 API。只有教学中自动触发的图片生成（`teaching/views.py:560` → `generate_image_async.delay()`）走 Celery 异步。

---

## 2. 并发瓶颈分析（按严重程度排序）

### 瓶颈 1（致命）：Gunicorn Worker 被长 I/O 调用长时间阻塞

**核心问题**：教学聊天、TTS 语音合成、ASR 语音识别、手动图片生成这些核心功能全部在 Gunicorn 同步 worker 内执行对外部 API 的同步 HTTP 调用，耗时从数秒到 60 秒不等。worker 在此期间完全阻塞，无法处理任何其他请求。

每个阻塞路径都涉及真实的网络往返：
- MiniMax LLM API（`llm_client.py:104`）：15-60s，每次新建 TLS 连接
- TTS 语音合成（`media_app/views.py:130-211`）：Volcengine HTTP 请求 5-15s，同步执行
- TTS 流式合成（`media_app/views.py:217-275`）：`StreamingHttpResponse` 持有 worker 直到流结束
- ASR 语音识别（`media_app/views.py:288-365`）：提交 + 轮询，最多 6s（30 次 × 0.2s）
- 手动图片生成（`media_app/views.py:56`）：MiniMax HTTP 10-120s，同步执行

涉及端点及对应的 Gunicorn 阻塞时长：

| 端点 | 阻塞时长 | 是否释放 worker |
|------|----------|-----------------|
| `POST /teaching/session/<id>/send/` | 15-60s (LLM) | 仅响应结束时释放 |
| `POST /teaching/session/<id>/stream/` | 30-60s (LLM 流) | SSE 流结束时释放 |
| `POST /media/tts/` | 5-15s (TTS) | TTS 完成后释放 |
| `POST /media/tts/stream/` | 5-15s (TTS 流) | 流结束时释放 |
| `POST /media/asr/` | 1-6s (ASR 轮询) | ASR 完成后释放 |
| `POST /media/image/generate/` | 10-120s (Image API) | 图片生成后释放 |

**影响**：3 个活跃教学用户即可耗尽所有 worker；如果任一用户同时触发 TTS 播放，就额外占用另一个 worker。第 4 个用户连页面都加载不出来。

**位置**：
- `teaching/views.py:224` — `send_message_view`
- `teaching/views.py:447` — `stream_message_view`
- `media_app/views.py:130` — `synthesize_speech_view`
- `media_app/views.py:217` — `stream_speech_view`
- `media_app/views.py:288` — `transcribe_audio_view`
- `media_app/views.py:31` — `generate_image_view`
- `knowledge_base/rag/llm_client.py:49` — `minimax_chat_completion`
- `knowledge_base/rag/llm_client.py:148` — `minimax_chat_completion_stream`

### 瓶颈 2（严重）：HTTP 连接未复用

`llm_client.py` 和 `media_app/services.py` 每次调用 MiniMax/Volcengine API 都使用 `requests.post()` 新建连接，无 `requests.Session()` 复用。每次 API 调用额外增加 TCP 握手 + TLS 协商开销（~100-300ms）。

**位置**：
- `knowledge_base/rag/llm_client.py:104` — `requests.post(url, ...)`
- `media_app/services.py:187` — `requests.post(url, ...)`
- `media_app/services.py:345` — `requests.post(url, ..., stream=True)`

### 瓶颈 3（中等）：Embedding 模型存在多进程重复驻留风险

`BGE-M3` 模型约 2GB/份。当前加载策略是「懒加载 + 启动预热」混合模式：

- **启动预热**：`apps.py:10-31` 在 `AppConfig.ready()` 中启动 daemon 线程调用 `preload_embedding_model()`，仅在主进程（`RUN_MAIN=true`）且非测试模式下执行。这意味着至少 1 份会被加载。
- **懒加载**：`services.py:66-90` 的 `get_embedding_model()` 在首次语义检索时触发加载（局部缓存优先，失败后网络下载回退）。
- **进程级单例**：模型变量 `_embedding_model` 是模块级全局变量，每个 Gunicorn worker 进程有自己独立的内存空间。

**风险**：在实际使用中，如果多个 Gunicorn worker 都处理过带语义检索的请求（教学聊天每次都有），每个 worker 进程都会触发懒加载并驻留一份模型副本。极端情况下（所有 worker + Celery 都加载），内存占用可能达到 **5 进程 × 2GB ≈ 10GB**。但也存在不会全部加载的场景（如某个 worker 只处理了无语义检索的页面请求）。

**验证方式**：不能在纸面上直接下定量结论。需要在真实负载下用 `docker stats` 或 `smem -P gunicorn` 测量各进程的 RSS/USS 内存，确认实际的模型驻留份数。

**位置**：`knowledge_base/services.py:66` — `get_embedding_model()`；`knowledge_base/apps.py:10-31` — `preload_embedding_model()`

### 瓶颈 4（中等）：Celery Worker 并发不足

`concurrency=2` 意味着同时只能处理 2 个 Celery 异步任务。当前实际进入 Celery 的任务如下（**TTS、ASR、手动图片生成均不在 Celery 中**）：

| 异步任务 | 触发方式 | 耗时 |
|----------|---------|------|
| `generate_image_async` | 教学对话中 AI 提供了 `image_prompt` 时自动触发（`teaching/views.py:561`） | 10-120s (MiniMax API) |
| `process_document_async` | 管理员上传知识文档时触发 | 取决于文档大小 |

教学中自动图片生成与 LLM 对话是串行关系（先 LLM 返回 → 再触发 Celery），所以 Celery 图片任务不会额外争夺 web worker。但多个教学会话同时完成 LLM 调用并触发图片生成时，`concurrency=2` 会导致第 3+ 张图片排队等待。

**位置**：`docker-compose.yml:28` — `--concurrency=2`

### 瓶颈 5（低）：Django 数据库连接未持久化

未设置 `CONN_MAX_AGE`，每个请求周期都重新建立 MongoDB 连接。

**位置**：`dbt_platform/settings.py:85` — DATABASES 配置

---

## 3. 并发容量估算

| 场景 | 预估并发用户数 | 说明 |
|------|---------------|------|
| 纯浏览（看页面、填表单、看报告） | 10-20 | 轻量请求 10-100ms，3 worker 足够 |
| 活跃教学（AI 对话中，LLM 阻塞） | 3-5 | LLM 调用 15-60s 阻塞 worker；TTS 播放额外占用 worker |
| 教学 + TTS 并发（聊天同时播放语音） | ≤3 | 一个用户同时占用 2 个 worker（LLM + TTS） |
| 混合负载（部分浏览 + 部分教学） | 5-8 | 教学用户抢占 worker，浏览用户排队 |
| 峰值突发 | ≤3 | 3 个 LLM 调用同时进行即饱和；再加任何 TTS/ASR 请求直接排队或超时 |

**结论：当前配置下，系统最多同时支持约 3-5 个活跃教学用户，或 10-15 个纯浏览用户。TTS/ASR/图片生成等功能会进一步挤占本已紧张的 worker 池。**

---

## 4. 优化方案

### 优先级 0（低投入，需充分验证）

#### 4.1 启用 Gunicorn 异步 Worker（gevent）— 有潜力但风险需充分评估

当前 `sync` worker 被 I/O 阻塞。gevent 通过 monkey-patching 将标准库的阻塞 I/O（socket、ssl、threading 等）替换为协程实现，使单个 worker 在等待 I/O 时能切换到其他请求。**但并非所有组件在 gevent 下都能安全工作**。

**潜在收益**：提升 I/O 密集型并发（多个连接同时等待 LLM/TTS 响应），对 CPU 密集型操作（BGE-M3 embedding 推理）无帮助。

**Dockerfile CMD**：
```dockerfile
CMD ["gunicorn", "dbt_platform.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--worker-class", "gevent", \
     "--workers", "3", \
     "--worker-connections", "100", \
     "--timeout", "120"]
```

**requirements.txt 需添加**：`gevent>=24.0`

**wsgi.py 顶部需添加**（必须在所有 import 之前）：
```python
from gevent import monkey
monkey.patch_all()
```

**需要逐一验证的兼容性风险（至少 4 项，缺一不可）**：

| 组件 | 风险 | 验证方式 |
|------|------|----------|
| PyMongo C 扩展 | `pymongo` 的 C 扩展（`bson` 编解码）可能不服从 gevent 的协程调度，导致某些 worker 仍然阻塞 | 用 gevent spawn 并发 10 个 MongoDB 查询，确认全部返回且无阻塞 |
| Qdrant HTTP 客户端 | `qdrant_client` 内部使用 `httpx` 或 `requests`，gevent 下需要确认连接复用正常 | 并发 5 个 `query_points` 调用，观察耗时与错误率 |
| SSE `StreamingHttpResponse` | gevent 对 Django `StreamingHttpResponse` 的生成器 yield 语义支持需要验证；协程切换时可能打断流 | 启动教学流式响应，验证前端收到的 token 流完整无中断 |
| BGE-M3 embedding 推理 | `sentence-transformers` 的 `model.encode()` 是 CPU 密集型操作（PyTorch 底层），**gevent 不会让它在等待时切换**——embedding 计算期间该 worker 仍然阻塞，其他请求排队 | 在并发下实测：一个 embedding 推理期间，同一 worker 的其他协程能否被调度 |
| `requests` + SSL | gevent 的 SSL 支持历史上与 `requests` 库有过兼容问题 | 并发调用 MiniMax HTTPS API 100 次，验证成功率 |
| 火山引擎 TTS/ASR | `media_app/services.py` 中的流式 HTTP 请求（`stream=True` + `iter_lines`）在 gevent 下的行为 | 流式 TTS 请求 + 教学聊天并发，验证两个流同时正常工作 |

**如果 gevent 验证不通过，优先考虑以下替代方案**：
- **gthread**：Gunicorn 的多线程 worker class，无需 monkey-patching，兼容性好，但受 GIL 限制（CPU 密集部分仍串行）。在当前这个 I/O 密集型场景下，`--workers 3 --threads 8` 的 gthread 配置通常就够用且风险远低于 gevent。
- **独立出 LLM/TTS 调用为 Celery 任务**（见 4.4），从根本上解耦 I/O 与 web worker。

**结论**：gevent 是可选方向之一，但**不能作为低风险的默认推荐**。建议先在 staging 环境完成上述 6 项兼容性验证，根据结果决定采用 gevent、gthread 还是任务队列方案。

#### 4.2 调整 Gunicorn Workers 数量（需结合资源论证）

当前配置 3 workers。增加 worker 数量能线性提升纯浏览并发，但对活跃教学场景的边际效益递减——因为瓶颈在于外部 API 延迟而非 CPU。

**资源约束分析**：

当前单机需要承载的内存负载：
1. 每个 Gunicorn worker 进程（含 Django 框架开销）：~200-400MB
2. BGE-M3 embedding 模型（懒加载 + 启动预热混合模式，见 `knowledge_base/services.py:33-90` 和 `apps.py:10-31`）：~2GB/份，但**不是所有进程启动时就必然各持一份热模型**——`preload_embedding_model()` 在后台线程中加载，`get_embedding_model()` 是首次使用时懒加载。实际驻留份数取决于哪些 worker 处理过需要语义检索的请求
3. Celery worker 进程：~200MB + 可能加载 embedding 模型（文档处理时需要）
4. MongoDB、Redis、Qdrant、MinIO 容器：各数百 MB 到 1GB

**安全规则**：在通过 `docker stats` 或 `ps aux --sort=-%mem` 实际测量当前内存占用之前，不应盲目增加 worker 数量。如果当前 3 workers 下总内存已接近服务器物理上限，增加 worker 会触发 OOM Killer 或大量 swap（后者对性能是灾难性的）。

**推荐做法**：
1. 先在目标部署环境运行 `docker stats`，记录空闲和负载状态下的实际内存使用
2. 计算 `(可用内存 - 预留 2GB) / 单 worker 进程内存` 得到安全 worker 上限
3. 如果内存充裕，可将 worker 调整到 4-6 个（不建议超过 CPU 核心数 × 2）
4. 如果内存紧张，优先做 embedding 模型独立化（见 4.5），再考虑加 worker

#### 4.3 使用 requests.Session() 复用 HTTP 连接

当前每次 API 调用都新建 TCP + TLS 连接。改为连接池复用。

**knowledge_base/rag/llm_client.py 新增**：
```python
_session: requests.Session | None = None

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0,
        )
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session
```

然后把 `.post()` 改为 `_get_session().post()`。

**预期提升**：每次 API 调用减少 ~100-300ms（TCP + TLS 握手），在高并发下效果显著。

### 优先级 1（显著提升，需少量改造）

#### 4.4 将 LLM 调用改为 Celery 异步任务 + 轮询

当前架构中 LLM 调用在 Gunicorn worker 中同步执行。改为：

1. 用户发送消息 → view 立即返回 "处理中"
2. Celery task 执行 LLM 调用
3. 前端轮询或 WebSocket 获取结果

**注意**：这会改变用户体验（从实时流式变为需要等待）。更优方案是使用 SSE + gevent（见 4.1）。

#### 4.5 将 Embedding 模型独立为微服务

当前 `BGE-M3` 在每个 Gunicorn worker 和 Celery worker 进程中各自加载一份。改为独立服务：

- **方案 A**：使用 Qdrant 的 fastembed 集成（Qdrant 原生支持 BGE-M3，无需应用层 embedding）
- **方案 B**：使用 FastAPI 包装为独立 embedding 服务，通过 HTTP/gRPC 调用

**预期提升**：消除每个 web worker 进程中重复驻留一份模型的风险，将 embedding 内存收敛到独立服务的 1 份（约 2GB），避免多进程重复加载带来的内存膨胀。推理速度因增加一次网络调用而略有增加（应实测）。

#### 4.6 MongoDB 连接池优化

在 `settings.py` 的 `DATABASES["default"]["OPTIONS"]` 中添加：

```python
"OPTIONS": {
    "authSource": env("MONGODB_NAME", default="dbt_platform"),
    "maxPoolSize": 50,
    "minPoolSize": 10,
    "maxIdleTimeMS": 60000,
    "connectTimeoutMS": 3000,
    "serverSelectionTimeoutMS": 5000,
}
```

同时设置 `CONN_MAX_AGE = 300`（Django 连接持久化 5 分钟）。

### 优先级 2（中期优化）

#### 4.7 Celery Worker 增加并发和副本

```yaml
# docker-compose.yml
worker:
  command: celery -A dbt_platform worker --loglevel=info --concurrency=4
```

或者启动多个 worker service 实例实现并行处理。

#### 4.8 增加 API 限流保护

对 MiniMax API 调用增加应用层 Rate Limiter，防止超并发导致 API 侧限流或超额计费。

使用 Django cache + Redis 实现令牌桶或滑动窗口限流（例如每分钟最多 30 次 LLM 调用）。

#### 4.9 RAG 缓存命中率优化

当前 RAG 缓存 TTL 为 5 分钟（`RAG_CACHE_TTL_SECONDS = 300`），key 是 query + top_k 的 hash。对于相似但不完全相同的查询缓存不命中。优化方向：

- 对检索 query 做语义归一化后再缓存
- 增加缓存预热（热门技能预加载）

### 优先级 3（长期架构演进）

#### 4.10 使用 ASGI + uvicorn 替代 WSGI + gunicorn

Django 6.0 支持 ASGI。ASGI 原生支持异步 I/O + WebSocket + SSE，无需 gevent monkey-patching。配合 `httpx.AsyncClient` 替换 `requests`，可以将 LLM/TTS API 调用变为真正的非阻塞 async。

**但需要清醒区分两类并发**：

| 概念 | 含义 | 当前上限 |
|------|------|----------|
| **连接并发** | 同时保持的 HTTP/SSE 连接数 | ASGI 可以轻松支撑数百个 |
| **活跃教学并发** | 同时在进行 LLM/TTS 调用的用户数 | 受外部 API 延迟、QPS 配额、单机 CPU/内存的硬限制 |

切到 ASGI 后，**连接并发**确实可以到 100+，但那只是"100 个用户可以同时打开页面等待"，不是"100 个用户同时在跟 AI 对话"。真正的活跃教学并发仍然受限于：
- MiniMax API 的 QPS 配额（可能是 5-30 QPS，取决于账号）
- 单次 LLM 调用的延迟（15-60s 不可压缩）
- 单机 BGE-M3 embedding 推理的 CPU 吞吐
- 火山引擎 TTS/ASR 的 QPS 和延迟
- RAG 检索（MongoDB + Qdrant）的吞吐

**合理的预期**：ASGI 化后，"连接并发"从 3 → 100+，"活跃教学并发"的上限由外部 API QPS 配额决定，而非由 web worker 数量决定。在确认 MiniMax QPS 配额之前，不宜直接给出"100+ 活跃用户"的承诺。

**投入评估**：需要改造所有同步 `requests` 调用为 `httpx.AsyncClient`、DAO 层适配 Django 的异步 ORM（`sync_to_async` 或原生 `async` ORM）、prompt 构建和 retriever 调用链全部异步化。工作量约 3-5 天，且需要全链路回归测试。

```bash
uvicorn dbt_platform.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

#### 4.11 读写分离 + MongoDB 副本集

当前单节点 MongoDB。如果并发增长，考虑：
- MongoDB 副本集（1 primary + 2 secondary）
- Django 配置读偏好（`readPreference=secondaryPreferred`）
- 后台报表查询走 secondary

---

## 5. 推荐实施路线

| 阶段 | 措施 | 投入 | 风险 | 效果 |
|------|------|------|------|------|
| 立即 | `requests.Session()` HTTP 连接池复用 | 0.5h | 极低，纯替换 | 每次 API 调用减少 ~100-300ms TCP/TLS 开销 |
| 立即 | MongoDB pool + `CONN_MAX_AGE` | 0.5h | 极低，pymongo 原生支持 | 消除请求间的建连开销 |
| 短期 | 验证 gevent vs gthread 兼容性（staging 环境） | 1-2 天 | 中等，需完整回归 | 通过后选择 I/O 并发方案 |
| 短期 | 实测内存占用后调整 worker 数量 | 0.5 天 | 低（先测量再调整） | 按实际资源余量安全扩 worker |
| 短期 | Celery concurrency 2→4 | 1 行配置 | 极低 | 异步任务吞吐翻倍 |
| 中期 | Embedding 模型独立服务化 | 1 天 | 低 | 解除 per-worker 内存重复驻留风险 |
| 中期 | API 限流保护 | 0.5 天 | 极低 | 防超并发触达外部 API QPS 上限 |
| 长期 | ASGI + httpx 异步化 | 3-5 天 | 高（全链路改造） | 连接并发大幅提升，活跃并发上限仍受外部 API 配额约束 |

---

## 6. 关键风险提示

1. **MiniMax API 限流未知**：如果 MiniMax 侧 QPS 限制较低（如 5 QPS），应用层加再多 worker 也无济于事。需要确认 MiniMax 账号的 QPS 配额。同理，火山引擎 TTS/ASR 的 QPS 配额也需要确认。

2. **gevent 兼容性面广、验证成本高**：gevent monkey-patching 需要验证的组件至少包括：pymongo C 扩展、Qdrant HTTP 客户端、SSE `StreamingHttpResponse`、BGE-M3 embedding 下的协程调度、`requests` + SSL、火山引擎流式 HTTP。如有任一项不通过，应回退到 gthread 或任务队列方案。

3. **Embedding 模型内存需实测**：BGE-M3 每份约 2GB，多进程重复驻留的实际份数取决于哪些 worker 处理了语义检索请求。不能仅凭纸面推算 5 × 2GB = 10GB，必须在真实负载下通过 `docker stats` / `smem` 测量确认。

4. **增加 worker 之前必须先测量**：在不知道当前单 worker 实际内存占用和服务器剩余内存的情况下盲目加 worker，可能触发 OOM 或引起 swap thrashing，反而降低整体吞吐。

5. **TTS/ASR/手动图片生成仍在 web 进程中**：这些同步外部 API 调用与 LLM 调用共享同一批 Gunicorn worker，任何优化方案都必须一并覆盖它们，不能只盯着教学聊天链路。

6. **WSGI + SSE 流式响应的兼容性**：`StreamingHttpResponse` 在 sync worker 下会长时间占用 worker；gevent/gthread 下可以缓解，但需确保 Nginx 的 `proxy_buffering off` 配置正确（当前已配置）。

---

## 7. 涉及的关键文件

| 文件 | 作用 | 优化相关性 |
|------|------|------------|
| `docker-compose.yml` | Gunicorn/Celery 配置 | worker 数量、worker class |
| `Dockerfile` | 容器启动命令 | CMD 改为 gevent |
| `dbt_platform/wsgi.py` | WSGI 入口 | monkey.patch_all() |
| `dbt_platform/settings.py` | Django 配置 | CONN_MAX_AGE, MongoDB pool |
| `knowledge_base/rag/llm_client.py` | MiniMax 客户端 | requests.Session |
| `knowledge_base/services.py` | RAG 检索 + Embedding | 模型加载策略 |
| `media_app/services.py` | 图片/TTS/ASR 服务 | requests.Session |
| `teaching/views.py` | 教学会话视图 | 流式响应处理 |
| `docker/nginx.conf` | Nginx 反向代理 | SSE buffering 配置 |
