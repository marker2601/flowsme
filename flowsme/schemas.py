"""JSON schemas for structured output: models that support it must reply in exactly these shapes.
Strict-mode rules: every property is required and no extra properties are allowed."""
from __future__ import annotations


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

STEP_GROUP = _obj(steps=_STRS)

# "Does the narration say where this typed value comes from?" The quote is checked against the transcript.
NARRATION_ANSWER = _obj(
    answered={"type": "boolean"},
    answer=_STR,
    rule=_STR,
    quote=_STR,
)

# "Does this system have an API?" answered from a web search; URLs are checked against the citations.
API_CHECK = _obj(
    has_public_api={"type": "string", "enum": ["yes", "no", "unclear"]},
    documentation_urls=_STRS,
    how_to_connect=_STR,
    notes=_STR,
)

TASK = _obj(name=_STR, summary=_STR, inputs=_STRS, outputs=_STRS, decision_points=_STRS)

# A standard operating procedure: one instruction per recorded step, same order and count.
SOP = _obj(purpose=_STR, steps=_STRS)

RECOMMENDATION = _obj(
    automation_potential={"type": "string", "enum": ["high", "medium", "low"]},
    assumptions=_STRS,
    automatable_fraction={"type": "number"},
    bottlenecks=_STRS,
    recommendations={"type": "array", "items": _obj(
        title=_STR,
        approach={"type": "string", "enum": ["api_integration", "rpa", "script", "built_in_feature",
                                             "ai_extraction", "process_change"]},
        steps_covered=_STR,
        tools=_STRS,
        how=_STR,
        why=_STR,
        effort={"type": "string", "enum": ["hours", "days", "weeks"]},
        time_saved_pct={"type": "number"},
    )},
    quick_wins=_STRS,
    risks=_STRS,
    automation_sketch=_obj(
        language={"type": "string", "enum": ["python", "power_automate", "n8n", "pseudocode"]},
        description=_STR,
        code=_STR,
    ),
)
