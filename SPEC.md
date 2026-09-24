# DBT Revised — current project specification

## Purpose and users

The project is an invitation-only, bilingual (Simplified Chinese/English) web prototype for adolescent DBT skills education and progress tracking. It supports three roles: students, assigned report viewers, and administrators. It does not diagnose or treat mental illness, replace a clinician, or provide emergency monitoring. Its safety flow is a prototype guardrail, not a guarantee that all risky language will be detected.

## Functional requirements and implementation status

| Area | Current behavior |
| --- | --- |
| Access | Invitation-code registration, login/logout, role-aware report access and administration. Students complete a profile before lessons. |
| Teaching | A student records a pre-lesson mood, discusses current concerns, reviews an AI-selected DBT skill, receives a plan, and practices through a streaming chat. The lesson can be completed or terminated; a summary is stored. |
| Assessment | A completed lesson can create a five-question multiple-choice test, with immediate answer feedback, pass/fail result, and retakes. Question generation runs in the `questions` Celery queue and uses an 8192-token output budget. |
| Reflection | Mood entries before/after lessons and tests, an EMA/DBT diary, achievements, and progress views. |
| Safety | Keyword screening plus model risk assessment can interrupt teaching and show help-seeking guidance. The app must not be relied on as a crisis response system. |
| Knowledge | Authorized document upload/processing, chunks and embeddings in Qdrant, retrieval-augmented prompts, and source identifiers for applicable AI flows. |
| Media | Optional scene/test illustrations and speech input/output use configured external services; generation is queued and concurrency-limited. |
| Reporting | Authorized student reports, PDF export, and personal data export. |
| Language | A header control selects Chinese or English for the UI and new generated teaching/test prompts. Stored historic content and user-authored text are not retroactively translated; stable internal choice codes are preserved. |

## High-level architecture

```text
Browser (Django templates + HTMX + small JavaScript, language/theme controls)
   │ HTTPS/HTTP in the local demo
   ▼
Django 6 application (accounts, profile, teaching, testing, mood, EMA,
                    risk, knowledge, media, reports, export)
   ├─ MongoDB: users, sessions, messages, tests, mood/EMA, metadata
   ├─ Redis + Celery: asynchronous question/image/document work and scheduling
   ├─ Qdrant: embedded knowledge chunks for DBT retrieval
   ├─ MinIO: uploaded knowledge documents and object storage
   └─ External APIs: DeepSeek text model with configured Doubao overload
                     fallback; optional image and speech providers
```

`dbt_platform/` configures routing, localization, settings, health checks and the isolated test runner. `knowledge_base/rag/` builds retrieval context, Chinese/English prompts, model calls, output validation and schema normalization. `teaching/` owns lesson state and streaming conversations. `testing/` owns test state, Celery question generation, answers and scoring. `risk/` gates dangerous teaching content. `reports/` and `export_app/` produce authorized summaries/exports. `media_app/` handles optional image/voice calls. `docker-compose.yml` defines the full service stack; `docker-compose.local.yml` restricts the demo to localhost and skips the production proxy. `requirements.txt`, `Dockerfile`, `environment.yml`, `.env.example`, migrations, and app tests are versioned development artifacts.

## Data flow and safety boundaries

1. A student submits a profile and mood record; Django stores them in MongoDB.
2. Teaching retrieves relevant DBT chunks from Qdrant and sends selected context to the configured text model. Pydantic schemas validate structured outputs; the conversation is stored in MongoDB.
3. Risk checks can stop a lesson and show a help page. This is a best-effort safeguard and needs human oversight.
4. After lesson completion, a Celery worker generates five questions from the skill, lesson summary and retrieval context. Test results and further mood records update reports.
5. Knowledge uploads and external AI/media services may process student-related content. Keep `.env` and data exports private; deploy only with appropriate consent, access controls and institutional review.

## Verification and current gaps

The repository includes app-level unit/integration tests and 17 passing focused tests for localization and the question output budget. A Django system check, Python compilation check and localhost health check pass in the current Docker setup. The full database-backed suite is **not verified** in this local environment: the MongoDB app account lacks privileges on isolated `test_dbt_platform`, and the safety runner intentionally prevents tests from using production data. Give a disposable test user access to that test database before running the full suite.

External LLM/image/speech calls were not fully end-to-end tested for this handoff. Although the five-question budget was raised from 4096 to 8192 tokens, providers may still truncate or return invalid JSON. The question task currently flags a test as terminated on its first exception while retrying; the UI can show failure before retries finish. This is a known implementation defect. Existing Chinese records remain Chinese after switching the interface to English by design. Production hosting, clinical validation, and guaranteed crisis detection are outside this prototype's current scope.
