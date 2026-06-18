# DBT 技能教学平台 — 系统运行指南

本指南基于当前代码库与 memory_bank 中的所有文档，面向接手开发的工程师，用中文讲解系统的运行方式、各模块功能与使用方法。

---

## 一、系统概览

DBT 技能教学平台是一个基于 Django 6.0 单体应用的研究型教学系统。核心业务流程为：**邀请码注册 → 问卷建档 → AI 教学对话 → 自动测试 → 重测 → 情绪记录 → 成就解锁 → 报告查看与数据导出**。

系统采用 MongoDB 作为主数据库，Qdrant 作为向量检索引擎，DeepSeek 作为 LLM 供应商，火山引擎（Volcengine）作为图像生成/TTS/ASR 供应商，Redis 作为缓存与 Celery 消息队列，MinIO 作为对象存储。

### 1.1 技术栈速览

| 层级 | 选型 |
|------|------|
| 后端框架 | Django 6.0 + `django-mongodb-backend` |
| 主数据库 | MongoDB 7.x（自建，需鉴权） |
| 向量检索 | Qdrant + 本地 `bge-m3` embedding |
| 前端 | Django Templates + HTMX 2.x + Alpine.js + Tailwind CSS 4.x |
| AI 模型 | DeepSeek `deepseek-v4-flash` (LLM)、火山引擎 `jimeng_t2i_v31` (图像/即梦文生图3.1)、火山引擎 `BV700_streaming` (TTS 豆包语音合成2.0) |
| 异步任务 | Celery 5.6 + Redis 7 |
| 对象存储 | MinIO |
| 部署 | Docker Compose + Nginx (入口 `10443`) |

### 1.2 核心目录结构

```
DBT/
├── dbt_platform/     # Django 项目配置 (settings, urls, celery, health_urls)
├── accounts/         # 用户模型、邀请码、认证、权限装饰器、中间件
├── questionnaire/    # 用户画像 (UserProfile)、注册问卷表单与视图
├── teaching/         # 教学会话 (TeachingSession)、聊天消息 (ChatMessage)、教学流程视图
├── testing/          # 测试记录 (Test)、测试题目 (TestQuestion)、测试流程视图
├── mood/             # 情绪记录 (MoodRecord)、成就系统 (Achievement, UserAchievement)
├── risk/             # 风险事件 (RiskEvent)、关键词+AI 双重风险检测
├── knowledge_base/   # DBT 技能文档 (KnowledgeDocument)、文档片段 (KnowledgeChunk)、检索日志 (RetrievalLog)、RAG 检索
├── media_app/        # 图像生成 (MiniMax) + TTS & ASR 语音服务 (火山引擎)
├── reports/          # 学生个体报告（网页+PDF）、报告查看访问日志 (ReportAccessLog)
├── export_app/       # 研究后台数据导出（JSON/CSV）、管理员操作日志 (AdminOperationLog)
├── templates/        # Django 模板 (base.html + 各模块页面)
├── docker/           # Nginx 配置、MongoDB 初始化脚本、SSL 证书
├── scripts/          # MinIO + Qdrant 安装启动脚本
└── memory_bank/      # 项目文档 (PRD, 技术栈, 实施计划, 架构参考, 进度记录)
```

---

## 二、环境搭建与启动

### 2.1 前置依赖

运行本系统需要以下服务在本地运行：

| 服务 | 端口 | 用途 |
|------|------|------|
| MongoDB 7.x | 27017 | 主数据库 |
| Redis 7 | 6379 | Celery broker + 缓存 |
| MinIO | 9000 (API), 9001 (Console) | 对象存储 |
| Qdrant | 6333 (gRPC), 6334 (HTTP) | 向量检索 |

MongoDB 必须开启鉴权，应用用户 `dbt_app` 拥有 `dbt_platform` 数据库的 `readWrite` 权限。

### 2.2 Conda 环境

