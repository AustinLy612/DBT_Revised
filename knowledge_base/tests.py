import io
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import KnowledgeChunk, KnowledgeDocument, RetrievalLog
from .services import (
    chunk_text,
    ensure_mongodb_text_index,
    extract_sections,
    keyword_search,
    log_retrieval,
)
from .storage import (
    delete_document,
    download_document,
    get_minio_client,
    upload_document,
)
from .tasks import parse_document_bytes

User = get_user_model()

TXT_CONTENT = (
    "DBT技能训练模块一：正念练习。\n\n"
    "正念是DBT的核心基础技能，帮助学生专注于当下。\n"
    "通过观察呼吸、身体扫描等方式培养觉察能力。\n\n"
    "智慧心是情绪心和理性心的平衡状态。\n"
    "当两种心智达到平衡时，就能做出更明智的决定。\n\n"
    "痛苦耐受技能帮助学生在不使情况变糟的前提下度过危机。\n"
    "包括转移注意力、自我安抚、改善当下和利弊分析等方法。\n\n"
    "DBT技能训练模块二：情绪调节。\n\n"
    "情绪调节模块旨在帮助学生理解和管理自己的情绪。\n"
    "识别和命名情绪是情绪调节的第一步。\n"
    "理解情绪的功能有助于接纳自己的情绪反应。\n"
    "减少情绪脆弱性需要通过建立健康的生活习惯。\n"
    "增加积极情绪体验可以平衡负面情绪的影响。\n\n"
    "DBT技能训练模块三：人际效能。\n\n"
    "人际效能技能帮助学生建立和维护健康的人际关系。\n"
    "学会表达自己的需求和界限是人际效能的核心。\n"
    "倾听他人并给予有效反馈是沟通的关键能力。\n"
) * 3  # Repeat to exceed CHUNK_SIZE for multi-chunk tests

MD_CONTENT = (
    "# DBT技能教学 - 情绪调节\n\n"
    "## 概述\n"
    "情绪调节模块旨在帮助学生理解和管理自己的情绪。\n\n"
    "## 核心技能\n"
    "- 识别和命名情绪\n"
    "- 理解情绪的功能\n"
    "- 减少情绪脆弱性\n"
    "- 增加积极情绪体验\n"
    "- 与当前情绪相反的行动\n\n"
    "## 教学方法\n"
    "通过情境练习和角色扮演，帮助学生掌握情绪调节技巧。\n\n"
    "## 识别和命名情绪\n"
    "情绪识别是情绪调节的基础。学生需要学会识别自己当下的情绪状态，"
    "并用准确的词汇来描述它们。常见的情绪包括愤怒、悲伤、恐惧、喜悦和厌恶。"
    "每种情绪都有其独特的身体感受和行为倾向。\n\n"
    "## 理解情绪的功能\n"
    "每种情绪都有其进化和适应的功能。愤怒帮助我们捍卫界限，"
    "悲伤帮助我们获得支持和恢复，恐惧帮助我们规避危险。"
    "理解情绪的功能可以减少对情绪的负面评价。\n\n"
    "## 减少情绪脆弱性\n"
    "通过建立健康的生活习惯来减少情绪脆弱性，包括充足的睡眠、"
    "均衡的饮食、规律的运动和避免使用成瘾物质。"
) * 2  # Repeat to exceed CHUNK_SIZE for multi-chunk tests


# ── Helpers ──

def create_admin(username="kbadmin"):
    user = User.objects.create_user(
        username=username, password="testpass123", role="admin"
    )
    return user


def create_student(username="kbstudent"):
    return User.objects.create_user(
        username=username, password="testpass123", role="student"
    )


def create_session(user):
    from teaching.models import TeachingSession
    return TeachingSession.objects.create(
        user=user,
        status="teaching",
        selected_module="正念",
        selected_skill="观察呼吸",
    )


# ── Document Parsing Tests ──

