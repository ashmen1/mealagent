from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from backend.core.dialogue_constraint_contract import (
    CONSTRAINT_OUTPUT_SCHEMA,
    CUISINES,
    DISH_FIELDS,
    DISH_TYPES,
    DialogueConstraintExtractionError,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_REQUIREMENT_FIELDS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
    SPECIAL_POPULATIONS,
    TASTE_PREFERENCES,
    TOP_LEVEL_FIELDS,
)
from backend.infrastructure.database.ingredient_repository import (
    IngredientRepositoryError,
    load_ingredient_constraint_values,
)


SessionFactory = Callable[[], Session]


class DialogueConstraintService:
    """使用长期复用的LLM提取单轮对话约束。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: Callable[[str], object],
    ) -> None:
        if not callable(session_factory):
            raise DialogueConstraintExtractionError(500, "Session工厂无效")
        if not callable(llm_client):
            raise DialogueConstraintExtractionError(500, "LLM约束提取器无效")
        self._session_factory = session_factory
        self._llm_client = llm_client

    def extract(self, dialogue: dict[str, Any]) -> dict[str, Any]:
        """提取一条单轮对话的菜单约束。"""

        dialogue_id, user_message = _validate_dialogue(dialogue)
        try:
            session = self._session_factory()
        except Exception as exc:
            raise DialogueConstraintExtractionError(
                500, "数据库 Session 创建失败"
            ) from exc
        if not isinstance(session, Session):
            raise DialogueConstraintExtractionError(500, "数据库 Session 无效")
        with session:
            return _extract_single_turn_constraints(
                dialogue,
                dialogue_id,
                user_message,
                self._llm_client,
                session,
            )


def _extract_single_turn_constraints(
    dialogue: dict[str, Any],
    dialogue_id: int,
    user_message: str,
    llm_client: Callable[[str], object],
    session: Session,
) -> dict[str, Any]:
    """从一条单轮对话中提取经过严格校验的菜单约束。"""

    ingredient_names, ingredient_categories = _load_ingredient_values(session)
    prompt = _build_prompt(
        dialogue,
        ingredient_categories,
    )

    try:
        return _extract_validated_result(
            prompt,
            dialogue_id,
            user_message,
            ingredient_names,
            ingredient_categories,
            llm_client,
        )
    except DialogueConstraintExtractionError as exc:
        if exc.status_code != 502:
            raise
        # LLM 概率输出偶发违例（如 evidence 不是原文连续片段），重试一次；
        # 再次违例仍按 502 抛出，不静默吞掉
        return _extract_validated_result(
            prompt,
            dialogue_id,
            user_message,
            ingredient_names,
            ingredient_categories,
            llm_client,
        )


def _extract_validated_result(
    prompt: str,
    dialogue_id: int,
    user_message: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
    llm_client: Callable[[str], object],
) -> dict[str, Any]:
    """调用 LLM 一次并完成结构校验与数字归一化。"""

    try:
        result = llm_client(prompt)
    except (TimeoutError, ConnectionError) as exc:
        raise DialogueConstraintExtractionError(
            503,
            "LLM服务请求超时或不可用",
        ) from exc

    if not isinstance(result, dict):
        raise DialogueConstraintExtractionError(
            502,
            "LangChain必须返回结构化对象",
        )
    normalized = _normalize_llm_numeric_fields(result)
    _validate_result(
        normalized,
        dialogue_id,
        user_message,
        ingredient_names,
        ingredient_categories,
    )
    return normalized


def _validate_dialogue(dialogue: object) -> tuple[int, str]:
    if not isinstance(dialogue, dict):
        raise DialogueConstraintExtractionError(400, "对话必须是对象")

    dialogue_id = dialogue.get("id")
    if type(dialogue_id) is not int or dialogue_id <= 0:
        raise DialogueConstraintExtractionError(400, "id必须是正整数")

    turn_count = dialogue.get("turn_count")
    if type(turn_count) is not int or turn_count != 1:
        raise DialogueConstraintExtractionError(400, "turn_count必须等于1")

    user_messages = dialogue.get("user_messages")
    if not isinstance(user_messages, list) or len(user_messages) != 1:
        raise DialogueConstraintExtractionError(
            400,
            "user_messages必须是长度为1的数组",
        )
    user_message = user_messages[0]
    if not isinstance(user_message, str) or not user_message.strip():
        raise DialogueConstraintExtractionError(
            400,
            "user_messages必须包含一条非空字符串",
        )

    return dialogue_id, user_message


def _load_ingredient_values(session: Session) -> tuple[set[str], set[str]]:
    try:
        return load_ingredient_constraint_values(session)
    except IngredientRepositoryError as exc:
        raise DialogueConstraintExtractionError(
            500,
            str(exc),
        ) from exc


def _normalize_llm_numeric_fields(
    result: dict[str, Any],
) -> dict[str, Any]:
    """归一化 LLM 输出的数字字段。

    百炼 qwen 对 anyOf[integer, null] 声明的字段会输出字符串数字
    （如 \"2\"、\"None\"），这里做确定性归一：纯数字字符串转整数，
    null/None 字符串转 None；其余值原样保留，由校验层拒绝。
    """

    for field in ("diner_count", "max_total_time_minutes"):
        if field in result:
            result[field] = _normalize_optional_integer(result[field])
    for dish in result.get("dishes", []):
        if isinstance(dish, dict) and "count" in dish:
            dish["count"] = _normalize_optional_integer(dish["count"])
    return result


def _normalize_optional_integer(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        if stripped.lower() in {"null", "none"}:
            return None
    return value


def _build_prompt(
    dialogue: Mapping[str, Any],
    ingredient_categories: set[str],
) -> str:
    # 工具调用的 tools 参数已携带完整 JSON Schema，提示词不再重复；
    # 食材标准名交由代码层校验，提示词只给高频示例与规则
    allowed_values = {
        "meal_periods": sorted(MEAL_PERIODS),
        "dish_type": sorted(DISH_TYPES),
        "taste_preferences.keys": sorted(TASTE_PREFERENCES),
        "cuisines": sorted(CUISINES),
        "effects": sorted(EFFECTS),
        "special_populations": sorted(SPECIAL_POPULATIONS),
        "required_ingredients.kind": sorted(INGREDIENT_REQUIREMENT_KINDS),
        "category": sorted(ingredient_categories),
        "concept": sorted(INGREDIENT_CONCEPTS),
    }
    examples = _build_golden_examples()

    sections = [
        "你负责从一条中文单轮对话中提取菜单约束，通过工具调用返回结果。"
        "字段类型与必填项以工具参数定义为准，全部数字字段输出 JSON 数字"
        "（不带引号），未明确时为 null。",
        "字段允许值：\n"
        + json.dumps(allowed_values, ensure_ascii=False, indent=2),
        (
            "归一规则：微辣、香辣、麻辣归一为is_spicy=true；不辣归一为"
            "is_spicy=false；咸鲜归一为is_salty=true。暖胃、胃口不好、养胃、"
            "健胃消食、便秘归一为养胃健胃消食；夜宵归一为晚餐；公司、上班、"
            "下班归一为上班族；仪式感归一为西餐风味；清爽、别太抢味归一为"
            "is_light=true。面保留为kind=concept、value=面，不提前展开。"
            "available_ingredients只保存可用核心食材；盐、油、水等辅料无需列入，"
            "也不表示列出的食材必须全部使用。没有既定映射的描述直接忽略。"
        ),
        (
            "食材名规则：ingredient 的值使用常见标准名称，如番茄、鸡蛋、土豆、"
            "猪肉、牛肉、鸡肉、鱼、虾、白菜、豆腐、米饭、面条；用户用了同义说法"
            "（如西红柿、马铃薯）时归一到标准名称；无法确定时输出用户原文说法。"
        ),
        (
            "证据规则：每个非空约束必须在evidence中给出连续用户原文。使用叶子路径，"
            "例如meal_periods[0]、dishes[0].count、"
            "dishes[0].taste_preferences.is_spicy、"
            "dishes[0].required_ingredients[0].value。required_ingredients.kind"
            "不单独提供证据。dialogue_id、null、[]、{}和默认未指定Dish不需要证据。"
        ),
        "参考示例：\n"
        + "\n".join(
            "用户原文："
            + message
            + "\n对应结果："
            + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            for message, result in examples
        ),
        (
            "当前对话绑定规则：输出 dialogue_id 必须原样复制当前对话的id，"
            f"本次必须输出 dialogue_id={dialogue['id']}，不得复制示例id。"
        ),
        "当前对话（必须在上述示例之后处理）：\n"
        + json.dumps(dialogue, ensure_ascii=False, separators=(",", ":")),
    ]
    return "\n\n".join(sections)


def _build_empty_dish() -> dict[str, Any]:
    return {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredients": [],
    }


def _build_example_result(
    dialogue_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    result = {
        "dialogue_id": dialogue_id,
        "meal_periods": [],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_build_empty_dish()],
        "evidence": {},
    }
    result.update(overrides)
    return result


def _build_golden_examples() -> list[tuple[str, dict[str, Any]]]:
    empty_dish = _build_empty_dish
    return [
        (
            "今晚吃啥比较好？",
            _build_example_result(
                1,
                meal_periods=["晚餐"],
                evidence={"meal_periods[0]": "今晚"},
            ),
        ),
        (
            "晚上两个人吃",
            _build_example_result(
                2,
                meal_periods=["晚餐"],
                diner_count=2,
                evidence={
                    "meal_periods[0]": "晚上",
                    "diner_count": "两个人",
                },
            ),
        ),
        (
            "家里现在就剩番茄、鸡蛋和土豆了，这顿饭还能怎么弄？",
            _build_example_result(
                3,
                available_ingredients=["番茄", "鸡蛋", "土豆"],
                evidence={
                    "available_ingredients[0]": "番茄",
                    "available_ingredients[1]": "鸡蛋",
                    "available_ingredients[2]": "土豆",
                },
            ),
        ),
        (
            "我今晚有点想吃面，再帮我配个别太抢味的小菜。",
            _build_example_result(
                4,
                meal_periods=["晚餐"],
                dishes=[
                    {
                        **empty_dish(),
                        "count": 1,
                        "dish_type": "主食",
                        "required_ingredients": [
                            {"kind": "concept", "value": "面"}
                        ],
                    },
                    {
                        **empty_dish(),
                        "count": 1,
                        "dish_type": "小菜",
                        "taste_preferences": {"is_light": True},
                    },
                ],
                evidence={
                    "meal_periods[0]": "今晚",
                    "dishes[0].count": "面",
                    "dishes[0].dish_type": "面",
                    "dishes[0].required_ingredients[0].value": "面",
                    "dishes[1].count": "配个",
                    "dishes[1].dish_type": "小菜",
                    "dishes[1].taste_preferences.is_light": "别太抢味",
                },
            ),
        ),
    ]


def _validate_result(
    result: dict[str, Any],
    dialogue_id: int,
    user_message: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> None:
    _require_exact_fields(result, TOP_LEVEL_FIELDS, "顶层")
    _validate_top_level_types(result)

    if result["dialogue_id"] != dialogue_id:
        _invalid_response("dialogue_id必须等于输入id")

    _validate_optional_positive_integer(result["diner_count"], "diner_count")
    _validate_optional_positive_integer(
        result["max_total_time_minutes"],
        "max_total_time_minutes",
    )
    _validate_string_array(result["meal_periods"], "meal_periods")
    _validate_string_array(
        result["available_ingredients"],
        "available_ingredients",
    )
    _require_allowed_values(result["meal_periods"], MEAL_PERIODS, "meal_periods")
    _require_allowed_values(
        result["available_ingredients"],
        ingredient_names,
        "available_ingredients",
    )

    dishes = result["dishes"]
    if not dishes:
        _invalid_response("dishes至少包含一项")
    _require_no_duplicates(dishes, "dishes")
    for index, dish in enumerate(dishes):
        _validate_dish(
            dish,
            index,
            ingredient_names,
            ingredient_categories,
        )

    expected_evidence_paths = _collect_evidence_paths(result)
    evidence = result["evidence"]
    if set(evidence) != expected_evidence_paths:
        _invalid_response("evidence路径必须与所有非空约束精确对应")
    for path, fragment in evidence.items():
        if not isinstance(path, str) or not isinstance(fragment, str):
            _invalid_response("evidence的键和值必须是字符串")
        if not fragment.strip() or fragment not in user_message:
            _invalid_response(f"evidence不是连续非空用户原文：{path}")


def _validate_top_level_types(result: Mapping[str, Any]) -> None:
    if type(result["dialogue_id"]) is not int:
        _invalid_response("dialogue_id必须是整数")
    if not isinstance(result["meal_periods"], list):
        _invalid_response("meal_periods必须是数组")
    if not isinstance(result["available_ingredients"], list):
        _invalid_response("available_ingredients必须是数组")
    if not isinstance(result["dishes"], list):
        _invalid_response("dishes必须是数组")
    if not isinstance(result["evidence"], dict):
        _invalid_response("evidence必须是对象")


def _validate_dish(
    dish: object,
    dish_index: int,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> None:
    location = f"dishes[{dish_index}]"
    if not isinstance(dish, dict):
        _invalid_response(f"{location}必须是对象")
    _require_exact_fields(dish, DISH_FIELDS, location)

    _validate_optional_positive_integer(dish["count"], f"{location}.count")
    if not isinstance(dish["dish_type"], str):
        _invalid_response(f"{location}.dish_type必须是字符串")
    if dish["dish_type"] not in DISH_TYPES:
        _invalid_response(f"{location}.dish_type不在允许值中")

    tastes = dish["taste_preferences"]
    if not isinstance(tastes, dict):
        _invalid_response(f"{location}.taste_preferences必须是对象")
    if not set(tastes).issubset(TASTE_PREFERENCES):
        _invalid_response(f"{location}.taste_preferences包含非法键")
    if any(type(value) is not bool for value in tastes.values()):
        _invalid_response(f"{location}.taste_preferences值必须是布尔值")

    arrays_and_allowed_values = (
        ("cuisines", CUISINES),
        ("effects", EFFECTS),
        ("special_populations", SPECIAL_POPULATIONS),
    )
    for field, allowed_values in arrays_and_allowed_values:
        value = dish[field]
        _validate_string_array(value, f"{location}.{field}")
        _require_allowed_values(value, allowed_values, f"{location}.{field}")

    requirements = dish["required_ingredients"]
    if not isinstance(requirements, list):
        _invalid_response(f"{location}.required_ingredients必须是数组")
    _require_no_duplicates(requirements, f"{location}.required_ingredients")
    for requirement_index, requirement in enumerate(requirements):
        _validate_ingredient_requirement(
            requirement,
            f"{location}.required_ingredients[{requirement_index}]",
            ingredient_names,
            ingredient_categories,
        )


def _validate_ingredient_requirement(
    requirement: object,
    location: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> None:
    if not isinstance(requirement, dict):
        _invalid_response(f"{location}必须是对象")
    _require_exact_fields(
        requirement,
        INGREDIENT_REQUIREMENT_FIELDS,
        location,
    )
    kind = requirement["kind"]
    value = requirement["value"]
    if not isinstance(kind, str) or kind not in INGREDIENT_REQUIREMENT_KINDS:
        _invalid_response(f"{location}.kind不在允许值中")
    if not isinstance(value, str):
        _invalid_response(f"{location}.value必须是字符串")

    values_by_kind = {
        "ingredient": ingredient_names,
        "category": ingredient_categories,
        "concept": INGREDIENT_CONCEPTS,
    }
    if value not in values_by_kind[kind]:
        _invalid_response(f"{location}的kind与value不匹配")


def _validate_optional_positive_integer(value: object, location: str) -> None:
    if value is None:
        return
    if type(value) is not int or value <= 0:
        _invalid_response(f"{location}必须是正整数或null")


def _validate_string_array(value: object, location: str) -> None:
    if not isinstance(value, list):
        _invalid_response(f"{location}必须是数组")
    if any(not isinstance(item, str) for item in value):
        _invalid_response(f"{location}的元素必须是字符串")
    _require_no_duplicates(value, location)


def _require_allowed_values(
    values: Iterable[str],
    allowed_values: Collection[str],
    location: str,
) -> None:
    if any(value not in allowed_values for value in values):
        _invalid_response(f"{location}包含非法值")


def _require_no_duplicates(values: list[Any], location: str) -> None:
    canonical_values = [
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        for value in values
    ]
    if len(canonical_values) != len(set(canonical_values)):
        _invalid_response(f"{location}不允许重复值")


def _require_exact_fields(
    value: Mapping[str, Any],
    expected_fields: Collection[str],
    location: str,
) -> None:
    if set(value) != set(expected_fields):
        _invalid_response(f"{location}字段必须与Schema完全一致")


def _collect_evidence_paths(result: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    paths.update(
        f"meal_periods[{index}]"
        for index in range(len(result["meal_periods"]))
    )
    if result["diner_count"] is not None:
        paths.add("diner_count")
    if result["max_total_time_minutes"] is not None:
        paths.add("max_total_time_minutes")
    paths.update(
        f"available_ingredients[{index}]"
        for index in range(len(result["available_ingredients"]))
    )

    for dish_index, dish in enumerate(result["dishes"]):
        prefix = f"dishes[{dish_index}]"
        if dish["count"] is not None:
            paths.add(f"{prefix}.count")
        if dish["dish_type"] != "未指定":
            paths.add(f"{prefix}.dish_type")
        paths.update(
            f"{prefix}.taste_preferences.{taste}"
            for taste in dish["taste_preferences"]
        )
        for field in ("cuisines", "effects", "special_populations"):
            paths.update(
                f"{prefix}.{field}[{index}]"
                for index in range(len(dish[field]))
            )
        paths.update(
            f"{prefix}.required_ingredients[{index}].value"
            for index in range(len(dish["required_ingredients"]))
        )
    return paths


def _invalid_response(message: str) -> None:
    raise DialogueConstraintExtractionError(502, message)


__all__ = [
    "DialogueConstraintService",
    "DialogueConstraintExtractionError",
]
