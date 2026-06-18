# DBT 技能教学平台技术栈最终建议

基于 [DBT_PRD_v0.6.md](./DBT_PRD_v0.6.md) 与截至 `2026-06-18` 核对的官方文档，推荐采用一套**Django 单体应用 + 阿里云自建 MongoDB + DeepSeek/火山引擎 为核心 AI 供应商**的技术栈。目标是保持实现简单、后台稳健、研究数据可审计，同时尽量统一外部 AI 依赖，并满足你对自建数据库安全可控的要求。

---

## 0. 文档契约

### 0.1 文档定位

本文件是**技术实现约束文档（technical constraints）**。  
它回答“在不违背 PRD 的前提下，应该用什么技术、怎样部署、哪些技术规则已经固定”。

### 0.2 与其他文档的关系

本文件默认服从以下优先级：

1. `DBT_PRD_v0.6.md`：产品事实与需求边界。
2. `tech-stack.md`：技术选型、部署、环境与安全约束。
3. `inplementation-plan.md`：按步骤执行这些约束。

如果本文件与 PRD 冲突，应修改本文件；如果实施计划与本文件冲突，应修改实施计划。

### 0.3 LLM 阅读规则

模型在读取本文件时应遵循：

1. “最终选择”“固定”“正式入口”“必须”表示**已经定案**，不要重新发散备选栈。
2. “推荐”“建议”表示默认实现方向；如果替换，必须说明替换理由和影响。
3. “待确认项”之外的内容，默认已经可用于开发。
4. 本文件定义的是**实现方式**，不是新增产品需求；若要新增能力，应先回到 PRD 修改。

### 0.4 本文件中的信息分层

为了便于 LLM 读取，后续内容按以下层级理解：

- `最终推荐`：可直接用于初始化工程的确定选型。
- `为什么`：解释取舍逻辑，帮助模型在局部实现时不偏题。
- `各层选型说明`：面向编码时的模块化约束。
- `实施建议`：帮助把技术栈映射到阶段性落地顺序。
- `待确认项`：当前仍会阻塞部分实现的残余不确定性。

### 0.5 固定技术决策摘要

以下技术事实已经确定：

1. 应用框架：Django 单体应用，不做前后端完全分离。
2. 正式入口：`https://<domain>:10443`。
3. 反向代理：Nginx。
4. 主数据库：阿里云自建 MongoDB。
5. 向量检索：Qdrant。
6. 浏览器入口必须同源，前端不得写死 `localhost`。
7. Django 不直接对公网暴露。
8. Let's Encrypt 证书签发方式固定为 `DNS-01`。
9. 电脑与平板双端兼容是固定约束，不是后续优化项。

---

## 1. 最终推荐

| 层级 | 最终选择 |
|---|---|
| 编程语言 | Python 3.12 |
| Web 框架 | Django 6.0 |
| MongoDB 集成 | `django-mongodb-backend` |
| 前端 | Django Templates + HTMX 2.x + Alpine.js + Tailwind CSS 4.x + Chart.js 4.x |
| 主数据库 | 阿里云 ECS / VPC 内自建 MongoDB 7.x / 8.x |
| 关键词检索 | MongoDB text index + 应用层 metadata 过滤 |
| 向量检索 | Qdrant |
| Embedding | 本地 `bge-m3` |
| 教学 / 测试子流程 | LangChain-based RAG + structured output |
| 异步任务 | Celery 5.6 |
| Broker / Cache | Redis 7 |
| 对象存储 | MinIO |
| LLM | DeepSeek `deepseek-v4-flash` |
| TTS | 火山引擎 `豆包语音合成模型2.0` (Volcengine TTS) |
| ASR | 火山引擎 (Volcengine ASR) |
| 图像生成 | 火山引擎 `即梦文生图3.1` (jimeng_t2i_v31) |
| 部署 | Docker Compose + Nginx(`10443`) + HTTPS |
| 测试 | pytest + Playwright |

---

## 2. 为什么这套栈最合适

### 2.1 应用架构

本项目是研究平台，不是高并发互联网产品。核心是：

- 邀请码注册
- 问卷建档
- AI 教学
- 自动测试与重测
- 情绪记录
- 风险事件记录
- 研究后台查看与导出

