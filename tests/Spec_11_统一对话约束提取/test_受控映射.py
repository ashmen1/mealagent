from __future__ import annotations

from .spec11_support import build_dish, build_turn_result


def test_Prompt完整声明允许映射和禁止推导(production_contract):
    prompt = production_contract.build_prompt(
        1,
        "简单点的早餐",
        None,
        {"蔬菜", "水产"},
    )

    for expected in (
        "早上、早饭",
        "早餐",
        "晚上、今晚、晚饭",
        "晚餐",
        "微辣、香辣、麻辣",
        "is_spicy=true",
        "不辣、别做辣的",
        "is_spicy=false",
        "清淡、清爽、别太抢味",
        "is_light=true",
        "咸鲜",
        "is_salty=true",
        "别太甜、不太甜",
        "is_sweet=false",
        "西餐、西式",
        "西餐风味",
        "广东菜",
        "粤菜",
        "川菜、湘菜",
        "川湘菜",
        "暖胃、养胃、健胃消食",
        "养胃健胃消食",
        "公司、上班、下班",
        "上班族",
        "小孩、孩子",
        "儿童",
        "简单、简单点、家常、家常一点",
        "max_difficulty=简单",
        "不太复杂、不想太复杂、别太复杂、别太难做、太麻烦不行",
        "max_difficulty=中等",
    ):
        assert expected in prompt

    for forbidden_rule in (
        "简单不得产生清淡",
        "正式、仪式感不得产生西餐风味",
        "胃口不好、便秘不得产生养胃健胃消食",
        "补气血、没精神不得产生贫血",
        "夜宵不得直接产生晚餐",
        "适合夏天",
        "热乎",
        "牙口不好",
        "大部分食材共用",
    ):
        assert forbidden_rule in prompt


def test_Prompt包含通用食材AND_OR与同Dish单声明示例(production_contract):
    prompt = production_contract.build_prompt(
        2,
        "鱼或者鸡翅",
        None,
        {"蔬菜", "水产"},
    )

    assert "required_ingredient_groups" in prompt
    assert "和、并且、都要" in prompt and "all" in prompt
    assert "或、或者、二选一" in prompt and "any" in prompt
    assert "任意" in prompt and "食材" in prompt
    assert "别做辣的，口味清淡一点" in prompt
    assert "同一Dish" in prompt and "一条" in prompt


def test_简单早餐只得到餐次和难度(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=["早餐"],
        max_difficulty="简单",
        evidence={
            "meal_periods[0]": "早餐",
            "max_difficulty": "简单点",
        },
    )

    result = service.submit_turn(session_id, "简单点的早餐")

    merged = result["merged_constraints"]
    assert merged["meal_periods"] == ["早餐"]
    assert merged["max_difficulty"] == "简单"
    assert merged["dishes"][0]["taste_preferences"] == {}


def test_仪式感不推西餐但不复杂得到中等难度(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        max_difficulty="中等",
        evidence={"max_difficulty": "不想做太复杂"},
    )

    result = service.submit_turn(
        session_id,
        "想吃得有点仪式感，但不想做太复杂",
    )

    merged = result["merged_constraints"]
    assert merged["max_difficulty"] == "中等"
    assert merged["dishes"][0]["cuisines"] == []


def test_胃口不好不产生功效而暖胃产生受控功效(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(session_id),
        build_turn_result(
            session_id,
            dishes=[build_dish(effects=["养胃健胃消食"])],
            evidence={"dishes[0].effects[0]": "暖胃"},
            change_actions=[
                {
                    "field": None,
                    "dish_index": 0,
                    "action": "replace",
                    "evidence": "暖胃",
                }
            ],
        ),
    ]

    first = service.submit_turn(session_id, "最近胃口不好")
    second = service.submit_turn(session_id, "那就吃点暖胃的")

    assert first["merged_constraints"]["dishes"][0]["effects"] == []
    assert second["merged_constraints"]["dishes"][0]["effects"] == [
        "养胃健胃消食"
    ]


def test_补气血和没精神不产生贫血(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(session_id)

    result = service.submit_turn(
        session_id,
        "最近没精神，想补气血",
    )

    assert result["merged_constraints"]["dishes"][0]["effects"] == []


def test_夜宵本身不映射晚餐但晚上可以独立映射(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(session_id),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "晚上"},
            change_actions=[
                {
                    "field": "meal_periods",
                    "dish_index": None,
                    "action": "replace",
                    "evidence": "晚上",
                }
            ],
        ),
    ]

    night_snack = service.submit_turn(session_id, "想吃个夜宵")
    evening = service.submit_turn(session_id, "晚上吃个夜宵")

    assert night_snack["merged_constraints"]["meal_periods"] == []
    assert evening["merged_constraints"]["meal_periods"] == ["晚餐"]
