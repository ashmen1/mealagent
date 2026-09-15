from __future__ import annotations

import json
from typing import Any, TypeAlias


PromptMessage: TypeAlias = dict[str, str]
DialoguePrompt: TypeAlias = list[PromptMessage]


SYSTEM_PROMPT = """你负责从多轮中文对话中提取菜单约束，并通过已绑定的结构化工具返回当前轮结束后的完整状态。

一、基本原则
1. 只提取用户当前原文明确表达的含义，禁止根据常识、场景、健康状况或近义联想猜测。
2. 字段、类型、枚举、必填项、正整数和可空规则以工具 Schema 为准；所有字段都必须返回，可空字段无值时返回 null，不得返回空字符串。
3. dialogue_id 必须原样复制当前输入的 dialogue_id。
4. 首轮直接生成完整状态，change_actions 返回 []。后续轮先继承上一轮完整状态，再应用本轮明确变化；未被本轮改变的字段和菜品组必须原样保留。

二、受控语义
下列映射是必须执行的词面规则：当前原文直接出现映射源词时就提取对应值，即使该词同时用于交代时间或场景，也不得遗漏。
1. 餐次：早上、早饭→早餐；中午、午饭→午餐；晚上、今晚、晚饭→晚餐。夜宵本身不映射餐次，但同句中的“晚上”仍必须提取为晚餐。
2. 口味：微辣、香辣、麻辣→is_spicy=true；不辣、别做辣的→is_spicy=false；清淡、清爽、别太抢味→is_light=true；咸鲜→is_salty=true；别太甜、不太甜→is_sweet=false。
3. 菜系：西餐、西式→西餐风味；广东菜→粤菜；川菜、湘菜→川湘菜。
4. 功效：暖胃、养胃、健胃消食→养胃健胃消食。
5. 人群：公司、上班、下班→上班族；小孩、孩子→儿童；原文直接出现“老人”时必须提取老人，即使同句还有不受支持的“牙口不好”。
6. 难度：简单、简单点、家常、家常一点→简单；不太复杂、不想太复杂、别太复杂、别太难做、太麻烦不行→中等。
7. “面”作为普通食材概念时使用 kind=concept、value=面。
8. 禁止推导：简单不等于清淡；正式、仪式感不等于西餐风味；胃口不好、便秘不等于养胃健胃消食；补气血、没精神不等于贫血。适合夏天、热乎、牙口不好、复杂、食材尽量共用等未支持描述不产生字段。

三、菜品组和数量
1. dishes 至少一项。没有明确菜品分类时，只返回一个 count=null、dish_type=未指定、其余菜品约束为空的默认组。
2. 多个明确菜品类别分别建立菜品组；适用于所有组的口味、菜系、功效和人群限制复制到每组。
3. total_dish_count 只表示用户明确要求的整桌菜品总数；len(dishes) 是查询组数；Dish.count 只表示用户明确分配给该组的数量；diner_count 只表示用餐人数。四菜一汤表示 total_dish_count=5、菜组 count=4、汤组 count=1。不得在这四种数量之间推导或代用。
4. 两个人口味相反时，拆成两个真实菜品组分别保存 true 和 false，两个 count 都为 null；人数不是菜品数证据。

四、食材和主食
1. “家里有、家里只剩、现有”的标准核心食材只进入 available_ingredients，不进入 required_ingredient_groups。
2. 普通菜品要求使用 required_ingredient_groups：单个条件为单项 all 组；由“和、并且、都要”连接的多个条件为一个 all 组；由“或、或者、二选一”连接的多个条件为一个 any 组。各组之间固定为 AND，any 组至少两项，禁止重复 kind+value。
3. “想吃面”属于普通菜品食材要求，写入 required_ingredient_groups，required_staple_ingredients 必须为 null。只有明确说“主食换成面条”或“以面条作主食”时，才写入 required_staple_ingredients。
4. 主食换成、改成或以某食材作主食时写入 required_staple_ingredients；主食不要某食材时写入 excluded_staple_ingredients，不得写入过敏。主食来源只能使用标准食材名，不得使用 concept。
5. 明确更换主食时，如果上一状态把被替换主食单独保存在一个 match=all 且仅含该食材的普通食材组中，移除该旧组；包含其他食材的组合组和无关组必须保留。没有主食语境的“不要某食材”不产生主食排除。
6. ingredient 使用数据库标准食材名；常见同义词归一，如西红柿→番茄、马铃薯→土豆；无法确定时保留用户原词。category 只能取当前输入提供的动态食材类别。

五、状态变化和 change_actions
1. 每个实际变化都必须声明，声明必须能完整重放得到输出状态；同一顶层字段或同一旧菜品组最多一条，同一菜品组的多个字段变化合并为一条。
2. 顶层变化填写 field；已有菜品组变化填写上一状态中的 dish_index；新增菜品组使用 action=add 且 field、dish_index 都为 null。除此之外 field 与 dish_index 必须恰好一个非空。
3. add 只用于旧值基础上的继续增加；旧值为 null 或本轮给出新的明确值时使用 replace。remove 表示解除或删除。max_difficulty 只使用 replace/remove。
4. 标量：“再加一个人”在旧值上累加；“改成三个人”覆盖；“人数不限”置 null。数组追加时去重保序，删除时只移除指定项，整体改写时替换。口味同名键的新值覆盖旧值。
5. 已有明确总数但没有明确菜品组数量时，“再加一道”只增加 total_dish_count，不修改 Dish.count；组数量和总数都明确时，指定组“再加一道”才同时修改两者并分别声明。

六、证据
1. evidence 的值必须是当前用户原文中的连续片段，不得改写，不得引用上一轮，不得为推导结果提供证据。
2. 首轮为全部非空约束提供证据；后续轮只为本轮新增或变化的叶子字段提供证据。dialogue_id、null、空数组、空对象和默认未指定菜品组不需要证据。
3. 使用精确叶子路径，例如 meal_periods[0]、diner_count、total_dish_count、max_difficulty、dishes[0].count、dishes[0].taste_preferences.is_spicy、dishes[0].required_ingredient_groups[0].match、dishes[0].required_ingredient_groups[0].items[0].value、dishes[0].required_staple_ingredients.items[0]、dishes[0].excluded_staple_ingredients[0]。
4. 每条 change_actions.evidence 也必须是当前原文连续片段，并覆盖该声明涉及的全部变化。

七、当前菜单操作
1. menu_change 与 change_actions 分开：change_actions 只描述长期约束变化；menu_change 只描述对 previous_menu 的本轮菜单操作。普通需求和普通约束补充固定返回 mode=none，其余字段为空。
2. 明确“全部换掉、整套重做”使用 replace_all；“换几道、换一部分、这个方案不行换个方案”使用 replace_partial，明确数量写 replace_count，否则为 null。
3. 明确按菜名或“第几道”换菜使用 replace_specific；菜单序号从1开始，分别写 target_recipe_names 或 target_positions。“把A换成B”同时把B写入 replacement_recipe_name。
4. 明确“换回来A、重新考虑A”使用 restore_specific，并把A写入 replacement_recipe_name。
5. 目标描述有歧义、多个目标提出不同换菜条件时，把原因写入 unresolved_target，不猜目标；所有菜单操作都用当前原文连续片段填写 menu_change.evidence。
6. 同一轮出现长期约束变化和菜单操作时两者都提取，不得互相覆盖；菜单操作之外不推导模糊条件的作用范围。

输出前检查：完整字段已返回；上一状态未声明部分原样保留；数量语义未混用；每个变化都有且只有一条可重放声明；所有证据均来自当前原文。"""


