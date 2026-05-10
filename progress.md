# DBT Platform — 部署进度

> 最后更新: 2026-05-09

## 已完成配置

### 0. Docker 环境 (2026-05-09)
- 卸载旧版 `docker.io` (29.1.3)
- 安装 Docker CE 29.4.3 + Docker Compose v5.1.3
- 使用阿里云镜像源
- docker registry 镜像源: `docker.1ms.run`, `docker.m.daocloud.io`
- 当前 dockerd 为手动启动（无 systemd），重启机器后需重新执行 `dockerd &`

### 1. Dockerfile 修复
- `libgdk-pixbuf2.0-0` → `libgdk-pixbuf-xlib-2.0-0` (Debian Trixie 兼容)
- apt 源替换为阿里云镜像 (`mirrors.aliyun.com`)
- pip 源替换为清华镜像 (`pypi.tuna.tsinghua.edu.cn`)

### 2. SSL 证书 (`docker/certs/`)
- 生成自签名证书 `fullchain.pem` + `privkey.pem`
- 有效期 365 天，用于 Nginx HTTPS 终止

### 3. 网络配置 (2026-05-09)
- Nginx 对外暴露端口: `10443→443`
- CSRF_TRUSTED_ORIGINS 已添加公网访问地址
- 阿里云安全组已开放 10443 端口，公网可访问

### 4. 迁移修复
- `accounts/migrations/0001_initial.py`: 依赖从 `auth.0013` 改为 `auth.0012` (Django 6.0 兼容)
- MongoDB 用户 `dbt_app` 手动创建 (init.js 未在已有 volume 上触发)

### 5. 管理员账户
- 已创建: `admin` / `admin123456`

## Bug 修复记录

### Bug #1: Admin 后台 CSS/JS 404，页面样式异常 (2026-05-09)
- **原因**: Docker 构建时 `collectstatic` 写入镜像内部 `/app/staticfiles/`，但容器运行时 volume mount `./:/app` 覆盖了该目录，宿主机 `staticfiles/` 为空
- **修复**: `docker compose exec web python manage.py collectstatic --noinput`

### Bug #2: admin 用户无法登录后台 (2026-05-09)
- **原因**: `create_superuser` 通过 shell 命令执行时，`is_staff` 和 `is_superuser` 未正确设为 `True`
- **修复**: `User.objects.filter(username='admin').update(is_staff=True, is_superuser=True)`

### Bug #3: 已登录用户访问 /admin/ 被重定向到首页 (2026-05-09)
- **原因**: `AdminAccessMiddleware` 检查 `user.role != "admin"` 就重定向到首页；admin 用户的 `role` 字段值为 `student`
- **修复**: `User.objects.filter(username='admin').update(role='admin')`

### Bug #4: 邀请码新增页面无输入框，显示 "-" (2026-05-09)
- **原因**: `InviteCodeAdmin.readonly_fields` 包含 `code`，Django admin 在新增时对只读字段显示 `-`
- **修复**: 改用 `get_readonly_fields` — 新增时 `code` 可编辑，修改时 `code` 只读

### Bug #5: 教学会话 info_collection 阶段"继续"按钮只刷新页面不进入教学 (2026-05-09)
- **原因**: `run_pre_mood` 把 phase 推进到 `info_collection` 后，`run_info_collection` 调用 AI API 失败（Minimax），phase 卡在 `info_collection`。模板在该阶段 POST 到 `record_pre_mood_view`，但该 view 只接受 `pre_mood_recording` 阶段，直接 reject 并重定向回当前页 → 无限刷新循环
- **修复**: `record_pre_mood_view` 新增 `info_collection` 阶段处理 — 检测到该阶段时跳过 mood 记录，重试 `run_info_collection`

### Bug #6: HuggingFace 模型下载导致 500 / OOM (2026-05-09)
- **原因**: `SentenceTransformer(BAAI/bge-m3)` 需从 huggingface.co 下载 2GB+ 模型，容器内无法访问 → 连接超时 → Gunicorn worker OOM
- **修复**: `.env` 中添加 `HF_ENDPOINT=https://hf-mirror.com`；`get_embedding_model()` 改为优先 `local_files_only=True` 从缓存加载

### Bug #7: 教学对话发送文本 500 错误 (2026-05-09)
- **原因**: MiniMax API 返回的 JSON 格式异常 — LLM 在 `content` 字段后错误地开启了新的 JSON 对象 `,{...}` 而非继续外层对象的字段
- **修复**: `validator.py` 增强 `repair_json` — 新增嵌套对象修复（`_fix_nested_object_mid_json`）和正则字段提取回退（`_extract_fields_regex`）

### Bug #8: 图片生成/TTS 返回 403 (2026-05-09)
- **原因**: JS 通过 `fetch()` 调用 `/media/image/generate/` 和 `/media/tts/synthesize/` 时未携带 CSRF token，Django 拒绝请求
- **修复**: `generate_image_view` 和 `synthesize_speech_view` 添加 `@csrf_exempt` 装饰器（ASR 端点已有此装饰器）

