# 15 用户扩展压测与宕机分析（2026-07）

## 压测脚本

- **脚本**：`scripts/loadtest_15users.py`
- **扩展模式**：`--extended-only`（Phase D–G）
- **目标**：`https://genaidbt.top`（生产，需 `LOADTEST_ALLOW_RUN=1`）
- **报告**：`scripts/loadtest_extended_report.json`
- **日志**：`scripts/loadtest_extended_run.log`（第一次）、`scripts/loadtest_extended_run2.log`（第二次，未完成）

### Phase 说明

| Phase | 场景 | 主要压力点 |
|-------|------|------------|
| D | 15 路同时教学 SSE | Gunicorn gthread 长连接（3 workers × 8 threads） |
| E | 5 SSE + 5 TTS + 5 浏览 | 外部 API（DeepSeek、火山 TTS）+ 短请求 |
| F1/F2 | 5 / 10 路同时场景配图 | `interactive-images` 队列 + Seedream API |
| G | 15 人同时 POST 开始测试 | `questions` 队列 RAG 出题（embedding + LLM） |

---

## 第一次压测结果（2026-07-11 UTC，代码部署后 web 未重启）

| Phase | 结果 | 备注 |
|-------|------|------|
| D — 15 路 SSE | **15/15 成功**，p95 ≈ 10.0s | 通过 |
| E — 混合 | **90/90 成功** | 通过 |
| F1 — 5 路配图 | **0/5**，`missing_job_id` | web 容器仍为旧代码（Gunicorn `--preload`） |
| F2 — 10 路配图 | **0/10**，`missing_job_id` | 同上；响应 ~20ms，HTML 无 `job_id` |
| G — 15 人开始测试 | **15/15 出题完成**（75 题） | 队列深度全程 0；**0 张配图就绪**（按需设计，压测未浏览题目） |

**总体**：`overall_pass: false`（F 与 G 配图指标未达标）。

### Phase F 失败原因（已修复，待复测）

压测脚本从场景配图 dispatch 响应中解析 `data-job-id` 或 `job_id=`。第一次运行时 `web` 未重启，仍返回无 job_id 的旧轮询 HTML。修复代码已合并，**重启 `web` 后应恢复正常**。

### Phase G 配图指标说明

改进后测试题配图改为**用户打开题目时按需 dispatch**（见 [capacity-improvements.md](./capacity-improvements.md)）。Phase G 仅 POST 开始测试、轮询出题完成，**不模拟用户进入测试页**，因此 `question_images_ready: 0` 是预期行为，不代表功能回退。

改进效果已体现：**无 75 张图同时入队**，各队列深度均为 0。

---

## 第二次压测 — 服务器冻结（未完成）

- **时间**：约 2026-07-11 11:22 CST 启动，11:32 左右 Phase G 期间宿主机无响应，用户手动重启。
- **环境**：4 vCPU，约 15 GiB RAM，**无 swap**。

### 时间线

1. 11:22 — 压测在 `web` 容器内启动，embedding 预加载完成。
2. 11:32:46 — Phase G：15 用户 barrier 同步 POST 开始测试。
3. `worker-questions`（`concurrency=4`）同时处理多个出题任务，各子进程加载 `intfloat/multilingual-e5-large`（约 **1.4 GiB/进程**）。
4. 宿主机内存使用率约 **81%**（~12.8 GiB），committed memory 远超物理内存；系统僵死，未见完整 OOM 日志。

### 根因分析

| 因素 | 说明 |
|------|------|
| Embedding 内存倍增 | `worker-questions` concurrency=4 → 最多 4 个进程各持有一份 e5-large |
| 基线内存已高 | 空闲时容器合计约 8.3 GiB（web ~3.5G 含预加载 embedding，各 worker 等） |
| Gunicorn SSE 占满 | 3×8 gthread，Phase D 已验证 15 长连接可同时存活 |
| 压测跑在 web 容器内 | 与 Gunicorn 争用同机内存，并触发额外 Django/embedding 加载 |
| 15 路同时 Phase G | 4 路并行 RAG 出题瞬间拉高内存峰值 |

### 已确认的非原因

- Phase G **未**产生批量配图 backlog（队列深度 0），说明步骤 2 按需配图有效。
- 第一次压测 Phase G 在相同硬件上**已完成**（227s），第二次叠加了 web 重启后更新代码 + 连续压测的内存碎片/缓存因素。

---

## 容量规格建议

基于两次压测与资源观察：

| 规格 | 15 用户扩展压测（D–G 连续） | 说明 |
|------|------------------------------|------|
| **4C15G**（当前） | 不推荐 | 内存紧张；Phase G 有宕机风险 |
| **4C32G** | 内存改善，CPU 仍瓶颈 | SSE + 4 路并行 RAG 争抢 4 核，延迟偏高 |
| **8C32G** | **可行最低配**（需调参） | 见下方运行建议 |

### 运行建议（不改代码也可先做）

1. **`worker-questions` 并发降至 1–2**，避免多份 embedding 同时驻留。
2. **压测从独立机器发起**，不要在 `web` 容器内执行 `loadtest_15users.py`。
3. **Phase 间留冷却时间**，或拆分阶段执行，避免 D→E→F→G 连续峰值。
4. **Phase G 配图指标**：若需验证配图，压测应增加「打开测试页并轮询当前题配图」步骤，而非仅 POST 开始测试。
5. **部署 job_id 修复后重启 web**：`docker compose restart web`，再跑 Phase F 验证。

---

## 待办 / 未验证项

| 项 | 状态 |
|----|------|
| Phase F 在 web 重启后复测 | 待验证 |
| 完整扩展压测 `overall_pass` | 待稳定环境后重跑 |
| `worker-questions` concurrency 调优 | 建议降至 1–2，尚未改配置 |
| Phase G 压测脚本适配按需配图 | 可选：增加浏览题目步骤 |

---

## 相关文档

- [capacity-improvements.md](./capacity-improvements.md) — 已落地改进细节
- [test-database-isolation.md](./test-database-isolation.md) — 测试库安全（与压测数据准备无关）
