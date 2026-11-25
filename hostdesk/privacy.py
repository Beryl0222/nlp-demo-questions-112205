"""公开范围、转交许可和未授权客户名检测。"""
from __future__ import annotations

import re
from typing import Any

from .models import HOST_ONLY, SCOPE_FLAGS, STAGE, TRANSFER, Interpretation
from .semantics import extract_entities, interpret_question


CONTACT_ONLY_RE = re.compile(r"只(?:在|能).{0,6}会后(?:单独)?(?:交流|联系|找我)|会后单独交流|不要在台上|不能说(?:出)?")
DO_NOT_NAME_RE = re.compile(r"不能说(?:是|出)?([^，。；!?？]+)")


def scope_for_question(row: dict[str, Any], interp: Interpretation | None = None) -> dict[str, Any]:
    sharing = row["sharing"]
    flags = dict(SCOPE_FLAGS[sharing])
    text = row["text"]
    interp = interp or interpret_question(text)
    explicit_contact_only = bool(CONTACT_ONLY_RE.search(text))
    do_not_name = bool(DO_NOT_NAME_RE.search(text)) or any("不能说" in c for c in [text])

    # 提交人在原话中作了更严格限制时，采用更严格口径。
    if explicit_contact_only and sharing != TRANSFER:
        flags["stage_verbatim"] = False
        flags["stage_anonymized"] = False
    if do_not_name:
        flags["stage_verbatim"] = False
    if explicit_contact_only and sharing == TRANSFER:
        flags["contact_after_meeting"] = True
    else:
        flags.setdefault("contact_after_meeting", sharing == TRANSFER)

    flags.update(
        {
            "sharing": sharing,
            "explicit_contact_only": explicit_contact_only,
            "do_not_name": do_not_name,
            # 主持人可以采用不含对象、且不是由该私有细节拼出的中性公开问法；
            # 但“仅主持人”问句本身不能上台或转交。
            "neutral_generic_wording_allowed": True,
            "generic_silent_representation_allowed": True,
        }
    )
    return flags


def collect_protected_entities(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for q in questions:
        interp = interpret_question(q["text"])
        scope = scope_for_question(q, interp)
        names = set(interp.entities)
        for match in DO_NOT_NAME_RE.finditer(q["text"]):
            names.update(extract_entities(match.group(1)))
        for name in names:
            item = index.setdefault(
                name,
                {"entity": name, "question_ids": [], "stage_allowed": False, "transfer_allowed": False, "host_only": True},
            )
            item["question_ids"].append(q["id"])
            item["stage_allowed"] = item["stage_allowed"] or (q["sharing"] == STAGE and not scope["do_not_name"])
            item["transfer_allowed"] = item["transfer_allowed"] or scope["transfer_permitted"]
            item["host_only"] = item["host_only"] and q["sharing"] == HOST_ONLY
    return index


def scan_utterance(text: str, protected: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for name in extract_entities(text):
        info = protected.get(name)
        if info and not info["stage_allowed"]:
            alerts.append(
                {
                    "level": "unauthorized_public_naming",
                    "entity": name,
                    "question_ids": info["question_ids"],
                    "message": f"{name} 未被许可在台上公开；不得在后续问法或公开摘要中复述该名称。",
                }
            )
        elif not info:
            alerts.append(
                {
                    "level": "unlisted_org_mention",
                    "entity": name,
                    "question_ids": [],
                    "message": f"{name} 不在已授权公开名单中；需主持人核对公开许可。",
                }
            )
    return alerts


def mask_text(text: str, protected: dict[str, dict[str, Any]], role: str) -> str:
    """按角色遮罩。host 不遮；coordinator 可见许可转交对象；public 全遮。"""
    if role == "host":
        return text
    result = text
    for name, info in protected.items():
        if role == "coordinator" and info.get("transfer_allowed"):
            continue
        result = result.replace(name, "■■")
    return result