### Bug #9: 多轮对话后发送文本 500 错误 (2026-05-09)
- **原因**: 经过多轮对话后，MiniMax LLM 的输出格式漂移，返回的 JSON 使用了不同的字段名（如 `description` 替代 `content`，`type` 替代 `message_type`），导致 Pydantic schema 验证失败
- **修复**: `validator.py` 新增 `_remap_fields()` 方法 — 当 Pydantic 验证失败时，尝试将已知的 LLM 替代字段名映射回标准字段名；`validate_and_repair` 的 `max_attempts` 从 1 改为 2

### Bug #10: 多轮对话后 LLM 输出 JSON Schema 元数据导致 500 (2026-05-09)
- **原因**: 教学内容的 system prompt 通过 `model_json_schema()` 注入 JSON Schema 定义（含 `"type": "object"` 等元数据），多轮对话后 LLM 开始模仿这种元数据格式输出，而非输出教学内容本身；同时 LLM 的推理内容偶尔混入 `content` 字段
- **修复（根因）**: 
  1. **`prompts.py`** — 重写 `_TEACHING_CONTENT_SYSTEM`：用三个具体示例替代 JSON Schema 注入；新增 "关键禁忌" 规则（禁止输出 `"type": "object"`、禁止输出思考过程、禁止 markdown）；将格式约束放在对话规则之前
  2. **`prompts.py`** — 用户 prompt 末尾新增强提醒："只输出纯JSON对象...不要输出任何其他文字、思考过程或markdown标记"
  3. **`validator.py`** — 新增 `_sanitize_values()`：字段映射后若 `message_type` 值无效，默认设为 "讲解"

### Bug #11: SkillSelection 等其余 4 个 prompt 同样因 JSON Schema 注入导致验证失败 (2026-05-09)
- **原因**: Bug #10 只修复了 `_TEACHING_CONTENT_SYSTEM`，但 `_TEACHING_PLAN_SYSTEM`、`_TEACHING_SUMMARY_SYSTEM`、`_TEST_QUESTIONS_SYSTEM`、`_RISK_ASSESSMENT_SYSTEM` 仍通过 `model_json_schema()` 注入 JSON Schema，LLM 输出 `{"type": "object", "description": "..."}` 导致 SkillSelectionResult 等 schema 验证失败
- **修复（根因）**:
  1. **`prompts.py`** — 全部 5 个 prompt 统一用具体 JSON 示例替代 `{schema}` + `{_JSON_OUTPUT_RULE}` 注入
  2. **`prompts.py`** — 每个 prompt 新增 "关键禁忌" 规则（禁止 `"type": "object"`、思考过程、markdown 标记）
  3. **`prompts.py`** — 所有 user prompt 末尾统一追加格式提醒："只输出纯JSON对象，以{开头、以}结尾"
  4. **`prompts.py`** — 删除废弃的 `_schema_to_json_schema()`、`_JSON_OUTPUT_RULE` 及 `json`/`BaseModel`/schema imports
- **修复范围**: `_SKILL_SELECTION_SYSTEM`、`_TEACHING_PLAN_SYSTEM`、`_TEACHING_SUMMARY_SYSTEM`、`_TEST_QUESTIONS_SYSTEM`、`_RISK_ASSESSMENT_SYSTEM`

### Bug #12: 语音输入 200 但显示"语音识别失败" (2026-05-10)
- **原因 1（根因）**: MiniMax **没有公开的 ASR API** — 测试了 10 个可能的端点全部返回 404。MiniMax Speech 2.5 的 STT 能力仅在 Assistant SDK 中可用，未作为独立 API 提供
- **原因 2（架构）**: Web Speech API (SpeechRecognition) 依赖 Google 服务器，在中国大陆无法访问
- **原因 3（前端）**: 原 `_transcribe()` 将识别结果写入 `#asr-result` 元素，但页面中不存在该元素
- **修复（前端 — hybrid ASR）**:
  1. **`static/js/media.js`** — 双路径策略：优先使用浏览器 SpeechRecognition API（支持实时识别），当出现 `network`/`service-not-allowed` 错误时自动切换至 MediaRecorder + 服务端 ASR
  2. **`static/js/media.js`** — 识别结果直接写入 `#chat-input`，支持中间结果实时显示
  3. **`templates/teaching/session.html`** — 状态文字更新（"聆听中..." / "录音中..." / "识别中..."）
- **修复（后端 — volcengine ASR）**:
  1. **`media_app/services.py`** — 新增 volcengine ASR 集成（含 HMAC-SHA256 签名），作为主 ASR 提供商；原 MiniMax ASR 保留为 fallback
  2. **`media_app/views.py`** — 错误信息优化：区分"未配置"和"API 失败"，提供具体解决指引
  3. **`dbt_platform/settings.py`** — 新增 `VOLCENGINE_APP_KEY`、`VOLCENGINE_ACCESS_KEY`、`VOLCENGINE_SECRET_KEY` 配置项
  4. **`.env`** — 新增火山引擎凭证占位字段
