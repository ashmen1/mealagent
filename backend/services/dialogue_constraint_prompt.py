from __future__ import annotations

import copy
import json
from typing import Any

from backend.core.dialogue_constraint_contract import (
    CUISINES,
    DISH_TYPES,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
    SPECIAL_POPULATIONS,
    TASTE_PREFERENCES,
)


def build_retry_prompt(prompt: str, validation_error: str) -> str:
    """在原Prompt后附加首次校验错误，要求模型纠正后重新输出。"""

    return (
        f"{prompt}\n\n"
        "上一次结构化输出未通过服务校验。请根据以下具体错误纠正，"
        "重新返回完整结构，不得删除约束或改用自由文本：\n"
        f"{validation_error}"
    )


def build_dialogue_prompt(
    session_id: int,
    user_message: str,
    previous: dict[str, Any] | None,
    ingredient_categories: set[str],
) -> str:
    allowed_values = {
        "meal_periods": sorted(MEAL_PERIODS),
        "dish_type": sorted(DISH_TYPES),
        "taste_preferences.keys": sorted(TASTE_PREFERENCES),
        "cuisines": sorted(CUISINES),
        "effects": sorted(EFFECTS),
        "special_populations": sorted(SPECIAL_POPULATIONS),
        "required_ingredient_groups.match": ["all", "any"],
        "required_ingredient_groups.items.kind": sorted(
            INGREDIENT_REQUIREMENT_KINDS
        ),
        "category": sorted(ingredient_categories),
        "concept": sorted(INGREDIENT_CONCEPTS),
        "max_difficulty": ["简单", "中等"],
        "change_actions.action": ["add", "replace", "remove"],
    }
    state_text = (
        json.dumps(previous, ensure_ascii=False, separators=(",", ":"))
        if previous is not None
        else "尚无约束(首轮)"
    )

    sections = [
        (
            "你负责从多轮中文对话中提取菜单约束。每轮你会收到当前轮用户原文"
            "和已有约束状态,需要结合两者判断本轮对约束的新增、修改与删除,"
            "并通过工具调用返回完整更新后的约束。字段类型与必填项以工具参数"
            "定义为准,全部数字字段输出 JSON 数字(不带引号),未明确时为 JSON "
            "null。任何可空字段都绝对不得用空字符串\"\"代替 null。"
        ),
        "字段允许值:\n"
        + json.dumps(allowed_values, ensure_ascii=False, indent=2),
        (
            "受控映射规则:早上、早饭→早餐;中午、午饭→午餐;"
            "晚上、今晚、晚饭→晚餐。微辣、香辣、麻辣→is_spicy=true;"
            "不辣、别做辣的→is_spicy=false;清淡、清爽、别太抢味→"
            "is_light=true;咸鲜→is_salty=true;别太甜、不太甜→"
            "is_sweet=false。西餐、西式→西餐风味;广东菜→粤菜;"
            "川菜、湘菜→川湘菜。暖胃、养胃、健胃消食→养胃健胃消食;"
            "公司、上班、下班→上班族;小孩、孩子→儿童。"
            "简单、简单点、家常、家常一点→max_difficulty=简单;"
            "不太复杂、不想太复杂、别太复杂、别太难做、太麻烦不行→"
            "max_difficulty=中等。面保留为kind=concept、value=面。"
            "禁止推导:简单不得产生清淡;正式、仪式感不得产生西餐风味;"
            "胃口不好、便秘不得产生养胃健胃消食;补气血、没精神不得产生贫血;"
            "夜宵不得直接产生晚餐,但同句中的晚上可独立产生晚餐。"
            "适合夏天、热乎、牙口不好、复杂、大部分食材共用等未支持描述"
            "不产生字段。家里有、家里只剩、现有的标准核心食材只进入"
            "available_ingredients,不进入required_ingredient_groups。"
            "没有既定映射的描述直接忽略。"
        ),
        (
            "食材名规则:ingredient 的值使用常见标准名称,如番茄、鸡蛋、土豆、"
            "猪肉、牛肉、鸡肉、鱼、虾、白菜、豆腐、米饭、面条;用户用了同义说法"
            "(如西红柿、马铃薯)时归一到标准名称;无法确定时输出用户原文说法。"
        ),
        (
            "菜品规则:dishes至少包含一项。没有明确菜品分类时返回一项"
            "count=null、dish_type=未指定且其余约束为空的菜品,并将口味、菜系、"
            "功效、人群和食材要求直接放入该项;存在多个菜品组时,适用于所有组的"
            "限制复制到每个Dish中。total_dish_count表示整桌确切菜品总数;"
            "len(dishes)表示查询组数;Dish.count只表示用户明确分配给该组的"
            "菜品数,三者不得混用。四个菜写total_dish_count=4,默认Dish.count"
            "仍为null。一人要求、另一人拒绝同一布尔口味时拆成两个真实Dish,"
            "分别保存true和false,两个count都为null;一个人不是菜品数量证据。"
        ),
        (
            "演化规则:标量(diner_count、total_dish_count、"
            "max_total_time_minutes):增=旧值累加"
            "(再加一个人 2→3);改=新值覆盖(改成三个人);删=解除约束置null"
            "(人数不限)。数组(meal_periods、available_ingredients及Dish内"
            "cuisines、effects、special_populations、"
            "required_ingredient_groups):"
            "增=追加元素去重保序;删=移除元素;改=整体替换。口味"
            "(taste_preferences):增=新增键;改=同名键新值覆盖(改口);"
            "删=移除键。max_difficulty只允许replace和remove,add非法。"
            "dishes:增=同类型count累加或新增菜品组;"
            "删=移除整个Dish;改=替换count或修改Dish内字段。"
            "上一状态中已有的约束,只要本轮原文没有改变它们的表述,必须"
            "原样保留,不得修改或删除。"
        ),
        (
            "变更声明规则:每轮对上一状态做的每个增删改都必须在change_actions"
            "中声明。作用于顶层字段时填field(meal_periods、diner_count、"
            "total_dish_count、max_total_time_minutes、max_difficulty、"
            "available_ingredients);作用于Dish时填"
            "dish_index(上一状态中的Dish索引);新增全新菜品组时dish_index为"
            "null且放在输出dishes末尾。field与dish_index必须恰好一个非空,"
            "唯一例外是新增全新菜品组(action=add)时两者均为null;"
            "同一字段或同一Dish只允许一条声明;同一Dish内多个字段变化必须合并"
            "为一条声明;未声明的字段和Dish必须原样保留。"
            "标量或Dish的count在旧值为null时必须用replace;每条声明的evidence"
            "必须是本轮原文的连续片段。add只用于在旧值基础上继续增加"
            "(如再加一个人 2→3);旧值为null、或本轮给出的是新的明确数值"
            "(如两个人、别超过45分钟)时,一律用replace。当前约束状态为"
            "尚无约束(首轮)时,change_actions必须输出[],所有约束直接写入输出字段。"
            "输出前自检:①每条声明引用的dish_index必须存在于上一状态;"
            "②action为add且dish_index为null时,输出dishes末尾必须比上一状态"
            "恰好多一项;③未声明的字段与Dish必须与上一状态完全一致,"
            "声明与输出之间不得有对不上的地方。"
            "已有明确总数且未指定菜品组时,再加一个菜只增加"
            "total_dish_count,不修改任何Dish.count。指定组count和总数都明确"
            "时,该组再加一道必须分别声明并同时增加total_dish_count和组count。"
            "输出前逐项检查所有可空数值字段:上一状态为null且本轮未改变时"
            "必须继续输出JSON null,不得输出空字符串。明确对照:"
            "错误写法为\"total_dish_count\":\"\";正确写法为"
            "\"total_dish_count\":null。"
        ),
        (
            "证据规则:只为本轮新增或变更的字段提供evidence,使用叶子路径"
            "(如meal_periods[0]、diner_count、total_dish_count、"
            "max_difficulty、dishes[0].count、"
            "dishes[0].taste_preferences.is_spicy、"
            "dishes[0].required_ingredient_groups[0].match、"
            "dishes[0].required_ingredient_groups[0].items[0].value),"
            "片段必须是本轮原文的"
            "连续子串;上一状态已有的字段不要重复提供evidence。首轮所有非空"
            "约束都必须提供evidence。dialogue_id、null、[]、{}和默认未指定"
            "Dish不需要证据。"
        ),
        (
            "食材分组规则:任意两个或更多有效食材条件由和、并且、都要连接时"
            "生成一个match=all组;由或、或者、二选一连接时生成一个match=any"
            "组。Dish内各组之间固定为AND;单个食材要求生成单项all组;any组"
            "至少两项。同组和跨组都不允许重复kind+value。每组match和每个"
            "items.value都必须提供连续原文证据。"
        ),
        (
            "当前对话绑定规则:输出 dialogue_id 必须原样复制当前会话id,"
            f"本次必须输出 dialogue_id={session_id},不得使用其他值。"
        ),
        "参考示例(必须在上述示例之后处理当前对话):\n"
        + "\n\n".join(
            "当前约束状态:"
            + (
                json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                if state is not None
                else "尚无约束(首轮)"
            )
            + "\n当前对话原文:"
            + message
            + "\n对应输出:"
            + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            for state, message, result in _build_dialogue_examples()
        ),
        "当前约束状态:\n" + state_text,
        "当前对话原文:\n" + user_message,
    ]
    return "\n\n".join(sections)