```bash
# 创建并激活环境
conda env create -f environment.yml
conda activate dbt

# 或从 requirements.txt 安装
pip install -r requirements.txt
```

### 2.3 环境变量配置

复制 `.env.example` 为 `.env`，按实际情况填写。关键变量分为 6 个区块：

1. **Django 核心**：`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
2. **MongoDB**：`MONGODB_HOST`, `MONGODB_PORT`, `MONGODB_NAME`, `MONGODB_USER`, `MONGODB_PASSWORD`
3. **Redis**：`REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`
4. **MinIO**：`MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
5. **Qdrant**：`QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_COLLECTION`
6. **MiniMax**：`MINIMAX_API_KEY`, `MINIMAX_BASE_URL`

注意：`.env` 中包含真实的 API 密钥，不应提交到版本控制。

### 2.4 启动服务与验证

```bash
# 1. 启动 MongoDB、Redis、MinIO、Qdrant
sh scripts/install_services.sh

# 2. Django 系统检查
python manage.py check

# 3. 应用迁移（MongoDB 使用 --fake，因为数据库是无模式的）
python manage.py migrate --fake

# 4. 创建超级用户（如果还没有）
python manage.py createsuperuser

# 5. 启动开发服务器
python manage.py runserver

# 6. 启动 Celery worker（另一个终端）
celery -A dbt_platform worker -l info

# 7. 验证健康检查
curl http://localhost:8000/health/        # 返回 {"status": "ok"}
curl http://localhost:8000/health/ready/  # 所有后端 ok 时返回 200，否则 503
```

### 2.5 Docker Compose 部署（生产模式）

```bash
docker-compose up -d
```

这将启动全部 8 个容器：`nginx`（监听 `10443`）、`web`、`worker`、`beat`、`mongodb`、`redis`、`qdrant`、`minio`。

正式入口为 `https://<domain>:10443`。Nginx 不直接暴露 Django，浏览器请求统一通过 Nginx 反向代理。

### 2.6 运行测试

```bash
# 运行全部测试
python manage.py test

# 运行特定模块测试
python manage.py test accounts
python manage.py test teaching

# 运行 P0 合规验证
python manage.py test dbt_platform.p0_verification
```

测试数据库为 `test_dbt_platform`——`dbt_app` 用户需要拥有对其的 `readWrite` + `dbAdmin` 权限。

---

## 三、功能模块详解

### 3.1 用户认证与权限 (`accounts/`)

#### 三种角色

| 角色 | 权限范围 |
|------|---------|
| `student`（学生） | 只能访问自己的教学、测试、情绪记录、成就。**无法**查看任何报告页面，**无法**访问后台。 |
| `report_viewer`（报告查看者） | 只能查看被授权学生的个体可视化报告页面与 PDF 导出。**无法**访问原始数据、后台或导出功能。 |
| `admin`（管理员） | 完整的 Django 后台权限，可查看所有数据、导出原始数据（JSON/CSV）、管理文档和用户。 |

#### 注册流程

```
用户访问 /accounts/register/
  → 填写：用户名、密码、确认密码、邀请码
  → 系统校验：邀请码存在、状态为 active、未被使用
  → 创建用户（role=student）→ 标记邀请码为 used → 自动登录
  → 重定向到问卷 /questionnaire/profile/
```

#### 邀请码管理

在 Django 后台 `/admin/accounts/invitecode/` 中：
- 可批量创建（一次生成 10 个 UUID 格式的邀请码）
- 可单独启用/禁用
- 可按状态筛选（active / used / disabled）
- 已使用的邀请码会记录使用者与使用时间

#### 关键文件

