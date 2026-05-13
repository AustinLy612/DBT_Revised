# DBT Platform — Architecture Reference

## Directory Layout

```
DBT/
├── dbt_platform/          # Django project config (settings, urls, celery, wsgi)
│   ├── apps.py            # MongoDB-compatible AppConfigs for built-in Django apps
│   ├── celery.py          # Celery app definition
│   ├── health_urls.py     # /health/ (liveness) and /health/ready/ (backend checks)
│   ├── settings.py        # All Django settings; reads from .env via django-environ
│   ├── urls.py            # Root URL conf, includes all app URLs + health
│   └── utils.py           # gen_uuid() shared across all models
│
├── accounts/              # User model (AbstractUser), InviteCode model, auth
│   ├── admin.py            # CustomUserAdmin, InviteCodeAdmin, ReportViewerAssignmentAdmin
│   ├── decorators.py       # role_required, student_required, admin_required, report_viewer_required
│   ├── middleware.py        # AdminAccessMiddleware (blocks non-admin from /admin/)
│   └── tests_admin.py      # Step 3 tests: admin registration, CRUD, accessibility, relationships
├── questionnaire/         # UserProfile model & registration questionnaire (Step 4)
│   ├── admin.py            # UserProfileAdmin
│   ├── decorators.py       # profile_required — blocks teaching until questionnaire done
│   ├── forms.py            # ProfileForm — all PRD fields with validation
│   └── views.py            # profile_view — first-time + modification flow
├── teaching/              # TeachingSession + ChatMessage models, teaching entry point (Step 4)
│   ├── admin.py            # TeachingSessionAdmin + ChatMessageAdmin + ChatMessageInline
│   ├── views.py            # teaching_home_view — @profile_required, reads UserProfile
│   └── urls.py             # /teaching/ → teaching_home_view (namespace "teaching")
├── testing/               # Test + TestQuestion models
│   └── admin.py            # TestAdmin + TestQuestionAdmin + TestQuestionInline
├── mood/                  # MoodRecord + Achievement + UserAchievement models
│   └── admin.py            # MoodRecordAdmin, AchievementAdmin, UserAchievementAdmin
├── risk/                  # RiskEvent model
│   └── admin.py            # RiskEventAdmin
├── knowledge_base/        # KnowledgeDocument + KnowledgeChunk + RetrievalLog models
│   └── admin.py            # KnowledgeDocumentAdmin + KnowledgeChunkAdmin + RetrievalLogAdmin + KnowledgeChunkInline
├── export_app/            # AdminOperationLog model
│   └── admin.py            # AdminOperationLogAdmin (read-only)
├── reports/               # ReportAccessLog model
│   └── admin.py            # ReportAccessLogAdmin (read-only)
│
├── templates/             # Django templates
│   ├── base.html           # Base layout (Tailwind + Alpine.js + htmx)
│   ├── index.html          # Role/profile-aware landing page
│   ├── accounts/           # Login, register, logout_confirm
│   ├── questionnaire/      # profile.html — questionnaire form
│   ├── teaching/           # home.html — teaching entry (reads profile)
│   └── reports/            # dashboard, student_report
├── static/                # Static assets (CSS, JS, images)
├── media/                 # User uploads (local dev; MinIO-backed in production)
├── logs/                  # Application log files
│
├── docker/                # Deployment configs
│   ├── nginx.conf         # Reverse proxy, :443 HTTP/2 (maps to host :10443)
│   ├── mongo-init.js      # Creates dbt_app user on first MongoDB start
│   └── certs/             # SSL certificates (mounted into Nginx)
│
├── scripts/
│   └── install_services.sh # One-shot MinIO + Qdrant install/start script
│
├── docker-compose.yml     # 8-service orchestration (web, worker, beat, nginx, mongodb, redis, qdrant, minio)
├── Dockerfile             # Python 3.12-slim image for web/worker/beat
├── environment.yml        # Conda environment definition (conda env create -f)
├── requirements.txt       # Pinned pip dependencies (154 packages)
├── .env.example           # Environment variable template with 6 documented sections
├── .env                   # Active environment config (gitignored)
└── manage.py              # Django CLI entry point
```

## Key Architectural Decisions

### 1. Django + MongoDB via django-mongodb-backend
- **ENGINE**: `django_mongodb_backend`
- **DEFAULT_AUTO_FIELD**: `django_mongodb_backend.fields.ObjectIdAutoField`
- **Migrations**: Applied with `--fake`. MongoDB is schemaless — collections auto-create on first model access. The migration table (`django_migrations`) tracks what's been applied but no DDL is executed.
- **Why**: MongoDB fits document-oriented teaching sessions, nested test structures, and flexible questionnaire data. Django provides mature auth, admin, and session management.

### 2. Built-in App Compatibility
Django's `auth`, `contenttypes`, `admin`, `sessions`, `messages` apps use `AutoField` primary keys. MongoDB doesn't support `AutoField`. The fix is in `dbt_platform/apps.py`:
- Custom AppConfig subclasses override `default_auto_field = ObjectIdAutoField`
- These replace the default string references in `INSTALLED_APPS`

### 3. Authentication & User Model
- `accounts.User` extends `AbstractUser` with a `role` field (student / report_viewer / admin)
- Primary key is a UUID string (CharField), not an integer
- Password hashing uses Django's default PBKDF2

### 4. Internal vs External Addressing
**Internal services** (MongoDB, Redis, MinIO, Qdrant, Celery, Django itself) communicate over `localhost` or Docker network hostnames. These addresses never appear in browser-side code.

**External access** (browsers on other devices) goes through:
1. Nginx on `:10443` (the only port exposed to the network)
2. Nginx proxies to Django on the internal network
3. All browser requests use relative paths (`/health/`, `/accounts/login/`)
4. Absolute URLs (exports, reports) use `EXTERNAL_BASE_URL`

Configuration variables that MUST change for production:
- `DJANGO_ALLOWED_HOSTS` → add domain
- `EXTERNAL_BASE_URL` → `https://<domain>:10443`
- `CSRF_TRUSTED_ORIGINS` → `https://<domain>:10443`
- `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` → auto-enabled when `DEBUG=False`

### 5. MongoDB Security Model
- `root` account: full admin access (never in application config)
- `dbt_app` account: `readWrite` on `dbt_platform` only
- `authSource: dbt_platform` (user was created in business DB, not admin DB)
- `bindIp: 127.0.0.1` in mongod.conf (no public exposure)
- Future: separate backup account for dump/restore operations

### 6. Celery Async Tasks
- Broker: Redis
- Serializer: JSON
- Time limit: 30 minutes per task
- Expected async workloads: document chunking/embedding, image generation, TTS synthesis, batch export
- **Gunicorn timeout**: Increased from default 30s to 120s (2026-05-13) — matches `API_TIMEOUT_SECONDS` in media_app/services.py, allowing synchronous image generation fallback to complete without worker kill

### 7. RAG Architecture (to be implemented in later steps)
- **MongoDB text index**: keyword search on skill names, module names, rule documents
- **Qdrant**: semantic vector search with `bge-m3` embeddings
- **Hybrid retrieval**: Application layer merges and deduplicates by chunk_id
- **LangChain**: Wraps retrieval, prompt templates, structured output schemas for teaching/test sub-flows

### 8. Three-Tier Permission Model
| Role | Access |
|------|--------|
| `student` | Own teaching, tests, mood, achievements; cannot view reports |
| `report_viewer` | Read-only individual student visualization reports within authorized scope; can export PDF reports; cannot access raw data or admin |
| `admin` | Full admin panel, raw data export (JSON/CSV/Excel), RAG document management, operation logs |

### 9. Data Flow for a Teaching Session
```
User Login → Pre-Mood Recording → Personal Inquiry (AI asks about recent experiences)
  → Info Collection (AI reads profile + history) → Skill Selection (AI + RAG, informed by personal context)
  → RAG Retrieval → Dialogue Teaching
  → Auto-Transition to Test → Test Generation (AI + RAG) → Per-Question Answer + Explanation
  → Test Result → Retest or Pass → Post-Mood Recording → Achievement Check → Save All Records
```
Risk detection (keyword + AI semantic) runs during teaching and testing. A `should_assess_risk()` gate (Step 14) skips the AI call for benign text — only keyword matches or moderate concern indicators trigger the full LLM assessment. If risk confirmed by either channel, session stops immediately.

The **personal inquiry** phase (added 2026-05-12) is a key design element: before recommending a DBT skill, the AI first asks the student a warm, empathetic question about their recent experiences. The student's personal context then becomes the most important input for skill recommendation, combined with questionnaire data, teaching history, and test performance.

### 10. Account & Auth Module (Step 2)

**`accounts/forms.py`** — Registration and login form classes:
- `RegisterForm`: validates username uniqueness (min 3 chars), password strength (min 8 chars), password confirmation match, and invite code (must exist, be active, unused). On success returns the InviteCode object as `cleaned_data["invite_code"]` so the view can mark it used.
- `LoginForm`: thin wrapper around Django's `AuthenticationForm` with Chinese labels and Tailwind CSS classes.

**`accounts/views.py`** — Three core views:
- `register_view`: GET renders form; POST validates, creates user with role=student, marks invite code as used (code, used_by, used_at), auto-logs in, redirects to questionnaire. Authenticated users are redirected to index.
- `login_view`: standard Django login with `last_login` timestamp update. Supports `?next=` parameter.
- `logout_view`: GET shows confirmation page; POST executes logout and redirects to login. Preventing accidental logout via GET.

**`accounts/decorators.py`** — Composable role-gating:
- `role_required(allowed_roles, redirect_url=None)`: factory that returns a decorator. Raises `PermissionDenied` (403) by default, or redirects if `redirect_url` is set. Unauthenticated users are sent to login.
- `student_required`, `admin_required`, `report_viewer_required`: convenience wrappers that can be used as `@student_required` or `@student_required(redirect_url="/")`.

