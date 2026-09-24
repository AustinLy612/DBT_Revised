# DBT Revised

DBT Revised is a research/teaching prototype for learning dialectical behavior therapy (DBT) skills. Students register with an invitation code, complete a profile, record mood, work through AI-guided lessons, answer five-question skill tests, and keep a DBT diary. Authorized report viewers can review assigned students' progress. It is an educational tool, **not** a clinician, diagnosis, emergency service, or substitute for professional care. See [SPEC.md](SPEC.md) for the current feature and architecture specification.

## Local setup

The supported demo environment is Docker Desktop with its Linux engine (or Docker Engine with Compose). The web container uses Python 3.12; host Python is not required. Install Docker, then from this directory:

1. Copy `.env.example` to `.env` and replace every `change-me` value with a private secret or API key. `.env` is ignored by Git; never commit it. For Compose networking set `MONGODB_HOST=mongodb`, `REDIS_HOST=redis`, `QDRANT_HOST=qdrant`, `MINIO_ENDPOINT=minio:9000`, `CELERY_BROKER_URL=redis://redis:6379/0`, and `CELERY_RESULT_BACKEND=redis://redis:6379/0`. Keep `MONGODB_USER=dbt_app` and make `MONGODB_PASSWORD` match the password used by the Mongo initialization script. Set `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` to match `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`.
2. Start the local-only profile and apply migrations:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python manage.py migrate --noinput
```

Open <http://localhost:8000/>; verify <http://localhost:8000/health/ready/>. First build is large because the pinned Python dependencies include ML libraries. Subsequent starts use `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build`. Stop without deleting data with `docker compose -f docker-compose.yml -f docker-compose.local.yml down`. The local override binds the site to loopback and omits the production TLS proxy. Do not expose this development profile to the internet.

An administrator must create invitation codes for student registration. New AI-generated lessons and tests require configured text-model API keys. Image generation and speech features additionally require their respective provider keys. The app still starts and its non-AI pages work without those external services.

## Testing

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python manage.py check
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python manage.py test dbt_platform.tests_i18n knowledge_base.tests_question_budget
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python -m compileall -q accounts dbt_platform ema_log export_app knowledge_base media_app mood questionnaire reports risk teaching testing
```

The focused bilingual and five-question-budget suite currently has 17 passing tests. Other app tests are in each app's `tests.py` and `knowledge_base/tests_rag.py`; run them with `python manage.py test` in a suitably privileged, **isolated** test environment. The current local MongoDB application user has `readWrite` on `dbt_platform` but not the separate `test_dbt_platform` database, so the full database-backed suite fails during `listCollections`/test DB setup. The custom test runner refuses to use a non-`test_` database; do not point tests at the application database to work around this. Give a disposable development test user appropriate rights on `test_dbt_platform` instead.

## Language and known limitations

The site header switches Chinese/English. UI labels, forms, reports, and prompts for **newly generated** teaching/test material follow the selected language. Existing transcripts, test questions, and user-authored text are deliberately not translated. The English translation catalog is `locale/en/LC_MESSAGES/django.po`; after editing it, run `python scripts/compile_locale.py` and commit the generated `django.mo` too.

Five-question generation has a dedicated 8192-token output budget (other text flows retain 4096). A provider can still return truncated or invalid JSON. Currently the question worker marks a test terminated on its first exception even though Celery may retry successfully; the UI can therefore briefly report failure too early. That retry-state bug remains open. External model/image/speech calls have not been covered by a live end-to-end test in this local handoff; provider availability, cost, and language support vary. The production Compose/Nginx path also needs a real domain, TLS certificate, secure secrets, and maintained service images before public deployment.

No private `.env`, API key, local model cache, uploaded media, database contents, or exported student data should be added to Git.