| 文件 | 作用 |
|------|------|
| `accounts/models.py` | User 模型（扩展 AbstractUser，增加 role 字段）、InviteCode、ReportViewerAssignment |
| `accounts/forms.py` | RegisterForm（含邀请码校验）、LoginForm |
| `accounts/views.py` | register_view, login_view, logout_view |
| `accounts/decorators.py` | role_required, student_required, admin_required, report_viewer_required |
| `accounts/middleware.py` | AdminAccessMiddleware —— 拦截非 admin 用户访问 /admin/ |
| `accounts/admin.py` | CustomUserAdmin（含 12 个内联聚合）、InviteCodeAdmin、ReportViewerAssignmentAdmin |

#### ReportViewerAssignment（报告查看授权）

管理员在后台建立 `report_viewer` 用户与 `student` 用户的授权关系：
- `is_active=True`：授权生效，报告查看者可看到该学生的报告
- `is_active=False`：授权冻结，立即撤销访问权限（每次请求实时检查）

---

### 3.2 注册问卷 (`questionnaire/`)

学生注册后首先要完成问卷，系统会通过 `@profile_required` 装饰器阻断未完成问卷的用户进入教学。

#### 问卷字段

| 字段 | 类型 | 说明 |
|------|------|------|
| 性别 | 单选 | 男 / 女 / 其他 / 不愿透露 |
| 年龄 | 整数 | 具体年龄 |
| 年级 | 下拉 | 初一至高三 |
| 爱好多选标签 | 多选标签 | 如阅读、篮球、音乐等 |
| 其他爱好 | 文本 | 自由填写 |
| 困扰多选标签 | 多选标签 | 如学业压力、人际困扰等 |
| 其他困扰 | 文本 | 自由填写 |

所有数据保存在 `UserProfile` 模型中，与 User 一对一关联。这些信息会在 AI 教学和测试生成时被读取，用于个性化教学内容。

---

### 3.3 教学会话 (`teaching/`)

教学是整个系统的核心链路。每次教学会话固定推演：

```
教学前情绪记录 → 信息收集 → AI 技能选择 → RAG 文档检索
  → AI 对话教学 → 自动过渡到测试 → 教学后情绪记录
```

#### 会话状态机

| 状态 | 含义 |
|------|------|
| `ongoing` | 正在进行 |
| `completed` | 正常完成 |
| `stopped_by_risk` | 因风险检测中止 |
| `user_terminated` | 用户主动终止或 API 错误导致终止 |

#### 会话阶段 (phase)

| 阶段 | 说明 |
|------|------|
| `info_collection` | AI 收集本次教学所需信息 |
| `skill_selection` | AI 推荐技能，学生确认或自定义 |
| `teaching` | AI 对话式教学 |
| `completed` | 教学完成，自动进入测试 |

#### 关键文件

| 文件 | 作用 |
|------|------|
| `teaching/models.py` | TeachingSession（会话状态、阶段、技能、RAG 上下文）+ ChatMessage（角色、内容、模态） |
| `teaching/services.py` | 教学流程编排：信息收集、技能选择、RAG 检索、AI 对话生成、摘要生成 |
| `teaching/views.py` | teaching_home_view（入口）、start_view、record_pre_mood、confirm_skill、send_message_view、end_session_view |
| `knowledge_base/rag/chains.py` | LangChain RAG 链：技能选择、教学计划、教学内容、教学摘要生成 |

#### 技能列表

当前教学围绕 DBT 正念模块的技能展开，包括：正念呼吸、观察念头、接纳感受、身体扫描、智慧心、情绪调节等。

---

### 3.4 测试 (`testing/`)

教学完成后自动进入测试。每个测试包含 5 道选择题，每题 4 个选项。学生逐题作答，每题作答后立即展示解析。

#### 测试状态机

| 状态 | 含义 |
|------|------|
| `ongoing` | 正在进行 |
| `completed` | 已完成 |
| `user_terminated` | 被终止（风险触发或 API 错误） |

#### 通过标准

答对 ≥ 4 题（共 5 题）视为通过。未通过可无限重测。

#### 关键文件

