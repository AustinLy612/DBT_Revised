"""Pydantic structured output schemas for DBT RAG sub-flows.

Each schema enforces the exact format the LLM must return.  These schemas
serve two purposes simultaneously:
1. Output parsing: Pydantic validates the LLM's JSON response
2. Prompt generation: the JSON schema is injected into the system prompt
   so the LLM knows the expected output shape.
"""

from pydantic import BaseModel, Field


# ── Skill Selection ──

class PersonalInquiryResult(BaseModel):
    """AI-generated warm inquiry to understand the student's recent situation.

    Generated before skill recommendation so the student's personal context
    can inform which skill is most appropriate.
    """

    greeting: str = Field(
        ...,
        description="温暖、亲切的开场问候语（包含对学生心情的共情回应）",
        min_length=1,
    )
    question: str = Field(
        ...,
        description="了解学生近期经历和当前状态的开放式问题",
        min_length=1,
    )
    inquiry_focus: str = Field(
        default="",
        description="本次询问的关注方向（如情绪状态、学业压力、人际关系等）",
    )


class SkillSelectionResult(BaseModel):
    """AI-driven specific skill recommendation for a student.

    Recommends a concrete skill within a DBT module (e.g. "观察呼吸"
    within "正念"), not a broad module name.
    """

    selected_module: str = Field(
        ...,
        description="推荐的DBT模块名称（如：正念、情绪调节、痛苦耐受、人际效能）",
        min_length=1,
    )
    selected_skill: str = Field(
        ...,
        description="推荐的DBT具体技能名称（如：观察呼吸、身体扫描、STOP技能、TIP技能）",
        min_length=1,
    )
    reason: str = Field(
        ...,
        description="选择该技能的理由，结合学生问卷和历史记录",
        min_length=1,
    )
    skill_difficulty: str = Field(
        ...,
        description="技能难度",
        pattern=r"^(初级|中级|高级)$",
    )
    alternative_skills: list[str] = Field(
        default_factory=list,
        description="备选技能列表（2-3个）",
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        description="支撑推荐的知识库chunk ID列表",
    )


# ── Teaching Plan ──

class TeachingPlanStep(BaseModel):
    """One step in a teaching plan."""

    step_number: int = Field(..., description="步骤编号", ge=1)
    title: str = Field(..., description="步骤标题", min_length=1)
    content: str = Field(..., description="本步骤的教学内容概要", min_length=1)
    estimated_minutes: int = Field(default=5, description="预计时长（分钟）", ge=1)


class TeachingPlan(BaseModel):
    """Full teaching plan for a session."""

    module: str = Field(..., description="教学模块", min_length=1)
    skill: str = Field(..., description="教学技能", min_length=1)
    plan_steps: list[TeachingPlanStep] = Field(
        ..., description="教学步骤列表", min_length=1
    )
    estimated_total_minutes: int = Field(
        ..., description="预计总时长（分钟）", ge=1
    )
    prerequisites: list[str] = Field(
        default_factory=list, description="前置知识"
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list, description="支撑教学计划的知识库chunk ID列表"
    )


# ── Teaching Content ──

class TeachingContent(BaseModel):
    """Individual teaching message generated during a session.

    When include_risk_assessment=True, risk fields are populated from the
    same LLM call, avoiding a separate risk-assessment API round-trip.
    """

    message_type: str = Field(
        ...,
        description="消息类型",
        pattern=r"^(讲解|示例|提问|反馈|总结|练习)$",
    )
    content: str = Field(
        ..., description="教学内容文本", min_length=1
    )
    question: str = Field(
        default="",
        description="若message_type为'提问'，此处为具体问题",
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        description="本条教学内容引用的知识库chunk ID列表",
    )
    confidence: str = Field(
        default="medium",
        description="模型对内容的信心",
        pattern=r"^(high|medium|low)$",
    )
    image_prompt: str = Field(
        default="",
        description="可选：若本消息描述了一个适合配图的情境（如考试场景、练习场景），填写中文图片生成prompt。留空表示不需要配图。",
    )
    # ── Embedded risk assessment (only populated when include_risk_assessment=True) ──
    risk_level: str = Field(
        default="无",
        description="风险等级：无、低、中、高。仅在合并风险评估时填充。",
        pattern=r"^(无|低|中|高)$",
    )
    should_stop_session: bool = Field(
        default=False,
        description="是否应立即中止会话",
    )
    risk_reasoning: str = Field(
        default="",
        description="风险判定理由",
    )


# ── Teaching Summary ──

class TeachingSummary(BaseModel):
    """Summary generated after a teaching session ends."""

    skill_covered: str = Field(..., description="涵盖的DBT技能", min_length=1)
    key_points: list[str] = Field(
        ..., description="教学要点列表", min_length=1
    )
    student_understanding: str = Field(
        ...,
        description="对学生理解的评估",
        pattern=r"^(良好|一般|需要复习)$",
    )
    recommendations: list[str] = Field(
        default_factory=list, description="后续学习建议"
    )
    summary_text: str = Field(
        default="", description="综合教学摘要文本"
    )


# ── Test Questions ──

class TestQuestion(BaseModel):
    """Single multiple-choice test question."""

    question_text: str = Field(..., description="题目文本", min_length=1)
    options: list[str] = Field(
        ...,
        description="四个选项",
        min_length=4,
        max_length=4,
    )
    correct_option: int = Field(
        ..., description="正确答案序号（0-3）", ge=0, le=3
    )
    explanation: str = Field(
        ..., description="答案解析", min_length=1
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        description="支撑本题的知识库chunk ID列表",
    )


class TestQuestions(BaseModel):
    """Set of 5 test questions generated for a test session."""

    questions: list[TestQuestion] = Field(
        ..., description="5道测试题", min_length=5, max_length=5
    )
    test_difficulty: str = Field(
        ...,
        description="测试总体难度",
        pattern=r"^(初级|中级|高级)$",
    )


# ── Risk Assessment ──

class RiskAssessment(BaseModel):
    """Semantic risk evaluation of user input."""

    risk_level: str = Field(
        ...,
        description="风险等级",
        pattern=r"^(无|低|中|高)$",
    )
    risk_type: str = Field(
        default="",
        description="风险类型（如：自伤、自杀、暴力、危机）",
    )
    reasoning: str = Field(
        ..., description="判定理由", min_length=1
    )
    should_stop_session: bool = Field(
        ..., description="是否应立即中止当前会话"
    )
    follow_up_action: str = Field(
        default="",
        description="建议的后续处理方式",
    )
    triggered_keywords: list[str] = Field(
        default_factory=list,
        description="触发的关键词列表（如有）",
    )