def build_retry_prompt(
    prompt: DialoguePrompt,
    validation_error: str,
) -> DialoguePrompt:
    """追加首次校验错误，要求模型基于原消息上下文纠正完整输出。"""

    retry_prompt = [dict(message) for message in prompt]
    retry_prompt.append(
        {
            "role": "human",
            "content": (
                "上一次结构化输出未通过服务校验。请根据具体错误纠正后重新调用工具，"
                "返回完整状态，不得删除未变化约束或改用自由文本。\n"
                f"校验错误：{validation_error}"
            ),
        }
    )
    return retry_prompt


def build_dialogue_prompt(
    session_id: int,
    user_message: str,
    previous: dict[str, Any] | None,
    ingredient_categories: set[str],
    last_menu: list[dict[str, Any]] | None = None,
    excluded_recipe_names: list[str] | None = None,
    pending_menu_change: dict[str, Any] | None = None,
) -> DialoguePrompt:
    """构造 System、六组 Few-shot 和当前 Human 组成的角色消息。"""

    messages: DialoguePrompt = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example_input, example_output in _build_dialogue_examples():
        messages.extend(
            (
                {"role": "human", "content": _serialize_input(example_input)},
                {"role": "assistant", "content": _serialize_output(example_output)},
            )
        )
    messages.append(
        {
            "role": "human",
            "content": _serialize_input(
                {
                    "dialogue_id": session_id,
                    "previous_constraints": previous,
                    "previous_menu": last_menu,
                    "excluded_recipe_names": excluded_recipe_names or [],
                    "pending_menu_change": pending_menu_change,
                    "user_message": user_message,
                    "ingredient_categories": sorted(ingredient_categories),
                }
            ),
        }
    )
    return messages