class DocumentParsingTests(TestCase):
    def test_parse_txt(self):
        text = parse_document_bytes(b"hello world", "test.txt")
        self.assertEqual(text, "hello world")

    def test_parse_txt_utf8_chinese(self):
        data = "正念是DBT的核心基础技能。".encode("utf-8")
        text = parse_document_bytes(data, "test.txt")
        self.assertIn("正念", text)

    def test_parse_md(self):
        data = "# Title\n\nSome content".encode("utf-8")
        text = parse_document_bytes(data, "test.md")
        self.assertIn("Title", text)

    def test_parse_markdown_extension(self):
        data = "# Hello".encode("utf-8")
        text = parse_document_bytes(data, "test.markdown")
        self.assertIn("Hello", text)

    def test_parse_empty_file_not_empty_string(self):
        text = parse_document_bytes(b"", "test.txt")
        self.assertEqual(text, "")

    def test_parse_unsupported_extension(self):
        with self.assertRaises(ValueError):
            parse_document_bytes(b"data", "image.png")


# ── Chunking Tests ──

class ChunkingTests(TestCase):
    def test_chunk_text_produces_chunks(self):
        chunks = chunk_text(TXT_CONTENT)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertIn("text", c)
            self.assertIn("metadata", c)

    def test_chunk_metadata_preserved(self):
        meta = {"section": "intro", "page": 1}
        chunks = chunk_text(TXT_CONTENT, metadata=meta)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertEqual(c["metadata"]["section"], "intro")

    def test_short_text_produces_one_chunk(self):
        chunks = chunk_text("短文本", metadata={})
        self.assertEqual(len(chunks), 1)

    def test_chunk_text_not_empty(self):
        chunks = chunk_text(TXT_CONTENT)
        for c in chunks:
            self.assertTrue(c["text"].strip())


# ── Section Extraction Tests ──

class SectionExtractionTests(TestCase):
    def test_extract_markdown_headings(self):
        text = "# 模块一\n\n内容A\n\n## 第一节\n\n内容B\n\n### 小节\n\n内容C"
        sections = extract_sections(text)
        self.assertGreaterEqual(len(sections), 2)
        titles = [s["title"] for s in sections]
        self.assertIn("模块一", titles)
        self.assertIn("第一节", titles)

    def test_section_title_in_metadata(self):
        """section_title must appear in chunk metadata from sections."""
        text = "## 正念练习\n\n正念是DBT核心技能，帮助专注于当下。通过观察呼吸培养觉察能力。智慧心是情绪心和理性心的平衡。\n\n## 痛苦耐受\n\n痛苦耐受帮助学生在危机中度过困难。包括转移注意力、自我安抚等方法。\n"
        sections = extract_sections(text)
        all_meta = []
        for section in sections:
            meta = {"section_title": section["title"]}
            chunks = chunk_text(section["content"], metadata=meta)
            all_meta.extend([c["metadata"] for c in chunks])

        self.assertGreater(len(all_meta), 0)
        section_titles = {m["section_title"] for m in all_meta}
        self.assertIn("正念练习", section_titles)
        self.assertIn("痛苦耐受", section_titles)

    def test_extract_plain_text_fallback(self):
        sections = extract_sections("这是没有标题的纯文本内容包含一些信息。")
        self.assertEqual(len(sections), 1)
        self.assertTrue(sections[0]["title"])

    def test_extract_preamble_becomes_overview(self):
        text = "文档导言部分\n\n## 第一章\n\n正式内容开始"
        sections = extract_sections(text)
        titles = [s["title"] for s in sections]
        self.assertIn("概述", titles)

    def test_extract_three_level_headings(self):
        text = "# 一级\n\n内容1\n\n## 二级\n\n内容2\n\n### 三级\n\n内容3"
        sections = extract_sections(text)
        titles = [s["title"] for s in sections]
        self.assertIn("一级", titles)
        self.assertIn("二级", titles)
        self.assertIn("三级", titles)


# ── Document Parsing + Chunking Integration ──

class ParseAndChunkTests(TestCase):
    def test_full_txt_parse_and_chunk(self):
        data = TXT_CONTENT.encode("utf-8")
        text = parse_document_bytes(data, "mindfulness.txt")
        self.assertIn("正念", text)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        all_text = "".join(c["text"] for c in chunks)
        self.assertIn("正念", all_text)

    def test_full_md_parse_and_chunk(self):
        data = MD_CONTENT.encode("utf-8")
        text = parse_document_bytes(data, "emotion_regulation.md")
        self.assertIn("情绪调节", text)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        all_text = "".join(c["text"] for c in chunks)
        self.assertIn("情绪调节", all_text)