- **待用户操作**: 在 https://console.volcengine.com 获取 ASR 凭证并填入 `.env`，重启容器后语音输入即可使用

### 改进: LLM API 重试机制 (2026-05-09)
- **`llm_client.py`** — `minimax_chat_completion()` 新增对瞬时错误的重试：529（服务过载）、502/503（服务不可用）、超时、连接错误均自动重试最多 2 次，指数退避（1.5s → 3s）

### 功能: 教学自动完成 (2026-05-09)
- **`teaching/views.py`** — `send_message_view` 新增自动完成检测：当 AI 返回 `message_type="总结"` 且已完成教学计划所有步骤时，自动结束会话并生成摘要，无需用户手动点击"结束教学"

### 功能: 自动配图 + 教学结构优化 (2026-05-09)
- **`schemas.py`** — `TeachingContent` 新增 `image_prompt` 可选字段和 `"练习"` message_type
- **`prompts.py`** — 重写 `_TEACHING_CONTENT_SYSTEM`：练习优先、理论精简；新增配图 prompt 引导；重写 `_TEACHING_PLAN_SYSTEM`：练习步骤占 60% 以上
- **`teaching/models.py`** — `ChatMessage` 新增 `image_prompt`/`image_url` 字段
- **`teaching/services.py`** — `generate_teaching_response` 存储 `image_prompt`；`get_conversation_history` 返回 `image_url`
- **`teaching/views.py`** — `send_message_view` 检测到 `image_prompt` 时自动调用 MiniMax 生成配图并关联到消息
- **`templates/teaching/messages_partial.html`** — 消息气泡宽度从 80% 扩至 85%，内联展示配图（max-w-lg）
- **`templates/teaching/session.html`** — 同上

### 聊天 UX 优化 (2026-05-09)
- **`templates/teaching/session.html`** — 语音播报开关默认开启且可正常关闭；发送后自动清空输入框 + 自动滚到底部
- **`templates/teaching/messages_partial.html`** — 移除重复的 autoPlayLatest 调用
- **`static/js/media.js`** — 新增 `DBT_Chat.scrollToBottom()`

### 功能变更: report_viewer 权限升级 (2026-05-09)
- **改动**: `reports/views.py` — `report_viewer` 角色现在可查看**所有**学生的报告并导出 PDF
- **之前**: report_viewer 只能看 ReportViewerAssignment 中授权绑定的特定学生
- **之后**: report_viewer 和 admin 拥有相同的报告查看权限，无需逐条授权

---

## 启动方式 (Docker Compose)

### 前置条件
- 安装 Docker + Docker Compose
- 确保以下端口未被占用: `10443`

### 启动命令
```bash
cd /root/program/DBT
docker compose up -d
```

### 首次启动后
```bash
# 执行数据库迁移
docker compose exec web python manage.py migrate

# 创建管理员账户
docker compose exec web python manage.py createsuperuser
```

### 停止
```bash
docker compose down
```

---

## 访问地址

| 入口 | 地址 |
|------|------|
| **Web 应用 (HTTPS)** | `https://118.178.170.46:10443` |
| **MinIO 控制台** | `http://118.178.170.46:9001` (需开放安全组) |
| **Qdrant API** | `http://118.178.170.46:6333` (需开放安全组) |

> 由于使用的是自签名证书，浏览器会报不安全警告，点击「高级」→「继续访问」即可。

---

## 服务清单 (docker compose up 后)

| 容器 | 镜像 | 端口 |
|------|------|------|
| nginx | nginx:1.27-alpine | 10443→443 |
| web | 本地构建 (Django + Gunicorn) | 8000 (内部) |
| worker | 本地构建 (Celery) | — |
| beat | 本地构建 (Celery Beat) | — |
| mongodb | mongo:7 | 27017 (内部) |
| redis | redis:7-alpine | 6379 (内部) |
| minio | minio/minio:latest | 9000, 9001 (内部) |
| qdrant | qdrant/qdrant:latest | 6333 (内部) |

---

## 默认凭据

| 服务 | 用户名 | 密码 |
|------|--------|------|
| Django admin | admin | Dhgj18.8jhx25 |
| MongoDB (root) | root | Dhgj18.8jhx25 |
| MongoDB (app) | dbt_app | dbt_dev_password |
| MinIO | minioadmin | minioadmin |

> **重要**: 生产环境务必修改默认密码和 `DJANGO_SECRET_KEY`。

---

## 让其他用户访问

公网地址: `https://118.178.170.46:10443`

安全组已开放 10443 端口，任意网络设备均可访问。

> 如果公网 IP 变化，更新 `.env` 中 `CSRF_TRUSTED_ORIGINS` 添加新 IP，然后 `docker compose restart web`。

---

## 已知待处理
- [x] 安装 Docker（当前环境未安装）
- [x] `docker compose up -d` 首次启动
- [x] 执行 `migrate` + 创建管理员
- [ ] 生产部署：替换 SSL 证书、关闭 DEBUG、修改密钥/密码
- [ ] `CSRF_TRUSTED_ORIGINS` 添加其他用户的实际访问地址