| 文件 | 作用 |
|------|------|
| `testing/models.py` | Test（测试记录：尝试次数、正确数、总题数、通过标记）+ TestQuestion（每题：选项、正确答案、用户答案、解析、配图） |
| `testing/services.py` | 测试创建（AI 生成题目）、答题处理、测试完成判定、重测逻辑 |
| `testing/views.py` | test_view（测试主页）、answer_view（提交答案）、finish_test_view（完成测试） |

---

### 3.5 情绪记录 (`mood/`)

在教学前、教学后、测试后三个时间点记录学生情绪。

#### 情绪值

1-5 评分 + 对应 emoji 表情（😢😟😐🙂😄）。

#### 记录上下文 (context)

| 值 | 含义 |
|------|------|
| `before_teaching` | 教学前 |
| `after_teaching` | 教学后 |
| `after_testing` | 测试后 |
| `manual` | 手动记录 |

#### 成就系统

- `Achievement`：定义成就（名称、描述、图标、触发规则 JSON）
- `UserAchievement`：用户解锁成就的记录（解锁时间）

---

### 3.6 风险控制 (`risk/`)

#### 双重检测机制

系统对教学消息和测试答案进行**双通道**风险检测：

1. **关键词通道**（纯 Python，不依赖 AI）：
   - **高危关键词**（17 个）：自杀、自伤、想死、割腕、跳楼……→ 匹配后立即停止会话
   - **中危关键词**（5 个）：绝望、毫无希望、没有意义……→ 触发 AI 语义评估

2. **AI 语义通道**（DeepSeek LLM）：对中危关键词匹配 + 中度担忧指标（如"活得没意义"、"我恨我自己"）进行语义深度评估。

**结果判定**（两个通道独立运行）：

| 关键词 | AI | 结果 | detection_source |
|--------|-----|------|------------------|
| 触发高危 | 确认高危 | 停止会话 | `both` |
| 触发高危 | 未确认 | 仅记录事件 | `keyword` |
| 触发中危 | 确认高危 | 停止会话 | `both` |
| 触发中危 | 未确认 | 仅记录事件 | `keyword` |
| 未触发 | AI 确认高危 | 停止会话 | `ai` |
| 未触发 | AI 未确认 | 不记录 | — |

#### AI 不可用时的降级行为（Step 13 修复）

当 DeepSeek LLM 不可用时（`APIError` 或任何异常），系统降级为**纯关键词模式**：
- 高危关键词匹配 → 仍然**停止会话**（保守策略，宁可误停不可漏过）
- 中危关键词匹配 → 创建风险事件但**不停止会话**
- 普通文本 → 不创建事件
- `detection_source` 正确标记为 `"keyword"` 而非 `"both"`（因为 AI 未实际贡献）

#### 风险事件记录（RiskEvent）

| 字段 | 说明 |
|------|------|
| `trigger_text` | 触发文本 |
| `detection_source` | keyword / ai / both |
| `session_stopped` | 是否停止了教学会话 |
| `action_taken` | 采取的措施 |
| `follow_up_mode` | 跟进模式：onsite_manual_followup / no_action |

#### 风险弹窗

当高风险被触发时，浏览器端会展示风险弹窗页面（`/risk/popup/`），包含：心理援助热线 12356、北京心理危机研究与干预中心热线、联系老师的提示、返回安全的按钮。

#### 关键文件

| 文件 | 作用 |
|------|------|
| `risk/services.py` | 关键词列表、check_keyword_risk、process_risk_check、process_test_risk_check、create_risk_event、stop_session_for_risk |
| `risk/models.py` | RiskEvent 模型 |
| `risk/views.py` | popup_view（风险弹窗页面） |

---

### 3.7 知识库与 RAG 检索 (`knowledge_base/`)

#### 文档处理链路