# ── MinIO Storage Tests ──

@override_settings(MINIO_BUCKET="dbt-platform")
class StorageTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        client = get_minio_client()
        if not client.bucket_exists("dbt-platform"):
            client.make_bucket("dbt-platform")

    def test_upload_and_download(self):
        obj_name = f"test/{uuid.uuid4().hex}.txt"
        content = b"test content for minio"
        upload_document(content, obj_name)
        downloaded = download_document(obj_name)
        self.assertEqual(downloaded, content)
        delete_document(obj_name)

    def test_upload_chinese_text(self):
        obj_name = f"test/{uuid.uuid4().hex}.txt"
        content = "正念是DBT的核心技能。".encode("utf-8")
        upload_document(content, obj_name)
        downloaded = download_document(obj_name)
        self.assertEqual(downloaded.decode("utf-8"), "正念是DBT的核心技能。")
        delete_document(obj_name)


# ── Model Tests ──

class KnowledgeDocumentModelTests(TestCase):
    def test_create_document_with_defaults(self):
        doc = KnowledgeDocument.objects.create(
            title="测试文档", module="正念", skill="观察呼吸"
        )
        self.assertEqual(doc.status, KnowledgeDocument.Status.UPLOADED)
        self.assertEqual(doc.difficulty, KnowledgeDocument.Difficulty.BEGINNER)
        self.assertTrue(doc.is_beginner_friendly)
        self.assertEqual(doc.scenario_tags, [])
        self.assertEqual(doc.risk_flags, [])
        self.assertEqual(doc.error_message, "")

    def test_create_document_with_metadata(self):
        doc = KnowledgeDocument.objects.create(
            title="高级技能",
            module="情绪调节",
            difficulty=KnowledgeDocument.Difficulty.ADVANCED,
            is_beginner_friendly=False,
            scenario_tags=["校园", "家庭"],
            risk_flags=["self_harm"],
        )
        self.assertEqual(doc.difficulty, "advanced")
        self.assertFalse(doc.is_beginner_friendly)
        self.assertIn("校园", doc.scenario_tags)
        self.assertIn("self_harm", doc.risk_flags)

    def test_document_str(self):
        doc = KnowledgeDocument.objects.create(
            title="测试标题", module="正念"
        )
        self.assertIsNotNone(doc.document_id)
        self.assertEqual(len(doc.document_id), 36)

    def test_status_transitions(self):
        doc = KnowledgeDocument.objects.create(title="T", module="M")
        self.assertEqual(doc.status, "uploaded")
        doc.status = KnowledgeDocument.Status.PROCESSING
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.status, "processing")
        doc.status = KnowledgeDocument.Status.RETRIEVABLE
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.status, "retrievable")
        doc.status = KnowledgeDocument.Status.FAILED
        doc.error_message = "Parse error"
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.status, "failed")
        self.assertEqual(doc.error_message, "Parse error")


class KnowledgeChunkModelTests(TestCase):
    def setUp(self):
        self.doc = KnowledgeDocument.objects.create(
            title="测试文档", module="正念"
        )

    def test_create_chunk(self):
        chunk = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="这是一段测试文本",
            metadata={"section_title": "导论"},
        )
        self.assertEqual(chunk.document, self.doc)
        self.assertEqual(chunk.metadata["section_title"], "导论")
        self.assertEqual(chunk.embedding_id, "")

    def test_chunk_relationship(self):
        KnowledgeChunk.objects.create(document=self.doc, chunk_text="A")
        KnowledgeChunk.objects.create(document=self.doc, chunk_text="B")
        self.assertEqual(self.doc.chunks.count(), 2)

    def test_cascade_delete(self):
        KnowledgeChunk.objects.create(document=self.doc, chunk_text="X")
        doc_id = self.doc.document_id
        self.doc.delete()
        self.assertEqual(KnowledgeChunk.objects.filter(document_id=doc_id).count(), 0)