因此优先级是：

- 数据结构清晰
- 后台能力成熟
- 权限与审计可靠
- 外部依赖尽量少
- 运维简单

这决定了当前阶段不应采用微服务，也不应做前后端完全分离。

同时，本项目必须避免“本机 localhost 可用，但其他设备不可用”的开发方式。架构上应固定以下原则：

- 浏览器统一只访问一个正式入口：`https://<domain>:10443`
- Django 不直接对外暴露，仅通过 Nginx 反向代理提供服务
- 浏览器侧接口调用优先使用同源相对路径，而不是写死 `localhost`
- 内部服务地址与对外访问地址显式分离配置
- 跨设备访问能力从开发早期就持续验证，而不是等上线前一次性发现问题

### 2.2 为什么保留 Django

Django 仍然是当前最合适的主框架，因为它天然适合：

- 认证与 Session
- 管理后台
- 表单与流程页
- 权限控制
- 审计日志
- 数据导出

即使数据库选 MongoDB，这个判断仍然成立。

### 2.3 为什么可以用 MongoDB

你对 MongoDB 更熟悉，而 MongoDB 官方的 Django backend 已经 GA，可以支持 Django admin，因此 MongoDB 仍然是可行主方案。由于你明确希望使用阿里云上的自建 MongoDB，并且希望严格控制鉴权和网络暴露面，因此这里不再选 Atlas 托管模式。

MongoDB 对这个项目尤其合适的地方在于：

- 教学会话和聊天记录天然偏文档型
- 测试题、解析、风险事件适合嵌套结构
- RAG 片段与业务元数据可以和主业务数据保持同一主数据库体系
- 在研究规模下，自建 MongoDB + 独立向量库已足够支撑当前检索需求

### 2.4 为什么前端不做 React / Next.js

当前页面主要是：

- 注册 / 登录
- 问卷
- 教学页
- 测试页
- 结果页
- 情绪记录
- 后台列表与详情

这些页面并不需要一开始就用 SPA。Django Templates + HTMX 足够覆盖：

- 教学区块刷新
- 逐题推进
- 弹窗
- 后台筛选
- 局部状态更新
- 报告页局部图表刷新

这比前后端分离更简单，也更稳。

---

## 3. 各层选型说明

## 3.1 应用层

### 后端

- Django 6.0
- `django-mongodb-backend`

作用：

- 用户认证
- 页面渲染
- 会话状态推进
- 后台管理
- 导出接口
- 风险记录

部署约束：

- Django 建议仅监听 `127.0.0.1:8000` 或容器内私有网络地址
- 不建议将 Django 服务端口直接暴露给外部电脑或平板
- 对外流量统一先进入 Nginx，再由 Nginx 转发给 Django

### 前端

- Django Templates
- HTMX 2.x
- Alpine.js
- Tailwind CSS 4.x
- Chart.js 4.x

作用：

- 表单页和后台页服务端渲染
- 教学 / 测试局部刷新
- 情绪弹窗、风险弹窗、录音按钮等轻交互
- 学生个体可视化报告图表展示

前端实现要求应明确包含：

- 首版支持电脑与平板，不以手机为首版目标
- 至少验证 Windows / Chrome 与 iPad / Safari
- 录音、播放、测试选项和弹窗在触屏设备上具备足够点击面积
- 浏览器端请求优先使用相对路径，避免写死 `localhost` / `127.0.0.1`
- 页面、音频、图片、报告等前端可见资源地址应统一从同源入口暴露

---

## 3.2 数据层

### 主数据库

- 阿里云 ECS / VPC 内自建 MongoDB

建议承载的数据：

- 用户账号
- 邀请码信息
- 问卷数据
- 教学会话
- 聊天消息
- 测试记录
- 情绪记录
- 风险事件
- 导出日志

生产环境强制要求：

- 开启 MongoDB 鉴权
- 应用账号、管理员账号、备份账号分离
- 业务应用禁止使用 root 账号
- MongoDB 端口不直接暴露公网
- 推荐启用 TLS 连接

### 搜索与向量检索

- MongoDB text index
- Qdrant

建议承载的数据：

