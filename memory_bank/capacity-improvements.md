# 容量改进（2026-07）

本文记录已落地的容量与可靠性改进（对应原计划步骤 **1、2、3、5**）。步骤 4（重跑压测验证）见 [loadtest-incident-2026-07.md](./loadtest-incident-2026-07.md)。

---

## 背景问题

15 用户并发场景下，原先存在：

1. **场景配图竞态**：同一 `session_id` 共用一个 Redis 缓存键，后触发的配图会覆盖先触发的轮询结果。
2. **图片队列拥塞**：教学配图与测试批量配图共用 `images` 队列，压测时易出现 75 张图同时入队。
3. **出题与通用 Celery 争抢**：`generate_test_questions_async` 与文档处理等同走 `celery` 队列。
4. **可观测性不足**：健康检查仅暴露单一 `celery` 队列深度，无法区分 interactive / batch / questions。
5. **压测误触生产**：脚本可对生产域名直接施压；`ensure_admin_password` 存在重复定义。

---

## 步骤 1 — P0：场景配图 job_id + 压测安全

### 1.1 场景配图 job_id 链路

每次触发场景配图时生成唯一 `job_id`，缓存与状态均按 `(session_id, job_id)` 隔离。

| 环节 | 实现 |
|------|------|
| 派发 | `dispatch_scene_image()` → `uuid4` → `generate_scene_image_async.delay(..., job_id)` |
| Redis 缓存 | `dbt:teaching:scene_image:{session_id}:{job_id}` |
| 活跃任务指针 | `dbt:teaching:scene_active_job:{session_id}` → 最新 job_id |
| 任务状态 | `dbt:image:status:scene:{session_id}:{job_id}` |
| 轮询 HTML | `data-job-id="{job_id}"`，状态 URL 带 `?job_id=` |
| 兼容旧缓存 | `get_scene_image_url()` 在无 job_id 时回退旧单键 `dbt:teaching:scene_image:{session_id}` |

**涉及文件**：`media_app/tasks.py`、`teaching/views.py`、`static/js/media.js`

**部署注意**：Gunicorn 使用 `--preload` 时，代码更新后需 **重启 `web` 容器**，否则 Phase F 压测会出现 `missing_job_id`（HTTP 200 但 HTML 无 job_id）。

### 1.2 压测生产环境保护

`scripts/loadtest_15users.py` 在启动时调用 `assert_loadtest_allowed()`：

- 默认阻止 `genaidbt.top` / `www.genaidbt.top`（可通过 `LOADTEST_BLOCKED_HOSTS` 覆盖）。
- 对生产域名压测须设置 `LOADTEST_ALLOW_RUN=1`。
- 移除重复的 `ensure_admin_password`；仅当 `LOADTEST_RESET_ADMIN=1` 时重置 admin 密码。

**环境变量**（见 `.env.example`）：

```bash
LOADTEST_ALLOW_RUN=1          # 允许对生产域名压测
LOADTEST_RESET_ADMIN=1        # 可选：重置 admin 密码
LOADTEST_ADMIN_PASS=...       # admin 密码
LOADTEST_BLOCKED_HOSTS=genaidbt.top,www.genaidbt.top
```

---

## 步骤 2 — 队列拆分 + 按需配图

### 2.1 Celery 队列架构

| 队列 | 任务 | Worker |
|------|------|--------|
| `interactive-images` | 教学消息配图、**场景配图**、测试题**当前题**配图 | `worker-images`（`-Q interactive-images,batch-images,images`） |
| `batch-images` | 测试题**下一题**预取配图（低优先级） | 同上 |
| `images` | 遗留队列（worker 仍监听以便排空） | 同上 |
| `questions` | `generate_test_questions_async` | `worker-questions` |
| `documents` | 知识库文档处理 | `worker` |
| `celery` | 其他默认任务 | `worker` |

路由定义：`dbt_platform/settings.py` → `CELERY_TASK_ROUTES`

### 2.2 Redis 图片并发槽位（拆分）

原先单一 `IMAGE_MAX_CONCURRENT` 计数器拆为两类：

| 槽位类型 | Redis 键 | 环境变量 | 默认 |
|----------|----------|----------|------|
| interactive | `dbt:image:active_slots:interactive` | `IMAGE_INTERACTIVE_MAX_CONCURRENT` | 2 |
| batch | `dbt:image:active_slots:batch` | `IMAGE_BATCH_MAX_CONCURRENT` | 2 |

`try_acquire_image_slot(kind)` / `release_image_slot(kind)` 在 `media_app/concurrency.py`；任务通过 `slot_kind` 参数选择槽位。

`IMAGE_MAX_CONCURRENT` 仍保留，语义为 interactive + batch 预算之和（兼容旧配置）。

### 2.3 测试题配图改为按需

**变更前**：`generate_test_questions_async` 在出题完成后批量入队全部配图任务（15 用户 × 5 题 ≈ 75 张同时排队）。

**变更后**：

- `generate_test_questions_async` **仅生成题目文本**，不再批量配图。
- 用户打开测试页 / 切题时：`ensure_question_image_dispatched()` → `interactive-images` 队列，占 interactive 槽位。
- 答完当前题后：`prefetch_question_image()` → `batch-images` 队列（`countdown=2`），占 batch 槽位。

