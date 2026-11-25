"""中文问句、草稿和回录的确定性语义规则。

规则不试图做开放域中文理解，只把路演主持中常见的“事实点 + 限定 +
预设”拆成可比较的规范键。临时问句只要使用相近措辞，也能得到同样的键。
"""
from __future__ import annotations

import re
from typing import Any

from .models import Fact, Hypothesis, Interpretation, Need


TOPIC_CATEGORY = {
    "payment": "客户核实",
    "customer": "客户核实",
    "order": "客户核实",
    "trial": "客户核实",
    "cabinet": "实际效果",
    "medical": "实际效果",
    "readability": "实际效果",
    "effect": "实际效果",
    "manufacturing": "量产",
}

ORG_RE = re.compile(
    r"[一-鿿A-Za-z0-9]{2,12}?(?:美术馆|博物馆|科技馆|研究院|门诊|医院|学校|大学|公司)"
)
PRONOUN_PARTS = ("哪", "这", "那", "什么", "怎么", "一个", "这家", "那家")
GENERIC_ORG_PARTS = ("屏幕", "展馆", "馆屏幕", "问题", "时候", "场景", "现场")
LEADING_ENTITY_STRIP = (
    "请别把",
    "不要把",
    "别把",
    "据说",
    "听说",
    "传称",
    "名叫",
    "叫做",
    "我们",
    "他们",
    "你们",
    "这个",
    "那个",
    "一家",
    "有个",
    "是",
    "叫",
    "在",
    "到",
    "去",
    "的",
    "了",
    "有",
    "个",
    "家",
    "把",
)

TEMP_RE = re.compile(r"(?:零下)?\s*([0-9]+|[一二三四五六七八九十两]+)\s*度")
PAYMENT_WORDS = ("付费", "付款", "支付", "付过钱", "付钱", "打款", "回款", "到账")
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[，。？?；;：:！!、]+|\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_entities(text: str) -> list[str]:
    found: list[str] = []
    for match in ORG_RE.finditer(text):
        name = match.group(0)
        # 最左匹配可能把“据说东津门诊”整个吃下；剥掉引导语，留下真正专名。
        changed = True
        while changed:
            changed = False
            for prefix in LEADING_ENTITY_STRIP:
                if name.startswith(prefix) and len(name) - len(prefix) >= 2:
                    name = name[len(prefix) :]
                    changed = True
                    break
        if any(p in name for p in PRONOUN_PARTS):
            continue
        if any(p in name for p in GENERIC_ORG_PARTS):
            continue
        if name not in found:
            found.append(name)
    return found


def _temp_value(clause: str) -> str | None:
    m = TEMP_RE.search(clause)
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        num = int(raw)
    else:
        num = CN_NUM.get(raw)
        if num is None:
            return None
    if "零下" in clause[: max(0, m.start()) + 3]:
        return f"零下{num}摄氏度"
    return f"{num}摄氏度"


def _has(clause: str, *words: str) -> bool:
    return any(w in clause for w in words)


def _add_need(
    needs: list[Need],
    key: str,
    label: str,
    topic: str,
    conditions: tuple[str, ...] = (),
    entity: str | None = None,
) -> None:
    category = TOPIC_CATEGORY[topic]
    need = Need(key, label, category, topic, tuple(conditions), entity)
    if not any(n.key == need.key and n.entity == entity and n.conditions == need.conditions for n in needs):
        needs.append(need)