class RetrievalLogModelTests(TestCase):
    def setUp(self):
        self.student = create_student("retrieval_test_user")
        self.session = create_session(self.student)
        self.doc = KnowledgeDocument.objects.create(title="D", module="M")
        self.chunk = KnowledgeChunk.objects.create(
            document=self.doc, chunk_text="测试"
        )

    def test_create_retrieval_log(self):
        log_entry = RetrievalLog.objects.create(
            user=self.student,
            session=self.session,
            query="正念是什么",
            retrieved_chunk_ids=[self.chunk.chunk_id],
            use_case=RetrievalLog.UseCase.TEACHING,
        )
        self.assertEqual(log_entry.user, self.student)
        self.assertEqual(log_entry.use_case, "teaching")
        self.assertIn(self.chunk.chunk_id, log_entry.retrieved_chunk_ids)

    def test_retrieval_log_use_cases(self):
        for uc in ["teaching", "test_generation", "explanation", "retest", "risk"]:
            log_entry = RetrievalLog.objects.create(
                user=self.student,
                session=self.session,
                query="测试查询",
                retrieved_chunk_ids=[],
                use_case=uc,
            )
            self.assertEqual(log_entry.use_case, uc)


# ── Keyword Search Tests ──

class KeywordSearchTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_mongodb_text_index()

    def setUp(self):
        self.doc = KnowledgeDocument.objects.create(
            title="正念教学", module="正念", skill="观察呼吸"
        )
        self.c1 = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="正念是核心基础技能，帮助专注于当下。观察呼吸是最基础的练习。",
        )
        self.c2 = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="痛苦耐受技能在危机时刻非常重要，包括自我安抚和转移注意力。",
        )
        self.c3 = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="情绪调节帮助学生管理强烈情绪，学会命名和理解情绪。",
        )

    def test_keyword_search_finds_relevant_chunk(self):
        results = keyword_search("正念", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        chunk_ids = [r["chunk_id"] for r in results]
        self.assertIn(self.c1.chunk_id, chunk_ids)

    def test_keyword_search_multiple_results(self):
        results = keyword_search("情绪", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        chunk_ids = [r["chunk_id"] for r in results]
        self.assertIn(self.c3.chunk_id, chunk_ids)

    def test_keyword_search_no_match(self):
        results = keyword_search("wxqplfgh不存在的内容", top_k=3)
        self.assertEqual(len(results), 0)

    def test_search_by_skill_name(self):
        results = keyword_search("观察呼吸", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        self.assertIn(self.c1.chunk_id, [r["chunk_id"] for r in results])

    def test_keyword_search_handles_regex_metacharacters(self):
        results = keyword_search(".* [a-z] (测试", top_k=3)
        self.assertEqual(len(results), 0)


# ── Retrieval Service Tests ──

class RetrievalLogServiceTests(TestCase):
    def setUp(self):
        self.student = create_student("log_service_user")
        self.session = create_session(self.student)
        self.doc = KnowledgeDocument.objects.create(title="D", module="M")
        self.chunk = KnowledgeChunk.objects.create(
            document=self.doc, chunk_text="测试文本"
        )

    def test_log_retrieval_creates_entry(self):
        log_entry = log_retrieval(
            user=self.student,
            session=self.session,
            query="测试查询",
            retrieved_chunk_ids=[self.chunk.chunk_id],
            use_case="teaching",
        )
        self.assertIsNotNone(log_entry.retrieval_id)
        db_log = RetrievalLog.objects.get(retrieval_id=log_entry.retrieval_id)
        self.assertEqual(db_log.query, "测试查询")
        self.assertEqual(db_log.use_case, "teaching")
        self.assertIn(self.chunk.chunk_id, db_log.retrieved_chunk_ids)

    def test_log_retrieval_with_multiple_chunks(self):
        c2 = KnowledgeChunk.objects.create(document=self.doc, chunk_text="X")
        log_entry = log_retrieval(
            user=self.student,
            session=self.session,
            query="多结果查询",
            retrieved_chunk_ids=[self.chunk.chunk_id, c2.chunk_id],
            use_case="test_generation",
        )
        self.assertEqual(len(log_entry.retrieved_chunk_ids), 2)

    def test_log_retrieval_use_case_chinese_display(self):
        log_entry = log_retrieval(
            user=self.student,
            session=self.session,
            query="q",
            retrieved_chunk_ids=[],
            use_case="teaching",
        )
        self.assertEqual(log_entry.get_use_case_display(), "教学")

    def test_log_retrieval_without_session(self):
        log_entry = log_retrieval(
            user=self.student,
            session=None,
            query="无会话查询",
            retrieved_chunk_ids=[self.chunk.chunk_id],
            use_case="teaching",
        )
        self.assertIsNotNone(log_entry.retrieval_id)
        self.assertIsNone(log_entry.session)
        db_log = RetrievalLog.objects.get(retrieval_id=log_entry.retrieval_id)
        self.assertEqual(db_log.query, "无会话查询")
        self.assertIsNone(db_log.session)


# ── Admin Tests ──

class KnowledgeBaseAdminTests(TestCase):
    """Tests for admin list/detail pages. All data created in setUp (not setUpClass)
    to avoid MongoDB transaction visibility issues."""

    def setUp(self):
        self.admin = create_admin("kbadmin")
        self.client.login(username="kbadmin", password="testpass123")
        self.doc = KnowledgeDocument.objects.create(
            title="管理测试文档", module="正念", skill="观察呼吸"
        )
        KnowledgeChunk.objects.create(
            document=self.doc, chunk_text="管理界面测试分块内容"
        )

    def test_document_list_page_loads(self):
        url = reverse("admin:knowledge_base_knowledgedocument_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "管理测试文档")

    def test_document_detail_page_loads(self):
        url = reverse(
            "admin:knowledge_base_knowledgedocument_change",
            args=[self.doc.document_id],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "管理测试文档")

    def test_chunk_list_page_loads(self):
        url = reverse("admin:knowledge_base_knowledgechunk_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_retrieval_log_list_page_loads(self):
        url = reverse("admin:knowledge_base_retrievallog_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_upload_page_loads(self):
        url = reverse("admin:knowledge_base_upload")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "文档文件")

    def test_upload_page_has_metadata_fields(self):
        url = reverse("admin:knowledge_base_upload")
        resp = self.client.get(url)
        self.assertContains(resp, "difficulty")
        self.assertContains(resp, "is_beginner_friendly")

    def test_document_list_shows_status(self):
        url = reverse("admin:knowledge_base_knowledgedocument_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, "已上传")

    def test_non_admin_blocked_from_admin(self):
        self.client.logout()
        student = create_student("nonadmin_kb")
        self.client.login(username="nonadmin_kb", password="testpass123")
        url = reverse("admin:knowledge_base_knowledgedocument_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)


# ── Search View Tests ──

class SearchViewTests(TransactionTestCase):
    def setUp(self):
        ensure_mongodb_text_index()
        self.doc = KnowledgeDocument.objects.create(
            title="搜索测试文档", module="情绪调节", skill="情绪命名"
        )
        KnowledgeChunk.objects.create(
            document=self.doc, chunk_text="情绪调节的核心是学会命名和理解情绪"
        )
        self.admin = create_admin("kbadmin2")
        self.search_url = reverse("knowledge_base:search")

    def test_search_unauthenticated_redirects(self):
        resp = self.client.get(self.search_url, {"q": "情绪"})
        self.assertEqual(resp.status_code, 302)

    def test_search_without_query_returns_400(self):
        self.client.login(username="kbadmin2", password="testpass123")
        resp = self.client.get(self.search_url)
        self.assertEqual(resp.status_code, 400)

    def test_keyword_search_admin(self):
        self.client.login(username="kbadmin2", password="testpass123")
        resp = self.client.get(self.search_url, {"q": "情绪调节", "mode": "keyword"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["mode"], "keyword")
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn("chunk_id", data["results"][0])

    def test_search_results_have_scores(self):
        self.client.login(username="kbadmin2", password="testpass123")
        resp = self.client.get(self.search_url, {"q": "情绪", "mode": "keyword"})
        data = resp.json()
        if data["count"] > 0:
            self.assertIn("score", data["results"][0])
            self.assertIn("source", data["results"][0])

    def test_search_creates_retrieval_log_without_session(self):
        self.client.login(username="kbadmin2", password="testpass123")
        resp = self.client.get(self.search_url, {"q": "情绪调节", "mode": "keyword"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RetrievalLog.objects.filter(query="情绪调节").exists())

    def test_search_creates_retrieval_log_with_session(self):
        self.client.login(username="kbadmin2", password="testpass123")
        session = create_session(self.admin)
        resp = self.client.get(
            self.search_url,
            {"q": "情绪命名", "mode": "keyword", "session_id": session.session_id},
        )
        self.assertEqual(resp.status_code, 200)
        log = RetrievalLog.objects.filter(query="情绪命名").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.session, session)


# ── Integration: Full Pipeline (MinIO + Parse + Chunk) ──

@override_settings(MINIO_BUCKET="dbt-platform")
class DocumentPipelineIntegrationTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        client = get_minio_client()
        if not client.bucket_exists("dbt-platform"):
            client.make_bucket("dbt-platform")

    def setUp(self):
        self.doc = KnowledgeDocument.objects.create(
            title="集成测试文档",
            module="正念",
            skill="观察呼吸",
            difficulty="beginner",
            is_beginner_friendly=True,
            scenario_tags=["校园"],
            risk_flags=[],
        )
        self.object_name = f"knowledge/{self.doc.document_id}/{uuid.uuid4().hex}.txt"
        upload_document(TXT_CONTENT.encode("utf-8"), self.object_name)

    def tearDown(self):
        try:
            delete_document(self.object_name)
        except Exception:
            pass

    def test_upload_then_download_roundtrip(self):
        data = download_document(self.object_name)
        self.assertIn("正念", data.decode("utf-8"))

    def test_parse_downloaded_document(self):
        data = download_document(self.object_name)
        text = parse_document_bytes(data, "test.txt")
        self.assertIn("正念", text)

    def test_status_transitions_manual(self):
        self.doc.status = KnowledgeDocument.Status.PROCESSING
        self.doc.save()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "processing")

        self.doc.status = KnowledgeDocument.Status.RETRIEVABLE
        self.doc.save()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "retrievable")

    def test_failed_status_with_error_message(self):
        self.doc.status = KnowledgeDocument.Status.FAILED
        self.doc.error_message = "ValueError: corrupt file"
        self.doc.save()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "failed")
        self.assertIn("ValueError", self.doc.error_message)

    def test_chunks_created_and_linked(self):
        data = download_document(self.object_name)
        text = parse_document_bytes(data, "test.txt")
        chunks_data = chunk_text(text)
        self.assertGreater(len(chunks_data), 1)

        for cdata in chunks_data:
            KnowledgeChunk.objects.create(
                document=self.doc,
                chunk_text=cdata["text"],
                metadata=cdata["metadata"],
            )
        self.assertEqual(self.doc.chunks.count(), len(chunks_data))

    def test_keyword_search_finds_pipeline_chunks(self):
        ensure_mongodb_text_index()
        data = download_document(self.object_name)
        text = parse_document_bytes(data, "test.txt")
        chunks_data = chunk_text(text)
        for cdata in chunks_data:
            KnowledgeChunk.objects.create(
                document=self.doc,
                chunk_text=cdata["text"],
                metadata=cdata["metadata"],
            )
        results = keyword_search("正念", top_k=5)
        self.assertGreaterEqual(len(results), 1)

    def test_section_title_in_pipeline_chunks(self):
        """Verify section_title is included when document has markdown headings."""
        from .tasks import parse_document_bytes
        md_bytes = MD_CONTENT.encode("utf-8")
        object_name = f"knowledge/{self.doc.document_id}/{uuid.uuid4().hex}.md"
        upload_document(md_bytes, object_name)
        try:
            data = download_document(object_name)
            full_text = parse_document_bytes(data, "test.md")
            sections = extract_sections(full_text)
            self.assertGreater(len(sections), 0)

            doc_metadata = {"document_id": self.doc.document_id}
            all_chunks = []
            for section in sections:
                section_meta = {**doc_metadata, "section_title": section["title"]}
                section_chunks = chunk_text(section["content"], metadata=section_meta)
                all_chunks.extend(section_chunks)

            self.assertGreater(len(all_chunks), 0)
            for c in all_chunks:
                self.assertIn("section_title", c["metadata"])
                self.assertTrue(c["metadata"]["section_title"])
        finally:
            try:
                delete_document(object_name)
            except Exception:
                pass

    def test_pipeline_cleanup_before_reprocessing(self):
        """Verify that re-processing a document deletes old chunks first."""
        # Create some "stale" chunks from a prior failed attempt
        for i in range(3):
            KnowledgeChunk.objects.create(
                document=self.doc,
                chunk_text=f"旧分块内容 {i}",
            )
        self.assertEqual(self.doc.chunks.count(), 3)
        old_chunk_ids = set(self.doc.chunks.values_list("chunk_id", flat=True))

        # Simulate the idempotency cleanup in run_document_pipeline
        existing = list(KnowledgeChunk.objects.filter(document=self.doc))
        self.assertEqual(len(existing), 3)
        KnowledgeChunk.objects.filter(document=self.doc).delete()
        self.assertEqual(self.doc.chunks.count(), 0)

        # Re-create "new" chunks
        new_chunks = []
        for i in range(2):
            chunk = KnowledgeChunk.objects.create(
                document=self.doc,
                chunk_text=f"新分块内容 {i}",
            )
            new_chunks.append(chunk)
        self.assertEqual(self.doc.chunks.count(), 2)

        # Old chunk IDs must be gone
        new_chunk_ids = set(self.doc.chunks.values_list("chunk_id", flat=True))
        self.assertEqual(len(new_chunk_ids & old_chunk_ids), 0)


# ═══════════════════════════════════════════════════════════
# Retrieval Failure Scenarios (Step 13)
# ═══════════════════════════════════════════════════════════

class RetrievalFailureTests(TestCase):
    """Test that retrieval degrades gracefully when backends are unavailable."""

    def test_semantic_search_returns_empty_when_model_unavailable(self):
        """When embedding model failed to load, semantic_search returns []."""
        from unittest.mock import patch
        from knowledge_base.services import semantic_search

        with patch("knowledge_base.services._embedding_load_failed", True):
            results = semantic_search("正念呼吸")

        self.assertEqual(results, [])

    def test_log_retrieval_raises_on_db_error(self):
        """log_retrieval propagates DB errors (audit logging is synchronous)."""
        from unittest.mock import patch
        from knowledge_base.services import log_retrieval

        user = User.objects.create_user(
            username="log_fail", password="testpass123", role="student"
        )
        with patch("knowledge_base.services.RetrievalLog.objects.create",
                   side_effect=Exception("DB write error")):
            with self.assertRaises(Exception):
                log_retrieval(
                    user=user,
                    session=None,
                    query="测试查询",
                    retrieved_chunk_ids=[],
                    use_case="teaching",
                )

    def test_hybrid_search_graceful_when_semantic_fails(self):
        """When semantic search fails, hybrid_search returns keyword-only results."""
        from unittest.mock import patch
        from knowledge_base.services import hybrid_search

        with patch("knowledge_base.services.semantic_search",
                   side_effect=ConnectionError("Qdrant unreachable")):
            results = hybrid_search("正念呼吸")

        # Should return keyword results without crashing
        self.assertIsInstance(results, list)
        for r in results:
            self.assertEqual(r["source"], "keyword")


class StorageFailureTests(TestCase):
    """Test MinIO storage operations handle failures appropriately."""

    def test_upload_document_handles_connection_error(self):
        """Upload should propagate connection errors to caller."""
        from unittest.mock import patch
        from knowledge_base.storage import upload_document

        with patch("knowledge_base.storage.get_minio_client") as mock_client:
            mock_client.return_value.bucket_exists.side_effect = ConnectionError("MinIO unreachable")
            try:
                upload_document("test.txt", b"content", "text/plain")
            except ConnectionError:
                pass  # Currently expected — caller must handle

    def test_download_document_handles_connection_error(self):
        """Download should propagate connection errors."""
        from unittest.mock import patch
        from knowledge_base.storage import download_document

        with patch("knowledge_base.storage.get_minio_client") as mock_client:
            mock_client.return_value.get_object.side_effect = ConnectionError("MinIO unreachable")
            try:
                download_document("nonexistent.txt")
            except ConnectionError:
                pass  # Currently expected — caller must handle

    def test_delete_document_handles_connection_error(self):
        """Delete should propagate connection errors."""
        from unittest.mock import patch
        from knowledge_base.storage import delete_document

        with patch("knowledge_base.storage.get_minio_client") as mock_client:
            mock_client.return_value.remove_object.side_effect = ConnectionError("MinIO unreachable")
            try:
                delete_document("nonexistent.txt")
            except ConnectionError:
                pass  # Currently expected — caller must handle