```
原始 DBT 技能文档（PDF/TXT/DOCX）
  → 上传至 MinIO (knowledge_base/storage.py)
  → 解析 + 切分 (knowledge_base/services.py: chunk_text)
  → 本地 BGE-M3 embedding 生成 (knowledge_base/services.py: generate_embeddings)
  → 向量存入 Qdrant (knowledge_base/services.py: index_chunks_to_qdrant)
  → 文本索引存入 MongoDB（text index）
```

#### 检索方式

| 方式 | 使用的后端 | 适用场景 |
|------|-----------|---------|
| 关键词检索 (`keyword_search`) | MongoDB text index | 技能名、模块名、规则文档关键词召回 |
| 语义检索 (`semantic_search`) | Qdrant + bge-m3 embedding | 语义相似的 DBT 教学内容 |
| 混合检索 (`hybrid_search`) | 两者合并去重 | 教学和测试生成时的标准检索方式 |

**故障隔离**（Step 13 修复）：`hybrid_search` 现在对语义检索做了 try/except 包装——当 Qdrant 不可用时，仍然返回关键词检索结果，不会让整个检索链路崩溃。

#### 检索日志 (`RetrievalLog`)

每次检索都会记录：查询文本、检索方式、召回片段 ID 列表、用途（teaching / test_generation / explanation / retest / risk）。

#### 关键文件

| 文件 | 作用 |
|------|------|
| `knowledge_base/models.py` | KnowledgeDocument、KnowledgeChunk、RetrievalLog |
| `knowledge_base/services.py` | chunk_text、generate_embeddings、keyword_search、semantic_search、hybrid_search、index_chunks_to_qdrant、log_retrieval |
| `knowledge_base/storage.py` | MinIO 操作：upload_document、download_document、delete_document |
| `knowledge_base/rag/chains.py` | LangChain RAG 链（全部支持 `mock_llm_response` 参数用于测试） |
| `knowledge_base/rag/retriever.py` | 检索器封装 |
| `knowledge_base/rag/schemas.py` | Pydantic structured output schemas |
| `knowledge_base/rag/llm_client.py` | DeepSeek LLM 客户端、APIError、ConfigurationError |

---

### 3.8 媒体服务 (`media_app/`)

统一封装所有 MiniMax 外部 API 调用：

| 功能 | 服务商/模型 | 服务函数 | 说明 |
|------|-------------|---------|------|
| 图像生成 | MiniMax `image-01-live` | `generate_image()` | 用于教学情景配图、测试题配图。返回 URL（24 小时有效），不持久化图片文件。 |
| TTS 语音合成 | 火山引擎 `豆包语音合成2.0` | `synthesize_speech()` | 将 AI 教学文本转为语音播报。默认音色 BV700_streaming（灿灿2.0）。 |
| ASR 语音识别 | 火山引擎 ASR | `transcribe_audio()` | 学生语音输入转文本。提交+轮询模式。 |

所有函数都使用自定义异常 `ConfigurationError`（缺少 API Key）和 `APIError`（网络错误、超时、非 200 响应）进行错误处理。这是整个代码库中错误处理最完善的文件。

#### 关键文件

| 文件 | 作用 |
|------|------|
| `media_app/services.py` | generate_image、synthesize_speech、transcribe_audio |
| `media_app/views.py` | 媒体生成视图（供前端 HTMX 调用） |
| `media_app/models.py` | AudioSynthesisLog（TTS 合成日志） |

---

### 3.9 学生报告 (`reports/`)

#### 报告页面（7 个区块）

1. **基础信息**：用户名、注册时间、性别、年龄、年级、兴趣爱好、困扰/担忧
2. **报告摘要**：AI 生成的自然语言总结（参与度、通过率、情绪趋势、技能掌握、成就、风险）
3. **概览卡片**：完成教学次数、测试次数、成就解锁数、总正确率
4. **情绪变化**：内联 SVG 折线图 + 情绪历史记录表格
5. **技能学习次数**：CSS 条形图展示各项技能的学习次数
6. **测试记录**：表格展示每次测试的时间、技能、成绩、通过/未通过/重测
7. **成就解锁**：已解锁成就的卡片列表（图标、名称、描述、解锁时间）