- MongoDB：
  - DBT 文档
  - chunk 元数据
  - 检索日志
- Qdrant：
  - embedding 向量
  - chunk id 引用
  - 检索 payload 元数据

### 需要注意的限制

MongoDB Django backend 的一个重要限制是：

- Django 原生 transaction API 不支持
- 需要使用该 backend 提供的 custom transaction API

当前项目可以接受这个限制，但实现时不要按 PostgreSQL 思维写事务。

同时也要注意：

- 自建 MongoDB 后，不再可用 Atlas Search / Vector Search
- 关键词检索与向量检索需要分别由 MongoDB text index 与 Qdrant 承担
- 应用层需要负责合并关键词检索结果与向量检索结果

---

## 3.3 教学 / 测试子流程与 RAG

推荐做法：

- 原始文档存 MinIO
- chunk 与 metadata 存 MongoDB
- MongoDB text index 做关键词检索
- Qdrant 做语义检索
- 本地 `bge-m3` 做 embedding
- 教学与测试子流程使用 **LangChain-based RAG**
- 教学计划、技能选择、测试题、标准答案、解析、风险判定等关键输出使用 **structured output**

推荐的 chunk metadata：

- `document_id`
- `version`
- `module`
- `skill`
- `section_title`
- `difficulty`
- `is_beginner_friendly`
- `scenario_tags`
- `risk_flags`

推荐的职责边界是：

- 应用层负责：
  - 注册登录
  - 邀请码
  - 问卷
  - 会话状态
  - 风险中止
  - 数据落库
  - 后台导出
- LangChain 子流程负责：
  - RAG 检索封装
  - 技能选择
  - 教学内容生成
  - 测试题生成
  - 标准答案与解析生成
  - 风险语义判定

这样做的原因是：

- RAG、retriever、structured output、prompt 组织用 LangChain 更省力
- 产品主流程仍由应用代码控制，更容易审计和验收
- 既复用成熟组件，又不把整站逻辑变成黑盒 agent runtime

针对当前项目，推荐的 hybrid retrieval 方式是：

1. MongoDB text index 负责技能名、模块名、规则文档等关键词召回
2. Qdrant 负责语义召回
3. 应用层按 chunk id 合并、去重、排序后再送入 LLM

这样做比为了研究型项目额外引入 Elasticsearch / OpenSearch 更简单。

---

## 3.4 AI / 模型层

### LLM

- DeepSeek `deepseek-v4-flash`

API：

- 端点：`https://api.deepseek.com/v1/chat/completions`
- 鉴权：`Authorization: Bearer <DEEPSEEK_API_KEY>`
- JSON Mode：`response_format={"type": "json_object"}`（OpenAI 兼容格式）
- 流式：SSE 格式，`data: {"choices": [{"delta": {"content": "..."}}]}\n\n`，以 `data: [DONE]` 结束
- 超时：120s，自动重试（429/502/503/529），2 次重试 × 指数退避

用途：

- 技能选择
- 对话式教学
- 测试题生成
- 逐题解析
- 风险语义识别
- 教学总结

### TTS

- 供应商：火山引擎（Volcengine）豆包语音合成模型2.0
- 默认音色：`BV700_streaming`（灿灿2.0 温暖女声）
- API：`POST https://openspeech.bytedance.com/api/v1/tts`，鉴权 `Authorization: Bearer;{token}`

用途：

- AI 教学播报
- 测试解析播报

### 图像生成

- 供应商：火山引擎（Volcengine）即梦文生图3.1
- 模型：`jimeng_t2i_v31`
- API：异步提交+轮询模式，`POST https://visual.volcengineapi.com`
- 鉴权：Volcengine Signature V4 (HMAC-SHA256)，使用 STS 临时凭证

用途：

- 教学情景图片
- 测试题配图

说明：

- API 为异步模式：提交任务 → 获得 task_id → 轮询查询结果
- 返回图片 URL 默认 24 小时有效
- 图片格式为 JPEG，支持自定义宽高（1328×1328 至 2048×2048）
- 这与 PRD 中”不持久化保存生成图片文件”的约束相容

### ASR

最终建议：

- **优先**：MiniMax ASR
- **回退**：火山引擎语音识别