**涉及文件**：`testing/tasks.py`、`testing/views.py`、`testing/tests.py`（`ImageTaskDispatchTests` 已更新）

**压测含义**：Phase G 的 `question_images_ready` 指标在按需模式下，若压测未模拟用户打开测试页浏览题目，**预期为 0**——这不代表出题失败。

---

## 步骤 3 — 独立出题 Worker

新增 `worker-questions` 服务（`docker-compose.yml`）：

```yaml
command: celery -A dbt_platform worker --loglevel=info --concurrency=4 -Q questions
```

- 挂载 `hf_cache`，与 RAG 出题共用 `intfloat/multilingual-e5-large` embedding。
- 与 `worker`（`celery,documents`）分离，避免出题高峰阻塞文档处理。

**内存注意**：每个 Celery 子进程可能各自加载 embedding 模型（约 1.4 GiB/进程）。`concurrency=4` 在 15 用户同时开始测试时内存压力大，见 [loadtest-incident-2026-07.md](./loadtest-incident-2026-07.md) 的运行建议。

---

## 步骤 5 — 多队列健康指标

`GET /health/metrics/`（`dbt_platform/health_urls.py`）扩展为：

```json
{
  "celery": {
    "queues": {
      "celery": 0,
      "questions": 0,
      "documents": 0,
      "interactive-images": 0,
      "batch-images": 0,
      "images": 0
    },
    "image_slots": { "interactive": 0, "batch": 0 },
    "active_tasks": 0
  },
  "alerts": [ ... ]
}
```

**告警规则**：

- 各监控队列深度 ≥ 10 → warning，≥ 50 → critical（`MONITORED_QUEUE_THRESHOLDS`）。
- 遗留 `images` 队列深度 > 0 → warning（提示迁移至 `interactive-images`）。
- 任一 critical 告警 → HTTP 503。

监控队列列表：`CELERY_MONITOR_QUEUES` in `settings.py`。

单元测试：`dbt_platform/tests.py`（`MetricsCheckTests`）。

---

## 文件清单

```
media_app/
├── concurrency.py          # interactive/batch 槽位
└── tasks.py                  # job_id、队列、dispatch

teaching/
└── views.py                  # 场景配图轮询

testing/
├── tasks.py                  # questions 队列出题；按需配图任务
├── views.py                  # ensure_question_image_dispatched / prefetch
└── tests.py                  # ImageTaskDispatchTests

dbt_platform/
├── settings.py               # CELERY_TASK_ROUTES、CELERY_MONITOR_QUEUES
├── health_urls.py            # 多队列 metrics + alerts
└── tests.py                  # metrics 测试

docker-compose.yml            # worker-images、worker-questions

scripts/loadtest_15users.py   # job_id 解析、生产 guard

.env.example                  # IMAGE_*、LOADTEST_* 变量
```

---

## 步骤 6 — Agent Plan 配图可靠性（2026-07-17）

### 6.1 Agent Plan API

| 配置 | 值 |
|------|-----|
| Base URL | `https://ark.cn-beijing.volces.com/api/plan/v3` |
| Endpoint | `POST /images/generations` |
| Model | `doubao-seedream-5.0-lite` |
| Size | `2K`（Agent Plan 官方最低规格；不支持 1024） |
| Key | `ARK_AGENT_PLAN_API_KEY`（`.env`，不入库） |

### 6.2 槽位等待与失败终态

- 槽位等待通过 **重新 `apply_async`** 排队，**不消耗** Celery provider `max_retries`。
- 等待超时默认 120s → Redis `failed` + UI 停止轮询并提供异步重试。
- Provider 失败（429/5xx/超时）单独指数退避，最多 3 次后写 `failed`。
- 同步 `/media/image/generate/` 纳入 interactive 槽位，避免绕过限流。

### 6.3 容量与优先级

| 参数 | 值 |
|------|----|
| `IMAGE_INTERACTIVE_MAX_CONCURRENT` | 3 |
| `IMAGE_BATCH_MAX_CONCURRENT` | 1 |
| `worker-images` | `--concurrency=4 --prefetch-multiplier=1` |
| Celery priority | interactive 场景图 9 / 教学图 8 / 当前题 7 / batch 预取 1 |

### 6.4 体验与成本

- 教学：每步骤最多 1 张、每会话最多 3 张；提示词仅在明确场景想象/角色扮演时生成 `image_prompt`。
- 测试：每场测试最多 2 张图（当前题 interactive + 下一题 batch 预取）。
- SSE 文本完成与消息持久化隔离于配图派发异常。

### 6.5 验证结果（8C32G）

- 单张 Agent Plan 冒烟：成功。
- Phase F1：**5/5**，p95 ≈ 48s；Phase F2：**10/10**，p95 ≈ 103–106s。
- 扩展 D–G：D/E/F 全通过；G 出题 15/15；`question_images_ready=0` 仍为按需设计预期。
- 出题 `questions_with_image_prompt` 从约 64 降至约 22，说明提示词成本控制生效。

---

## 验证

针对性单元测试（2026-07 改动后已通过）：

```bash
python manage.py test dbt_platform.tests.MetricsCheckTests \
  media_app.tests.ImageGenerationServiceTests \
  media_app.tests.ImageConcurrencyGuardTests \
  testing.tests.ImageTaskDispatchTests \
  teaching.tests.TeachingImageAsyncTests
```