def _question_needs(text: str) -> tuple[list[Need], list[str]]:
    needs: list[Need] = []
    exclusions: list[str] = []
    entities = extract_entities(text)
    clauses = split_clauses(text)

    # 先处理“订单先不问”这类显式排除，避免把它误建成订单争点。
    for clause in clauses:
        if _has(clause, "先不问", "先别问", "不要问", "不用问") and _has(clause, "订单", "付款", "付费"):
            exclusions.append(clause)

    for clause in clauses:
        excluded_order_clause = (
            _has(clause, "订单") and _has(clause, "先不", "先别", "不要", "不用")
        )
        if excluded_order_clause:
            continue

        temp = _temp_value(clause)
        if temp and _has(clause, "稳定", "正常", "能用", "使用", "启动", "开合", "可靠", "好用"):
            cond = [f"温度={temp}"]
            if _has(clause, "室外", "户外", "棚外"):
                cond.append("室外/棚外")
            if _has(clause, "机柜", "柜门"):
                cond.append("机柜/柜门")
            _add_need(
                needs,
                "effect.low_temperature_performance",
                "限定低温下设备/柜门能否稳定使用",
                "cabinet",
                tuple(cond),
            )

        if _has(clause, "柜门", "门") and _has(clause, "棚外", "室外", "户外", "低温") or (
            "柜门" in clause and temp
        ):
            cond = ["棚外/室外空间", "柜门"]
            if temp:
                cond.append(f"温度={temp}")
            _add_need(
                needs,
                "effect.cabinet_temperature",
                "棚外空间柜门在限定低温下能否使用",
                "cabinet",
                tuple(cond),
            )

        # 客户/付款：具体主体、场景类型、特定对象是三个不同事实点。
        # “订单”是独立事实：有订单不等于已付款，不能直接并入付费客户。
        if "订单" in clause:
            if _has(clause, "哪一家", "哪家", "哪个客户", "是谁"):
                _add_need(
                    needs,
                    "order.customer_identity",
                    "订单/签约客户是哪一家",
                    "order",
                    ("订单主体", "未等同于付款"),
                )
            if _has(clause, "有没有", "是否", "有吗", "多少", "几", "吗") or "订单" in clause:
                _add_need(
                    needs,
                    "order.exists",
                    "是否已有订单/签约",
                    "order",
                    ("订单状态", "不等同付款状态"),
                )

        payment_word = _has(clause, *PAYMENT_WORDS)
        if payment_word and _has(clause, "哪一家", "哪家", "是谁", "哪个客户", "哪个公司"):
            cond = ["已付费/已付款"]
            if re.search(r"试用[^。？?；;]{0,10}(?:不要算|不算|别算|排除)|(?:不要算|别把|不算)[^。？?；;]{0,6}试用", text):
                cond.append("排除仅试用客户")
            _add_need(
                needs,
                "payment.payer_identity",
                "已付费客户具体是哪一家",
                "payment",
                tuple(cond),
            )

        if payment_word and _has(clause, "哪类场景", "什么场景", "哪种场景"):
            _add_need(
                needs,
                "payment.payer_scenario",
                "哪类场景已有支付客户",
                "payment",
                ("已付费/已付款",),
            )

        if _has(clause, "同一家", "是不是同一家", "是否同一家") and (
            _has(clause, "续订", "试用") or _has(text, "续订", "已经续订")
        ):
            _add_need(
                needs,
                "payment.payer_renewed_trial_same",
                "支付客户是否与已续订试用方为同一主体",
                "payment",
                ("已续订试用",),
            )

        entity_in_clause = next((e for e in entities if e in clause), None)
        if payment_word and entity_in_clause and _has(clause, "到底", "有没有", "是否", "过", "没有"):
            _add_need(
                needs,
                "payment.entity_status",
                f"{entity_in_clause}是否付过钱",
                "payment",
                ("特定对象",),
                entity=entity_in_clause,
            )

        if _has(clause, "续订", "续费", "续签") and payment_word:
            _add_need(
                needs,
                "trial.renewal_payment_status",
                "续订/续费是否已经付款",
                "trial",
                ("已续订", "付款状态"),
            )

        if _has(clause, "现在有没有", "目前有没有", "是否有", "有没有") and payment_word and not any(
            k in clause for k in ("哪一家", "哪家", "哪类", "续订", "续费", entity_in_clause or "___")
        ):
            _add_need(needs, "payment.exists", "当前是否存在已付款客户", "payment", ("非试用",))

        # 宽泛的“现在有哪个客户”是主体事实，但没有付款限定。
        if _has(clause, "哪个客户", "哪些客户", "有哪个", "现在有谁") and not payment_word:
            _add_need(
                needs,
                "customer.identity_broad",
                "当前提到的客户是谁/是什么状态",
                "customer",
                ("未限定是否付费",),
            )

        # 试用场景与续订。
        if _has(clause, "续订", "续费", "续签") and _has(clause, "商铺", "商店", "门店"):
            _add_need(
                needs,
                "trial.renewed_scene_shop",
                "已续订的试用协议是否位于商铺",
                "trial",
                ("已续订", "候选场景=商铺"),
            )

        if _has(clause, "试用协议", "签试用", "试用的是", "已经签试用", "试用是") and _has(
            clause, "场景", "馆内还是商铺", "馆内", "商铺", "哪里", "哪种"
        ) and not ("续订" in clause and _has(clause, "商铺", "商店", "门店")):
            _add_need(
                needs,
                "trial.scene",
                "已签试用协议的使用场景（馆内/商铺等）",
                "trial",
                ("场景选择",),
            )

        if _has(clause, "实际续订", "有没有续订", "是否续订", "续订了吗", "有无续订") or (
            "续订" in clause
            and _has(clause, "有没有", "是否", "吗")
            and not payment_word
            and "续订的" not in clause
        ):
            _add_need(
                needs,
                "trial.renewal_status",
                "试用是否已有实际续订",
                "trial",
                ("续订是否发生",),
            )

        # P2：医院、患者确认、语言和读屏距离是不同限定。
        if _has(clause, "患者") and _has(clause, "看屏", "屏幕", "读屏") and _has(clause, "确认"):
            _add_need(
                needs,
                "medical.patient_screen_confirmation",
                "双语字幕是否经过患者实际看屏确认",
                "medical",
                ("患者视角", "看屏确认", "双语字幕"),
            )

        if _has(clause, "双语字幕") and _has(clause, "正式测", "测过", "测试", "验过"):
            _add_need(
                needs,
                "medical.bilingual_formal_test",
                "医院场景是否正式测过双语字幕",
                "medical",
                ("医院场景", "正式测试"),
            )

        if _has(clause, "哪些语言", "什么语言", "哪几种语言"):
            cond = ["医院试验后确认"]
            if _has(text, "别把展馆屏幕当医院屏幕", "不要把展馆屏幕当医院屏幕"):
                cond.append("不得以展馆结果替代医院结果")
            _add_need(
                needs,
                "medical.hospital_confirmed_languages",
                "医院试验后确认了哪些语言",
                "medical",
                tuple(cond),
            )

        if _has(clause, "视线", "视距", "距离", "三米", "五米") and _has(clause, "两行", "二行", "2行", "两行字"):
            _add_need(
                needs,
                "readability.two_line_line_of_sight",
                "视线范围内能否读完两行",
                "readability",
                ("两行", "视线范围"),
            )

        if _has(clause, "展馆演练", "馆内演练", "展馆练习") and _has(clause, "读屏", "读屏表现", "观察"):
            _add_need(
                needs,
                "readability.gallery_observation",
                "展馆演练观察到何种读屏表现",
                "readability",
                ("展馆场景，不得替代医院",),
            )

        if _has(clause, "医院") and _has(clause, "那场", "那次", "演练", "进场", "安排", "过去", "进行了吗") and _has(
            clause, "过去", "进行", "了吗", "安排", "进场", "是否", "有没有"
        ):
            _add_need(
                needs,
                "medical.hospital_event_status",
                "医院那场演练/进场是否已经发生或安排",
                "medical",
                ("医院场景",),
            )

        # P3：一百件报价与手板/样件状态。
        if _has(clause, "一百件", "100件", "百件") and _has(clause, "报价", "价格", "多少钱", "什么价", "单价"):
            _add_need(
                needs,
                "manufacturing.quote_100",
                "量产一百件能否正式报价",
                "manufacturing",
                ("数量=100件", "量产"),
            )

        if _has(clause, "手板", "样件", "样品") and _has(clause, "只有", "已做", "准备好", "好了", "交"):
            _add_need(
                needs,
                "manufacturing.prototype_status",
                "是否仅完成手板/替代钢制样件",
                "manufacturing",
                ("原型/样件状态",),
            )

    # 有些问句跨小句，例如“量产是否能按一百件报价，还是只有手板已做？”
    if "量产" in text and _has(text, "报价", "价格", "多少钱", "什么价", "单价") and _has(text, "一百件", "100件", "百件"):
        _add_need(
            needs,
            "manufacturing.quote_100",
            "量产一百件能否正式报价",
            "manufacturing",
            ("数量=100件", "量产"),
        )
    if _has(text, "手板", "样件"):
        _add_need(
            needs,
            "manufacturing.prototype_status",
            "是否仅完成手板/替代钢制样件",
            "manufacturing",
            ("原型/样件状态",),
        )

    return needs, exclusions