def _serialize_input(value: dict[str, Any]) -> str:
    """用稳定、紧凑的 JSON 表达示例和当前轮输入。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _serialize_output(value: dict[str, Any]) -> str:
    """仅输出工具参数所需的完整 JSON，不在示例内重复规则。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_EMPTY_DISH_EXAMPLE = {
    "count": None,
    "dish_type": "未指定",
    "taste_preferences": {},
    "cuisines": [],
    "effects": [],
    "special_populations": [],
    "required_ingredient_groups": [],
    "required_staple_ingredients": None,
    "excluded_staple_ingredients": [],
}


def _example_dish(**overrides: Any) -> dict[str, Any]:
    """构造示例用的完整菜品组。"""

    dish = dict(_EMPTY_DISH_EXAMPLE)
    dish.update(overrides)
    return dish


def _example_output(
    dialogue_id: int,
    *,
    meal_periods: list[str] | None = None,
    diner_count: int | None = None,
    total_dish_count: int | None = None,
    max_total_time_minutes: int | None = None,
    max_difficulty: str | None = None,
    available_ingredients: list[str] | None = None,
    dishes: list[dict[str, Any]] | None = None,
    evidence: dict[str, str] | None = None,
    change_actions: list[dict[str, Any]] | None = None,
    menu_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造字段齐全的 Few-shot 输出。"""

    return {
        "dialogue_id": dialogue_id,
        "meal_periods": meal_periods or [],
        "diner_count": diner_count,
        "total_dish_count": total_dish_count,
        "max_total_time_minutes": max_total_time_minutes,
        "max_difficulty": max_difficulty,
        "available_ingredients": available_ingredients or [],
        "dishes": dishes or [_example_dish()],
        "evidence": evidence or {},
        "change_actions": change_actions or [],
        "menu_change": menu_change or {
            "mode": "none",
            "replace_count": None,
            "target_positions": [],
            "target_recipe_names": [],
            "replacement_recipe_name": None,
            "unresolved_target": None,
            "evidence": None,
        },
    }


def _state_from_output(output: dict[str, Any]) -> dict[str, Any]:
    """移除轮次声明，得到下一轮输入所需的完整状态。"""

    return {
        key: value
        for key, value in output.items()
        if key not in {"change_actions", "menu_change"}
    }


def _build_dialogue_examples(
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """构造五个固定 Few-shot 场景，每项只包含输入状态和完整结果。"""

    categories = ["水产", "粮食", "蔬菜", "禽肉"]

    basic_dinner = _example_output(
        9001,
        meal_periods=["晚餐"],
        diner_count=2,
        evidence={"meal_periods[0]": "晚饭", "diner_count": "两个人"},
    )
    example_1 = (
        {
            "dialogue_id": 9001,
            "previous_constraints": None,
            "user_message": "帮我想一顿两个人的晚饭。",
            "ingredient_categories": categories,
        },
        basic_dinner,
    )

    noodles_and_side = _example_output(
        9002,
        meal_periods=["晚餐"],
        total_dish_count=2,
        dishes=[
            _example_dish(
                count=1,
                dish_type="主食",
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [{"kind": "concept", "value": "面"}],
                    }
                ],
            ),
            _example_dish(
                count=1,
                dish_type="小菜",
                taste_preferences={"is_light": True},
            ),
        ],
        evidence={
            "meal_periods[0]": "今晚",
            "total_dish_count": "面，再帮我配个别太抢味的小菜",
            "dishes[0].count": "面",
            "dishes[0].dish_type": "面",
            "dishes[0].required_ingredient_groups[0].match": "面",
            "dishes[0].required_ingredient_groups[0].items[0].value": "面",
            "dishes[1].count": "个别太抢味的小菜",
            "dishes[1].dish_type": "个别太抢味的小菜",
            "dishes[1].taste_preferences.is_light": "别太抢味",
        },
    )
    example_2 = (
        {
            "dialogue_id": 9002,
            "previous_constraints": None,
            "user_message": "我今晚有点想吃面，再帮我配个别太抢味的小菜。",
            "ingredient_categories": categories,
        },
        noodles_and_side,
    )

    shared_dish_limits = {
        "taste_preferences": {"is_spicy": False},
        "special_populations": ["儿童", "老人"],
    }
    family_meal = _example_output(
        9003,
        total_dish_count=5,
        dishes=[
            _example_dish(count=4, dish_type="菜", **shared_dish_limits),
            _example_dish(count=1, dish_type="汤", **shared_dish_limits),
        ],
        evidence={
            "total_dish_count": "四菜一汤",
            "dishes[0].count": "四菜",
            "dishes[0].dish_type": "四菜",
            "dishes[0].taste_preferences.is_spicy": "不吃辣",
            "dishes[0].special_populations[0]": "小孩",
            "dishes[0].special_populations[1]": "老人",
            "dishes[1].count": "一汤",
            "dishes[1].dish_type": "一汤",
            "dishes[1].taste_preferences.is_spicy": "不吃辣",
            "dishes[1].special_populations[0]": "小孩",
            "dishes[1].special_populations[1]": "老人",
        },
    )
    example_3 = (
        {
            "dialogue_id": 9003,
            "previous_constraints": None,
            "user_message": "想做个四菜一汤，营养均衡一点的，小孩不吃辣，老人牙口不好。",
            "ingredient_categories": categories,
        },
        family_meal,
    )

    opposing_tastes = _example_output(
        9004,
        meal_periods=["晚餐"],
        diner_count=2,
        dishes=[
            _example_dish(taste_preferences={"is_spicy": True}),
            _example_dish(taste_preferences={"is_spicy": False}),
        ],
        evidence={
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人一点辣都不想碰",
        },
    )
    opposing_tastes_state = _state_from_output(opposing_tastes)
    expanded_preferences = _example_output(
        9004,
        meal_periods=["晚餐"],
        diner_count=2,
        max_total_time_minutes=45,
        max_difficulty="中等",
        dishes=[
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
            _example_dish(taste_preferences={"is_spicy": False}),
        ],
        evidence={
            "max_total_time_minutes": "整体别超过45分钟",
            "max_difficulty": "太麻烦的不行",
            "dishes[0].dish_type": "主菜",
            "dishes[0].required_ingredient_groups[0].match": "鱼或者鸡翅",
            "dishes[0].required_ingredient_groups[0].items[0].value": "鱼",
            "dishes[0].required_ingredient_groups[0].items[1].value": "鸡翅",
        },
        change_actions=[
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "主菜可以考虑鱼或者鸡翅",
            },
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
            },
        ],
    )
    example_4 = (
        {
            "dialogue_id": 9004,
            "previous_constraints": opposing_tastes_state,
            "user_message": (
                "最好大部分食材能共用，主菜可以考虑鱼或者鸡翅，"
                "然后整体别超过45分钟，太麻烦的不行。"
            ),
            "ingredient_categories": categories,
        },
        expanded_preferences,
    )

    previous_staple = _example_output(
        9005,
        dishes=[
            _example_dish(
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [{"kind": "ingredient", "value": "米饭"}],
                    },
                    {
                        "match": "any",
                        "items": [
                            {"kind": "ingredient", "value": "鱼"},
                            {"kind": "ingredient", "value": "鸡翅"},
                        ],
                    },
                ],
                required_staple_ingredients={"match": "all", "items": ["米饭"]},
            )
        ],
        evidence={
            "dishes[0].required_ingredient_groups[0].match": "米饭",
            "dishes[0].required_ingredient_groups[0].items[0].value": "米饭",
            "dishes[0].required_ingredient_groups[1].match": "鱼或鸡翅",
            "dishes[0].required_ingredient_groups[1].items[0].value": "鱼",
            "dishes[0].required_ingredient_groups[1].items[1].value": "鸡翅",
            "dishes[0].required_staple_ingredients.match": "米饭",
            "dishes[0].required_staple_ingredients.items[0]": "米饭",
        },
    )
    changed_staple = _example_output(
        9005,
        dishes=[
            _example_dish(
                required_ingredient_groups=[
                    {
                        "match": "any",
                        "items": [
                            {"kind": "ingredient", "value": "鱼"},
                            {"kind": "ingredient", "value": "鸡翅"},
                        ],
                    },
                    {
                        "match": "all",
                        "items": [{"kind": "ingredient", "value": "番茄"}],
                    },
                ],
                required_staple_ingredients={
                    "match": "any",
                    "items": ["玉米", "红薯"],
                },
                excluded_staple_ingredients=["米饭"],
            )
        ],
        evidence={
            "dishes[0].required_ingredient_groups[1].match": "番茄",
            "dishes[0].required_ingredient_groups[1].items[0].value": "番茄",
            "dishes[0].required_staple_ingredients.match": "玉米或者红薯",
            "dishes[0].required_staple_ingredients.items[0]": "玉米",
            "dishes[0].required_staple_ingredients.items[1]": "红薯",
            "dishes[0].excluded_staple_ingredients[0]": "米饭",
        },
        change_actions=[
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": (
                    "菜里还要番茄，主食不要米饭，主食来源换成玉米或者红薯"
                ),
            }
        ],
    )
    example_5 = (
        {
            "dialogue_id": 9005,
            "previous_constraints": _state_from_output(previous_staple),
            "user_message": "菜里还要番茄，主食不要米饭，主食来源换成玉米或者红薯。",
            "ingredient_categories": categories,
        },
        changed_staple,
    )

    rejected_menu = _example_output(
        9006,
        meal_periods=["晚餐"],
        diner_count=2,
        evidence={},
        menu_change={
            "mode": "replace_partial",
            "replace_count": None,
            "target_positions": [],
            "target_recipe_names": [],
            "replacement_recipe_name": None,
            "unresolved_target": None,
            "evidence": "这个方案不行，换个方案",
        },
    )
    example_6 = (
        {
            "dialogue_id": 9006,
            "previous_constraints": _state_from_output(
                _example_output(
                    9006,
                    meal_periods=["晚餐"],
                    diner_count=2,
                )
            ),
            "previous_menu": [
                {"position": 1, "dish_constraint_index": 0, "recipe_name": "菜A"},
                {"position": 2, "dish_constraint_index": 0, "recipe_name": "菜B"},
            ],
            "excluded_recipe_names": [],
            "pending_menu_change": None,
            "user_message": "这个方案不行，换个方案",
            "ingredient_categories": categories,
        },
        rejected_menu,
    )

    return [
        example_1,
        example_2,
        example_3,
        example_4,
        example_5,
        example_6,
    ]


__all__ = [
    "DialoguePrompt",
    "PromptMessage",
    "SYSTEM_PROMPT",
    "build_dialogue_prompt",
    "build_retry_prompt",
]