原因：

- LLM 已迁移至 DeepSeek，TTS/ASR/图片使用火山引擎
- MiniMax ASR 账号侧尚未完全确认
- 因此工程上保留 fallback 结构

当前 AI 栈分布：
- LLM：DeepSeek（deepseek-v4-flash）
- TTS：火山引擎（豆包语音合成模型2.0）
- Image：火山引擎（即梦文生图3.1）
- ASR：火山引擎（优先），MiniMax（备用）

---

## 3.5 异步任务

- Celery 5.6
- Redis 7

建议异步化的能力：

- 文档切分与向量化
- 图像生成
- 语音转文字
- TTS 合成
- 批量导出

建议同步处理的能力：

- 登录
- 问卷提交
- 教学流程状态推进
- 风险事件写入
- 常规页面渲染

---

## 3.6 文件与导出

### 对象存储

- MinIO

用于保存：

- 原始 DBT 文档
- 后台导出文件

明确不保存：

- 用户原始音频
- 生成图片文件

### 导出

- JSON：默认格式
- CSV / Excel：研究整理
- PDF：单用户摘要报告
- HTML 页面：前台只读个体可视化报告

建议技术：

- `pandas`
- `openpyxl`
- `WeasyPrint`
- `Chart.js`

---

## 3.7 部署

推荐部署拓扑：

- `nginx`
- `django web`
- `celery worker`
- `celery beat`
- `redis`
- `mongodb`
- `qdrant`
- `minio`

部署方式：

- Docker Compose

访问与证书建议：

- 预发布与生产环境必须启用 HTTPS，否则浏览器麦克风权限不稳定或不可用
- 推荐使用正式域名或学校 / 项目已有子域名
- 正式公网访问入口固定为 `https://<domain>:10443`
- Nginx 对外监听 `10443`，不使用 `80`、`443`、`8080` 作为正式业务入口
- Let's Encrypt 证书签发方式固定为 `DNS-01`，不依赖 `80` 的 `HTTP-01` 验证，也不依赖 `443` 的 `TLS-ALPN-01` 验证
- 不建议直接裸 IP 对外提供教学系统，尤其在平板和 Safari 环境下
- 若管理员需要远程使用 MongoDB Compass，应优先通过 VPN / 堡垒机接入，而不是公开数据库端口
- 公网安全组仅开放业务所需端口，其中教学系统正式入口为 `10443`

环境一致性要求：

- 本地开发、预发布、生产都应尽量采用接近正式入口的访问方式
- 预发布环境必须验证跨设备登录态、Cookie、CSRF、录音与报告功能
- 不应以“localhost 正常”替代“异机访问正常”的验收

当前不建议：

- Kubernetes
- 微服务拆分
- 前后端完全分离

### Conda 环境建议

本项目在当前阶段建议**统一使用同一个 Conda 环境**运行本地开发所需的 Python 依赖。

推荐原因：

- Django、Celery、LangChain、embedding、导出工具都属于 Python 生态，统一环境最易管理
- 对研究项目而言，单环境更方便复现、交接和排查问题
- 当前外部依赖主要通过 API 调用，不需要为不同模型能力分别拆 Python 运行时

建议做法：

- 开发环境使用一个主 Conda 环境承载全部 Python 包
- 通过 `requirements` 或等效锁定文件固定依赖版本
- MongoDB、Redis、MinIO 继续以独立服务运行，不放进 Conda 环境

需要注意：

- 如果后续引入彼此强冲突的本地模型依赖，再评估是否拆分子环境
- 在当前技术栈下，尚无必须拆分多个 Conda 环境的理由

---

## 4. 实施建议

建议按以下顺序推进：

### Phase 1

- Django 基础工程
- MongoDB 接入
- MongoDB 鉴权、账号分离、网络隔离
- 邀请码注册 / 登录
- 问卷建档
- 基础后台

### Phase 2

- DBT 文档上传
- 文档切分 / embedding
- MongoDB text index + Qdrant
- 教学链路

### Phase 3

- 自动测试
- 逐题解析
- 无限重测
- 教学 / 测试状态机

### Phase 4

- 情绪记录
- 成就系统
- 前台个体可视化报告
- JSON / CSV / Excel 导出

