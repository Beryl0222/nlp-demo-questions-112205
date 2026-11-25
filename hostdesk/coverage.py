"""事实点覆盖、部分合用和实际答复消解规则。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Fact, Need


@dataclass(frozen=True)
class Relation:
    level: str  # full / partial / none
    reason: str = ""
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "reason": self.reason, "missing": list(self.missing)}


_FULL_ALIASES = {
    ("manufacturing.prototype_status", "manufacturing.prototype_status"),
}

# 方向为“提问人的需要 -> 候选问法实际问出的需要”。
_PARTIAL: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("payment.payer_identity", "customer.identity_broad"): (
        "实际问法只宽泛询问现有客户，没有要求“已付费”并排除试用",
        ("已付费限定", "试用不计入"),
    ),
    ("payment.payer_identity", "payment.payer_scenario"): (
        "只问支付客户所在场景，未问清具体主体",
        ("具体是哪一家",),
    ),
    ("payment.entity_status", "payment.payer_scenario"): (
        "只问一般支付场景，未覆盖指定对象；只有得到“所有对象均未付”的全称回答时才能间接消解",
        ("指定对象的付款状态",),
    ),
    ("payment.payer_identity", "payment.exists"): (
        "只问是否存在非试用付款，未问出具体是哪一家",
        ("具体付费主体",),
    ),
    ("payment.entity_status", "payment.exists"): (
        "只问一般付款是否存在，未覆盖指定对象；全称否定时间接有效",
        ("指定对象的付款状态",),
    ),
    ("trial.renewal_payment_status", "payment.payer_renewed_trial_same"): (
        "问的是付款方与续订试用方是否同一，可能间接引出付款状态，但没有直接要求说明续订是否付款",
        ("续订付款状态",),
    ),
    ("trial.renewed_scene_shop", "trial.scene"): (
        "只问一般试用场景，未确认“已经续订”这一限定",
        ("续订是否存在", "已续订协议是否在商铺"),
    ),
    ("medical.patient_screen_confirmation", "medical.bilingual_formal_test"): (
        "正式测过双语字幕不等于经过患者看屏确认",
        ("患者实际看屏确认",),
    ),
    ("medical.hospital_confirmed_languages", "medical.bilingual_formal_test"): (
        "只问是否正式测过双语，不等于列出医院场景确认的语言",
        ("医院场景确认了哪些语言",),
    ),
    ("readability.two_line_line_of_sight", "readability.gallery_observation"): (
        "展馆读屏观察可能提供线索，但没有限定视线范围和两行读完指标",
        ("视线范围", "两行能否读完"),
    ),
    ("medical.hospital_event_status", "medical.bilingual_formal_test"): (
        "正式测试以医院进场为前提，但问“有没有正式测”不能单独说明那场演练是否已举行",
        ("医院演练/进场本身的状态",),
    ),
}

# 两个原问句之间的近义桥接。这里刻意只给 partial，防止传递性全面合并。
_PAIR_BRIDGES: dict[frozenset[str], tuple[str, tuple[str, ...]]] = {
    frozenset(
        ("trial.scene", "trial.renewed_scene_shop")
    ): (
        "都涉及试用场景/商铺；一般场景回答可覆盖当前商铺，但不能确认“已续订”限定",
        ("续订限定",),
    ),
    frozenset(
        ("trial.renewal_status", "trial.renewed_scene_shop")
    ): (
        "都以续订是否存在为前提；“没有续订”会同时消解两问，若有续订则仍需问商铺位置",
        ("续订发生后的商铺位置",),
    ),
    frozenset(
        ("trial.renewal_status", "trial.renewal_payment_status")
    ): (
        "都要求先核实续订是否发生；“没有续订”会使付款问法失去前提，若有续订则仍需付款状态",
        ("续订发生后的付款状态",),
    ),
    frozenset(
        ("payment.payer_identity", "trial.renewal_payment_status")
    ): (
        "都碰到付款事实，但前者要非试用的具体付费客户，后者只问续订账单是否支付",
        ("非试用限定", "具体主体", "续订账单限定"),
    ),
    frozenset(
        ("payment.payer_identity", "order.customer_identity")
    ): (
        "都指向某个客户主体，但订单/签约主体不能直接当成已付款客户",
        ("付款状态", "是否仅签约/赊账", "试用或订单限定"),
    ),
    frozenset(
        ("payment.exists", "order.exists")
    ): (
        "都像在问“有没有单”，但订单成立与款项到账是两个事实",
        ("订单/签约状态", "实际付款状态"),
    ),
    frozenset(
        ("payment.payer_identity", "payment.payer_scenario")
    ): (
        "都要求支付客户事实，但场景类型不能代替具体主体",
        ("具体主体",),
    ),
    frozenset(
        ("medical.hospital_event_status", "medical.hospital_confirmed_languages")
    ): (
        "都依赖医院试验是否发生；医院未进场会否定语言确认的前提，但不会产生已确认语言",
        ("未来医院试验后的语言确认",),
    ),
    frozenset(
        ("medical.hospital_event_status", "medical.patient_screen_confirmation")
    ): (
        "都依赖医院场景；未进场可推出目前没有患者看屏确认，但未来确认仍待跟进",
        ("未来患者看屏确认",),
    ),
    frozenset(
        ("medical.patient_screen_confirmation", "medical.hospital_confirmed_languages")
    ): (
        "都要求医院实地确认，但患者看屏与确认语言清单不是同一事实",
        ("患者看屏", "语言清单"),
    ),
}


def relation_between(need: Need, candidate: Need, *, include_bridges: bool = False) -> Relation:
    if need.key == candidate.key and need.entity == candidate.entity:
        # 条件越多越严格；候选若漏掉条件，只能算部分覆盖。
        missing = tuple(c for c in need.conditions if c not in candidate.conditions)
        if missing:
            return Relation("partial", "规范事实点相同，但候选问法漏掉限定", missing)
        return Relation("full", "同一规范事实点且限定兼容")

    pair = (need.key, candidate.key)
    if pair in _PARTIAL:
        reason, missing = _PARTIAL[pair]
        return Relation("partial", reason, missing)

    if include_bridges:
        bridge = _PAIR_BRIDGES.get(frozenset((need.key, candidate.key)))
        if bridge:
            reason, missing = bridge
            return Relation("partial", reason, missing)

    # 反向查询直接部分关系，用于两个原问句的两两解释。
    reverse = _PARTIAL.get((candidate.key, need.key))
    if reverse:
        reason, missing = reverse
        return Relation("partial", reason, tuple(f"反向覆盖：{m}" for m in missing))

    return Relation("none")


@dataclass
class Resolution:
    status: str  # resolved / partial / entailed_negative / premise_rejected / unresolved
    reason: str
    evidence_ids: tuple[str, ...] = ()
    future_followup: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "future_followup": self.future_followup,
        }


def _facts_for(facts: list[Fact], key: str) -> list[Fact]:
    return [f for f in facts if f.key == key]


def resolve_need(need: Need, facts: list[Fact]) -> Resolution:
    """根据已经播出的团队发言判断事实点是否解除。"""

    if need.key == "payment.payer_identity":
        if any(f.key == "payment.status" and f.value == "unpaid" for f in facts):
            return Resolution("resolved", "发言明确表示尚无付款客户，因此不存在“哪一家已付费”。")
        if any(f.key == "payment.status" and f.value == "unstated" for f in facts):
            return Resolution("unresolved", "发言只说某商铺在试用且“没说付费”，付款状态仍未说明。")
        return Resolution("unresolved", "尚无任何足以确认非试用付费客户身份的发言。")

    if need.key == "payment.exists":
        if any(f.key == "payment.status" and f.value == "unpaid" for f in facts):
            return Resolution("resolved", "已明确尚无付款。")
        if any(f.key == "payment.status" and f.value == "unstated" for f in facts):
            return Resolution("unresolved", "“没说付费”不是“没有付费”。")
        return Resolution("unresolved", "尚未说明是否存在非试用付款。")

    if need.key == "payment.entity_status" and need.entity:
        exact = [f for f in _facts_for(facts, "payment.entity_status") if f.entity == need.entity]
        if exact and exact[0].value in {"paid", "unpaid"}:
            return Resolution("resolved", f"发言已直接说明{need.entity}的付款状态。")
        generic_unpaid = any(f.key == "payment.status" and f.value == "unpaid" for f in facts)
        if generic_unpaid:
            return Resolution(
                "partial",
                "有一般性未付款表述，但若不是全称判断，仍不能代替该特定对象的会后核实。",
            )
        return Resolution("unresolved", f"尚未核实{need.entity}的付款状态。")

    if need.key == "trial.scene":
        scene = _facts_for(facts, "trial.scene")
        gallery = _facts_for(facts, "gallery.event_time") + _facts_for(facts, "gallery.drill_status")
        if scene or gallery:
            bits = []
            if any(f.value == "商铺" for f in scene):
                bits.append("当前试用在商铺")
            if gallery:
                bits.append("馆方/展馆另有安排或演练，不能与当前商铺试用混为同一事实")
            return Resolution("resolved", "；".join(bits) + "。")
        return Resolution("unresolved", "尚未说明试用场景。")

    if need.key == "trial.renewed_scene_shop":
        renewal = _facts_for(facts, "trial.renewal_status")
        if any(f.value == "none" or f.polarity == "negative" for f in renewal):
            return Resolution("premise_rejected", "续订不存在，因此“已续订协议是否在商铺”的预设不成立。")
        shop = any(f.key == "trial.scene" and f.value == "商铺" for f in facts)
        if shop:
            return Resolution(
                "partial",
                "已知当前试用方在商铺，但发言没有确认它已经续订，不能补上加粗限定。",
            )
        return Resolution("unresolved", "尚未确认续订及其场景。")

    if need.key == "trial.renewal_status":
        renewal = _facts_for(facts, "trial.renewal_status")
        if renewal:
            return Resolution("resolved", "续订是否发生已有明确发言。")
        return Resolution("unresolved", "发言没有回答是否实际续订。")

    if need.key == "trial.renewal_payment_status":
        renewal = _facts_for(facts, "trial.renewal_status")
        if any(f.polarity == "negative" for f in renewal):
            return Resolution("premise_rejected", "没有续订时，续订付款问题的前提不成立。")
        if any(f.key == "payment.status" for f in facts if "续订" in f.evidence):
            return Resolution("resolved", "续订付款状态已说明。")
        return Resolution("unresolved", "尚未说明续订是否付款。")

    if need.key == "medical.hospital_event_status":
        hosp = _facts_for(facts, "hospital.drill_status")
        if hosp and hosp[0].value == "not_started":
            return Resolution("resolved", "团队明确说医院尚未安排进场，医院那场目前未发生。")
        schedule = _facts_for(facts, "hospital.drill_schedule")
        if schedule:
            return Resolution("partial", "已有计划时间，但尚未确认实际进场/演练。")
        return Resolution("unresolved", "尚未说明医院那场的状态。")

    if need.key == "medical.patient_screen_confirmation":
        hosp = any(f.key == "hospital.drill_status" and f.value == "not_started" for f in facts)
        if hosp:
            return Resolution(
                "entailed_negative",
                "医院尚未进场，可推出目前没有发生该院场景下的患者看屏确认；但患者确认本身仍需未来跟进。",
                future_followup=True,
            )
        if any(f.key == need.key for f in facts):
            return Resolution("resolved", "已有患者看屏确认的发言。")
        return Resolution("unresolved", "尚未说明患者看屏确认。", future_followup=True)

    if need.key == "medical.hospital_confirmed_languages":
        hosp = any(f.key == "hospital.drill_status" and f.value == "not_started" for f in facts)
        if hosp:
            return Resolution(
                "premise_rejected",
                "医院尚未进场，“医院试过后确认哪些语言”的前提暂不成立；展馆语言不能替代。",
                future_followup=True,
            )
        return Resolution("unresolved", "尚未获得医院场景确认的语言清单。", future_followup=True)

    if need.key == "readability.two_line_line_of_sight":
        if any(f.key == need.key for f in facts):
            return Resolution("resolved", "已有两行/视线范围读屏结果。")
        gallery = any(f.key == "readability.gallery_observation" for f in facts)
        if gallery:
            return Resolution("partial", "只有展馆读屏观察，未覆盖视线范围两行指标。")
        return Resolution("unresolved", "尚未说明视线范围内能否读完两行。")

    if need.key == "readability.gallery_observation":
        if any(f.key == need.key for f in facts):
            return Resolution("resolved", "已有展馆读屏表现观察。")
        if any(f.key == "gallery.drill_status" for f in facts):
            return Resolution("partial", "只确认展馆演练做过，未说明具体观察到的读屏表现。")
        return Resolution("unresolved", "尚未说明展馆演练观察到的读屏表现。")

    if need.key == "manufacturing.quote_100":
        fact = next((f for f in _facts_for(facts, "manufacturing.quote_100")), None)
        if fact and fact.value == "no_quote":
            return Resolution("resolved", "项目稿/发言明确量产还没有报价，因此一百件正式报价目前无法给出。")
        return Resolution("unresolved", "尚未说明一百件量产能否报价。")

    if need.key == "manufacturing.prototype_status":
        fact = next((f for f in _facts_for(facts, "manufacturing.prototype_status")), None)
        if fact and fact.value == "ready":
            return Resolution("resolved", "已确认手板/替代钢制样件状态。")
        return Resolution("unresolved", "尚未确认是否只有手板/样件。")

    if any(f.key == need.key for f in facts):
        return Resolution("resolved", "已有对应发言。")
    return Resolution("unresolved", "尚未被实际回录解除。")