def _hypotheses(text: str) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []
    entities = extract_entities(text)

    for match in re.finditer(r"据说([^，。；!?？]+?)(看过|去过|来过|试过|已经)", text):
        body = match.group(1).strip()
        entity = next((e for e in entities if e in body), None)
        hypotheses.append(
            Hypothesis(
                "report." + (entity or body[:12]),
                f"提问人转述：{body}{match.group(2)}（不得当作已证实事实）",
                match.group(0),
                "reported",
                entity,
            )
        )

    if _has(text, "医院试过以后", "医院试过之后", "医院试过后"):
        hypotheses.append(
            Hypothesis(
                "presupposition.hospital_trial_done",
                "措辞预设“医院已经试过”；该预设需先核实，不能直接接受",
                "医院试过以后",
                "presupposition",
                "医院",
            )
        )

    if re.search(r"有续订的?试用", text):
        hypotheses.append(
            Hypothesis(
                "presupposition.renewal_exists",
                "措辞预设“存在已续订的试用协议”；应先核实续订是否发生",
                "有续订的试用协议",
                "presupposition",
            )
        )

    m = re.search(r"别把([^，。；]+?)当([^，。；]+)", text)
    if m:
        hypotheses.append(
            Hypothesis(
                "warning.gallery_not_hospital",
                f"限定：不得把{m.group(1).strip()}替代为{m.group(2).strip()}",
                m.group(0),
                "warning",
            )
        )

    return hypotheses