**`accounts/middleware.py`** — `AdminAccessMiddleware`:
- Placed AFTER `AuthenticationMiddleware` in the stack (so `request.user` is available)
- Intercepts any path starting with `/admin/`
- If user is authenticated but NOT an admin → redirects to index
- Unauthenticated users pass through (Django admin's own login redirect handles them)
- Runs on every request, so it catches direct URL access, not just link clicks

**`accounts/models.py`** — `ReportViewerAssignment`:
- Links a `report_viewer` user to a `student` user
- `unique_together("viewer", "student")` prevents duplicate assignments
- `is_active` flag allows soft enable/disable without deleting the assignment
- Admin formfield filters restrict viewer choices to report_viewer role and student choices to student role

**`accounts/admin.py`** — Three admin classes:
- `CustomUserAdmin`: extends Django's `UserAdmin` with DBT-specific fields (role, invite_code, profile_completed)
- `InviteCodeAdmin`: batch creation (10 UUID-based codes), enable/disable actions, search, status filtering
- `ReportViewerAssignmentAdmin`: CRUD for viewer→student mappings with role-filtered autocomplete fields

### 11. Registration Flow Invariant

```
User enters: username, password, password_confirm, invite_code
  → Form validates invite code (exists, active, unused)
  → User created with role=student, invite_code field populated
  → InviteCode.status → "used", InviteCode.used_by → user.id, InviteCode.used_at → now
  → Auto-login (login(request, user))
  → Redirect to questionnaire:profile
```

The invite code is consumed atomically — the `clean_invite_code` method returns the InviteCode object, and the view uses it to set `user.invite_code` and update the invite. If user creation fails (e.g., IntegrityError), the invite is NOT consumed (form validation is independent of save).

### 12. Test Infrastructure

Tests use Django's standard `TestCase` with MongoDB. The test database is `test_dbt_platform` — the `dbt_app` user was granted `readWrite` + `dbAdmin` on it. All 36 tests create and clean up their own data; no shared fixtures. Tests are organized into 6 classes matching functional areas: RegistrationTests, LoginLogoutTests, RoleBasedAccessTests, InviteCodeModelTests, ReportViewerAssignmentTests, RoleDecoratorTests.

### 13. Admin Access Invariant (Post-Review Fix)

Django's admin panel (`/admin/`) requires TWO conditions to admit a user:
1. `AdminAccessMiddleware` passes (user.role == "admin")
2. Django's `AdminSite.has_permission()` passes (user.is_staff == True AND user.is_active == True)

The `User.save()` override guarantees these stay in sync: whenever `role="admin"`, the model sets `is_staff=True, is_superuser=True` before saving. This means:
- Creating a user via `User.objects.create_user(role="admin")` → is_staff automatically True
- Changing an existing user's role to "admin" in admin → save() fires, is_staff flips to True
- The middleware and Django's own admin checks now agree on who is an admin

### 14. Reports Module (Post-Review Fix)

**`reports/views.py`** — Two role-gated views:
- `dashboard_view`: report_viewers see only students assigned via active `ReportViewerAssignment`; admins see all students. Students and unauthenticated users get 403.
- `student_report_view`: checks `ReportViewerAssignment.is_active` for report_viewers; admins bypass. Accessing a non-assigned student returns 403.

**`reports/urls.py`** — `/reports/` (dashboard) and `/reports/student/<student_id>/` (individual report).
**`templates/reports/dashboard.html`** — Lists authorized students as clickable cards.
**`templates/reports/student_report.html`** — Placeholder for full report (Step 9+).

The authorization check in `student_report_view` is the enforcement point for "报告查看用户只能看到被授权学生的报告入口". The `ReportViewerAssignment` model's `is_active` flag is checked on every request, so deactivating an assignment immediately revokes access.

### 15. Admin Visibility Layer (Step 3)

All 17 models across all 9 apps are now registered in Django admin. Each admin class provides:

**`questionnaire/admin.py`** — `UserProfileAdmin`: Displays user, gender, age, grade fields with profile_completed derived from User model. Fieldsets group form fields into user info, hobby/concern tags, and system fields.

**`teaching/admin.py`** — `TeachingSessionAdmin` + `ChatMessageAdmin`:
- `TeachingSessionAdmin` lists sessions with status, module, skill, message count. Detail page includes `ChatMessageInline` (read-only, shows role + 80-char content preview). Fieldsets: basic info, teaching content, RAG context, mood records.
- `ChatMessageAdmin` standalone list for searching all messages by content. Session column links back to parent session.

**`testing/admin.py`** — `TestAdmin` + `TestQuestionAdmin`:
- `TestAdmin` lists tests with attempt_no, passed status, correct/total count. Detail page includes `TestQuestionInline` (read-only). Fieldsets: basic info, test results, RAG context.
- `TestQuestionAdmin` standalone list with question preview, user answer vs correct option, is_correct flag. Fieldsets include image metadata (prompt, model, url, generated_at) for future MiniMax integration.

**`mood/admin.py`** — `MoodRecordAdmin`, `AchievementAdmin`, `UserAchievementAdmin`:
- `MoodRecordAdmin` filters by context (manual/before_teaching/after_teaching) and mood_value (1-5).
- `AchievementAdmin` shows name, description preview, icon, is_active, and JSON trigger_rule.
- `UserAchievementAdmin` links user to achievement with unlocked_at timestamp.

**`risk/admin.py`** — `RiskEventAdmin`: High-priority model. Lists detection_source (keyword/ai/both), session_stopped flag, follow_up_mode. Searchable by trigger_text. Fieldsets: basic info, risk details (trigger text + source + stopped flag), handling info (action_taken, follow_up_mode, exported_flag).

**`knowledge_base/admin.py`** — `KnowledgeDocumentAdmin`, `KnowledgeChunkAdmin`, `RetrievalLogAdmin`:
- `KnowledgeDocumentAdmin` includes `KnowledgeChunkInline` (read-only, 80-char text preview). Lists with chunk_count, status (uploaded→processing→retrievable→failed), module filter.
- `KnowledgeChunkAdmin` standalone search for chunk text content. Shows embedding_id and JSON metadata.
- `RetrievalLogAdmin` shows query preview, use_case (teaching/test_generation/explanation/retest/risk), chunk_count from retrieved_chunk_ids JSON.

**`export_app/admin.py`** — `AdminOperationLogAdmin`: Read-only (no add/change permissions). Tracks admin user, operation_type, target_type/id, export_format, export_scope. All fields readonly.

**`reports/admin.py`** — `ReportAccessLogAdmin`: Read-only (no add/change permissions). Tracks viewer, viewer_role, student, action_type (view/export), report_type, export_format.

### 16. Admin Inline Pattern

Three parent-child relationships use Django's `TabularInline`:

```
TeachingSession → ChatMessageInline  (fields: role, content_preview, modality, created_at)
Test            → TestQuestionInline  (fields: question_text_preview, user_answer, correct_option, is_correct)
KnowledgeDocument → KnowledgeChunkInline  (fields: chunk_text_preview, embedding_id, created_at)
```

All inlines are read-only (`has_add_permission = False`), set `extra = 0` (no empty rows), and display truncated content previews via custom methods. This keeps admin detail pages responsive even with hundreds of child records.

### 17. MongoDB Transaction Compatibility with Tests

Django `TestCase` wraps each test method in a transaction (atomic block). In SQL databases this is transparent, but with MongoDB:
- Objects created in `setUp` (per-test) are visible within the same test's transaction
- Objects created in `setUpClass` (per-class) run OUTSIDE the transaction and may become invisible to UPDATE operations inside the transaction
- `force_login` triggers `user.save(update_fields=["last_login"])` which can raise `User.NotUpdated` if the user was created outside the transaction

**Rule**: Always create test users in `setUp` (not `setUpClass`) when using `force_login` or any save-with-update_fields pattern. Use `setUpTestData` (Django's supported class-level fixture method) if class-level setup is needed.

### 18. User Admin Aggregation Pattern (Step 3 Post-Review Fix)

The `CustomUserAdmin` detail page now aggregates ALL related records from every module. This implements the Step 3 requirement "后台可以基于用户聚合查看主要记录" directly in the admin UI.

**`accounts/admin.py`** — `CustomUserAdmin` detail page inlines:

```
User (detail page)
├── UserProfileInline (Stacked) — one-to-one profile: gender, age, grade, tags
├── TeachingSessionInline — FK user → sessions: status, module, skill
├── TestInline — FK user → tests: attempt, passed, correct/total
├── MoodRecordInline — FK user → mood: value, emoji, context
├── RiskEventInline — FK user → risk: detection_source, trigger preview
├── UserAchievementInline — FK user → achievements: name, unlocked_at
├── RetrievalLogInline — FK user → retrieval: query, use_case
├── AdminOperationInline — FK admin → operation_logs (admin users only)
├── ViewerAssignmentInline — FK viewer → assignments (report_viewer users only)
├── StudentAssignmentInline — FK student → assignments (student users only)
├── ReportAccessByViewerInline — FK viewer → access_logs (report_viewer users only)
└── ReportAccessByStudentInline — FK student → access_logs (student users only)
```

All inlines are `TabularInline` except `UserProfileInline` which is `StackedInline`. All are read-only (`has_add_permission = False`, `can_delete = False`, `extra = 0`).

**Dynamic inline filtering** via `get_inlines(request, obj)`: When the admin detail page renders, `get_inlines` filters the 12 registered inlines based on the TARGET user's role. This prevents showing admin-specific sections on a student's page (and vice versa), keeping the UI focused on what's relevant for each role.

**Two-FK inline disambiguation**: `ReportViewerAssignment` and `ReportAccessLog` both have two FKs to `User`. The `fk_name` attribute on the inline class tells Django which FK to use:
- `ViewerAssignmentInline(fk_name="viewer")` → filter by `viewer = parent_user`
- `StudentAssignmentInline(fk_name="student")` → filter by `student = parent_user`
- `ReportAccessByViewerInline(fk_name="viewer")` → filter by `viewer = parent_user`
- `ReportAccessByStudentInline(fk_name="student")` → filter by `student = parent_user`

**Test verification**: `UserAdminAggregationTests` (13 tests) validates that each inline's data appears in the admin page HTML. Tests use `assertContains` to verify specific record content (module names, emoji strings, achievement names, usernames) is present in the rendered response. An end-to-end test creates ALL record types for one user and asserts every record type appears on the single detail page.

### 19. Registration Questionnaire & Profile Module (Step 4)

**`questionnaire/forms.py`** — `ProfileForm` (ModelForm for UserProfile):

All fields from the PRD with server-side validation:
- `gender`: RadioSelect with 4 options (male, female, other, prefer_not_to_say)
- `age`: NumberInput, validated to 10-25 range
- `grade`: Select with 6 choices (grade_7 through grade_12)
- `hobby_tags`: 15 checkboxes from PRD pool (§6.2.3), max 5 enforced
- `concern_tags`: 20 checkboxes from PRD pool (§6.2.4), max 5 enforced
- `other_hobby_text`: optional Textarea, placeholder prompts when "其他" selected
- `other_concern_text`: optional Textarea, placeholder prompts when "其他" selected

The `hobby_tags` and `concern_tags` fields are `MultipleChoiceField` with `CheckboxSelectMultiple` widget. They map to the model's `JSONField(default=list)` — the form's `cleaned_data` returns a Python list which the model stores as a JSON array.

**`questionnaire/views.py`** — `profile_view`:
- GET: loads existing `UserProfile` via `user.profile` (OneToOne reverse accessor). If no profile exists, `instance=None` and the form renders empty. If a profile exists (modification), the form is pre-filled.
- POST: `form.save(commit=False)`, assigns `profile.user = request.user`, saves. On first-time completion, sets `user.profile_completed = True` (with `update_fields` for efficiency). Redirects to index.
- Context passes `is_first_time` flag for the template to show appropriate title/banner.
- Decorated with `@login_required` only — NOT `@profile_required` (would cause redirect loop).

**`questionnaire/decorators.py`** — `profile_required`:
- Follows the project pattern established by `role_required` in `accounts/decorators.py`: wraps `@login_required` so a single decorator handles both auth and profile gating.
- Redirects to `questionnaire:profile` if `user.profile_completed` is False.
- Ready for application on teaching, testing, and mood views in later steps.

**`templates/questionnaire/profile.html`**:
- Full Tailwind CSS form with responsive grid layout
- Alpine.js (`x-data`, `x-show`, `x-transition`) for conditional "其他" textareas that appear when the corresponding checkbox is checked
- Vanilla JS event listeners on checkbox change to sync state with Alpine
- First-time mode: blue info banner "欢迎！请先完成问卷"
- Modification mode: simple title "修改问卷"

**`templates/index.html`** (updated):
- Three states based on auth + profile status:
  1. Unauthenticated → login/register buttons
  2. Authenticated, no profile → yellow prompt card with "填写问卷" button
  3. Authenticated, profile complete → "开始教学" button

### 20. Profile Completion Flow Invariant

```
Registration (accounts:register)
  → User created (role=student, profile_completed=False)
  → Auto-login
  → Redirect to questionnaire:profile

Questionnaire (questionnaire:profile)
  → GET: show form (empty for first-time, pre-filled for modification)
  → POST: create/update UserProfile
  → Set user.profile_completed = True (first-time only)
  → Redirect to index

Index (/)
  → user.profile_completed == False → show "填写问卷" prompt
  → user.profile_completed == True  → show "开始教学" button
  → unauthenticated → show login/register

Teaching (/teaching/) [future]
  → @profile_required blocks access until profile completed
  → Redirects to questionnaire:profile
```

The profile completion check on the index page is a **template-level gate**, not a view-level redirect. This preserves the login/redirect flows tested in Step 2 (login → index → 200, not login → index → 302 to questionnaire).

### 21. Questionnaire Test Organization

`questionnaire/tests.py` — 22 tests in 4 classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| `ProfilePageTests` | 4 | Page loads, unauthenticated redirect, modify title, existing data prefilled |
| `ProfileSubmissionTests` | 10 | First-time submit, modification, completed flag preserved, age validation (too young/old), max tags enforcement, empty tags allowed, "其他" + text, "不想回答", flag set |
| `ProfileRequiredTests` | 4 | Index shows prompt without profile, shows teaching button with profile, questionnaire page self-access, unauthenticated sees login/register |
| `ProfileDataTests` | 3 | All fields persist, updated_at changes on modify, created_at unchanged on modify |

### 22. Teaching Entry Point & Profile Integration Contract (Step 4 Post-Review)

**`teaching/views.py`** — `teaching_home_view`:
- Decorated with `@profile_required` from `questionnaire.decorators` — enforces "未完成问卷前无法开始教学"
- Reads `request.user.profile` (OneToOne reverse accessor on `accounts.User`) and passes it to the template
- This establishes the **profile integration contract**: all future teaching/testing flows (Steps 7-8) consume the latest UserProfile via `request.user.profile`

**`teaching/urls.py`** — namespace `teaching`, route `/teaching/` → `teaching_home_view`

**`templates/teaching/home.html`** — Renders a "当前使用问卷信息" card displaying:
- Gender, age, grade (with `get_*_display` for Chinese labels)
- Hobby tags and concern tags (joined with Chinese enumeration comma `、`)
- `other_hobby_text` and `other_concern_text` (conditionally shown)
- `updated_at` timestamp (proves the latest modification is reflected)

**Profile consumption verification chain**:
```
User modifies questionnaire → profile.updated_at changes
  → User navigates to /teaching/
  → @profile_required passes (profile_completed=True)
  → teaching_home_view reads request.user.profile
  → Template renders current profile fields
  → test_teaching_reflects_updated_profile validates:
      old data absent, new data present
```

The test `test_teaching_reflects_updated_profile` in `TeachingEnforcementTests` validates this end-to-end: create profile with hobby "游戏" → POST modified profile with hobbies ["音乐", "阅读"] → GET teaching page → assertContains "音乐", assertNotContains "游戏".

### 23. "其他" Textarea Visibility on Edit

When a user edits their profile after previously selecting the "其他" checkbox, the supplementary textareas must be visible so their previously-entered text is not hidden.

**Data flow**: `questionnaire/views.py::profile_view` determines `hobby_has_other` / `concern_has_other`:
- **GET (edit)**: Reads `profile.hobby_tags` / `profile.concern_tags` from existing profile. If `"其他" in existing_tags`, flag is `True`.
- **POST (validation failed)**: Reads `request.POST.getlist("hobby_tags")` / `request.POST.getlist("concern_tags")` to preserve visibility when the form is re-rendered with errors.

**Template**: `profile.html` uses `{{ hobby_has_other|yesno:'true,false' }}` to set Alpine.js initial state in `x-data`. The vanilla JS event listeners keep Alpine state in sync when the user toggles checkboxes. The `id="questionnaire-form"` on the x-data container enables reliable DOM selection.

### 24. Knowledge Base Storage Layer (Step 5)

**`knowledge_base/storage.py`** — MinIO client wrapper for knowledge document lifecycle:

- `get_minio_client()`: Singleton factory reading `MINIO_ENDPOINT`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`/`MINIO_SECURE` from Django settings. Returns `Minio` client instance.
- `upload_document(file_data, object_name)`: Accepts both `bytes` and file-like objects. Wraps bytes in `io.BytesIO` before passing to MinIO (minio-py 7.x requires `.read()`). Auto-creates bucket if missing.
- `download_document(object_name)`: Downloads from MinIO, returns raw bytes. Properly closes connection via `finally: response.close(); response.release_conn()`.
- `delete_document(object_name)`: Removes object from MinIO.

**Object naming convention**: `knowledge/{document_id}/{uuid}.{ext}` — ensures uniqueness per upload and groups all versions under the document's directory prefix.

### 25. Document Processing Pipeline (Step 5)

The processing pipeline has two layers:

**Synchronous core** (`tasks.py::run_document_pipeline`):
```
MinIO download → parse_document_bytes → extract_sections → chunk each section
  → generate_embeddings → ensure_qdrant_collection → KnowledgeChunk.bulk_create
  → index_chunks_to_qdrant → return chunk_count
```
This function is callable without Celery — used by tests to verify the full pipeline synchronously.

**Async wrapper** (`tasks.py::process_document_async`):
```
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
set status=PROCESSING → run_document_pipeline → set status=RETRIEVABLE
  OR: set status=FAILED + error_message → self.retry(exc=exc)
```
The Celery task handles status transitions and retry logic. The core logic is extracted so tests can verify pipeline correctness without a Celery worker.

**Document parsing** (`tasks.py::parse_document_bytes`):
- `.txt`, `.md`, `.markdown`: UTF-8 decode with error replacement
- `.pdf`: Uses `pypdf.PdfReader`, extracts text from all pages
- `.docx`: Uses `python-docx.Document`, extracts non-empty paragraph text
- Unsupported: raises `ValueError(f"Unsupported file type: .{ext}")`

**Text chunking** (`services.py::chunk_text`):
- Uses `RecursiveCharacterTextSplitter` from langchain-text-splitters
- `chunk_size=500`, `chunk_overlap=50`
- Chinese-aware separators: `["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "]`
- Accepts optional metadata dict propagated to every chunk
- Returns `list[{"text": str, "metadata": dict}]`

### 26. Embedding & Vector Search (Step 5)

**Model**: `BAAI/bge-m3` via `sentence-transformers` (1024-dim, L2-normalized)

**Lazy loading**: Both the embedding model and Qdrant client are module-level singletons initialized on first use:
```python
_embedding_model: SentenceTransformer | None = None
_qdrant_client: QdrantClient | None = None
```

**Qdrant collection**: `dbt_knowledge` (from `QDRANT_COLLECTION` setting). Auto-created on first index with `size=1024`, `distance=Cosine`.

**Index format**: Each point stored as:
```python
PointStruct(
    id=chunk.chunk_id,           # UUID string
    vector=embedding.tolist(),   # 1024 floats
    payload={
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "chunk_text": chunk.chunk_text,
        "metadata": chunk.metadata,  # JSON with doc-level + chunk-level fields
    },
)
```

### 27. Hybrid Retrieval Architecture (Step 5, Optimized Step 13)

**Redis cache layer** (Step 13): `hybrid_search()` first checks Redis (`rag:search:<sha256>`, 5-min TTL). Cache hit returns immediately. Cache miss runs both paths, caches the merged result. Cache gracefully degrades if Redis is unavailable.

Two independent retrieval paths merged at the application layer:

**Keyword search** (`services.py::keyword_search`):
- Uses MongoDB `$text` operator with the `chunk_text_text` index for fast token-based lookup
- Falls back to `$regex` with PCRE lookaheads when `$text` returns no results (e.g. single CJK characters)
- Multi-term queries: space-joined search string → `$text` OR semantics; fallback uses lookahead regex
- Relevance score: MongoDB `textScore` (TF-IDF-based) for `$text` path; fraction of query terms matched for `$regex` fallback
- `_keyword_search_regex()` extracted as standalone fallback function with `re.escape()` for metacharacter safety

**Semantic search** (`services.py::semantic_search`):
- Encodes query with same model → normalized vector
- Searches Qdrant collection with `limit=top_k`
- Returns payload fields + similarity score

**Hybrid merge** (`services.py::hybrid_search`):
```
keyword_results + semantic_results → deduplicate by chunk_id → sort → top_k
```
Keyword results get priority (listed first), then semantic results appended if chunk_id not already seen. Source field distinguishes: "keyword" for keyword-only, "hybrid" for semantic results merged in.

### 28. Retrieval Logging Contract (Step 5)

Every retrieval is recorded with:
- `user` (FK to accounts.User) — who made the query
- `session` (FK to teaching.TeachingSession, nullable) — which teaching session (may be null for standalone searches)
- `query` (TextField) — the raw search query
- `retrieved_chunk_ids` (JSONField list) — ordered list of chunk UUIDs returned
- `use_case` (CharField) — one of: teaching, test_generation, explanation, retest, risk
- `created_at` (auto_now_add) — when the retrieval occurred

The `log_retrieval` function in services.py is the single write point. The search view ALWAYS calls it — if a valid `session_id` is provided, the session FK is set; otherwise `session=None`. This guarantees every search writes a RetrievalLog regardless of whether it occurs within a teaching session context.

### 29. Admin Upload Flow (Step 5)

The KnowledgeDocument admin has a custom upload flow:

```
Admin clicks "增加 knowledge document" 
  → changeform_view redirects to upload_document_view (custom URL)
  → Upload template renders KnowledgeDocumentUploadForm
  → Admin selects file + fills metadata (title, module, skill, difficulty, etc.)
  → POST: save_model called
      → file uploaded to MinIO (knowledge/{document_id}/{uuid}.{ext})
      → file_url set to MinIO object name
      → status set to PROCESSING
      → process_document_async.delay() triggered
      → Admin redirected to changelist with info message
```

**Form uniqueness**: The `get_form` override returns `KnowledgeDocumentUploadForm` (with file field) when `obj is None` (add), and the default ModelForm (without file field) when editing (change). This keeps the edit page clean while the upload page has the full form.

**Metadata field parsing**: `scenario_tags` and `risk_flags` accept both JSON arrays (`["校园", "家庭"]`) and comma-separated values (`校园, 家庭`) in the admin form. The `clean_*` methods normalize both formats into Python lists.

### 30. MongoDB Text Search — $text with $regex Fallback (Updated Step 13 Optimization)

**Adopted solution (2026-05-11)**: `keyword_search()` now uses MongoDB's `$text` operator as the primary path, with `$regex` as fallback. MongoDB 7.0's ICU-based Unicode text segmentation tokenizes Chinese text into overlapping bigrams, enabling `$text` to match CJK substrings. The `chunk_text_text` index (created in `ensure_mongodb_text_index()`) is now actively used for querying.

**Fallback trigger**: When `$text` returns zero results — typically for single CJK characters that don't form complete bigram tokens — the function falls through to `_keyword_search_regex()`. This preserves the substring-matching correctness for edge cases while gaining indexed performance for the common case.

**Why this works now**: Previous analysis (pre-optimization) noted that Chinese text with `default_language: english` tokenizes the entire string as one token. However, MongoDB 7.0+ with ICU performs CJK bigram tokenization even with default language settings. "正念是核心基础技能" tokenizes into overlapping bigrams: "正念", "念是", "是核", "核心", "心基", "基础", "础技", "技能". A `$text` search for "正念" matches the bigram "正念" in the indexed tokens.

### 33. Security Invariants (Steps 1-5 Audit)

Several cross-cutting security invariants were hardened:

**Admin role downgrade (accounts/models.py)**: `User.save()` now clears `is_staff` and `is_superuser` when `role != admin`. Without this, a downgraded admin retains staff privileges because Django's `@staff_member_required` and `AdminSite.has_permission()` only check `is_staff`, not `role`. The middleware (`AdminAccessMiddleware`) checks `role`, so admin page access was blocked, but staff-only views like `search_view` were still reachable.

**Login redirect validation (accounts/views.py)**: `login_view` now validates the `next` parameter with `url_has_allowed_host_and_scheme()` before redirecting. External URLs are rejected and the user is redirected to index instead. This prevents open-redirect phishing attacks.

**Document pipeline idempotency (knowledge_base/tasks.py)**: `run_document_pipeline()` now cleans up existing chunks and Qdrant vectors before creating new ones. If a prior attempt partially succeeded (chunks created in MongoDB but Qdrant indexing failed), the Celery retry will not produce duplicates. Cleanup is best-effort on the Qdrant side (catches exceptions) since the collection might not exist yet on first run.

**Keyword search regex safety (knowledge_base/services.py)**: `keyword_search()` now escapes all user-provided query terms with `re.escape()` before constructing MongoDB `$regex` patterns. This prevents regex metacharacter injection that could alter match semantics or cause ReDoS.

**Audit log immutability (export_app/admin.py, reports/admin.py)**: Both `AdminOperationLogAdmin` and `ReportAccessLogAdmin` now override `has_delete_permission` to return `False`. Combined with the existing `has_add_permission=False` and `has_change_permission=False`, audit records are fully immutable through the admin interface.

**Report view error handling (reports/views.py)**: `student_report_view` catches `User.DoesNotExist` and raises `PermissionDenied` (403) instead of letting the exception propagate to a 500 error. This prevents information leakage through stack traces and maintains consistent authorization semantics.

### 31. TransactionTestCase vs TestCase for MongoDB Index Operations

**Pattern**: Tests that use MongoDB native operations (text search, regex on collections) should extend `TransactionTestCase` instead of `TestCase`.

**Why**: Django's `TestCase` wraps each test in a MongoDB transaction. While CRUD operations work within the transaction, index-dependent queries (especially with newly created indexes) may not properly index uncommitted data. `TransactionTestCase` avoids per-test transactions — data is flushed after the class completes, and all reads see committed state.

**Usage in Step 5**: `KeywordSearchTests`, `SearchViewTests`, and `DocumentPipelineIntegrationTests` all use `TransactionTestCase`. Simpler ORM-only tests continue using `TestCase`.

### 32. Section Extraction & Chunk Metadata Enrichment (Step 5 Post-Review)

**Problem**: The original `run_document_pipeline()` passed a flat `doc_metadata` dict (document_id, title, module, skill, etc.) to `chunk_text()`, which propagated it to every chunk unchanged. Chunk metadata had no awareness of the document's internal structure — all chunks carried identical metadata regardless of which section of the document they came from.

**Solution**: Pre-process text with `extract_sections()` before chunking:

```
full_text → extract_sections() → [{title, content}, ...]
  → for each section:
      section_meta = {**doc_metadata, "section_title": section["title"]}
      chunk_text(section["content"], metadata=section_meta)
  → combine all section chunks
```

**`services.py::extract_sections(text)`**:
- Parses markdown headings (`#{1,3}` pattern) to identify section boundaries
- Each heading becomes a section with `title` = heading text and `content` = text between this heading and the next
- Preamble (text before the first heading) becomes a section titled "概述"
- Fallback for plain text with no headings: uses the first line as title (if ≤80 chars) or "正文"
- Returns `list[{"title": str, "content": str}]`

**Result**: Every chunk's `metadata` dict now includes `section_title` alongside the document-level fields. This enables retrieval-aware context (the LLM can see which section a chunk belongs to) without post-hoc reconstruction of the document structure from chunk IDs.


### 34. RAG Module — File Structure & Responsibilities (Step 6)

```
knowledge_base/rag/
├── __init__.py          # Package exports (6 schemas, retriever, 6 chains, validator)
├── schemas.py           # 6 Pydantic v2 models — the contract between LLM output and downstream code
├── llm_client.py        # MiniMax API wrapper — endpoint, auth, error handling, reasoning support
├── prompts.py           # 6 prompt builders — construct system+user messages from context + schemas
├── retriever.py         # LangChain BaseRetriever wrapping hybrid_search() + RetrievalLog auto-write
├── chains.py            # 6 chain functions — retrieve → prompt → LLM → validate → return Pydantic
└── validator.py         # JSON repair + Pydantic validation for LLM output
```

**`schemas.py`** — The single source of truth for structured output shape. Each Pydantic model serves two purposes simultaneously:
1. **Output parsing**: Pydantic validates the LLM's JSON response
2. **Prompt generation**: `_schema_to_json_schema()` injects the JSON schema into system prompts so the LLM knows the expected output format

The 6 schemas map to the 6 DBT sub-flows:
```
SkillSelectionResult  → "根据学生档案和历史记录，推荐一个模块下的具体技能（如正念→观察呼吸）"
TeachingPlan          → "制定结构化的分步教学计划"
TeachingContent       → "生成单条教学对话消息"
TeachingSummary       → "教学会话结束后生成总结"
TestQuestions         → "根据教学总结生成5道情景选择题"
RiskAssessment        → "评估用户消息是否存在风险"
```

**SkillSelectionResult** now includes both `selected_module` and `selected_skill`:
- `selected_module`: The DBT module (正念 / 情绪调节 / 痛苦耐受 / 人际效能)
- `selected_skill`: The specific skill within that module (e.g. 观察呼吸, STOP技能, 情绪命名)
- This enforces specific skill recommendation rather than broad module-level selection.
- The prompt includes a module→skills mapping so the LLM understands the hierarchy.

**`llm_client.py`** — Thin wrapper around MiniMax ChatCompletion API:
- Endpoint: `https://api.minimaxi.com/v1/text/chatcompletion_v2`
- Auth: `Bearer <MINIMAX_API_KEY>` header
- Defaults: model=`MiniMax-M2.7`, temperature=0.3, max_tokens=4096, timeout=60s
- JSON mode: `reply_format="json"` (MiniMax-native parameter)
- Reasoning model handling: M2.7 outputs `reasoning_content` (thinking trace) separately from `content` (actual response). The client separates them — `reasoning_content` is logged for debugging, `content` is returned for parsing.
- Error hierarchy: `ConfigurationError` (missing API key) vs `APIError` (runtime failures: timeout, connection error, non-200, empty choices)
- Best-effort error extraction from multiple MiniMax response formats (`base_resp.status_msg`, `error.message`, raw text)

**`prompts.py`** — 6 `build_*_messages()` functions, each returning `[{"role": "system", "content": ...}, {"role": "user", "content": ...}]`. Shared helpers:
- `_format_profile(profile)`: renders UserProfile (Pydantic or dict) to Chinese text. Already handles both types via `isinstance(profile, dict)` check.
- `_format_chunks(chunks)`: renders retrieval results to numbered context blocks with metadata (section_title, module, difficulty).
- `_schema_to_json_schema(model)`: extracts Pydantic JSON schema for injection into system prompts.
- Two universal rules injected into every system prompt:
  - `_DBT_FABRICATION_RULE`: "禁止编造具体的DBT数据" — prevents hallucination of case studies, statistics, or specific patient data
  - `_JSON_OUTPUT_RULE`: "必须输出合法JSON" — enforces structured output format

**Skill selection prompt design** (`_SKILL_SELECTION_SYSTEM`): The prompt embeds the full DBT skill hierarchy so the LLM can recommend specific skills (not broad modules):
```
正念（核心基础）→ 观察呼吸、身体扫描、正念行走、正念饮食、观察-描述-参与、不评判练习
情绪调节 → 情绪命名、情绪追踪、相反行动、ABC情绪分析、事实核查
痛苦耐受 → STOP技能、TIP技能、转移注意力、自我安抚（五感）、接受现实
人际效能 → DEAR MAN沟通法、GIVE技巧、FAST技巧、设置边界
```
Recommendation priority: unlearned skills first → matched to concern tags → weak areas from test history → age/grade-appropriate difficulty. The output requires both `selected_module` and `selected_skill` fields.

**`retriever.py`** — `DBTRetriever(BaseRetriever)` bridges LangChain and the existing hybrid search:
- `_get_relevant_documents(query)`: returns `list[langchain_core.Document]` with `page_content` + `metadata` (chunk_id, document_id, source, score)
- `search_with_context(query)`: returns raw `list[dict]` for direct use in prompt builders that need chunk metadata
- Both methods auto-write `RetrievalLog` when `user` and valid `use_case` are provided
- `get_retriever(k, user, session, use_case)` factory for common defaults

**`chains.py`** — 6 self-contained chain functions. Each follows the identical pattern:
```
1. Retrieve chunks (skip if mock_llm_response provided)
2. Build messages via prompts.build_*_messages()
3. Call LLM (or use mock) via _call_llm_or_mock()
4. Parse JSON + validate against Pydantic schema
5. Return validated Pydantic model instance
```

**`validator.py`** — `OutputValidator` with two repair strategies:
- `repair_json(raw_content)`: strips markdown fences (```json...```), finds outermost `{...}`, removes trailing commas before `}` or `]`
- `validate_and_repair(data, schema_model)`: if input is a string, calls repair_json first; then validates against the Pydantic model

### 35. Chain Function Pattern & Data Flow

Each chain function is a self-contained RAG pipeline:

```
query → retriever.search_with_context(query)
      → chunks (list[dict])
      → build_*_messages(profile, ..., retrieval_chunks=chunks)
      → messages (list[{role, content}])
      → _call_llm_or_mock(messages, SchemaClass, mock_llm_response)
          ├─ mock path: OutputValidator.validate_and_repair(mock_llm_response, SchemaClass)
          └─ real path: minimax_chat_completion(messages, reply_format="json")
                         → json.loads(content)
                         → OutputValidator.validate_and_repair(parsed, SchemaClass)
      → SchemaClass(**result)  # validated Pydantic instance
```

**Retrieval is skipped when `mock_llm_response` is provided.** This is critical: chain tests don't require the embedding model or Qdrant to be running. The `is_mock` flag gates retrieval in all 6 chain functions.

**Chain-to-chain data flow** (the real runtime path):

```
generate_skill_selection()
  → SkillSelectionResult.selected_skill ──→ generate_teaching_plan(selected_skill=...)
  → SkillSelectionResult.selected_module ─┘   → TeachingPlan.plan_steps ──→ generate_teaching_content(teaching_plan_steps=...)
                                                                              → TeachingContent (per message, looped)
                                              → TeachingPlan ──→ generate_teaching_summary(skill=...)
                                                                  → TeachingSummary.key_points ──→ generate_test_questions(teaching_summary_key_points=...)
                                                                                                    → TestQuestions
```

### 36. Type Normalization at Prompt Builder Boundaries

**Problem**: Chain functions return Pydantic model instances, but prompt builders consume data as dicts with `.get()` / `[]` access. The boundary between chains (Pydantic objects) and prompts (dict consumers) is a type mismatch risk.

**Solution**: Prompt builders normalize inputs to dicts at the boundary:

```python
# In build_teaching_content_messages():
for s in teaching_plan_steps:
    if hasattr(s, "model_dump"):
        step = s.model_dump()      # Pydantic → dict
    elif isinstance(s, dict):
        step = s                    # already dict
    else:
        step = s.__dict__           # fallback for other objects
    # Then use step.get("step_number"), step["title"], etc.
```

This pattern ensures `plan.plan_steps` (list of `TeachingPlanStep` Pydantic objects) can be passed directly from `generate_teaching_plan()` into `generate_teaching_content()` without intermediate conversion.

**Existing safety**: `_format_profile()` already handles both types via `isinstance(profile, dict)` check. `_format_chunks()` always receives dicts from `hybrid_search()`. `conversation_history` is always `list[dict[str, str]]` from the chat session.

### 36b. Teaching Plan Step Context Pre-Fetch (Step 13 Optimization)

When `run_teaching_plan()` generates a teaching plan, it now pre-fetches RAG chunks for each plan step and stores them parallel to `plan_steps`:

```python
# teaching/services.py::run_teaching_plan()
step_retriever = get_retriever(k=3, user=user, session=session, use_case="teaching")
for step in plan_steps:
    chunks = step_retriever.search_with_context(f"{skill} {step_text}")
    step_contexts.append(chunks)
plan_dict["step_contexts"] = step_contexts  # list[list[dict]]
```

During teaching (`generate_teaching_response()`), the current step's pre-fetched context is passed to `generate_teaching_content(prefetched_chunks=...)`. The chain merges pre-fetched chunks into the dynamic retrieval results (deduplicated by `chunk_id`), giving the LLM broader context without additional round-trips.

**Interaction with Redis cache (Opt 7)**: Pre-fetch calls `hybrid_search()` which is Redis-cached. Subsequent messages in the same step benefit from both the warm cache and the pre-fetched context, minimizing backend queries.

### 37. LLM Client: MiniMax-Specific Design Decisions

| Decision | Rationale |
|----------|-----------|
| Native endpoint (`/v1/text/chatcompletion_v2`) over OpenAI-compatible (`/v1/chat/completions`) | OpenAI endpoint wraps reasoning in `<think>` XML tags inside `content`. Native endpoint separates `reasoning_content` from `content` — clean JSON in `content`. |
| `reply_format="json"` over `response_format={"type": "json_object"}` | `reply_format` is the MiniMax-native parameter. The OpenAI-style `response_format` dict is not recognized by the v2 endpoint. |
| `mask_sensitive_info=True` in request body | MiniMax privacy feature — masks PII in logs. Enabled by default. |
| Model lock to `MiniMax-M2.7` | Consistent behavior across all sub-flows. No per-call model switching needed. |
| Temperature 0.3 | Low enough for deterministic structured output, high enough for natural Chinese teaching dialogue. |

### 38. Graceful Degradation: Embedding Model Unavailability

The embedding model (BAAI/bge-m3, 2.12GB) may fail to load for several reasons:
- Network unreachable (first-time download)
- Insufficient disk space
- Model file corruption

**Degradation chain**:
```
get_embedding_model() returns None
  → generate_embeddings() returns zeros array
  → semantic_search() returns []
  → hybrid_search() = keyword_results only (no semantic contribution)
  → RAG chains still function with keyword-only retrieval
```

The `_embedding_load_failed` flag prevents retry storms — once loading fails, it stays failed for the process lifetime. This avoids repeated timeout/error cycles on every semantic search attempt.

### 38b. Embedding Model Preloading at Startup (Step 13 Optimization)

To eliminate ~9s cold-start latency on the first semantic search request, the model is preloaded in a background thread at Django startup:

```python
# knowledge_base/apps.py::KnowledgeBaseConfig.ready()
if os.environ.get("RUN_MAIN") == "true":
    return  # Skip Django auto-reloader child process

from .services import preload_embedding_model
t = threading.Thread(target=preload_embedding_model, daemon=True)
t.start()
```

**Preload function** (`preload_embedding_model()`):
- Idempotent: `_embedding_preload_started` flag prevents double-loading
- Tries `local_files_only=True` first (fast, ~1s from cache)
- Falls back to network load on cache miss
- Sets `_embedding_load_failed=True` if both fail, `get_embedding_model()` will return `None`
- Runs in a daemon thread — Django startup is not blocked

**Result**: The 2.12GB `BAAI/bge-m3` model is loaded and warm by the time a user logs in and navigates to the teaching page, removing embedding loading from the critical request path.

### 39. Mock LLM Response Pattern for Testing

All chain functions accept an optional `mock_llm_response: dict[str, Any]` parameter. When provided:
1. Retrieval is **skipped entirely** (chunks = [])
2. The mock dict is passed directly to `OutputValidator.validate_and_repair()`
3. No API call is made — no API key, network, or MiniMax account needed

Test flows use predefined valid response dicts (`VALID_TEACHING_PLAN`, `VALID_TEACHING_CONTENT`, etc.) that match the Pydantic schema exactly. This enables full chain testing in CI without external service dependencies.

```python
# Test example: chain test with mock
result = generate_skill_selection(
    profile=profile,
    history_skills=["观察呼吸"],
    mock_llm_response={
        "selected_skill": "正念呼吸",
        "reason": "学生已有基础",
        "skill_difficulty": "初级",
        "alternative_skills": ["情绪觉察"],
        "source_chunk_ids": [],
    },
)
assert isinstance(result, SkillSelectionResult)
assert result.selected_skill == "正念呼吸"
```

### 40. Retriever Dual-Mode Architecture

`DBTRetriever` provides two retrieval interfaces for different consumers:

| Method | Returns | Consumer |
|--------|---------|----------|
| `_get_relevant_documents(query)` | `list[Document]` (LangChain) | LangChain chains, `.invoke()` interface |
| `search_with_context(query)` | `list[dict]` (raw chunks with metadata) | Prompt builders that need `section_title`, `module`, `difficulty` |

Both methods:
- Delegate to `hybrid_search()` (keyword + semantic → dedup)
- Auto-write `RetrievalLog` when `user` and valid `use_case` are provided
- Log failures are non-fatal (catch + log, don't break retrieval)

The raw dicts from `search_with_context()` contain `chunk_text`, `score`, `source` ("keyword"/"semantic"/"hybrid"), and `document_id`. The prompt builder's `_format_chunks()` renders these into a structured context block with metadata for the LLM.

### 41. Teaching Session State Machine (Step 7)

The teaching session orchestrates 9 states — 6 transitional phases tracked as `TeachingSession.Phase` and 3 terminal outcomes tracked as `TeachingSession.Status`:

```
Phase flow (forward-moving):
  pre_mood_recording → personal_inquiry → info_collection → skill_selection
    → rag_retrieval_for_teaching → teaching

Status outcomes (terminal, reachable from any phase):
  → completed / stopped_by_risk / user_terminated
```

**Why Phase + Status decoupling**: The 3 terminal statuses are mutually exclusive and can be reached from any phase (e.g., risk detection stops session during teaching; user terminates during skill_selection). The 6 phases form a forward-moving sequence that's never skipped backward. Keeping them separate means Phase tracks "where am I in the flow" and Status tracks "did it end normally or abnormally."

**Phase transition triggers**:

| From → To | Trigger | Service call |
|-----------|---------|-------------|
| pre_mood_recording → personal_inquiry | User submits mood | `run_pre_mood()` |
| personal_inquiry → info_collection → skill_selection | User shares personal context | `run_personal_inquiry()` → `run_info_collection()` → auto-calls skill selection |
| skill_selection → rag_retrieval_for_teaching | User confirms skill | `run_teaching_plan()` → calls `run_rag_retrieval()` internally |
| rag_retrieval_for_teaching → teaching | System auto-advances after retrieval | `run_teaching_plan()` continues |
| (any) → completed | User ends session | `generate_session_summary()` |
| (any) → stopped_by_risk | Risk detected | `process_risk_check()` |
| (any) → user_terminated | User terminates | `terminate_session()` |

**The personal_inquiry phase** (added 2026-05-12): Before skill recommendation, the AI generates a warm, empathetic question based on the student's profile and pre-mood. The student shares their recent experiences, which becomes the most important input for skill selection.

**Model** (`teaching/models.py`):

```python
class TeachingSession(models.Model):
    class Phase(models.TextChoices):
        PRE_MOOD_RECORDING = "pre_mood_recording", "教学前心情记录"
        PERSONAL_INQUIRY = "personal_inquiry", "个人情况了解"
        INFO_COLLECTION = "info_collection", "信息收集"
        SKILL_SELECTION = "skill_selection", "技能选择"
        RAG_RETRIEVAL_FOR_TEACHING = "rag_retrieval_for_teaching", "RAG教学检索"
        TEACHING = "teaching", "教学中"

    class Status(models.TextChoices):
        ONGOING = "ongoing", "进行中"
        COMPLETED = "completed", "已完成"
        STOPPED_BY_RISK = "stopped_by_risk", "风险中止"
        USER_TERMINATED = "user_terminated", "用户终止"
```

New field: `personal_context = models.TextField(blank=True, default="")` — stores the student's recent experiences shared during the personal_inquiry phase.

### 42. Teaching Service Orchestration Layer (Step 7)

**`teaching/services.py`** — The single orchestration point between views and RAG chains. All LLM calls go through this layer; views never call chain functions directly.

**Service function responsibilities**:

| Function | Reads | Writes | Chains called |
|----------|-------|--------|---------------|
| `create_session()` | — | TeachingSession | — |
| `run_pre_mood()` | — | MoodRecord, TeachingSession.pre_mood_id, phase | — |
| `generate_inquiry_question()` | UserProfile, MoodRecord | — | `generate_personal_inquiry` |
| `run_personal_inquiry()` | — | TeachingSession.personal_context | — |
| `run_info_collection()` | UserProfile, TeachingSession history, **Test records**, **personal_context**, **pre_mood** | TeachingSession (phase, selected_skill, selection_reason, rag_context_ids) | `generate_skill_selection` |
| `run_rag_retrieval()` | Qdrant (via retriever) | TeachingSession.rag_context_ids | — (retriever only) |
| `run_teaching_plan()` | UserProfile | TeachingSession (teaching_plan, phase, rag_context_ids) | `generate_teaching_plan` |
| `generate_teaching_response()` | UserProfile, conversation_history | ChatMessage (×2: user + assistant), rag_context_ids | `generate_teaching_content` |
| `process_risk_check()` | User message, recent_context | RiskEvent, TeachingSession (status, completed_at), ChatMessage (system) | `run_risk_assessment` |
| `generate_session_summary()` | UserProfile, conversation_history | TeachingSession (teaching_summary, status, completed_at), ChatMessage (system) | `generate_teaching_summary` |
| `terminate_session()` | — | TeachingSession (status, completed_at) | — |

**Personal context in skill selection** — `run_info_collection()` now passes `session.personal_context` and pre-mood value to `generate_skill_selection()`. The personal context (student's recent experiences shared during the personal_inquiry phase) is the most important input for skill recommendation, prioritized above historical data.

**Test record integration in skill selection** — `run_skill_selection()` and `run_info_collection()` both query `testing.models.Test`:

```python
from testing.models import Test
previous_tests = Test.objects.filter(user=user).order_by("-created_at")[:20]

# Aggregated into test_stats:
test_stats = {
    "total_tests": len(previous_tests),
    "total_passed": sum(1 for t in previous_tests if t.passed),
    "tested_skills": sorted(set(t.session.selected_skill for t in previous_tests)),
    "recent_tests": [
        {"session_skill": t.session.selected_skill, "passed": t.passed}
        for t in previous_tests[:10]
    ],
}
```

Failed skills become "薄弱技能" (weak skills) in the RAG retrieval query:
```python
failed_skills = [t["session_skill"] for t in test_stats["recent_tests"]
                 if not t["passed"] and t["session_skill"]]
if failed_skills:
    retrieval_query += " 薄弱技能:" + ",".join(unique_failed)
```

This ensures the AI skill selection considers test performance history — a skill the student failed in a test will be weighted differently than one they passed.

**Keyword-based risk detection** — Two-tier keyword system:

```python
_HIGH_RISK_KEYWORDS = ["自杀", "自伤", "自残", "想死", ...]  # 15 keywords
_MODERATE_RISK_KEYWORDS = ["绝望", "毫无希望", ...]  # 8 keywords
```

`check_keyword_risk(text)` scans both lists. If triggered, `process_risk_check()` calls `run_risk_assessment()` (AI semantic analysis). On high risk, the session is stopped immediately with a system message.

### 43. Teaching View Layer (Step 7)

**`teaching/views.py`** — 8 view functions, all decorated with `@profile_required` (from questionnaire.decorators):

**Start flow**:
1. `start_session_view` (POST) → `create_session()` → redirect to session page
2. Session page renders `pre_mood_recording` phase UI
3. `record_pre_mood_view` (POST) → `run_pre_mood()` → `run_info_collection()` → redirect
4. Session page now renders `skill_selection` phase UI
5. `confirm_skill_view` (POST) → `run_teaching_plan()` (includes RAG retrieval) → redirect
6. Session page now renders `teaching` phase UI

**Teaching dialogue**:
7. `send_message_view` (POST) → risk check → `generate_teaching_response()` → HTMX partial
8. `end_session_view` (POST) → `generate_session_summary()` → redirect
9. `terminate_session_view` (POST) → `terminate_session()` → redirect

**Error handling pattern**: All views that call RAG chains wrap the call in `try/except (ConfigurationError, APIError)`. On failure, the session is either terminated or the user is shown an error message without losing their session state.

**HTMX pattern**: `send_message_view` returns `templates/teaching/messages_partial.html` with `hx-swap="outerHTML"` on the chat messages container. The partial renders all conversation bubbles (both user and assistant) — the server is the source of truth for message state.

### 44. Teaching Template Architecture (Step 7)

**`templates/teaching/session.html`** — A single page with 7 conditional blocks driven by `session.phase` and `is_terminal`:

```
session.html
├── is_terminal == True
│   ├── completed → 教学摘要 + 完成时间
│   ├── stopped_by_risk → 风险中止信息
│   └── user_terminated → 终止信息
│   └── 对话记录 (full conversation in all terminal states)
│
├── phase == pre_mood_recording → emoji mood selector (😫😟😐🙂😄) + optional note
├── phase == info_collection → "正在收集信息" auto-transition screen
├── phase == skill_selection → AI recommendation card + custom skill input + confirm button
├── phase == rag_retrieval_for_teaching → "正在准备教学资料" auto-transition screen
└── phase == teaching → 4-col grid
    ├── col-1: Teaching plan sidebar (sticky, numbered steps with estimated minutes)
    └── col-3: Chat area
        ├── Message bubbles (user=right/blue, assistant=left/gray, system=yellow)
        ├── HTMX form (POST to send_message, hx-target=#chat-messages)
        ├── Sending indicator
        └── End/Terminate buttons (bottom-right)
```

**`templates/teaching/home.html`** — Profile info card + "开始新教学" button + recent session list. Profile card displays: gender (Chinese label), age, grade (Chinese label), hobby tags (joined with `、`), concern tags, other hobby text, updated_at timestamp.

### 44b. Frontend Perceived Performance (Step 13 Optimization)

**Skeleton shimmer** (`base.html` + `session.html`): The sending indicator now renders animated placeholder bars (`.skeleton` class with `shimmer` keyframe animation) that mimic an incoming message bubble — replacing the plain "正在思考..." text. The `shimmer` animation uses a `linear-gradient` sliding across the placeholder, giving a visual cue that content is loading.

**FOUC prevention** (`base.html`): Added `[x-cloak] { display: none !important; }` CSS rule. Alpine.js removes the `x-cloak` attribute on initialization, preventing unstyled content flash before Alpine.js hydrates.

**HTMX indicator transitions** (`base.html`): Smooth opacity fade-in/fade-out (`.2s ease-in`) on `.htmx-indicator` elements via CSS transitions instead of instant show/hide.

**HTMX CSRF header** (`base.html`, 2026-05-13): `<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"'>` ensures all `hx-post` requests automatically include the Django CSRF token. This eliminates the need for `{% csrf_token %}` inside individual `<form>` tags for HTMX-driven buttons.

**Image lazy loading** (existing, verified): All `<img>` tags in `messages_partial.html` and `session.html` use `loading="lazy"` to defer off-screen image loading.

**`templates/teaching/messages_partial.html`** — HTMX partial template. Renders all `conversation` messages with role-based styling. When `is_terminal=True`, shows a "会话已中止" banner. Used exclusively by `send_message_view` as an HTMX response.

### 45. Teaching Test Architecture (Step 7)

**`teaching/tests.py`** — 75 tests in 11 classes following a layered mock strategy:

```
Layer 1: ViewTestMixin (class-level patches)
  → patches knowledge_base.rag.chains.* (6 chain functions)
  → patches knowledge_base.rag.retriever.get_retriever (returns mock with search_with_context=[])
  → All view tests inherit this mixin
  → Real service functions run, but LLM calls return predefined mock data

Layer 2: StateTransitionTests (per-test patches)
  → Patches individual chain functions
  → Tests the REAL service layer orchestration
  → Verifies state transitions, data persistence

Layer 3: KeywordRiskUnitTests (no patches)
  → Tests check_keyword_risk() in isolation
  → No database, no mocks, pure unit tests
```

**ViewTestMixin pattern**:
```python
class ViewTestMixin:
    _patches: list = []

    @classmethod
    def start_service_patches(cls):
        if cls._patches:
            return
        mock_ret = MagicMock()
        mock_ret.search_with_context.return_value = []
        cls._patches = [
            patch("knowledge_base.rag.retriever.get_retriever", return_value=mock_ret),
            patch("knowledge_base.rag.chains.generate_skill_selection",
                  return_value=SkillSelectionResult(**MOCK_SKILL_SELECTION)),
            # ... 5 more chain patches
        ]
        for p in cls._patches:
            p.start()
```

**Test helper functions**:
- `create_student(username)` — Creates User with `profile_completed=True` + UserProfile (via ProfileForm)
- `create_session(user)` — Creates TeachingSession in `skill_selection` phase (for tests that need a specific starting point)

### 46. Teaching Admin Visibility (Step 7)

**`teaching/admin.py`** — Two admin classes + one inline:

**TeachingSessionAdmin**: Lists sessions with `session_id_short` (12-char UUID preview), user, phase, status, selected_module, selected_skill, message_count, started_at, completed_at. Filters by status, phase, module, started_at. Search by username, module, skill, teaching_summary. Detail page organized in 4 fieldsets + ChatMessageInline.

**ChatMessageAdmin**: Standalone list for searching all messages by content, filtered by role, modality, created_at. Shows `content_preview` (60-char truncation) and session_link.

**ChatMessageInline**: Inside TeachingSession detail, read-only. Shows role, content_preview (80-char truncation), modality, created_at. `extra=0`, `can_delete=False`, `max_num=50`. `has_add_permission` returns False.

### 47. Teaching URL Structure (Step 7)

```
/teaching/                              → teaching_home_view (GET)
/teaching/start/                        → start_session_view (POST)
/teaching/session/<session_id>/         → session_view (GET)
/teaching/session/<session_id>/pre_mood/ → record_pre_mood_view (POST)
/teaching/session/<session_id>/skill/   → confirm_skill_view (POST)
/teaching/session/<session_id>/message/  → send_message_view (POST — HTMX)
/teaching/session/<session_id>/end/      → end_session_view (POST)
/teaching/session/<session_id>/terminate/ → terminate_session_view (POST)
```

The pre_mood endpoint was added because skill selection is no longer triggered from `start_session_view`. The flow is: start → session page (pre_mood UI) → pre_mood POST → (info_collection + skill_selection auto-run) → session page (skill_selection UI) → confirm_skill POST → (rag_retrieval + teaching_plan) → session page (teaching UI).

### 47b. Session Page Test Records Integration (Step 15)

`session_view` now queries all `Test` records for the current session when the session is in a terminal state, and passes them to the template as `tests`. The template renders a "测试记录" section below the terminal state card, showing each test as a color-coded card:

- **Green** (passed, ≥4/5): score + "查看详情" link
- **Yellow** (failed, <4/5): score + "查看详情" + "重新测试" button
- **Blue** (ongoing with questions): "继续答题" link
- **Blue** (ongoing, 0 questions — orphan): "继续答题" link → leads to stuck test page with recovery options
- **Gray** (user_terminated): status only

The `_question_count` annotation on each test object is available for the template but not currently rendered (reserved for future use, e.g. showing "3/5 answered").

This integration closes the gap between teaching and testing — students can see all their test attempts directly on the session page without navigating to individual test URLs.

### 48. Testing Module Structure (Step 8)

```
testing/
├── models.py               # Test + TestQuestion models (TestQuestion has created_at, Meta ordering)
├── services.py             # Orchestration layer — lazy imports for mock compatibility
├── tasks.py                # Celery tasks: async question generation + image generation
├── views.py                # 8 view functions — all @profile_required
├── urls.py                 # 8 URL patterns
├── admin.py                # TestAdmin + TestQuestionAdmin + TestQuestionInline
├── tests.py                # 60 tests in 11 classes
├── templatetags/
│   ├── __init__.py
│   └── test_filters.py     # `index` filter — list[n] lookup
└── migrations/
    ├── 0001_initial.py
    └── 0002_add_created_at_to_testquestion.py
```

**`testing/services.py`** — Orchestration layer (327 lines, 15 functions). All RAG chain imports are lazy (`from knowledge_base.rag.chains import ...` inside function bodies) so that `unittest.mock.patch` at the module level can intercept them. Module-level imports create local references that patches cannot reach.

**`testing/tasks.py`** — Two Celery tasks (added Step 8 async, 2026-05-13):
- `generate_test_questions_async(test_id)`: Calls `generate_and_save_questions()` via RAG + LLM, then auto-dispatches image generation for questions with `image_prompt` using **staggered countdown** (0s, 3s, 6s, 9s, 12s) to avoid MiniMax API rate-limiting. Max retries=2, delay=10s.
- `generate_test_question_image_async(question_id)`: Calls MiniMax `image-01` API via `media_app.services.generate_image()`, writes `temporary_image_url` + `image_model` + `image_generated_at` to TestQuestion. Max retries=2, delay=30s. The underlying `generate_image()` has its own internal retry (max 3, 2s base delay) for transient HTTP errors (429/502/503/529), so Celery retries only trigger on persistent failures.

**`testing/views.py`** — 8 view functions implementing the full test lifecycle:
1. `start_test_view` — creates test from completed session, dispatches Celery question generation
2. `test_view` — renders main test page (5 states: ongoing/completed/terminated + review + stuck). Orphan detection: if test is ongoing with 0 questions and created >5 min ago, marks as `is_stuck` and shows recovery options instead of infinite spinner (Step 15)
3. `poll_questions_view` — HTMX polling endpoint (checks if questions ≥ 5 are ready)
4. `answer_question_view` — HTMX endpoint for per-question answer + explanation
5. `finish_test_view` — calculates pass/fail (≥4/5 threshold)
6. `retest_view` — creates new test with incremented attempt_no
7. `generate_question_image_view` — POST, dispatches Celery image task, returns polling spinner (added 2026-05-13)
8. `question_image_status_view` — GET, returns image HTML when ready or polling spinner (added 2026-05-13)

All views use `@profile_required` and ownership checks via `services.get_test_or_404(test_id, user)`.

**Orphan test recovery (Step 15)**: `test_view` detects orphan tests (`is_ongoing + 0 questions + created > 5min ago`) and passes `is_stuck` context flag to template. The template shows "题目生成超时" with retry/back buttons instead of the infinite HTMX polling spinner. The `get_retest_attempt_no()` function uses `count() + 1` (not `max(attempt_no) + 1`) to avoid duplicate attempt_no from historical orphans.

### 49. Answer Letter-to-Index Mapping (Step 8)

The frontend uses letter answers (A, B, C, D) but `TestQuestion.correct_option` stores the answer as an index string ("0", "1", "2", "3"). This decouples display convention from storage format.

**Conversion maps** (defined in `testing/views.py` and tested in `testing/tests.py`):

```python
_OPTION_LETTERS = ["A", "B", "C", "D"]         # index → letter (view context)
_LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}  # letter → index (answer processing)
```

**Answer processing flow** (`services.py::answer_question`):
```python
answer_letter = user_answer.strip().upper()    # "B"
answer_idx = _LETTER_TO_INDEX.get(answer_letter, -1)  # 1
question.is_correct = (str(answer_idx) == str(question.correct_option).strip())  # "1" == "1"
```

The letter is stored as `question.user_answer` (for display), while `is_correct` is determined by index comparison. The view's `answer_question` helper also extracts `correct_text` (the option text at the correct index) for template display.

### 50. Template Option Letter Rendering (Step 8)

Django's built-in `slice` filter uses Python slice semantics: `slice:"n"` means `value[:n]` (first N elements from the start), NOT `value[n:]`. This makes index-based access impossible without a custom filter.

**Broken pattern** (never works correctly):
```
{{ option_letters|slice:forloop.counter0|first }}  → always "A" or empty
```

**Working solution**: Custom `index` filter in `testing/templatetags/test_filters.py`:
```python
@register.filter
def index(seq, i):
    try:
        return seq[int(i)]
    except (IndexError, TypeError, ValueError):
        return ""
```

Usage in templates:
```html
{% load test_filters %}
{{ option_letters|index:forloop.counter0 }}  {# → A, B, C, D correctly #}
```

### 51. Test Mock Strategy (Step 8)

Same layered mock strategy as teaching tests (Step 7, §45):

```
Layer 1: ViewTestMixin (class-level patches)
  → patches knowledge_base.rag.chains.generate_test_questions
  → patches knowledge_base.rag.chains.run_risk_assessment
  → patches knowledge_base.rag.retriever.get_retriever
  → All view tests inherit this mixin
  → Real service functions run, LLM calls return MOCK_TEST_QUESTIONS

Layer 2: Per-test patches (TestCreationTests.test_start_test_graceful_api_error)
  → Patches individual chain functions with APIError side_effect
  → Tests graceful degradation path

Layer 3: Unit tests (RiskDetectionTests)
  → Tests check_keyword_risk() in isolation
  → No mocks, no database
```

**Critical constraint: lazy imports required for mocking**. All RAG chain imports in `testing/services.py` are inside function bodies:
```python
def _generate_and_save_questions(test, user, session):
    from .models import TestQuestion
    from knowledge_base.rag.chains import generate_test_questions  # ← lazy
    from knowledge_base.rag.retriever import get_retriever          # ← lazy
```

If these were module-level imports, `patch("knowledge_base.rag.chains.generate_test_questions")` would not intercept the call because Python's `from X import Y` creates a local reference in the importing module's namespace.

### 52. HTMX Per-Question Answer Flow (Step 8)

Each question is answered individually via HTMX:

```
User selects radio + clicks "提交答案"
  → POST /testing/test/<test_id>/answer/ (question_id + answer)
  → services.process_test_risk() — keyword + AI risk check
  → services.answer_question() — letter→index conversion + is_correct check
  → Render answer_partial.html:
      ✓/✗ banner
      Options with highlighting (green=correct, red=wrong answer)
      Blue explanation box
      "下一题" button → onclick="location.reload()"
  → HTMX swaps #question-area with result partial
  
User clicks "下一题"
  → Page reloads → test_view finds next unanswered question
  → New question form rendered in #question-area
```

The page reload after each question ensures the progress sidebar updates correctly and the next unanswered question is selected. The server is always the source of truth for which questions have been answered.

### 53. Test Completion & Retest Flow (Step 8)

**Pass threshold**: 4+ correct out of 5 questions.

```
All 5 questions answered → "提交测试" button appears
  → POST /testing/test/<test_id>/finish/
  → services.finish_test():
      correct_count = sum(1 for q in questions if q.is_correct)
      passed = correct >= 4
      status → COMPLETED
  → redirect to test page

Test page (completed state):
  ┌─ Result summary ──────────────────────┐
  │ 正确率: 3/5  |  未通过  |  第 1 次测试  │
  │ [返回教学会话]  [重新测试]              │
  └────────────────────────────────────────┘
  ┌─ 题目回顾 ─────────────────────────────┐
  │ 第 1 题 ✗ 错误                         │
  │   - options with green/red highlighting │
  │   - 解析: <explanation text>           │
  │ ...                                    │
  └────────────────────────────────────────┘

If failed: "重新测试" button
  → POST /testing/test/<test_id>/retest/
  → services.get_retest_attempt_no(session) → attempt_no = 2
  → services.create_test(session, user, attempt_no=2)
  → 5 new questions generated via RAG
  → redirect to new test page
```

**Retest invariants**:
- Each retest creates a NEW Test record with incremented `attempt_no`
- Each retest generates 5 NEW questions (RAG chain called with "不同角度 新题型" query append)
- Previous test records are preserved — all attempts are traceable
- No limit on retest count (verified: 4 cycles in `test_unlimited_retests`)

### 54. TestTemplate Architecture (Step 8, Updated 2026-05-13)

`templates/testing/test.html` renders 4 distinct states driven by `test.status`:

```
test.html
├── is_completed == True + result_data
│   └── Result summary (pass/fail badge, correct_count/total, attempt_no)
│       ├── "返回教学会话" link
│       ├── "重新测试" button (only if not passed)
│       └── Post-test mood recording prompt
│
├── is_terminated == True
│   └── "测试已终止" info + "返回教学会话" link
│
├── (is_completed or is_terminated) + questions
│   └── 题目回顾 (question review):
│       └── Per question: correctness badge + question text
│           + options with highlighting (green=correct, red=wrong answer)
│           + explanation box
│           + Image area (3 states):
│               ├── temporary_image_url → <img> + model label
│               ├── image_prompt only → "生成配图" hx-post button (async Celery)
│               └── neither → "生成配图" hx-post button (with fallback prompt)
│
└── is_ongoing == True
    ├── total_count == 0 (loading: questions not yet generated)
    │   └── Spinner + HTMX polling (hx-get poll endpoint, hx-trigger="load delay:1s")
    └── total_count >= 5 (active test)
        ├── Progress sidebar (sticky, col-1)
        │   ├── Per-question status (gray/✓/✗)
        │   ├── "已答：N/5" counter
        │   ├── "提交测试" button (when all answered)
        │   └── "终止" button
        └── Question area (col-4)
            └── Current question: question_text + radio options (A/B/C/D)
                + HTMX submit form (hx-post, hx-target="#question-area")
                + Image area (3 states):
                    ├── temporary_image_url → <img> + sync regen button (DBT_Image.generate)
                    ├── image_prompt only → HTMX polling spinner
                    │     (hx-get image-status, hx-trigger="load delay:1s")
                    │     → auto-displays image when Celery task completes
                    └── neither → "生成配图" hx-post button
                          → dispatches Celery task, returns polling spinner
```

**Image area polling flow** (2026-05-13):

When a question has `image_prompt` but no URL yet, the template renders:
```html
<div hx-get="{% url 'testing:question_image_status' question_id %}"
     hx-trigger="load delay:1s" hx-swap="outerHTML">
  <spinner> 情景配图自动生成中... </spinner>
</div>
```

The `question_image_status_view` returns either:
- Image HTML (when `temporary_image_url` is set) → spinner replaced with `<img>`
- Polling spinner (when still waiting) → re-polls every 3s via `hx-trigger="every 3s"`

For questions without `image_prompt`, the "生成配图" button POSTs to `generate_question_image_view`, which constructs a fallback prompt from the question text, saves it, dispatches the Celery task, and returns the polling spinner.

**CSRF header** (2026-05-13): `<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"'>` in `base.html` ensures all HTMX POST requests carry the CSRF token without needing individual `{% csrf_token %}` tags on every button.

### 55. Teaching→Testing Integration Point (Step 8)

The bridge from teaching to testing is a single button in `templates/teaching/session.html`:

```html
{% if session.status == 'completed' %}
<form method="post" action="{% url 'testing:start' session.session_id %}">
    {% csrf_token %}
    <button type="submit">开始测试</button>
</form>
{% endif %}
```

This appears in the completed terminal state, next to the "返回教学首页" link. The button POSTs to `start_test_view` which:
1. Validates the session is completed and belongs to the current user
2. Calls `services.get_retest_attempt_no(session)` to determine attempt number
3. Calls `services.create_test(session, user, attempt_no)`
4. On API failure: catches `(ConfigurationError, APIError)`, shows error message, redirects back to session
5. On success: redirects to the new test page

The session's `teaching_summary.key_points` are extracted and passed to the test question generation chain as `teaching_summary_key_points`, ensuring test questions are relevant to what was taught.

### 56. Risk Detection During Testing (Step 8, Optimized Step 14)

Risk detection runs on every test answer submission:

```python
# In answer_question_view:
answer_idx = _LETTER_TO_INDEX.get(answer_letter, 0)
selected_text = question.options[answer_idx]  # the chosen option text

risk_context = _get_answer_context(test)      # last 3 answered questions
services.process_test_risk(test, user, selected_text, risk_context)
```

`_get_answer_context()` collects the 3 most recently answered question texts for AI context. `process_test_risk()` runs keyword check first. **As of Step 14**, a `should_assess_risk()` gate skips the MiniMax LLM call when the text contains no risk keywords or moderate concern indicators — the vast majority of benign test answers return in milliseconds. Only text matching keyword lists or concern indicators triggers the full AI semantic assessment.

On high risk, the test is terminated (status → USER_TERMINATED) and a RiskEvent is created with `detection_source="both"` or `"keyword"`.

### 57. Test Question Ordering Invariant

`TestQuestion` uses `Meta.ordering = ["created_at"]` (enforced by the `created_at` DateTimeField with `auto_now_add=True`). Questions are generated and saved sequentially by `_generate_and_save_questions()`, so the `created_at` timestamps reflect the intended order.

The `test_view` explicitly orders by `created_at` in its queryset:
```python
questions = list(TestQuestion.objects.filter(test=test).order_by("created_at"))
```

The `finish_test()` service also orders by `created_at` when computing results. This double ordering (model Meta + explicit queryset) ensures consistent question display order even if the database returns results in an unexpected sequence.

### 58. Mood Module — File Structure & Responsibilities (Step 9)

```
mood/
├── models.py               # MoodRecord + Achievement + UserAchievement models
├── services.py             # Core logic: recording, achievement checking, stats aggregation
├── views.py                # 6 view functions — all @profile_required
├── urls.py                 # 6 URL patterns under namespace "mood"
├── admin.py                # MoodRecordAdmin + AchievementAdmin + UserAchievementAdmin
├── tests.py                # 49 tests in 7 classes
└── templates/mood/
    ├── home.html           # Mood history + manual record entry
    ├── record.html         # Standalone mood recording (5-emoji selector)
    ├── post_mood.html      # Post-teaching / post-test mood recording (shared template)
    └── achievements.html   # Achievement grid with stats summary
```

**`mood/services.py`** — The single authority for mood recording, achievement checking, and stats aggregation (500 lines). Three concerns:

1. **Mood recording** (`record_mood`, `record_post_teaching_mood`, `record_post_test_mood`): Each creates a MoodRecord and calls `check_and_award_achievements()` as a side effect. Post-teaching and post-test variants additionally link the mood_id back to the parent session/test via `post_mood_id`.

2. **Achievement definitions** (`ACHIEVEMENT_DEFS`): A declarative list of 10 achievements, each specifying `key`, `name_cn`, `description_cn`, `icon`, and `trigger_rule` (`{"type": "...", "threshold": N}`). `ensure_achievements_exist()` lazily creates these in the database using `name_cn` as the dedup key.

3. **Achievement checking** (`check_and_award_achievements`, `_check_condition`, `_gather_user_stats`): Aggregates all user stats once, then evaluates each locked achievement against its trigger rule. Achievements are idempotent — already-unlocked achievements are skipped via `UserAchievement.objects.filter().exists()`.

**`mood/views.py`** — 6 view functions:
1. `mood_home_view` — mood history + manual recording button
2. `record_mood_view` — standalone manual mood recording
3. `record_post_teaching_mood_view` — post-teaching mood (auto-redirected from end_session_view)
4. `record_post_test_mood_view` — post-test mood (auto-redirected from finish_test_view)
5. `achievements_view` — achievement grid with stats
6. `mood_stats_view` — JSON API for future reports (staff-only)

### 59. Achievement Trigger Rule Pattern (Step 9)

Each achievement has a `trigger_rule` stored as a JSONField on the Achievement model. The rule follows a `{"type": "<stat_key>", "threshold": N}` pattern:

```
Rule types:
  successful_training_count  → stats["successful_trainings"] >= threshold
  consecutive_days           → stats["consecutive_days"] >= threshold
  first_test_failed          → stats["first_test_failed"] == True
  total_test_fails           → stats["total_test_fails"] >= threshold
  first_mindfulness_training → stats["has_mindfulness_training"] == True
  perfect_test               → stats["has_perfect_test"] == True
  retest_passed              → stats["has_retest_passed"] == True
  mood_recorded              → stats["total_mood_records"] >= threshold
```

**Why type + threshold instead of raw SQL**: JSONField rules are introspectable by the admin UI and future stats dashboards without code changes. Adding a new achievement only requires adding an entry to `ACHIEVEMENT_DEFS` — no new model fields or migration.

**Idempotency guarantee**: `check_and_award_achievements()` checks `UserAchievement.objects.filter(user=user, achievement=achievement).exists()` before awarding. This is safe to call after every key event (session completion, test completion, mood recording, login) without creating duplicates.

### 60. Post-Mood Auto-Redirect Flow (Step 9)

Post-teaching and post-test mood recording are mandatory UI steps, not optional links:

```
Teaching flow:
  end_session_view (POST)
    → check_and_award_achievements()  ← checks session-based achievements
    → if session.post_mood_id is empty → redirect("mood:post_teaching", session_id=session_id)
    → else → redirect("teaching:session", session_id=session_id)

  record_post_teaching_mood_view
    → GET → render post_mood.html (emoji selector + skip link)
    → POST → record mood + link to session.post_mood_id → redirect to session page

Test flow:
  finish_test_view (POST)
    → check_and_award_achievements()  ← checks test-based achievements
    → if test.post_mood_id is empty → redirect("mood:post_test", test_id=test_id)
    → else → redirect("testing:test", test_id=test_id)

  record_post_test_mood_view
    → GET → render post_mood.html (emoji selector + skip link)
    → POST → record mood + link to test.post_mood_id → redirect to test page
```

**Skip is valid**: The post_mood.html template provides a "跳过" (skip) link that redirects back without recording. Mood recording is encouraged but not required to continue.

**Achievement checking at both ends**: Achievements are checked when the session/test completes (in `end_session_view` / `finish_test_view`) AND when mood is recorded (in `record_mood`). This ensures mood-based achievements unlock even if the user records mood manually later.

### 61. Consecutive Learning Days Computation (Step 9)

`_compute_consecutive_learning_days()` replaces the original login-based computation with a session-based one:

```python
completed_dates = TeachingSession.objects
    .filter(user=user, status=TeachingSession.Status.COMPLETED)
    .values_list("completed_at", flat=True)

unique_dates = sorted(set(d.date() for d in completed_dates if d), reverse=True)

# Streak is active only if most recent learning day is today or yesterday
if (today - most_recent).days > 1:
    return 0

# Count consecutive days backwards from most_recent
consecutive = 1
for i in range(1, len(unique_dates)):
    if (unique_dates[i-1] - unique_dates[i]).days == 1:
        consecutive += 1
    else:
        break
```

**Why session dates, not login dates**: Students may log in without completing a teaching session. The PRD defines "连续学习" as consecutive days with completed teaching, not consecutive login days.

**Why unique dates, not session count**: A student might complete multiple sessions on the same day. The streak counts calendar days, not session count. Duplicate dates from multiple sessions on the same day are deduplicated via `set()`.

**Why today-or-yesterday gate**: If the most recent learning day was 2+ days ago, the streak is broken (returns 0). This correctly handles the case where a student had a 7-day streak last month but hasn't learned recently.

### 62. Mindfulness Achievement Detection (Step 9)

The "正念入门" achievement requires the student to have completed a teaching session about a mindfulness skill, not just any skill:

```python
has_mindfulness_training = TeachingSession.objects.filter(
    user=user, status=TeachingSession.Status.COMPLETED
).filter(
    Q(selected_module__icontains="正念") | Q(selected_skill__icontains="正念")
).exists()
```

**Why icontains on both module and skill**: Module names like "正念认知" and skill names like "正念呼吸" both contain "正念". Checking only `selected_skill` would miss sessions where only the module field references mindfulness.

### 63. Post-Mood Duplicate Prevention (Step 9)

Both `TeachingSession` and `Test` have a `post_mood_id` field that prevents duplicate mood recordings:

```
TeachingSession.post_mood_id  (CharField, max_length=36, blank=True, default="")
Test.post_mood_id             (CharField, max_length=36, blank=True, default="")
```

**View-level guard**: Before rendering the post-mood form, the view checks if `post_mood_id` is already set. If yes, it shows "已记录过" and redirects back. The service layer (`record_post_teaching_mood`, `record_post_test_mood`) sets the field after creating the MoodRecord.

**Why not database unique constraint**: The `post_mood_id` is set after MoodRecord creation, and MoodRecord has no FK back to session/test (it uses a generic `session` FK that's nullable). A model-level `unique_together` on `(user, session, context)` would be too restrictive — a user might legitimately record multiple manual moods. The view guard is the appropriate enforcement point.

### 64. Mood Module URL Structure (Step 9)

```
/mood/                              → mood_home_view (GET)
/mood/record/                       → record_mood_view (GET + POST)
/mood/teaching/<session_id>/        → record_post_teaching_mood_view (GET + POST)
/mood/testing/<test_id>/            → record_post_test_mood_view (GET + POST)
/mood/achievements/                 → achievements_view (GET)
/mood/stats/                        → mood_stats_view (GET, staff-only)
```

All URLs under namespace `"mood"`. Post-mood URLs receive the parent object ID as a URL parameter (not query string), making them bookmarkable and REST-like.

### 65. Achievement View Data Flow (Step 9)

```
achievements_view (GET /mood/achievements/)
  → services.get_user_achievements(request.user)
      → ensure_achievements_exist()  ← lazy-create any new achievement definitions
      → UserAchievement.objects.filter(user=user)  ← get unlocked set
      → Achievement.objects.filter(is_active=True)  ← get all definitions
      → _gather_user_stats(user)  ← aggregate all stats
  → render achievements.html with:
      achievements (list with unlocked + unlocked_at)
      unlocked_count, total_count
      total_trainings, successful_trainings, consecutive_learning_days
```

The template renders 4 stat cards (总训练次数, 成功训练, 连续学习天数, 成就解锁) and a 2-column grid of achievement cards. Locked achievements are shown at 50% opacity with a lock icon; unlocked achievements show the unlock timestamp.

The same `_gather_user_stats()` function serves both achievement checking and the stats display, ensuring the numbers shown to the user match the numbers used for achievement evaluation.

### 66. Retest Detection for Achievement (Step 9)

The "重新出发" (comeback) achievement detects when a student fails a test on attempt 1 and passes on a later attempt:

```python
session_test_map = {}
for t in all_tests:
    sid = t.session_id if t.session_id else str(t.session.session_id)
    session_test_map.setdefault(sid, []).append(t)

for tests in session_test_map.values():
    if len(tests) >= 2:
        failed_first = any(not t.passed and t.attempt_no == 1 for t in tests)
        passed_later = any(t.passed and t.attempt_no > 1 for t in tests)
        if failed_first and passed_later:
            has_retest_passed = True
            break
```

**Why group by session**: A student might fail test #1 for session A (attempt 1) and pass test #1 for session B (attempt 1). That's not a comeback — it's a fresh test for a different session. The achievement requires the same session to have multiple test attempts with the first failed and a later one passed.

**attempt_no tracking**: Each retest creates a new Test record with incremented `attempt_no`. This preserves the full history — all attempts are traceable, and the achievement check uses `attempt_no` to distinguish first from later attempts.

### 67. Centralized Risk Services — Single Source of Truth (Step 10)

Before Step 10, risk detection logic was duplicated across `teaching/services.py`, `testing/services.py`, and `risk/services.py` (empty stub). After Step 10, `risk/services.py` is the single source of truth — both teaching and testing import and delegate to it.

**`risk/services.py`** — Centralized risk detection (230 lines, 8 functions, 3 keyword lists):

```
risk/services.py
├── HIGH_RISK_KEYWORDS (18)          # Explicit self-harm/suicide keywords
├── MODERATE_RISK_KEYWORDS (7)       # Moderate risk keywords (despair, harm intent)
├── _MODERATE_CONCERN_INDICATORS (5) # Emotional distress indicators (not in keyword lists)
├── check_keyword_risk(text)         # → (triggered, keywords_found)
├── has_moderate_concern(text)       # → bool
├── should_assess_risk(text)         # → bool (keyword OR moderate concern)
├── _classify_detection_source()     # → "keyword" / "ai" / "both"
├── create_risk_event(...)           # → RiskEvent instance
├── stop_session_for_risk(...)       # → stops session, creates system message
├── process_risk_check(...)          # → risk_dict | None (teaching flow)
└── process_test_risk_check(...)     # → risk_dict | None (testing flow)
```

**Delegation pattern** — Callers re-export for backwards compatibility:

```python
# teaching/services.py
from risk.services import check_keyword_risk  # re-export
def process_risk_check(session, user, text, context):
    from risk.services import process_risk_check as _do_check
    return _do_check(session, user, text, context)

# testing/services.py
from risk.services import check_keyword_risk  # re-export
def process_test_risk(test, user, text, recent_answers):
    from risk.services import process_test_risk_check as _do_check
    return _do_check(test, user, text, recent_answers)
```

### 68. Dual-Channel Risk Detection Model (Step 10, Optimized Step 13)

Both `process_risk_check` (teaching) and `process_test_risk_check` (testing) implement dual-channel detection. After the Step 13 performance optimization, the teaching flow uses a **conditional dual-channel** pattern:

```
Every message text
    ├── Channel 1: Keyword matching (fast, synchronous, always runs)
    │       check_keyword_risk(text) → (triggered, keywords[])
    │
    ├── IF keyword_triggered: Channel 2 (separate LLM call, safety-critical path)
    │       run_risk_assessment(user_message, context, keywords) → RiskAssessment
    │
    └── IF NOT keyword_triggered: Risk embedded in teaching LLM call
            generate_teaching_content(include_risk_assessment=True)
            → TeachingContent (with risk_level, should_stop_session, risk_reasoning)
            (Saves 1 LLM API round-trip, ~11s for normal messages)
```

**Key invariant**: When keywords trigger, the AI channel still runs as a separate call (dual-channel preserved for safety-critical paths). When keywords don't trigger (~99% of messages), risk assessment fields are populated inline by the teaching LLM call — the schema has default values for backwards compatibility.

Only when BOTH channels find nothing:
    triggered == False AND ai_risk_level == "无"
    → return None (no risk, no event created)

If either channel flags concern:
    → create_risk_event(detection_source=...)
    → if should_stop_session: stop_session_for_risk() / terminate test
    → return risk_dict

**Detection source classification** via `_classify_detection_source(keyword_triggered, ai_risk_level)`:

| keyword_triggered | ai_risk_level | Result |
|-------------------|---------------|--------|
| True | "高" | `"both"` |
| False | "高" | `"ai"` |
| True | "无" or "中" | `"keyword"` |

When AI alone flags high risk (`detection_source="ai"`), this represents the semantic channel independently detecting risk that keywords missed — the core value of dual-channel architecture.

### 69. HX-Redirect Risk Popup Pattern (Step 10, Optimized Step 13)

When high-risk content is detected during an HTMX request, the standard `HX-Redirect` header triggers a full-page redirect to the risk popup:

**Teaching flow** (`teaching/views.py::send_message_view`) — optimized path:

```python
# Channel 1: Fast keyword check always runs first
keyword_triggered, _keywords = check_keyword_risk(student_text)

if keyword_triggered:
    # Safety-critical path: separate AI risk call (dual-channel preserved)
    risk_result = services.process_risk_check(...)
    if risk_result and risk_result.get("should_stop_session"):
        return HX-Redirect to /risk/popup/

# Normal path: risk assessment merged into teaching LLM call
response_data = services.generate_teaching_response(
    ..., include_risk_assessment=True
)

# Handle risk from merged response
if response_data.get("should_stop_session"):
    create_risk_event(detection_source="ai", ...)
    stop_session_for_risk(...)
    return HX-Redirect to /risk/popup/
```

**Testing flow** (`testing/views.py::answer_question_view`):
```python
risk_result = services.process_test_risk(test, request.user, selected_text, risk_context)
if risk_result and risk_result.get("should_stop_session"):
    response = HttpResponse(status=HTTPStatus.NO_CONTENT)
    response["HX-Redirect"] = "/risk/popup/"
    return response
```

Both flows follow the identical pattern:
1. Call risk service (which runs dual-channel detection)
2. If `should_stop_session` → return HTTP 204 with `HX-Redirect` header
3. HTMX client-side library intercepts the redirect header and navigates the full page to `/risk/popup/`
4. Risk popup shows hotline numbers (PRD §6.9.5: 12356, 010-82951332), contact teacher prompt, immediate danger warning

**Why `HTTP 204 No Content`**: The response body is empty since the page will be replaced. `204` signals success-without-body, which HTMX interprets correctly. Using `302` with a `Location` header would work for standard HTTP but HTMX specifically checks for `HX-Redirect` on non-3xx responses for client-side navigation.

### 70. Risk Popup Template (Step 10)

`templates/risk/popup.html` — Full-page centered card with Tailwind CSS:
- Red header: "⚠️ 风险提示"
- Body: explanation text, "联系身边可信任的成年人" prompt, hotline list in yellow info box (12356, 010-82951332, 800-810-1117)
- Footer: "返回教学首页" (red button) + "返回首页" (gray button)
- Extends `base.html` — consistent site chrome, authenticated user can navigate away

Content matches PRD §6.9.5 requirements: national hotline 12356, Beijing hotline 010-82951332, landline 800-810-1117, immediate danger warning text.

### 71. Session Stop by Risk — System Message & Status Transition

When risk is confirmed and `should_stop_session=True`, `stop_session_for_risk()` performs an atomic sequence:

1. `session.status = TeachingSession.Status.STOPPED_BY_RISK`
2. `session.completed_at = timezone.now()`
3. `session.save(update_fields=["status", "completed_at"])`
4. `ChatMessage.objects.create(role=SYSTEM, content="[系统] 检测到高风险内容，会话已自动中止。如需帮助，请联系专业人士。")`

For testing, `process_test_risk_check` sets `test.status = Test.Status.USER_TERMINATED` — tests interrupted by risk share the same termination status as user-initiated termination, with the RiskEvent providing the audit trail for why it was terminated.

### 72. Architecture Update — Section 56 Correction

Section 56 (Risk Detection During Testing, Step 8) previously stated that AI assessment only ran when keywords triggered. This was the pre-dual-channel behavior. With Step 10's dual-channel model, AI semantic assessment always runs on every message regardless of keyword match results. The flow described in §68 supersedes all earlier risk detection descriptions.

### 73. media_app — File Structure and Responsibilities (Step 11)

`media_app/` is a dedicated Django app for image generation (MiniMax), TTS (Volcengine 豆包语音合成模型2.0), and ASR (Volcengine). It is separate from teaching/testing to avoid circular imports — image/TTS/ASR are cross-cutting services consumed by both teaching and testing flows:

```
media_app/
├── __init__.py
├── apps.py              # MediaAppConfig (AppConfig)
├── models.py            # 3 metadata log models (no binary file storage)
├── services.py           # Image (MiniMax) + TTS/ASR (Volcengine) API clients
├── views.py              # 3 endpoints returning HTMX/audio/JSON
├── urls.py               # Namespace "media", 3 URL patterns
├── admin.py              # 3 read-only admin classes
├── tests.py              # 62 tests in 11 classes
└── migrations/
    └── 0001_initial.py   # Applied with --fake (MongoDB)
```

**API key sharing**: Image generation uses `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`. TTS and ASR share `VOLCENGINE_API_KEY` (set via `.env`). TTS additionally requires `VOLCENGINE_TTS_APP_ID` and `VOLCENGINE_TTS_CLUSTER` from the same volcengine speech app.

**Error model**: `ConfigurationError(Exception)` for missing API keys; `APIError(Exception)` for non-200 status, timeout, and connection errors. Views catch both and return user-facing messages without crashing the session.

### 74. Image Generation — Service and Data Flow (Step 11, Updated 2026-05-14)

**Service**: `media_app/services.py::generate_image(prompt, model="image-01", n=1, aspect_ratio="1:1")`

**Default model** (`DEFAULT_IMAGE_MODEL`): `"image-01"` (corrected from `"image-01-live"` on 2026-05-13 — the user's Token Plan Hs_plus has 0 quota for `image-01-live`).

Calls `POST {MINIMAX_BASE_URL}/v1/image/generation` with Bearer token auth and JSON body:
```python
{"model": "image-01", "prompt": prompt, "n": n, "aspect_ratio": aspect_ratio, "prompt_optimizer": True}
```

`aspect_ratio` replaces former `size` parameter (MiniMax API uses `aspect_ratio`, not `size`). `prompt_optimizer: True` enables server-side prompt enhancement for better image quality.

Returns `{"urls": [...], "model": "...", "usage": {...}}`. View extracts `urls[0]` as the temporary image URL.

**Two integration points**:

1. **Teaching scene** — Button `🎨 生成教学配图` in teaching sidebar. JS `DBT_Image.generate()` POSTs to `/media/image/generate/` with `source="teaching_scene"` and `session_id`. View returns HTMX fragment (`<img src="...">`) rendered into `#teaching-image-area`.

2. **Test question illustration** — Two paths:
   - **Auto (Celery, staggered Step 14)**: `generate_test_questions_async` dispatches `generate_test_question_image_async` for questions with `image_prompt` using `apply_async(countdown=i*3)` to space requests 3s apart. Image URL saved to `TestQuestion.temporary_image_url`. Frontend polls `question_image_status_view` every 3s until ready.
   - **Manual (async)**: "生成配图" button POSTs to `/testing/question/<id>/generate-image/`, which dispatches Celery task and returns polling spinner. For questions without `image_prompt`, a fallback prompt is constructed from question text.
   - **Internal retry (Step 14)**: `generate_image()` in `media_app/services.py` retries transient HTTP errors (429, 502, 503, 529) with exponential backoff (2s → 4s → 8s, max 3 retries) before the Celery-level retry (30s delay) is triggered.
   - **Manual (sync, regen)**: "🔄 重新生成配图" on existing images calls `DBT_Image.generate()` → synchronous `/media/image/generate/` (viable with 120s gunicorn timeout).

**Data flow** (async path):
```
Browser: hx-post to /testing/question/<id>/generate-image/
  → testing.views.generate_question_image_view
  → generate_test_question_image_async.delay(question_id)    # fire-and-forget via Redis
  → Celery worker picks up task
  → media_app.services.generate_image(prompt)
  → MiniMax API POST /v1/image/generation
  → TestQuestion.temporary_image_url updated in DB
  → Browser polls /testing/question/<id>/image-status/ every 3s
  → Image <img> returned when URL is set
```

**Data flow** (sync path, for regen):
```
Browser JS (DBT_Image.generate)
  → POST /media/image/generate/ (HTMX, X-Requested-With)
  → media_app.views.generate_image_view
  → media_app.services.generate_image(prompt)
  → MiniMax API POST /v1/image/generation
  → ImageGenerationLog.objects.create(status="success", ...)
  → Returns HTMX fragment: <img src="..." class="...">
```

**PRD compliance**: Image files are NOT persisted. Only the temporary CDN URL (which expires) is stored in `ImageGenerationLog.temporary_image_url` and `TestQuestion.temporary_image_url`. The log preserves prompt, model, status, and timestamp for audit.

### 74b. Async Image Generation via Celery (Updated 2026-05-14)

**Teaching auto-generated images** (from `image_prompt` in AI responses):

```
generate_teaching_content() returns image_prompt
  → _start_image_generation(session, image_prompt)
  → generate_image_async.delay(session_id, image_prompt)    # fire-and-forget via Redis
  → Celery worker picks up task
  → media_app.services.generate_image(prompt)
  → MiniMax API POST /v1/image/generation
  → ChatMessage.image_url updated directly in DB
```

**Test question images** (auto + manual):

```
Path A — Auto (on test creation):
  generate_test_questions_async(test_id)
    → generate_and_save_questions(test, user, session)
    → LLM returns image_prompt per question → saved to TestQuestion.image_prompt
    → for each question with image_prompt:
        generate_test_question_image_async.delay(question_id)
    → Celery worker: generate_image(prompt) → write temporary_image_url to TestQuestion
    → Frontend: HTMX polls /testing/question/<id>/image-status/ every 3s
    → Image auto-displays when ready

Path B — Manual (user clicks "生成配图"):
  hx-post /testing/question/<id>/generate-image/
    → if no image_prompt: construct fallback from question text, save
    → generate_test_question_image_async.delay(question_id)
    → return polling spinner → auto-display when ready
```

**Tasks**:
- `media_app/tasks.py::generate_image_async` — `@shared_task(bind=True, max_retries=2, default_retry_delay=10)`. Writes `image_url` to latest assistant ChatMessage.
- `testing/tasks.py::generate_test_questions_async` — `@shared_task(bind=True, max_retries=2, default_retry_delay=10)`. Generates 5 questions, dispatches image tasks with staggered countdown (0s, 3s, 6s, 9s, 12s) via `apply_async(countdown=i*3)`.
- `testing/tasks.py::generate_test_question_image_async` — `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`. Writes `temporary_image_url`, `image_model`, `image_generated_at` to TestQuestion. Underlying `generate_image()` has its own internal retry for transient HTTP errors (429/502/503/529), so the 30s Celery retry only fires on persistent failures.

**Why Celery over synchronous API calls**:
- Survives gunicorn worker restarts (task re-queued by Redis broker)
- Doesn't block gunicorn workers during image API call (~25s)
- Retry logic is declarative via decorator parameters
- Task visible in Celery monitoring (Flower or `celery inspect`)
- Frontend polls independently — user can continue answering questions while images generate

### 75. TTS Service with Auto-Play Toggle Architecture (Step 11)

**Provider**: Volcengine (火山引擎) 豆包语音合成模型2.0 (migrated from MiniMax, 2026-05-12).

**Service**: `media_app/services.py::synthesize_speech(text, model="volcengine-tts", voice="", speed=1.0, vol=1.0, return_audio_bytes=True)`

Calls `POST https://openspeech.bytedance.com/api/v1/tts` with `Authorization: Bearer;{VOLCENGINE_API_KEY}` header and JSON body including `app.appid`, `app.token`, `app.cluster`, `audio.voice_type`, `audio.encoding`, `audio.rate`, `audio.speed_ratio`, `audio.volume_ratio`, `request.text`, `request.reqid`. Text is limited to 3000 chars (first) then trimmed to 1000 UTF-8 bytes to comply with volcengine's 1024-byte limit. Audio is returned as base64 in the response `data` field, decoded to bytes in-memory.

**View**: `media_app/views.py::synthesize_speech_view` — Two response modes:
- **Binary mode** (default): Returns `audio/mpeg` binary via `HttpResponse(audio_bytes, content_type="audio/mpeg")`. Audio is proxied through Django.
- **JSON fallback**: Returns `{"audio_url": "...", "message_id": "..."}` when binary data is unavailable.

Text is truncated to 3000 characters, then further trimmed to 1000 UTF-8 bytes before the API call (volcengine TTS V1 limit).

**Auto-play architecture** — Dual-mode playback:

```
Manual playback (always available):
  User clicks 🔊 button → DBT_TTS.play(text, messageId)
    → Fetch POST /media/tts/synthesize/ → audio/mpeg blob
    → new Audio(url).play()

Auto-play (gated by toggle):
  HTMX swaps messages_partial.html into #chat-messages
  → <script>DBT_TTS.autoPlayLatest();</script> fires
  → autoPlayLatest() checks localStorage dbt_tts_autoplay
      ON (default):  finds last [data-role="assistant"] →
                      extracts text → DBT_TTS.play(text, messageId)
      OFF:            no-op
```

**Toggle UI** (`session.html:244-252`): Slider switch in chat header with id `tts-autoplay-toggle`. On change, calls `DBT_TTS.toggleAutoPlay()` which flips `localStorage.dbt_tts_autoplay` and syncs the label ("自动播报" / "已关闭").

**Toggle state lifecycle**:
```
Page load → DOMContentLoaded → _syncToggleUI() reads localStorage → sets checkbox
User toggles → toggleAutoPlay() writes localStorage → _syncToggleUI()
HTMX swap → autoPlayLatest() reads localStorage → plays or skips
```

**Key invariant**: The toggle ONLY controls auto-play. Manual 🔊 buttons always work regardless of toggle state. This means a user with auto-play OFF can still click 🔊 to hear individual messages.

### 76. ASR Service with Recording Flow (Step 11)

**Service**: `media_app/services.py::transcribe_audio(audio_bytes, audio_format="wav", model="")`

Calls volcengine ASR API via submit/poll pattern (same `https://openspeech.bytedance.com` platform as TTS). Returns `{"transcribed_text": "...", "model": "volcengine-bigasr", "usage": {...}}`.

**Recording flow** (client-side MediaRecorder API):
```
User clicks 🎤 (mic button in chat input)
  → DBT_ASR.start()
  → navigator.mediaDevices.getUserMedia({audio: true})
  → new MediaRecorder(stream, {mimeType: "audio/webm;codecs=opus"})
  → Recording starts; button shows ⏹ (red)
  → User clicks ⏹ to stop
  → DBT_ASR.stop() → MediaRecorder.onstop
  → Fetch POST /media/asr/transcribe/ with audio Blob (FormData)
  → services.transcribe_audio(audio_bytes, format="webm")
  → Volcengine API POST submit + poll query
  → AudioTranscriptionLog created (transcribed_text, audio_duration_ms)
  → Response: {"success": true, "text": "transcribed text"}
  → JS fills chat input (asr-result element); user reviews before sending
```

**Safety design**: ASR transcription fills the chat input field but does NOT auto-submit. The user must review (ASR can hallucinate) and manually click "发送". This prevents accidental submission of incorrectly transcribed content.

**Audio duration estimation**: Since MediaRecorder doesn't expose duration directly, the view estimates it from `audioChunks` total byte size assuming the recorded MIME type's approximate bitrate.

**PRD compliance**: Raw audio bytes are processed in memory and discarded after transcription. Only `transcribed_text`, `model`, and `audio_duration_ms` are persisted in `AudioTranscriptionLog`.

**Fallback structure**: ASR uses volcengine (火山引擎) as the primary provider. TTS also uses volcengine (豆包语音合成模型2.0), sharing the same platform and API key. Error messages reference volcengine for both services.

### 77. Frontend JavaScript Architecture (Step 11)

`static/js/media.js` — Self-contained IIFE with 3 public modules and private helpers:

```
window.DBT_TTS           # Text-to-speech playback
  .play(text, msgId)     # Fetch POST /media/tts/synthesize/ → play audio blob
  .stop()                # Pause current audio
  .isAutoPlayEnabled()   # Read localStorage dbt_tts_autoplay
  .toggleAutoPlay()      # Flip localStorage + sync checkbox UI
  .autoPlayLatest()      # If auto-play ON, find last assistant msg + play

window.DBT_ASR           # Automatic speech recognition
  .isSupported()         # Check getUserMedia + MediaRecorder availability
  .start(onStart, onErr) # Request mic, start MediaRecorder
  .stop()                # Stop recording → transcribe via /media/asr/transcribe/
  .isRecording()         # → bool

window.DBT_Image         # Image generation
  .generate(prompt, targetId, extraParams)
                         # POST /media/image/generate/ → inject HTML into target

Private helpers:
  _getAutoPlay()         # localStorage.getItem("dbt_tts_autoplay") !== "false"
  _setAutoPlay(bool)     # localStorage.setItem(...)
  _syncToggleUI()        # Sync checkbox on DOMContentLoaded
  _playAudioBlob(blob)   # URL.createObjectURL → new Audio → play
  _playAudioUrl(url)     # new Audio(url) → play
  _transcribe(blob)      # POST audio blob → fill chat input
```

**Module design**: Each module is a plain object literal on `window`, compatible with inline `onclick` handlers in Django templates. No build step, no bundler, no JS framework dependency beyond HTMX (which is loaded separately).

**localStorage key**: `dbt_tts_autoplay` — `"true"` (default, auto-play ON) or `"false"` (auto-play OFF). Read on page load via `DOMContentLoaded` listener, written on toggle, read on every HTMX swap. Not a server-side preference — deliberately client-only since it's a per-device UX setting.

**data-role pattern**: Chat message `<div>` elements carry `data-role="{{ m.role }}"` (values: "user", "assistant", "system"). This allows `autoPlayLatest()` to find the last assistant message with `querySelector('[data-role="assistant"]')` without per-message JS injection or template-level iteration.

### 78. Template Integration Points (Step 11)

All template changes for Step 11 follow the same integration pattern:

**base.html** — Single script include:
```html
<script src="/static/js/media.js"></script>
```
Placed before closing `</body>` tag, after HTMX and Alpine.js. All other templates inherit this.

**session.html (teaching phase)** — 4 integration points:
1. **TTS toggle** (line 244-252): Slider switch in chat header bar, synced to `localStorage`
2. **data-role** (line 258): `data-role="{{ m.role }}"` on each chat bubble for JS detection
3. **🔊 TTS buttons** (line 262-265): On every assistant message bubble, calls `DBT_TTS.play()`
4. **🎤 ASR button** (line 283-286): In chat input form, calls `DBT_ASR.start()`/`stop()`
5. **🎨 Image button** (line 232-235): In teaching sidebar, calls `DBT_Image.generate()`

**session.html (terminal state)** — TTS buttons on conversation history (line 94-98), same pattern as teaching phase but without toggle (terminal = no new messages to auto-play).

**messages_partial.html** — HTMX partial returned by `send_message_view`:
1. `data-role` on all messages (line 4)
2. 🔊 buttons on AI messages (line 8-11)
3. `<script>DBT_TTS.autoPlayLatest();</script>` at end (line 21) — triggers after HTMX swap

**test.html** — Image display for test questions:
1. Existing image shown if `question.temporary_image_url` is set
2. 🎨 "生成配图" button calls `DBT_Image.generate()` with `source="test_illustration"` and `test_question_id`

**answer_partial.html** — Image shown in answer review when `result.temporary_image_url` exists.

### 79. Media Log Models and Admin (Step 11)

Three metadata log models — all use `gen_uuid()` for primary keys and `db_table` naming for MongoDB compatibility:

**ImageGenerationLog**:
| Field | Type | Purpose |
|-------|------|---------|
| `prompt` | TextField | The generation prompt |
| `model` | CharField(50) | MiniMax model (image-01) |
| `temporary_image_url` | URLField(500) | CDN URL (expires) |
| `status` | CharField(20) | success / error |
| `error_message` | TextField | API error detail |
| `source` | CharField(30) | teaching_scene / test_illustration / manual |
| `session` | FK → TeachingSession | For teaching scenes |
| `test_question` | FK → TestQuestion | For test illustrations |
| `user` | FK → settings.AUTH_USER_MODEL | Who requested |
| `created_at` | DateTimeField | auto_now_add |

**AudioSynthesisLog**:
| Field | Type | Purpose |
|-------|------|---------|
| `text` | TextField | Text sent to TTS |
| `model` | CharField(50) | TTS model label (volcengine-tts) |
| `voice` | CharField(50) | Voice ID |
| `temporary_audio_url` | URLField(500) | CDN URL (only when binary download fails) |
| `status` | CharField(20) | success / error |
| `error_message` | TextField | API error detail |
| `message` | FK → ChatMessage | Which chat message this audio is for |
| `user` | FK → AUTH_USER_MODEL | |
| `created_at` | DateTimeField | |

**AudioTranscriptionLog**:
| Field | Type | Purpose |
|-------|------|---------|
| `transcribed_text` | TextField | ASR result text |
| `model` | CharField(50) | MiniMax model |
| `audio_duration_ms` | PositiveIntegerField | Estimated recording length |
| `status` | CharField(20) | success / error |
| `error_message` | TextField | API error detail |
| `session` | FK → TeachingSession | |
| `user` | FK → AUTH_USER_MODEL | |
| `created_at` | DateTimeField | |

**Admin** — All 3 admin classes follow the read-only audit pattern (same as `AdminOperationLogAdmin` and `ReportAccessLogAdmin`):
```python
has_add_permission = False
has_change_permission = False
has_delete_permission = False
```
Each shows: ID preview (first 12 chars), user, model, status, created_at. Filterable by status and created_at. Searchable by prompt/text. `readonly_fields` for all fields.

### 80. URL Structure — Media Endpoints (Step 11)

All media endpoints live under `/media/` namespace (registered in root URLconf before health check):

```python
# dbt_platform/urls.py
path("media/", include("media_app.urls"))

# media_app/urls.py
app_name = "media"
urlpatterns = [
    path("image/generate/", views.generate_image_view, name="generate_image"),
    path("tts/synthesize/", views.synthesize_speech_view, name="synthesize_speech"),
    path("asr/transcribe/", views.transcribe_audio_view, name="transcribe_audio"),
]
```

**Auth requirements**:
| Endpoint | Decorator | Reason |
|----------|-----------|--------|
| `/media/image/generate/` | `@profile_required` | Image gen is in teaching/testing contexts |
| `/media/tts/synthesize/` | `@login_required` | TTS only plays AI messages (no profile check needed) |
| `/media/asr/transcribe/` | `@login_required` + `@csrf_exempt` | Audio upload from JS fetch needs CSRF exemption; `X-Requested-With` header provides CSRF-equivalent protection |

**Response types**:
| Endpoint | Method | Content-Type | Response Body |
|----------|--------|-------------|---------------|
| image/generate/ | POST | text/html | HTMX fragment (`<img>` tag or error div) |
| tts/synthesize/ | POST | audio/mpeg or application/json | Binary audio or `{"audio_url": "..."}` |
| asr/transcribe/ | POST | application/json | `{"success": true, "text": "..."}` or `{"success": false, "error": "..."}` |

### 81. TTS Auto-Play — localStorage Persistence Pattern (Step 11)

The TTS auto-play toggle is the first client-side preference in the DBT platform. Its architecture follows a deliberate pattern:

**Why localStorage, not server-side**:
- Auto-play is a per-device UX preference, not user data
- No model/database migration needed
- Instant toggle response (no server round-trip)
- Survives page reloads within the same browser
- Natural per-device behavior (auto-play ON on phone, OFF on shared computer)

**Persistence contract**:
```
Key:        dbt_tts_autoplay
Type:       string ("true" | "false")
Default:    "true" (auto-play ON) — absence of key ≡ "true"
Scope:      per-origin, per-browser
Lifetime:   until user clears browser data or toggles off
```

**Read path**: `_getAutoPlay()` returns `localStorage.getItem("dbt_tts_autoplay") !== "false"`. The negation means: missing key (first visit) → default true. Explicit `"false"` → false. Any other value → true.

**Write path**: `_setAutoPlay(enabled)` calls `localStorage.setItem("dbt_tts_autoplay", enabled)`. Only called from `toggleAutoPlay()`, never from `autoPlayLatest()` (read-only).

**UI sync**: `_syncToggleUI()` reads localStorage state and sets the checkbox `checked` property. Called once on `DOMContentLoaded` (page load) and after every toggle. The checkbox is the source of truth for display; localStorage is the source of truth for behavior.

**Separation of concerns**:
- `autoPlayLatest()` — consumer: reads localStorage, decides whether to play
- `toggleAutoPlay()` — mutator: writes localStorage, syncs UI
- `isAutoPlayEnabled()` — public accessor: allows other code to check state
- `_syncToggleUI()` — internal: bridges localStorage ↔ checkbox DOM

### 82. Report Data Aggregation Service (Step 12)

`reports/services.py::get_student_report_data(student)` is the single entry point for all student report data. It aggregates across 6 model types:

```
get_student_report_data(student)
  ├── _get_profile(student)            → UserProfile (OneToOneField)
  ├── MoodRecord.objects.filter(...)    → mood_history + mood_svg_chart
  ├── TeachingSession.objects.filter()  → skill_counts (Counter on selected_skill)
  ├── Test.objects.filter()             → test_summary + overall_pass_rate + retest_count
  ├── UserAchievement.objects.filter()  → achievements (select_related Achievement)
  ├── RiskEvent.objects.filter()        → risk_count
  └── _build_summary(...)               → narrative summary text
```

**Profile handling** (`_get_profile`): Uses `student.profile` (Django OneToOneField reverse accessor) wrapped in try/except. Returns empty defaults when no UserProfile exists (student registered but hasn't completed questionnaire yet). This avoids 500 errors on the report page.

**Mood SVG chart** (`_render_mood_chart_svg`): Generates inline SVG with:
- Polyline connecting all mood data points (value 1-5 mapped to Y coordinates, inverted so 5=top)
- Circles on first, last, min (red), and max (green) data points
- Y-axis guide lines at levels 1-5
- Title text showing record count
- Single-point fallback (just a dot, no polyline)
- Empty-string fallback when no mood data

All SVG rendering is pure Python string formatting — no external chart library. WeasyPrint renders SVG natively in PDF output.

**Summary builder** (`_build_summary`): Generates a multi-sentence Chinese narrative from quantitative data:
- Engagement level (session + test counts)
- Performance assessment (pass rate tiered: ≥80% good, ≥60% room to improve, <60% needs practice)
- Mood trend (first-half average vs second-half average: ±0.5 threshold for "improving"/"declining"/"stable")
- Skills, achievements, risk events counts
- Special message when student has no activity

### 83. Student Report Template — 7-Section Layout (Step 12)

`templates/reports/student_report.html` — Full Tailwind-styled report with 7 sections in order:

1. **Header**: Student name, back link, "导出 PDF" button
2. **基础信息** (Basic Info): Grid with username, registration date, gender, age, grade, report generation time. Optional second row for hobby tags and concern tags from questionnaire.
3. **报告摘要** (Summary): Blue gradient highlighted box with narrative summary text (`whitespace-pre-line` for line breaks)
4. **Overview Cards**: 4-column grid — completed sessions, test count, achievement count, overall pass rate (color-coded)
5. **情绪变化** (Mood): SVG chart centered above a scrollable mood history table (time, emoji, context tag, note)
6. **技能学习次数** (Skills): Horizontal bar chart using CSS `width` percentage (widthratio template tag)
7. **测试记录** (Tests): Table with time, skill, score, pass/fail tag, retest attempt indicator
8. **成就解锁** (Achievements): 2-column grid of achievement cards with icon, name, description, unlock date

### 84. PDF Report Architecture — WeasyPrint + Lazy Import (Step 12)

`reports/views.py::student_report_pdf_view` generates PDF via weasyprint with lazy import:

```python
def student_report_pdf_view(request, student_id):
    # ... auth checks ...
    report_data = get_student_report_data(student)
    html = render(request, "reports/student_report_pdf.html", report_data).content.decode("utf-8")
    from weasyprint import HTML  # lazy import
    doc = HTML(string=html, base_url=request.build_absolute_uri("/"))
    pdf_bytes = doc.write_pdf()
    _log_report_access(user, student, "export", "individual_report", export_format="pdf")
    return HttpResponse(pdf_bytes, content_type="application/pdf", ...)
```

**Why lazy import**: `weasyprint` depends on system libraries (libpango, libcairo, libgdk-pixbuf, etc.). If these are missing, a top-level `from weasyprint import HTML` would crash the entire Django process on startup. Lazy import inside the view function means:
- Module-level imports succeed regardless of system deps
- Only the PDF endpoint fails when deps are missing
- All other views (dashboard, student report, export) work independently

**PDF template** (`student_report_pdf.html`): Standalone HTML document (no `{% extends %}`) with:
- `@page { margin: 2cm; size: A4; }` for print layout
- Inline CSS (no Tailwind — weasyprint can't resolve Tailwind classes)
- All 7 sections from the HTML report, adapted for print typography
- `page-break-inside: avoid` on achievement cards
- SVG chart rendered inline (weasyprint natively renders SVG)
- Standard system fonts (Noto Sans SC, SimSun)

### 85. Admin Data Export — User-Centered Aggregation (Step 12)

`export_app/services.py::aggregate_user_data(user)` gathers the complete data chain for one user:

```
aggregate_user_data(user)
  ├── User fields (id, username, role, date_joined)
  ├── UserProfile fields (gender, age, grade, hobbies, troubles, other_*)
  ├── TeachingSession[] — each with:
  │     ChatMessage[].values(message_id, role, content, modality, created_at)
  ├── Test[] — each with:
  │     TestQuestion[].values(question_text, options, correct_option, user_answer, is_correct, explanation, source_chunk_ids)
  ├── MoodRecord[].values(mood_value, emoji, note, context, created_at)
  ├── RiskEvent[].values(risk_event_id, trigger_text, detection_source, action_taken, follow_up_mode, session_stopped, trigger_time)
  └── UserAchievement[].select_related(Achievement).values(name_cn, description_cn, icon, unlocked_at)
```

All datetime fields are serialized to ISO format strings. The result is a nested dict ready for `json.dumps()`.

**JSON export** (`export_user_json(student)`): `json.dumps(data, ensure_ascii=False, indent=2)` — Chinese characters preserved, pretty-printed.

**CSV export** (`export_user_csv(student)`): Python `csv.writer` with 6 labeled sections:
1. `=== 用户信息 ===` — username, role, registration date, gender, age, grade, hobbies, concerns
2. `=== 教学会话 ===` — session ID, skill, module, status, phase, start/end time, summary
3. `=== 测试记录 ===` — test ID, session ID, attempt, correct/total, passed, status, time
4. `=== 情绪记录 ===` — mood ID, value, emoji, context, note, time
5. `=== 风险事件 ===` — event ID, trigger text, detection source, action, follow-up mode, session stopped, time
6. `=== 成就 ===` — achievement name, description, unlock time

CSV output uses UTF-8 with BOM (`﻿`) for Excel compatibility — without BOM, Excel misinterprets Chinese characters.

### 86. Export URL Structure and Auth Model (Step 12)

```
/export/                               → export_page_view       (admin only)
/export/user/<user_id>/json/           → export_user_json_view   (admin only)
/export/user/<user_id>/csv/            → export_user_csv_view    (admin only)
/export/users/json/?user_ids=...       → export_users_json_view  (admin only)
/export/users/csv/?user_ids=...        → export_users_csv_view   (admin only)
```

**Auth model**: All export endpoints use `_is_admin(user)` which checks `user.role == "admin" or user.is_staff`. Report viewers and students get HTTP 403. The export page at `/export/` links back to `admin:index` for navigation.

**Bulk filtering**: `?user_ids=` query parameter accepts repeated values (`?user_ids=a&user_ids=b`). When absent, all students are exported. This allows partial exports without re-fetching all data.

### 87. Audit Logging Model Integration (Step 12)

Two existing models (created in Step 3) are now actively written to:

**ReportAccessLog** — Written from `reports/views.py::_log_report_access()`:
- `viewer` = the admin/report_viewer accessing the report
- `viewer_role` = snapshot of viewer's role at access time
- `student` = the student whose report is viewed/exported
- `action_type` = `"view"` (HTML report) or `"export"` (PDF)
- `report_type` = `"individual_report"`
- `export_format` = `"pdf"` (only for export actions)

Created on every `student_report_view` and `student_report_pdf_view` call after auth passes.

**AdminOperationLog** — Written from `export_app/views.py::_log_export()`:
- `admin` = the admin performing the export
- `operation_type` = `"export_data"`
- `target_type` = `"user"` (single) or `"users_bulk"` (bulk)
- `target_id` = user ID (single) or comma-joined IDs (bulk)
- `export_format` = `"json"` or `"csv"`
- `export_scope` = `{"user_count": N}` for bulk exports

Both models have read-only admin classes (Step 3 pattern: `has_add/change/delete_permission = False`).

### 88. Report Viewer Authorization Pattern (Step 12)

The reports app implements a two-tier auth model:

```
dashboard_view / student_report_view / student_report_pdf_view
  ├── user.role == "admin"              → full access (all students)
  ├── user.role == "report_viewer"      → checks ReportViewerAssignment
  │     ├── viewer=user, student_id=X, is_active=True  → authorized
  │     ├── viewer=user, student_id=X, is_active=False → 403
  │     └── no assignment exists                        → 403
  └── user.role == "student" or other   → 403
```

**Dashboard distinction**: Admin sees all students via `User.objects.filter(role="student")`. Report viewer sees only students with active `ReportViewerAssignment` records. Dashboard template shows each student as a clickable card linking to `student_report_view`.

**Inactive assignment**: Setting `is_active=False` immediately revokes access without deleting the assignment record — preserves the audit trail while enforcing current policy.

### 89. Step 12 File Responsibilities Summary

| File | Role |
|------|------|
| `reports/services.py` | Data aggregation hub: gathers profile, mood, sessions, tests, achievements, risk events; builds summary text; renders SVG mood chart |
| `reports/views.py` | 3 views: dashboard (student list), student_report (HTML with all 7 sections), student_report_pdf (weasyprint PDF); auth gating; ReportAccessLog on every view/export |
| `reports/urls.py` | 3 URL patterns under namespace `"reports"` |
| `reports/tests.py` | 37 tests: auth, service aggregation, summary builder, SVG chart, PDF generation, audit logging |
| `reports/models.py` | ReportAccessLog model (existing from Step 3, no changes) |
| `templates/reports/dashboard.html` | Student list for report viewer dashboard |
| `templates/reports/student_report.html` | 7-section HTML report with Tailwind CSS |
| `templates/reports/student_report_pdf.html` | Standalone print-optimized PDF template |
| `export_app/services.py` | User-centered data aggregation; JSON/CSV export with sectioned CSV format |
| `export_app/views.py` | 6 views: export page, single-user JSON/CSV, bulk JSON/CSV; auth gating; AdminOperationLog on every export |
| `export_app/urls.py` | 5 URL patterns under namespace `"export_app"` |
| `export_app/tests.py` | 21 tests: export page auth, JSON/CSV content, bulk filtering, audit logging, service aggregation |
| `templates/export_app/export_page.html` | Admin export UI with student list and download links |

### 90. Architecture Update — Step 12 Audit Trail Completeness

With Step 12, the DBT platform now has a complete audit trail for the two most sensitive non-student operations:

1. **Report viewing** → `ReportAccessLog` (who viewed which student's report, when, in what role)
2. **Report PDF export** → `ReportAccessLog` (who exported which student's report as PDF, when)
3. **Admin data export (JSON/CSV)** → `AdminOperationLog` (who exported what data, in what format, at what scope)

Both log models use the read-only admin pattern established in Step 3: no add/change/delete from Django admin. The audit trail is append-only from the application's perspective — logs can only be created via view code, never modified or deleted through the admin interface.

Combined with Step 10's RiskEvent logging and Step 9's MoodRecord/Achievement tracking, every significant user interaction and administrative action is now traceable through the admin interface.


---

### §91 `dbt_platform/health_urls.py` — Health Check Logging (Step 13 fix)

The readiness check (`/health/ready/`) probes MongoDB, Redis, Qdrant, and MinIO. Previously, failures were caught silently (the exception message was included in the JSON response but never logged). Step 13 added explicit `logger.error()` calls in all four `except` blocks, using the logger name `dbt_platform.health`. This ensures that operations staff monitoring the log file will see backend failures without needing to poll the JSON endpoint.

**Pattern**: Each check follows `try: ... except Exception as exc: logger.error("... failed: %s", exc); overall = False`.

---

### §92 `risk/services.py` — AI Failure Fallback (Step 13 fix)

The dual-channel risk system (keyword + AI semantic) previously called `run_risk_assessment()` without error handling. If the MiniMax LLM was unavailable, `APIError` propagated to the caller — the teaching view would crash or terminate the session unexpectedly. Step 13 added try/except blocks in both `process_risk_check()` and `process_test_risk_check()`, with fallback behavior:

- **AI raises `APIError`**: Logged at ERROR level, falls back to keyword-only assessment.
- **AI raises unexpected `Exception`**: Logged at ERROR with traceback, same fallback.
- **Fallback logic**: If any `HIGH_RISK_KEYWORDS` match the text, `should_stop = True` (conservative posture — stop session). If only moderate keywords match, create event but don't stop. `ai_risk_level` is set to `"无"` so `detection_source` correctly reflects `"keyword"` rather than falsely claiming `"both"`.
- **Caller protection**: Both `teaching/views.py` and `testing/views.py` already have try/except for `APIError`/`ConfigurationError` around the main LLM calls; the risk fix ensures the risk check itself doesn't fail before the existing try/except scope.

---

### §93 `knowledge_base/services.py:hybrid_search()` — Fault Isolation (Step 13 fix)

`hybrid_search()` was modified to wrap `semantic_search()` in try/except. When Qdrant is unreachable (ConnectionError, timeout, etc.), the exception is logged and `sem_results` defaults to `[]`. The keyword search channel still runs normally. Previously, a Qdrant outage would cause `hybrid_search()` to raise an unhandled exception, making the entire retrieval pipeline unavailable even if MongoDB text search was healthy.

---

### §94 `dbt_platform/tests.py` — Health Check Failure Tests

New test file. 3 test classes, 8 tests. Verifies:
- Each backend being unreachable causes `/health/ready/` to return HTTP 503 with `"status": "degraded"`.
- Each backend failure is logged at ERROR level with the expected message prefix (e.g., `"MongoDB health check failed"`).
- All backends up returns HTTP 200 with all checks `"ok"`.

Uses `patch()` at the imported-module level (`django.db.connections`, `redis.from_url`, `qdrant_client.QdrantClient`, `minio.Minio`) because `health_urls.py` imports these inside the function body at runtime.

---

### §95 `dbt_platform/p0_verification.py` — PRD P0 Compliance Tests

New test file. 8 test classes, 24 tests. Each test maps to a specific PRD P0 requirement:
- **AUTH**: Registration page accessible, login works, password hashed (not plaintext), user data isolation (403 on cross-user access), invite code required.
- **Q**: Questionnaire page accessible, ProfileForm contains gender/age/grade fields.
- **AI**: Teaching home accessible, RAG retriever importable, high-risk content stops session.
- **RAG**: Admin can access knowledge document changelist, chunking service produces chunks, Qdrant client importable.
- **RISK**: Keyword detection works, risk popup accessible, popup contains 12356 hotline.
- **REPORT**: Admin can view student report, PDF export generates valid PDF.
- **EXPORT**: Admin export page accessible, students denied (403).
- **SECURITY**: Report viewing logs ReportAccessLog, data export logs AdminOperationLog, no `localhost`/`127.0.0.1` in frontend source files.

---

### §96 `risk/tests.py` — AI Failure Scenario Tests (added in Step 13)

Two new test classes appended:
- `AIRiskAssessmentFailureTests` (6 tests): Verifies keyword-based fallback when `run_risk_assessment` raises `APIError` or unexpected `RuntimeError`. Tests that high-risk keywords still stop the session, moderate keywords create events without stopping, normal text returns None, and errors are logged.
- `TestRiskCheckFailureTests` (4 tests): Same coverage for `process_test_risk_check()` in the testing context — verifies test termination on high-risk keywords, no-op on normal answers, risk event creation, and error logging.

---

### §97 `knowledge_base/tests.py` — Retrieval & Storage Failure Tests (added in Step 13)

Two new test classes appended:
- `RetrievalFailureTests` (3 tests): Tests that semantic search returns `[]` when embedding model failed to load, `hybrid_search` returns keyword-only results when semantic search raises `ConnectionError`, and `log_retrieval` propagates DB errors as expected.
- `StorageFailureTests` (3 tests): Documents current error propagation behavior for MinIO upload/download/delete when `get_minio_client()` raises `ConnectionError`.

---

### §98 SSE Streaming Architecture (Opt 6, 2026-05-11)

Server-Sent Events streaming replaces the old request-response cycle for teaching chat. The pipeline:

```
Browser (Fetch + ReadableStream)
  → Nginx (proxy_buffering off, proxy_read_timeout 120s)
    → Gunicorn/Django (StreamingHttpResponse, text/event-stream)
      → chains.stream_teaching_content() (generator)
        → minimax_chat_completion_stream() (requests.iter_lines, stream=True)
          → MiniMax API (/v1/text/chatcompletion_v2, stream=True)
```

**Key files:**
| Layer | File | Change |
|-------|------|--------|
| LLM Client | `knowledge_base/rag/llm_client.py` | `minimax_chat_completion_stream()` — `stream=True`, iterates `resp.iter_lines()`, yields content deltas, then `[STREAM_DONE]` sentinel, then full accumulated text |
| Prompt | `knowledge_base/rag/prompts.py` | `_STREAMING_TEACHING_SYSTEM` — instructs LLM to output natural Chinese with `<!--META:{json}-->` HTML comment at end instead of pure JSON. `build_streaming_teaching_messages()` builds message list |
| Chain | `knowledge_base/rag/chains.py` | `stream_teaching_content()` — generator: RAG retrieval → build messages → call streaming API → yield `{"type":"content","text":"..."}` SSE events → parse META from full text → yield `{"type":"done","teaching_content":{...}}`. `_parse_streaming_content()` — regex extracts `<!--META:...-->`, returns clean TeachingContent dict |
| View | `teaching/views.py` | `stream_message_view()` — returns `StreamingHttpResponse(text/event-stream)`, creates user ChatMessage, runs streaming chain generator, creates assistant ChatMessage on "done" event, injects `message_id` into response for TTS button |
| URL | `teaching/urls.py` | `path("session/<id>/stream/", stream_message_view, name="stream_message")` |
| Frontend | `static/js/media.js` | `DBT_Stream` object: `send()` — creates user bubble + AI placeholder, fetches SSE stream; `_readStream()` — ReadableStream reader, SSE line parser, dynamic content rendering, META comment stripping, TTS button injection, risk redirect, image gen trigger |
| Template | `templates/teaching/session.html` | Form changed from `hx-post` to `onsubmit="DBT_Stream.send(event, '...')"`; skeleton shimmer indicator controlled by JS |
| Nginx | `docker/nginx.conf` | Location `/teaching/session/` with `proxy_buffering off`, `proxy_cache off`, `gzip off`, `proxy_http_version 1.1`, `proxy_read_timeout 120s` |

**SSE event protocol:**
- `{"type":"content","text":"..."}` — token chunk, frontend appends to bubble
- `{"type":"done","teaching_content":{...}}` — stream complete, ChatMessage saved, TTS button added
- `{"type":"error","message":"..."}` — error, shown in bubble

**META comment format:** `<!--META:{"message_type":"讲解","image_prompt":"...","risk_level":"无","should_stop_session":false,"risk_reasoning":""}-->`

HTML comment is invisible in browser DOM; `_parse_streaming_content()` extracts it server-side. Frontend `_metaRe` regex strips it as a safety net.

---

### §99 Post-Deployment Bug Fixes (2026-05-12)

Three production bugs discovered after deploying the streaming optimization:

**Bug 1: Incomplete deployment — static files + nginx config stale**
- Cause: `docker compose restart web` only restarts gunicorn. Static files in shared volume (`./staticfiles`) and nginx config (bind mount) were not refreshed
- Impact: `media.js` lacked `DBT_Stream` → form submission fell back to browser default GET (page refresh). Nginx lacked `proxy_buffering off` → SSE would be buffered
- Fix: `collectstatic --noinput` + `docker compose restart nginx`
- Prevention: After any static file or nginx config change, run both commands

**Bug 2: DOM ID collision across streaming bubbles**
- Cause: `_readStream()` used `document.getElementById("streaming-text")` — global lookup. When stream 1 completed, only `aiBubble.id` was cleared; child `<span id="streaming-text">` and `<span id="streaming-cursor">` retained their IDs. Stream 2's `getElementById` returned the first (stale) element
- Fix: Changed to `aiBubble.querySelector("#streaming-text")` (scoped to current bubble) + explicit child ID cleanup in all completion/error paths
- Pattern: Always scope DOM queries to the container element, never rely on global IDs for dynamically-created content

**Bug 3: Streaming content formatting**
- Cause: `textContent` on `<span>` collapses `\n` to whitespace and shows all characters literally (including markdown syntax). The streaming prompt had no formatting restrictions
- Fix (2-part):
  1. **Prompt**: Added explicit markdown prohibition + natural paragraph formatting rules to `_STREAMING_TEACHING_SYSTEM`
  2. **Frontend**: `_escapeHtml()` helper + `innerHTML` rendering with `\n` → `<br>` conversion; raw text preserved for TTS playback
- Design note: `innerHTML` is used only for LLM-generated content (trusted source); the `_escapeHtml()` helper still prevents any accidental HTML injection via `createTextNode` → `innerHTML` pattern

---

### §100 HTTP/2 TTS 500 Bug — Nginx HTTP/2 Module Regression (2026-05-13)

**Symptom**: First TTS request after browser page refresh returned HTTP 500 with 141-byte nginx default error body. Subsequent requests succeeded. Only occurred over HTTP/2; HTTP/1.1 requests always worked. Nginx error log was empty (0 bytes) during these failures — the error was generated at the HTTP/2 module level without triggering traditional error logging.

**Root cause**: nginx 1.27.5's **deprecated `listen 443 ssl http2;` syntax** caused the HTTP/2 module to intermittently reject POST requests to `/media/tts/synthesize/`. The `http2` parameter on the `listen` directive was deprecated by nginx — the correct modern syntax is `listen 443 ssl;` + `http2 on;` as a separate directive. When using the deprecated form, nginx's HTTP/2 stream multiplexing would reject the first request on a new connection's stream, returning 500 without ever proxying the request to Django or writing any log entry.

**Why "first request after refresh"**: HTTP/2 multiplexes all requests over a single TCP connection. A page refresh tears down the old connection and establishes a new one. The first TTS request on the new HTTP/2 connection triggered the module-level bug. After the connection stabilised, subsequent requests on the same multiplexed stream succeeded — until the next refresh.

**Diagnostic process**:
1. Added `logger.info()` at `synthesize_speech_view` entry — confirmed Django never received the 500-failing requests
2. Direct HTTP request from nginx container to Django (`curl http://web:8000/media/tts/synthesize/`) — 200 OK, confirmed Django-side code works
3. Disabled HTTP/2 entirely (`listen 443 ssl;` without http2) — TTS worked, confirmed HTTP/2-specific
4. Re-enabled HTTP/2 with corrected `http2 on;` syntax — TTS worked, confirmed the deprecated `listen ... http2` syntax was the root cause

**Fix** (2-part in `docker/nginx.conf`):

1. **Correct HTTP/2 directive**: `listen 443 ssl;` + `http2 on;` (separate directives) instead of deprecated `listen 443 ssl http2;`
2. **Dedicated TTS location** with streaming-compatible proxy settings matching the working SSE endpoint:

```nginx
server {
    listen 443 ssl;
    http2 on;
    ...

    # TTS endpoint — streaming settings for HTTP/2 compatibility
    location /media/tts/ {
        proxy_pass http://web:8000/media/tts/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        proxy_read_timeout 120s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
    }
}
```

**Key settings rationale**:
- `proxy_http_version 1.1` — required for HTTP/2-to-upstream proxying; the default HTTP/1.0 can cause issues with HTTP/2 multiplexing
- `proxy_buffering off` — prevents nginx from buffering the large TTS audio response (599KB+) to temporary files before forwarding
- `proxy_cache off` / `gzip off` — ensures raw binary audio passes through unmodified
- `proxy_set_header Connection ""` — clears the Connection header so nginx can manage keep-alive independently between client (HTTP/2) and upstream (HTTP/1.1)
- `proxy_read_timeout 120s` — matches the TTS API call timeout; volcengine TTS can take 5-10 seconds per synthesis

**Why the SSE location settings work for TTS**: Both SSE streaming and TTS involve relatively long-lived responses (5-14s for SSE, 5-10s for TTS audio) where the client needs to consume data as it arrives. In both cases, `proxy_buffering off` prevents nginx from holding the entire response before forwarding. For HTTP/2 connections, `proxy_http_version 1.1` is essential because HTTP/2 multiplexing requires the proxy to negotiate HTTP/1.1 with the upstream.

**Prevention**:
- Always use `http2 on;` directive, never `listen ... http2` parameter (deprecated since nginx 1.25)
- TTS/large-binary endpoints need `proxy_buffering off` and `proxy_http_version 1.1` for HTTP/2 compatibility
- Bind-mount config changes require nginx container **restart** (not reload) to guarantee propagation — `docker compose restart nginx`

---

### §101 TTS 双层缓存架构 (2026-05-13)

TTS 加载慢的根因是三重串行延迟：TTS API 合成时间 + 全部音频传输到服务器 + 全部音频传输到浏览器。每次播放都是全新的 API 调用，同一段文字被反复合成。

**双层缓存方案** — 服务端 Redis + 前端 Blob URL 互补：

```
用户点击 🔊 / 自动播报触发
  → DBT_TTS.play(text, messageId)
    ├── [Layer 1: Blob Cache] 检查 _blobCache.has(messageId)
    │     ├── HIT → _playAudioBlob(cached.blob, msgId, cached.url) ← 零网络请求
    │     └── MISS ↓
    ├── [Network] POST /media/tts/synthesize/
    │     ├── [Layer 2: Redis Cache] synthesize_speech() 检查 Redis
    │     │     ├── HIT → 直接返回 cached audio_bytes ← 跳过 API 调用
    │     │     └── MISS → 调用火山引擎 TTS API → 合成完成 → 写入 Redis
    │     └── 返回 audio/mpeg 响应
    └── 收到 Blob → 写入 _blobCache → _playAudioBlob()
```

**Layer 1 — 前端 Blob URL 缓存** (`static/js/media.js`):

| 属性 | 值 |
|------|-----|
| 存储位置 | `_blobCache` (Map)，浏览器内存 |
| Key | `messageId` (ChatMessage UUID) |
| Value | `{blob: Blob, url: string}` (Blob + createObjectURL) |
| 上限 | 20 条 (`BLOB_CACHE_MAX`) |
| 驱逐策略 | FIFO — 满时删除最旧条目，`URL.revokeObjectURL()` 释放 |
| 生命周期 | 页面内（刷新后重建） |
| 命中效果 | 零网络请求，即时播放 |

缓存 URL 不释放（onended 检查 `isUrlFromCache`），由缓存持有所有权直到被驱逐。

**Layer 2 — 服务端 Redis 缓存** (`media_app/services.py`):

| 属性 | 值 |
|------|-----|
| 存储位置 | Redis (同一实例，共享 Celery broker / RAG cache) |
| Key | `tts:audio:<sha256(text|voice)>` (前 16 hex chars) |
| Value | 原始 audio_bytes (MP3 binary) |
| TTL | 3600 秒 (1 小时) |
| 命中效果 | 跳过火山引擎 API 调用 (节省 5-15 秒) |

**缓存的 4 个辅助函数**:
- `_get_redis()` — 惰性连接，socket timeout 2s，不可用时返回 None (与 `knowledge_base/services.py` 相同模式)
- `_tts_cache_key(text, voice)` — `sha256(f"{text}|{voice}".encode()).hexdigest()[:16]`，前缀 `tts:audio:`
- `_tts_cache_get(text, voice)` — `client.get(key)`，返回 bytes 或 None
- `_tts_cache_set(text, voice, audio)` — `client.setex(key, TTL, audio)`

**synthesize_speech() 修改点**:
1. **API 调用前** (line ~253): `if return_audio_bytes: cached = _tts_cache_get(text, speaker); if cached: return {...}`
2. **合成完成后** (line ~348): `if return_audio_bytes: _tts_cache_set(text, speaker, audio_bytes)`

**容错设计**:
- Redis 不可用 → `_get_redis()` 返回 None → `_tts_cache_get/set` 均为 no-op → 正常走 API
- 前端 Map 满 → 驱逐最旧 + 释放 URL → 无内存泄漏
- Cache key 包含 voice → 不同音色独立缓存
- 现有测试无需修改 (测试 mock `requests.post`，缓存层透明)

**与已有 Redis 模式的关系**: 本缓存复用了 `knowledge_base/services.py` 中已建立的 Redis 客户端模式 (惰性连接 + graceful degradation)。TTS cache key 使用 `tts:audio:` 前缀，与 RAG cache key (`rag:search:`) 在同一 Redis 实例中共存，互不干扰。

---

### §102 TTS 流式音频传输架构 (2026-05-13)

TTS 首次播放延迟的根本原因是"全部缓冲再返回"模式：火山引擎 TTS API 本身支持流式传输（每个 JSON Line 包含一段 base64 MP3 音频），但之前的 `synthesize_speech()` 收集完全部 chunk 的 `b"".join(audio_chunks)` 后才返回。这意味着浏览器必须等待 **完整 API 合成 + 全部数据传输** 才能开始播放。

**流式方案** — 利用 HTTP chunked transfer encoding + MediaSource API 实现边合成边播放：

```
火山引擎 TTS API (stream=True)
  → JSON Lines: {"code":0, "data":"<base64 mp3>"}
  → base64 decode → yield raw MP3 bytes (generator)
    → Django StreamingHttpResponse (Transfer-Encoding: chunked)
      → Nginx (proxy_buffering off, proxy_http_version 1.1)
        → Browser ReadableStream reader
          → MediaSource SourceBuffer('audio/mpeg', mode='sequence')
            → <audio> element — 首 chunk 到达即开始解码播放
```

**后端 — `stream_synthesize_speech()` 生成器** (`media_app/services.py`):

```python
def stream_synthesize_speech(text, *, voice="", speed=1.0, vol=1.0):
    # 1. Redis cache check — hit: yield cached bytes in 16KB chunks
    cached = _tts_cache_get(text, speaker)
    if cached is not None:
        for i in range(0, len(cached), 16384):
            yield cached[i:i + 16384]
        return

    # 2. Call Volcengine TTS V3 with stream=True
    resp = requests.post(url, json=body, headers=headers, stream=True)

    # 3. Iterate JSON Lines, yield each decoded audio chunk immediately
    all_audio = []
    for line in resp.iter_lines(decode_unicode=True):
        chunk = json.loads(line)
        if chunk["code"] == 0 and chunk.get("data"):
            audio_chunk = base64.b64decode(chunk["data"])
            all_audio.append(audio_chunk)
            yield audio_chunk  # ← 浏览器立即收到此 chunk
        elif chunk["code"] == 20000000:
            success = True

    # 4. Cache for future requests (after streaming completes)
    _tts_cache_set(text, speaker, b"".join(all_audio))
```

**视图 — `stream_speech_view`** (`media_app/views.py`):

使用 "prime generator" 模式在返回 StreamingHttpResponse 前捕获 pre-flight 错误：

```python
generator = services.stream_synthesize_speech(text, voice=voice)
first_chunk = next(generator)  # 可能抛出 ConfigurationError / APIError

def _stream_with_first():
    yield first_chunk
    yield from generator

response = StreamingHttpResponse(_stream_with_first(), content_type="audio/mpeg")
response["X-Accel-Buffering"] = "no"
```

**前端 — `_playAudioStream()`** (`static/js/media.js`):

| 步骤 | 操作 |
|------|------|
| 1 | 检查 `window.MediaSource` 支持，不支持则降级 |
| 2 | `new MediaSource()` → `URL.createObjectURL(mediaSource)` → `new Audio(url)` |
| 3 | `audio.play()` 等待数据 |
| 4 | `sourceopen` 事件 → `mediaSource.addSourceBuffer('audio/mpeg')` → `mode='sequence'` |
| 5 | `fetch('/media/tts/stream/', {method:'POST', body:formData})` |
| 6 | `ReadableStream.getReader().read()` 循环读取 chunk |
| 7 | 每个 chunk → `sourceBuffer.appendBuffer(chunk)`（通过 updateend 队列串行化） |
| 8 | 流结束 → `mediaSource.endOfStream()` → 完整 Blob 写入 `_blobCache` |

**SourceBuffer 队列管理** — 关键约束：

SourceBuffer 不支持并发 `appendBuffer()`。在上一个 append 完成之前（`updateend` 事件前）调用会抛出 "still processing" 异常。解决方案：
- `pendingChunks` 队列缓冲到达的 chunk
- `appending` 标志防止并发
- `updateend` 事件驱动 `_appendNext()` 消费队列
- 流完成 + 队列空 + MediaSource open → `endOfStream()`

**三层回退链**:

```
_playAudioStream (MediaSource streaming)
  ├── MediaSource 不支持 → _fallbackToFetch
  ├── addSourceBuffer 失败 → _fallbackToFetch
  ├── fetch 错误 → _fallbackToFetch
  ├── SourceBuffer error → _fallbackToFetch
  ├── ReadableStream error → _fallbackToFetch
  └── audio.play() 失败 → _fallbackToFetch

_fallbackToFetch (非流式 /media/tts/synthesize/)
  ├── 返回 audio/mpeg blob → _playAudioBlob
  ├── 返回 audio_url JSON → _playAudioUrl
  └── 错误 → 红色提示条 (5s 自动隐藏)

_playAudioBlob (已缓存的 blob — 即时播放)
  ├── 缓存 URL → 直接使用 (不释放)
  └── 新 URL → 播放结束后释放

后续播放 (Blob cache hit)
  └── 零网络请求，即时播放
```

**与已有 Nginx 配置的关系**:

现有的 `/media/tts/` location 已配置流式所需的全部 Nginx 指令：
- `proxy_buffering off` — 不缓冲响应，chunk 立即转发
- `proxy_http_version 1.1` — HTTP/2 兼容
- `proxy_cache off` / `gzip off` — 原始二进制通过
- `proxy_read_timeout 120s` — 匹配 TTS API 超时

新增的 `/media/tts/stream/` URL 自动匹配此 location（`/media/tts/` 是前缀），无需修改 Nginx 配置。

**静态文件部署注意事项**:
- 前端 JS 更新后必须运行 `python manage.py collectstatic --noinput`
- Nginx 的 `staticfiles/` 目录映射需要在 collectstatic 后才会更新
- 遗漏此步骤会导致浏览器加载旧 JS，流式功能完全不生效（旧 JS 直接调用 `/media/tts/synthesize/` 非流式端点）

**视图异常处理** (2026-05-13 hotfix):
- `stream_speech_view` 在 `ConfigurationError` / `APIError` 之外新增 `except Exception` 兜底 — `logger.exception()` 记录完整 traceback，返回 502 JSON 错误，防止未预期异常导致静默 500

**延迟改善估算**:

| 阶段 | 之前 (全缓冲) | 之后 (流式) |
|------|-------------|-----------|
| API 首个 chunk 到达 | 等待中 | 浏览器开始接收 |
| API 调用完成 | 等待中 | 浏览器已在播放 |
| 完整音频传输到浏览器 | 现在才开始下载 | 已在播放中 |
| 浏览器开始播放 | API时间 + 传输时间 | ~首 chunk 延迟 (0.5-1s) |
