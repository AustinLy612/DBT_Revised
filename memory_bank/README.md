# Memory Bank — DBT Platform

项目长期记忆文档，供后续开发与排障时快速恢复上下文。

## 文档索引

| 文档 | 内容 |
|------|------|
| [test-database-isolation.md](./test-database-isolation.md) | Django 测试库隔离（`SafetyTestRunner`），防止误删生产 MongoDB |
| [capacity-improvements.md](./capacity-improvements.md) | 2026-07 容量改进：场景配图 `job_id`、队列拆分、按需配图、`worker-questions`、健康指标 |
| [loadtest-incident-2026-07.md](./loadtest-incident-2026-07.md) | 15 用户扩展压测结果、Phase F/G 说明、第二次压测宕机分析与容量建议 |

## 相关代码入口

```
dbt_platform/settings.py      # Celery 路由、队列监控、图片并发槽位配置
docker-compose.yml            # worker / worker-images / worker-questions 服务定义
media_app/concurrency.py      # Redis 图片槽位（interactive / batch）
media_app/tasks.py            # 场景配图 job_id、dispatch_scene_image
teaching/views.py             # 场景配图轮询（data-job-id、?job_id=）
testing/views.py              # 测试题配图按需 dispatch + batch 预取
dbt_platform/health_urls.py   # /health/metrics/ 多队列深度与告警
scripts/loadtest_15users.py   # 15 用户压测脚本（含生产环境保护）
```

## 压测快速命令

```bash
# 扩展压测（Phase D–G），需显式允许生产域名
LOADTEST_ALLOW_RUN=1 python scripts/loadtest_15users.py \
  --base-url https://genaidbt.top --extended-only

# 本地 / 预发（无需 LOADTEST_ALLOW_RUN）
python scripts/loadtest_15users.py --base-url http://localhost:8000
```