#### PDF 导出

通过 WeasyPrint 将报告渲染为 A4 格式的 PDF。使用独立的 `student_report_pdf.html` 模板（独立 HTML，内联 CSS，不继承 base.html），因为 WeasyPrint 不能执行 JavaScript。SVG 情绪图表在 PDF 中原生渲染。

#### 权限模型

| 角色 | 仪表盘 | 个体报告 | PDF 导出 |
|------|--------|---------|----------|
| admin | 显示所有学生 | 可查看任何人 | 可用 |
| report_viewer | 仅显示已授权的活跃学生 | 仅已授权学生（is_active=True） | 已授权学生可用 |
| student | 403 拒绝 | 403 拒绝 | 403 拒绝 |

#### 访问日志 (`ReportAccessLog`)

每次查看报告或导出 PDF 都会记录：访问者、访问者角色、目标学生、操作类型（view / export）、导出格式、时间戳。后台只读，不可修改或删除。

#### 关键文件

| 文件 | 作用 |
|------|------|
| `reports/services.py` | get_student_report_data（从 6 个模型聚合数据）、_get_profile、_build_summary、_render_mood_chart_svg |
| `reports/views.py` | dashboard_view、student_report_view、student_report_pdf_view |
| `reports/models.py` | ReportAccessLog |
| `templates/reports/student_report.html` | 网页版报告模板（继承 base.html） |
| `templates/reports/student_report_pdf.html` | PDF 版报告模板（独立 HTML，A4 打印优化） |

---

### 3.10 数据导出 (`export_app/`)

仅限管理员使用（非管理员返回 403）。

#### 导出格式

| 格式 | 说明 |
|------|------|
| JSON | 完整的嵌套数据结构，包含用户信息、教学会话（含每条消息）、测试记录（含每题详情）、情绪记录、风险事件、成就 |
| CSV | 分段格式（`=== 用户信息 ===` 等标签分隔），UTF-8 BOM 确保 Excel 中文兼容 |

#### 导出页面 (`/export/`)

展示所有学生列表，每个学生有单独的 JSON/CSV 下载链接，顶部有"批量导出"按钮。

#### 操作日志 (`AdminOperationLog`)

每次导出都记录：操作管理员、操作类型（`export_data`）、目标类型（user / users_bulk）、目标 ID、导出格式（json / csv）、导出范围。

#### 权限

`report_viewer` 和 `student` 角色无法访问 `/export/` 下的任何 URL。

---

## 四、Django 后台使用指南

Django 后台入口为 `/admin/`，仅 admin 角色用户可访问。

### 4.1 用户聚合视图

在后台点击任意用户进入详情页，该页面通过 12 个只读内联聚合了该用户的所有关联数据：

```
用户详情页
├── 用户画像（性别、年龄、年级、标签）
├── 教学会话列表（状态、模块、技能）
├── 测试记录列表（尝试次数、通过标记、正确数）
├── 情绪记录列表（情绪值、emoji、场景）
├── 风险事件列表（检测来源、触发文本预览）
├── 成就列表（成就名称、解锁时间）
├── 检索日志列表（查询文本、用途）
├── 管理员操作日志（仅 admin 用户可见）
├── 报告查看授权（仅 report_viewer 用户可见）
├── 被授权学生（仅 student 用户可见）
├── 报告访问记录（作为查看者）
└── 报告访问记录（作为被查看学生）
```

### 4.2 各模块后台功能速查

