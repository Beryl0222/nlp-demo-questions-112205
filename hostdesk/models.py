"""主持工作台的核心数据类型。

这里不依赖任何第三方库，所有结构都可以直接 JSON 序列化。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


STAGE = "现场"
ANONYMOUS_STAGE = "匿名现场"
TRANSFER = "许可转交"
HOST_ONLY = "仅主持人"

SHARINGS = (STAGE, ANONYMOUS_STAGE, TRANSFER, HOST_ONLY)

# 公开范围不是简单的等级：转交许可和上台许可是两件事。
SCOPE_FLAGS = {
    STAGE: {
        "stage_verbatim": True,
        "stage_anonymized": True,
        "transfer_permitted": False,
        "host_only": False,
    },
    ANONYMOUS_STAGE: {
        "stage_verbatim": False,
        "stage_anonymized": True,
        "transfer_permitted": False,
        "host_only": False,
    },
    TRANSFER: {
        "stage_verbatim": False,
        "stage_anonymized": False,
        "transfer_permitted": True,
        "host_only": False,
    },
    HOST_ONLY: {
        "stage_verbatim": False,
        "stage_anonymized": False,
        "transfer_permitted": False,
        "host_only": True,
    },
}


@dataclass(frozen=True)
class Need:
    """问句要求解除的一个事实点。"""

    key: str
    label: str
    category: str
    topic: str
    conditions: tuple[str, ...] = ()
    entity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["conditions"] = list(self.conditions)
        return d


@dataclass(frozen=True)
class Hypothesis:
    """提问人转述或措辞中预设、但不能当成事实的内容。"""

    key: str
    label: str
    source_text: str
    kind: str = "reported"  # reported / presupposition / warning
    entity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Fact:
    """从实际发言中解析出的事实。"""

    key: str
    label: str
    category: str
    topic: str
    value: str
    polarity: str = "unknown"  # positive / negative / unknown
    conditions: tuple[str, ...] = ()
    entity: str | None = None
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["conditions"] = list(self.conditions)
        return d


@dataclass
class Interpretation:
    needs: list[Need] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs": [n.to_dict() for n in self.needs],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "facts": [f.to_dict() for f in self.facts],
            "exclusions": self.exclusions,
            "entities": self.entities,
        }


def row_id(prefix: str, existing: list[dict[str, Any]]) -> str:
    """生成 Q10/R4/D6 这类连续编号。"""
    top = 0
    for row in existing:
        value = str(row.get("id", ""))
        if value.startswith(prefix):
            digits = value[len(prefix) :]
            if digits.isdigit():
                top = max(top, int(digits))
    return f"{prefix}{top + 1}"