_EMPTY_DISH_EXAMPLE = {
    "count": None,
    "dish_type": "未指定",
    "taste_preferences": {},
    "cuisines": [],
    "effects": [],
    "special_populations": [],
    "required_ingredient_groups": [],
}


def _example_dish(**overrides: Any) -> dict[str, Any]:
    """构造示例用的空 Dish 并覆盖指定字段。"""

    dish = dict(_EMPTY_DISH_EXAMPLE)
    dish.update(overrides)
    return dish


def _example_state(
    result: dict[str, Any],
    *,
    evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """从示例输出构造不含变更声明的下一轮状态。"""

    state = copy.deepcopy(result)
    state.pop("change_actions")
    if evidence is not None:
        state["evidence"] = evidence
    return state


def _build_dialogue_examples(
) -> list[tuple[dict[str, Any] | None, str, dict[str, Any]]]:
    """统一对话提取的参考示例:每项为(上一状态,本轮原文,期望输出)。"""

    example_1_result = {
        "dialogue_id": 9001,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
        },
        "change_actions": [],
    }
    example_1_state = _example_state(example_1_result)
    example_2_result = {
        "dialogue_id": 9001,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={
                    "is_spicy": False,
                    "is_light": True,
                },
            ),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "辣的",
            "dishes[0].taste_preferences.is_light": "清淡",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "别做辣的，口味清淡一点",
            }
        ],
    }
    example_3_state = {
        "dialogue_id": 9002,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(count=2, dish_type="菜"),
            _example_dish(count=1, dish_type="汤"),
        ],
        "evidence": {
            "meal_periods[0]": "晚上",
            "diner_count": "两个人",
            "dishes[0].count": "两菜",
            "dishes[0].dish_type": "两菜",
            "dishes[1].count": "一汤",
            "dishes[1].dish_type": "一汤",
        },
    }
    example_3_result = {
        "dialogue_id": 9002,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(count=3, dish_type="菜"),
            _example_dish(count=1, dish_type="汤"),
        ],
        "evidence": {"dishes[0].count": "再加一个菜"},
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "add",
                "evidence": "再加一个菜",
            }
        ],
    }
    example_4_state = {
        "dialogue_id": 9003,
        "meal_periods": ["午餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"meal_periods[0]": "中午"},
    }
    example_4_result = {
        "dialogue_id": 9003,
        "meal_periods": ["午餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(effects=["减脂"])],
        "evidence": {
            "diner_count": "两个人",
            "dishes[0].effects[0]": "减脂",
        },
        "change_actions": [
            {
                "field": "diner_count",
                "dish_index": None,
                "action": "replace",
                "evidence": "两个人",
            },
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "减脂",
            },
        ],
    }
    example_5_state = {
        "dialogue_id": 9004,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
        },
    }
    example_5_result = {
        "dialogue_id": 9004,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={"is_spicy": True},
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一点辣都不想碰",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "一个人想吃辣",
            },
            {
                "field": None,
                "dish_index": None,
                "action": "add",
                "evidence": "一点辣都不想碰",
            },
        ],
    }
    example_6_result = {
        "dialogue_id": 9005,
        "meal_periods": [],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(dish_type="菜")],
        "evidence": {"dishes[0].dish_type": "一桌菜"},
        "change_actions": [],
    }
    example_6_state = _example_state(example_6_result)
    example_7_state = {
        "dialogue_id": 9006,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={"is_spicy": True},
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一点辣都不想碰",
        },
    }
    example_7_result = {
        "dialogue_id": 9006,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                dish_type="菜",
                taste_preferences={"is_spicy": True},
                required_ingredient_groups=[
                    {
                        "match": "any",
                        "items": [
                            {"kind": "ingredient", "value": "鱼"},
                            {"kind": "ingredient", "value": "鸡翅"},
                        ],
                    }
                ],
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "dishes[0].dish_type": "主菜",
            "dishes[0].required_ingredient_groups[0].match": "鱼或者鸡翅",
            "dishes[0].required_ingredient_groups[0].items[0].value": "鱼",
            "dishes[0].required_ingredient_groups[0].items[1].value": "鸡翅",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "主菜",
            }
        ],
    }
    example_8_state = {
        "dialogue_id": 9007,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"meal_periods[0]": "晚饭"},
    }
    example_8_result = {
        "dialogue_id": 9007,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"max_difficulty": "家常一点"},
        "change_actions": [
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "家常一点",
            }
        ],
        "max_difficulty": "简单",
    }

    example_9_state = {
        "dialogue_id": 9008,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"meal_periods[0]": "晚饭"},
    }
    example_9_result = {
        **example_9_state,
        "max_total_time_minutes": 45,
        "evidence": {
            "max_total_time_minutes": "整体别超过45分钟",
            "max_difficulty": "太麻烦的不行",
        },
        "max_difficulty": "中等",
        "change_actions": [
            {
                "field": "max_total_time_minutes",
                "dish_index": None,
                "action": "replace",
                "evidence": "整体别超过45分钟",
            },
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "太麻烦的不行",
            }
        ],
    }
    example_10_result = {
        "dialogue_id": 9009,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": 4,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"total_dish_count": "四道菜"},
        "change_actions": [],
    }
    example_10_state = _example_state(example_10_result)
    example_11_result = {
        **example_10_state,
        "dishes": [
            _example_dish(taste_preferences={"is_spicy": True}),
            _example_dish(taste_preferences={"is_spicy": False}),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "一个人吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人不碰辣",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "一个人吃辣",
            },
            {
                "field": None,
                "dish_index": None,
                "action": "add",
                "evidence": "一个人不碰辣",
            },
        ],
    }
    example_11_state = _example_state(
        example_11_result,
        evidence={
            "total_dish_count": "四道菜",
            "dishes[0].taste_preferences.is_spicy": "一个人吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人不碰辣",
        },
    )
    example_12_result = {
        **example_11_state,
        "total_dish_count": 5,
        "evidence": {"total_dish_count": "再加一道"},
        "change_actions": [
            {
                "field": "total_dish_count",
                "dish_index": None,
                "action": "add",
                "evidence": "再加一道",
            }
        ],
    }
    example_13_result = {
        **example_6_state,
        "diner_count": 6,
        "max_difficulty": "中等",
        "evidence": {
            "diner_count": "大概六个人",
            "max_difficulty": "别整得太难做",
        },
        "change_actions": [
            {
                "field": "diner_count",
                "dish_index": None,
                "action": "replace",
                "evidence": "大概六个人",
            },
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "别整得太难做",
            },
        ],
    }

    examples = [
        (None, "帮我想一顿两个人的晚饭。", example_1_result),
        (None, "周末想请几个人来家里吃饭，你帮我设计一桌菜。", example_6_result),
        (example_1_state, "别做辣的，口味清淡一点。", example_2_result),
        (example_3_state, "再加一个菜", example_3_result),
        (example_4_state, "两个人吃，最近在减脂。", example_4_result),
        (example_5_state, "一个人想吃辣，一个人一点辣都不想碰。", example_5_result),
        (
            example_7_state,
            "最好大部分食材能共用，主菜可以考虑鱼或者鸡翅。",
            example_7_result,
        ),
        (example_8_state, "家常一点。", example_8_result),
        (
            example_9_state,
            "然后整体别超过45分钟，太麻烦的不行。",
            example_9_result,
        ),
        (None, "四道菜。", example_10_result),
        (
            example_10_state,
            "一个人吃辣，一个人不碰辣。",
            example_11_result,
        ),
        (example_11_state, "再加一道。", example_12_result),
        (
            example_6_state,
            "大概六个人，稍微正式点，但别整得太难做。",
            example_13_result,
        ),
    ]
    for state, _, result in examples:
        result.setdefault("total_dish_count", None)
        result.setdefault("max_difficulty", None)
        if state is not None:
            state.setdefault("total_dish_count", None)
            state.setdefault("max_difficulty", None)
    return examples

__all__ = ["build_dialogue_prompt", "build_retry_prompt"]