| 后台入口 | 管理内容 | 特色功能 |
|----------|---------|---------|
| 用户 | User, InviteCode, ReportViewerAssignment | 批量创建邀请码、管理报告查看授权 |
| 问卷 | UserProfile | 查看/编辑学生画像 |
| 教学 | TeachingSession, ChatMessage | 会话详情页展示完整聊天记录 |
| 测试 | Test, TestQuestion | 测试详情页展示每题问答与解析 |
| 情绪 | MoodRecord, Achievement, UserAchievement | 按情绪值/场景筛选 |
| 风险 | RiskEvent | 按检测来源筛选、搜索触发文本 |
| 知识库 | KnowledgeDocument, KnowledgeChunk, RetrievalLog | 文档状态流转、检索日志 |
| 导出 | AdminOperationLog | 只读，不可增删改 |
| 报告 | ReportAccessLog | 只读，不可增删改 |

所有内联（Inline）都是只读模式，不会有空白行，也不支持在详情页直接添加关联记录。

---

## 五、核心业务流程序列

### 5.1 完整教学流程

```
1. 学生注册（邀请码验证）
2. 完成问卷建档（@profile_required 门控）
3. 进入教学首页 → 点击"开始新教学"
4. 记录教学前情绪（1-5 分 + emoji）
5. AI 信息收集（读取用户画像 + 历史记录）
6. AI 推荐技能（学生确认或自定义）
7. RAG 检索（混合检索 DBT 文档片段）
8. AI 对话教学（自然语言对话，结合个人信息）
9. 教学中风险检测（每条消息实时检测）
10. 自动进入测试（5 道选择题，逐题作答+解析）
11. 测试中风险检测（每个答案实时检测）
12. 测试结果判定（≥4/5 通过；未通过可重测）
13. 记录教学后情绪
14. 成就检查与解锁
15. 会话完成，保存全部记录
```

### 5.2 风险触发流程

```
学生输入消息/答案
  → check_keyword_risk(text) — 纯 Python，毫秒级
  → run_risk_assessment(text, keywords, context) — DeepSeek LLM
  → 两通道独立判定
  → 任一通道判定 should_stop = True
    → create_risk_event（记录风险事件）
    → stop_session_for_risk（状态 → STOPPED_BY_RISK，创建系统消息）
    → 浏览器端弹出风险弹窗（热线 + 联系老师 + 返回安全区）
```

### 5.3 报告查看与导出流程

```
管理员或报告查看者登录
  → /reports/ 仪表盘（管理员看到全部学生，report_viewer 看到已授权学生）
  → 点击学生进入 /reports/student/<id>/
  → 页面展示 7 个区块（基础信息、摘要、概览、情绪图、技能条、测试表、成就）
  → 点击"导出 PDF" → /reports/student/<id>/pdf/ → 下载 PDF
  → 管理员访问 /export/ → 选择学生 → JSON/CSV 下载
```

---

## 六、关键架构决策与约束

### 6.1 为什么用 MongoDB

- 教学会话和聊天消息天然是文档型数据，MongoDB 的嵌套结构更自然
- 测试题、解析、风险事件适合内嵌
- 研究规模下，自建 MongoDB + Qdrant 已足够

### 6.2 MongoDB 的 Django 约束

- Django 的 `transaction.atomic()` 不适用于 MongoDB
- 所有迁移使用 `--fake`（MongoDB 是无模式的，集合在首次使用时自动创建）
- `AutoField` 不兼容 → 所有模型使用 `ObjectIdAutoField` 或 UUID 字符串主键
- `gen_uuid()` 函数（`dbt_platform/utils.py`）替代所有 lambda 默认值（因为 Django 迁移序列化器不支持 lambda）

### 6.3 浏览器端约束

- 所有浏览器端请求优先使用**相对路径**，不写死 `localhost` 或 `127.0.0.1`
- 正式入口为 `https://<domain>:10443`，Nginx 统一代理
- 电脑与平板双端兼容是固定约束（Windows/Chrome + iPad/Safari）

### 6.4 AI 调用约束

- 所有外部 AI 调用必须有失败处理和日志记录
- 风险检测必须"失败封闭"（fail closed）：AI 不可用时，关键词通道仍能独立工作
- 测试题生成失败时，测试状态标记为 `USER_TERMINATED` 并提示用户
- 图像生成、TTS、ASR 返回 URL 时，由前端直接加载（避免代理大文件）

