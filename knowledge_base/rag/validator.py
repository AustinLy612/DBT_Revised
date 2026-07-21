"""Output validator for structured LLM responses.

Validates JSON output against Pydantic schemas and attempts basic repairs
for common LLM output artifacts (markdown fences, trailing commas, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("dbt_platform.knowledge_base.rag")

# Common alternative field names the LLM may use when its output format drifts.
# Maps canonical field → list of known aliases.
_FIELD_ALIASES: dict[str, list[str]] = {
    "content": ["description", "reasoning", "response", "reply", "text", "answer", "body", "message", "summary_text"],
    "message_type": ["type", "category", "role", "msg_type", "kind", "action"],
    "question": ["query", "inquiry", "student_question", "question_text", "ask"],
    "confidence": ["confidence_level", "certainty", "sureness"],
    "source_chunk_ids": ["sources", "chunk_ids", "references", "citations"],
    "selected_skill": ["skill", "skill_name", "recommended_skill", "name"],
    "reason": ["rationale", "justification", "explanation", "reasoning"],
    "skill_difficulty": ["difficulty", "level", "difficulty_level"],
    "alternative_skills": ["alternatives", "backup_skills", "other_skills"],
    "is_repeat": ["repeat", "is_repeated", "repeated"],
    "repeat_justification": ["repeat_reason", "repeat_rationale", "why_repeat"],
    "transcribed_text": ["text", "transcription", "result"],
    "risk_level": ["level", "severity", "risk"],
    "risk_type": ["type", "category", "risk_category"],
    "should_stop_session": ["stop_session", "should_stop", "halt_session"],
    "follow_up_action": ["action", "follow_up", "recommendation"],
    "triggered_keywords": ["keywords", "triggers", "matched_keywords"],
}


class ValidationError(ValueError):
    """Raised when an LLM output fails schema validation even after repair."""


class OutputValidator:
    """Validates and repairs LLM JSON output against Pydantic schemas.

    All methods are static/class-methods — no instance state needed.
    """

    @staticmethod
    def repair_json(raw_content: str) -> dict[str, Any]:
        """Attempt to repair common JSON formatting issues in LLM output.

        Handles:
        - Markdown code fences (```json ... ```)
        - Trailing commas before closing } or ]
        - Leading/trailing non-JSON text
        - LLM accidentally starting a nested object mid-JSON: ,{...} instead of ,...
        - Missing outer closing brace
        """
        original = raw_content

        # Strip markdown code fences
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw_content, re.DOTALL)
        if fence_match:
            raw_content = fence_match.group(1)

        # Find the outermost JSON object
        brace_start = raw_content.find("{")
        brace_end = raw_content.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            raw_content = raw_content[brace_start:brace_end + 1]

        # Remove trailing commas before } or ]
        raw_content = re.sub(r",(\s*[}\]])", r"\1", raw_content)

        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            pass

        # Fix: LLM sometimes generates ,{"key":...} instead of ,"key":...
        # (accidentally starting a nested object mid-JSON)
        # Strategy: find pattern ,{ followed by known field names,
        # replace the extra { and remove the matching extra }
        raw_content = OutputValidator._fix_nested_object_mid_json(raw_content)

        # Also try adding a missing outer closing brace
        if raw_content.startswith("{") and not raw_content.endswith("}"):
            raw_content += "}"

        # Remove trailing commas again after fixes
        raw_content = re.sub(r",(\s*[}\]])", r"\1", raw_content)

        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            pass

        # Last resort: regex field extraction
        result = OutputValidator._extract_fields_regex(original)
        if result:
            return result

        raise ValidationError(
            f"Unable to parse LLM output as JSON. Raw content:\n{original[:1000]}"
        )

    @staticmethod
    def _fix_nested_object_mid_json(content: str) -> str:
        """Fix pattern where LLM starts a new JSON object mid-output.

        Example: {"message_type":"提问","content":"text",{"question":"q"}}
        Should be: {"message_type":"提问","content":"text","question":"q"}
        """
        # Find ,{ patterns that are not inside string values
        # We look for ,{ directly followed by a known field name pattern
        # The field names are quoted strings followed by :
        result = content
        depth = 0
        in_string = False
        escape = False
        brace_positions: list[int] = []

        for i, ch in enumerate(content):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                if depth == 1:
                    brace_positions.append(i)
                depth += 1
            elif ch == '}':
                depth -= 1

        if not brace_positions:
            return content

        # Remove the extra opening braces and their matching closing braces
        # Work backwards through positions
        for pos in reversed(brace_positions):
            # Remove the extra {
            result = result[:pos] + result[pos + 1:]
            # Find and remove the matching extra } — it's the last } before EOF
            # that's at the same nesting level
            last_brace = result.rfind("}")
            if last_brace >= 0 and result.count("{") < result.count("}"):
                result = result[:last_brace] + result[last_brace + 1:]

        return result

    @staticmethod
    def _extract_fields_regex(raw_content: str) -> dict[str, Any] | None:
        """Regex-based field extraction for TeachingContent-like schemas.

        Used as a last resort when JSON repair fails. Extracts known fields.
        """
        fields: dict[str, Any] = {}

        # Extract simple string fields
        str_fields = [
            ("message_type", r'"message_type"\s*:\s*"([^"]*)"'),
            ("content", r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"'),
            ("question", r'"question"\s*:\s*"([^"]*)"'),
            ("confidence", r'"confidence"\s*:\s*"([^"]*)"'),
        ]
        for name, pattern in str_fields:
            m = re.search(pattern, raw_content, re.DOTALL)
            if m:
                fields[name] = m.group(1).replace('\\"', '"').replace('\\n', '\n')

        # Extract array fields
        arr_match = re.search(r'"source_chunk_ids"\s*:\s*\[(.*?)\]', raw_content, re.DOTALL)
        if arr_match:
            items = re.findall(r'"([^"]*)"', arr_match.group(1))
            fields["source_chunk_ids"] = items
        else:
            fields["source_chunk_ids"] = []

        # Only return if we got at least content field
        if "content" in fields:
            fields.setdefault("message_type", "讲解")
            fields.setdefault("question", "")
            fields.setdefault("confidence", "medium")
            return fields
        return None

    @staticmethod
    def _remap_fields(data: dict[str, Any], schema_model: type) -> dict[str, Any]:
        """Remap alternative field names to canonical names when LLM output drifts.

        Returns a shallow copy with remapped fields, or the original dict
        if no remapping was needed.  Only remaps when the candidate key is
        NOT itself a valid field of the schema (to avoid consuming a
        legitimate field).
        """
        all_schema_fields = set(schema_model.model_fields.keys())
        required_fields = {
            name
            for name, info in schema_model.model_fields.items()
            if info.is_required()
        }

        # Prioritise required fields first, then try the rest
        missing_required = [f for f in required_fields if f not in data]
        missing_optional = [
            f for f in all_schema_fields if f not in required_fields and f not in data
        ]

        if not missing_required and not missing_optional:
            return data

        remapped = dict(data)
        for field in missing_required + missing_optional:
            candidates = _FIELD_ALIASES.get(field, [])
            for candidate in candidates:
                if candidate in remapped and candidate not in all_schema_fields:
                    remapped[field] = remapped.pop(candidate)
                    logger.info(
                        "Remapped LLM field '%s' → '%s' for schema %s",
                        candidate, field, schema_model.__name__,
                    )
                    break

        return remapped

    @staticmethod
    def _sanitize_values(data: dict[str, Any], schema_model: type) -> dict[str, Any]:
        """Fix known invalid values after field remapping.

        For example, if message_type was remapped from a JSON Schema
        ``"type": "object"`` field, the value ``"object"`` won't match
        the TeachingContent pattern.  Default it to a reasonable value.
        """
        sanitized = dict(data)

        # message_type must match a known pattern for TeachingContent
        if "message_type" in sanitized:
            mt = sanitized["message_type"]
            valid_types = {"讲解", "示例", "提问", "反馈", "总结", "练习"}
            if mt not in valid_types:
                logger.info(
                    "Sanitizing invalid message_type '%s' → '讲解' for schema %s",
                    mt, schema_model.__name__,
                )
                sanitized["message_type"] = "讲解"

        return sanitized

    @staticmethod
    def validate_and_repair(
        data: dict[str, Any],
        schema_model: type,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Validate `data` against `schema_model`. If it's a raw string, try
        repair first.  Returns the validated dict.

        Args:
            data: The parsed (or raw) LLM output.
            schema_model: A Pydantic BaseModel subclass.
            max_attempts: Maximum repair attempts (default 2).

        Returns:
            The validated data dict.

        Raises:
            ValidationError: If validation fails after all repair attempts.
        """
        if isinstance(data, str):
            data = OutputValidator.repair_json(data)

        for attempt in range(max_attempts):
            try:
                validated = schema_model(**data)
                return validated.model_dump()
            except Exception as exc:
                logger.warning(
                    "Schema validation attempt %d/%d failed: %s",
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts - 1:
                    data = OutputValidator._remap_fields(data, schema_model)
                    data = OutputValidator._sanitize_values(data, schema_model)
                else:
                    raise ValidationError(
                        f"Output validation failed after {max_attempts} attempt(s). "
                        f"Schema: {schema_model.__name__}. "
                        f"Errors: {exc}"
                    ) from exc

        raise ValidationError("Unexpected: validation loop exited without result.")

    @staticmethod
    def validate(
        data: dict[str, Any],
        schema_model: type,
    ) -> dict[str, Any]:
        """Validate without repair — raise immediately on failure."""
        validated = schema_model(**data)
        return validated.model_dump()