def interpret_question(text: str) -> Interpretation:
    needs, exclusions = _question_needs(text)
    return Interpretation(
        needs=needs,
        hypotheses=_hypotheses(text),
        exclusions=exclusions,
        entities=extract_entities(text),
    )


def _add_fact(facts: list[Fact], key: str, label: str, value: str, topic: str, **kwargs: Any) -> None:
    fact = Fact(key=key, label=label, category=TOPIC_CATEGORY[topic], topic=topic, value=value, **kwargs)
    if not any(f.key == fact.key and f.entity == fact.entity and f.value == fact.value for f in facts):
        facts.append(fact)


def interpret_answer(text: str) -> Interpretation:
    """解析团队/项目稿中的公开事实。

    “没说付费”是 unknown，而不是“未付费”：这是数据中最容易误判的点。
    """
    interp = interpret_question(text)
    facts: list[Fact] = []
    entities = extract_entities(text)
    clauses = split_clauses(text)

    for clause in clauses:
        entity = next((e for e in entities if e in clause), None)

        if "订单" in clause and re.search(r"没有|没|未|并无|暂无", clause):
            _add_fact(
                facts,
                "order.exists",
                "目前没有订单",
                "no_order",
                "order",
                polarity="negative",
                evidence=clause,
            )
        elif "订单" in clause and re.search(r"有|拿到|签了|签约|成交", clause):
            _add_fact(
                facts,
                "order.exists",
                "已有订单/签约",
                "exists",
                "order",
                polarity="positive",
                evidence=clause,
            )

        # 付款状态：先识别“没说/未说明”，不能被后面的否定规则吞掉。
        if re.search(r"(?:没|未)(?:有)?说(?:过|明)?[^，。；]{0,8}(?:付费|付款|支付|付钱|打款|回款|到账)", clause):
            _add_fact(
                facts,
                "payment.status",
                "付款状态未被明确说明",
                "unstated",
                "payment",
                polarity="unknown",
                entity=entity,
                evidence=clause,
            )
        elif entity and re.search(r"(?:已经|已)?(?:付过钱|付了款|付费|付款|支付|打款|回款|到账)", clause) and not re.search(
            r"(?:没有|没|未|并不).{0,6}(?:付过钱|付了款|付费|付款|支付|打款|回款|到账)", clause
        ):
            _add_fact(
                facts,
                "payment.entity_status",
                f"{entity}被表述为已付款",
                "paid",
                "payment",
                polarity="positive",
                entity=entity,
                evidence=clause,
            )
        elif entity and re.search(r"(?:没有|没|未|并不).{0,8}(?:付过钱|付了款|付费|付款|支付)", clause):
            _add_fact(
                facts,
                "payment.entity_status",
                f"{entity}被表述为未付款",
                "unpaid",
                "payment",
                polarity="negative",
                entity=entity,
                evidence=clause,
            )
        elif re.search(r"(?:没有|没|未|并无).{0,8}(?:付费|付款|支付|付钱|打款|回款|到账)", clause):
            _add_fact(
                facts,
                "payment.status",
                "被表述为尚未付款/付费",
                "unpaid",
                "payment",
                polarity="negative",
                entity=entity,
                evidence=clause,
            )

        if _has(clause, "续订", "续费", "续签") and re.search(r"没有|没|未|并无|不存在", clause):
            _add_fact(
                facts,
                "trial.renewal_status",
                "续订/续费尚未发生",
                "none",
                "trial",
                polarity="negative",
                evidence=clause,
            )
        elif _has(clause, "已经续订", "已续订", "有续订", "续费了", "续签了"):
            _add_fact(
                facts,
                "trial.renewal_status",
                "续订/续费已经发生",
                "active",
                "trial",
                polarity="positive",
                evidence=clause,
            )

        if _has(clause, "商铺", "商店", "门店") and _has(clause, "试用"):
            _add_fact(
                facts,
                "trial.scene",
                "当前试用场景为商铺",
                "商铺",
                "trial",
                polarity="positive",
                conditions=("当前/在试用",),
                evidence=clause,
            )

        if _has(clause, "签试用协议", "签了试用", "有一家签试用"):
            _add_fact(
                facts,
                "trial.exists",
                "存在一家已签试用协议的主体",
                "exists",
                "trial",
                polarity="positive",
                evidence=clause,
            )

        if _has(clause, "那家馆", "馆") and _has(clause, "下周"):
            _add_fact(
                facts,
                "gallery.event_time",
                "馆方相关安排在下周，尚未作为已完成演练",
                "下周/未来",
                "medical",
                polarity="unknown",
                conditions=("展馆/馆方",),
                evidence=clause,
            )

        if _has(clause, "展馆练过", "馆内演练", "展馆演练", "完成过馆内演练", "展馆") and _has(
            clause, "练过", "演练", "完成", "做过"
        ):
            _add_fact(
                facts,
                "gallery.drill_status",
                "展馆/馆内演练已经完成",
                "done",
                "medical",
                polarity="positive",
                conditions=("展馆场景",),
                evidence=clause,
            )

        if _has(clause, "医院") and re.search(r"还没|尚未|未|没有", clause) and _has(
            clause, "安排", "进场", "演练", "开始"
        ):
            _add_fact(
                facts,
                "hospital.drill_status",
                "医院进场/演练尚未安排或尚未发生",
                "not_started",
                "medical",
                polarity="negative",
                conditions=("医院场景",),
                evidence=clause,
            )
        elif _has(clause, "医院") and _has(clause, "下个月", "下月") and _has(clause, "安排", "演练"):
            _add_fact(
                facts,
                "hospital.drill_schedule",
                "医院演练计划在下个月安排",
                "next_month_planned",
                "medical",
                polarity="unknown",
                conditions=("医院场景",),
                evidence=clause,
            )

        if _has(clause, "样件", "样品", "手板") and _has(clause, "准备好", "好了", "完成", "已做"):
            _add_fact(
                facts,
                "manufacturing.prototype_status",
                "替代钢制样件/手板已准备",
                "ready",
                "manufacturing",
                polarity="positive",
                evidence=clause,
            )

        if _has(clause, "量产") and _has(clause, "没有报价", "还没报价", "未报价", "无报价"):
            _add_fact(
                facts,
                "manufacturing.quote_100",
                "量产报价尚不存在",
                "no_quote",
                "manufacturing",
                polarity="negative",
                conditions=("量产",),
                evidence=clause,
            )

        if _has(clause, "只允许会后", "只在会后", "会后台下", "台下交流"):
            _add_fact(
                facts,
                "privacy.offline_only",
                "有对象只允许会后台下交流",
                "offline_only",
                "customer",
                polarity="unknown",
                evidence=clause,
            )

    interp.facts = facts
    # 答案中若出现机构名，也保留实体，便于隐私扫描。
    interp.entities = list(dict.fromkeys(interp.entities + entities))
    return interp


def infer_category(text: str, fallback_project: str | None = None) -> str | None:
    """从发言内容推断公布的议题类别。"""
    interp = interpret_answer(text)
    counts: dict[str, int] = {}
    for item in [*interp.facts, *interp.needs]:
        counts[item.category] = counts.get(item.category, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    if _has(text, "客户", "付费", "付款", "试用", "续订", "订单"):
        return "客户核实"
    if _has(text, "医院", "字幕", "读屏", "柜门", "五度", "低温", "零下", "启动", "开合", "效果"):
        return "实际效果"
    if _has(text, "量产", "报价", "手板", "样件"):
        return "量产"
    return None