### 6.5 日志体系

| Logger 名称 | 覆盖范围 |
|-------------|---------|
| `dbt_platform.teaching` | 教学流程、LLM 调用 |
| `dbt_platform.testing` | 测试流程、答题处理 |
| `dbt_platform.risk` | 风险检测、事件创建 |
| `dbt_platform.knowledge_base` | 文档处理、检索 |
| `dbt_platform.media_app` | 图像生成、TTS、ASR |
| `dbt_platform.mood` | 情绪记录、成就系统 |
| `dbt_platform.health` | 健康检查（MongoDB/Redis/Qdrant/MinIO 故障） |

日志输出到控制台 + 文件（`logs/dbt.log`，10MB 轮转，保留 5 个历史文件）。

---

## 七、常见操作命令

```bash
# 系统检查
python manage.py check

# 迁移（MongoDB 必须用 --fake）
python manage.py migrate --fake

# 创建超级用户
python manage.py createsuperuser

# 批量创建邀请码（在 Django shell 中）
python manage.py shell
>>> from accounts.models import InviteCode
>>> codes = InviteCode.create_batch(10)
>>> for c in codes:
...     print(c.code)

# 运行测试
python manage.py test                          # 全部
python manage.py test accounts                 # 按模块
python manage.py test dbt_platform.p0_verification  # P0 合规

# Celery
celery -A dbt_platform worker -l info
celery -A dbt_platform beat -l info

# Docker Compose
docker-compose up -d       # 启动全部服务
docker-compose down        # 停止全部服务
docker-compose restart web # 仅重启 Django
```

---

## 八、测试统计

截至 Step 13 完成，全系统共 **671 个测试**，分布如下：

| 模块 | 测试数 | 主要内容 |
|------|--------|---------|
| accounts | ~40 | 注册、登录、权限、装饰器、中间件 |
| questionnaire | ~15 | 问卷表单、视图、画像创建 |
| teaching | ~40 | 教学流程、API 错误处理、风险检测 |
| testing | ~40 | 测试创建、答题、完成、重测、风险 |
| mood | ~25 | 情绪记录、成就系统 |
| risk | ~50 | 关键词检测、AI 语义检测、降级、检测来源 |
| knowledge_base | ~30 | 文档解析、切分、检索、存储、管理后台 |
| media_app | ~30 | 图像生成、TTS、ASR、错误处理 |
| reports | ~40 | 仪表盘、个体报告、PDF 导出、访问日志 |
| export_app | ~20 | JSON/CSV 导出、操作日志、权限 |
| dbt_platform | ~32 | 健康检查、P0 合规验证 |

---

## 九、待确认项与注意事项

1. **MiniMax ASR 可用性**：当前 MiniMax ASR 为优先方案，火山引擎为 fallback。如果 MiniMax ASR 账号侧已确认可用，可将音频栈完全统一到 MiniMax。

2. **`.env` 安全**：`.env` 中包含真实的 MiniMax API 密钥和数据库密码。在初始化 git 仓库前必须将其加入 `.gitignore`，并维护 `.env.example` 作为模板。

3. **MongoDB 事务**：Django 原生 `transaction.atomic()` 在 MongoDB 上不可用。当前代码库未使用任何事务操作，后续开发如需事务，应使用 `django-mongodb-backend` 提供的 custom transaction API。

4. **WeasyPrint 系统依赖**：PDF 导出需要系统级依赖（libpango、libcairo、libgdk-pixbuf）。在 Docker 环境中已在 Dockerfile 中安装，本地开发需手动安装。

5. **pymongo DEBUG 日志**：测试输出中 pymongo 的 DEBUG 日志非常冗长。运行时可通过 `2>/dev/null` 过滤，或调整 `LOGGING` 配置中的 pymongo logger 级别。