### Phase 5

- 风险识别
- 风险事件后台查看
- 审计日志

### Phase 6

- 图像生成（火山引擎 即梦文生图3.1）
- TTS（火山引擎 豆包语音合成模型2.0）
- ASR（火山引擎）
- `10443` HTTPS、DNS-01 证书与设备兼容性回归

---

## 5. 最终取舍

这份最终版的核心取舍是：

1. **数据库优先服从你的熟悉度与自建安全可控要求**，因此选阿里云上的自建 MongoDB。
2. **应用框架优先服从后台、权限和开发效率**，因此继续选 Django。
3. **AI 供应商尽量统一**，LLM 使用 DeepSeek，TTS/ASR/图像生成使用火山引擎。
4. **ASR 保留最小 fallback**，避免把整套架构押在未完全确认的公开接口上。
5. **RAG 不做过度工程化**，使用 MongoDB text index + Qdrant 的轻量双检索方案。
6. **教学与测试子流程使用 LangChain-based RAG + structured output**，但产品主流程仍由应用层控制。
7. **前台不做前后端分离**，但必须补足电脑与平板适配，以及报告可视化图表能力。
8. **生产环境默认使用 `https://domain:10443`**，并通过正式域名或子域名访问，保证麦克风权限与终端兼容性。
9. **跨设备一致性优先于本地临时便利**，禁止在浏览器侧写死 `localhost`，并要求统一入口与同源访问。

---

## 6. 待确认项

还剩一个真正影响最终落地的待确认项：

- 你们当前 MiniMax 账号是否已经稳定可用 **ASR API**

如果答案是”能”，那整套 AI 栈可以收敛为：

- LLM：DeepSeek
- TTS：MiniMax
- Image：MiniMax
- ASR：MiniMax

如果答案是“还不能确认”，就保持当前最终文档里的写法：

- ASR：MiniMax 优先，未确认时回退火山引擎

当前已经确认的部署前提：

- 预发布 / 生产使用 HTTPS
- 使用 Nginx 反向代理
- 正式业务入口使用 `10443`
- 不以 `80` / `443` / `8080` 作为正式业务端口
- 证书签发采用 Let's Encrypt `DNS-01`
- 不以裸 IP + HTTP 作为正式访问方式

---

## 7. 参考资料

- Django 6.0 文档：https://docs.djangoproject.com/en/6.0/
- Django 6.0 发布说明：https://docs.djangoproject.com/en/6.0/releases/6.0/
- MongoDB Django Backend GA：https://www.mongodb.com/company/blog/product-release-announcements/mongodb-django-backend-now-ga
- MongoDB Django Backend 文档：https://www.mongodb.com/docs/languages/python/django-mongodb/current/
- MongoDB Django Backend 安装说明：https://www.mongodb.com/docs/languages/python/django-mongodb/current/get-started/
- MongoDB Django Backend 事务说明：https://www.mongodb.com/docs/languages/python/django-mongodb/v5.2/interact-data/transactions/
- MongoDB Text Index 文档：https://www.mongodb.com/docs/manual/core/indexes/index-types/index-text/
- Qdrant 文档：https://qdrant.tech/documentation/
- LangChain Retrieval：https://docs.langchain.com/oss/python/langchain/retrieval
- LangChain Structured Output：https://docs.langchain.com/oss/python/langchain/structured-output
- DeepSeek API 文档：https://api-docs.deepseek.com/zh-cn/
- MiniMax API 总览：https://platform.minimaxi.com/docs/api-reference/api-overview（已弃用，仅作参考）
- MiniMax Speech T2A HTTP API：https://platform.minimaxi.com/docs/api-reference/speech-t2a-http（已弃用）
- MiniMax Image Generation T2I API：https://platform.minimaxi.com/docs/api-reference/image-generation-t2i（已弃用）
- 火山引擎语音能力文档入口：https://www.volcengine.com/docs/6561
- HTMX 文档：https://htmx.org/docs/
- Celery 文档：https://docs.celeryq.dev/en/stable/getting-started/
- Redis 文档：https://redis.io/docs/latest/
- Tailwind CSS 文档：https://tailwindcss.com/docs
