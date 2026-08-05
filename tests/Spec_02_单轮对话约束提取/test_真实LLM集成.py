from __future__ import annotations

import pytest

from spec02_support import ingredient_session, production_contract


@pytest.mark.integration
def test_真实DeepSeek模型通过Anthropic兼容接口完成单轮约束提取(
    production_contract,
    ingredient_session,
):
    dialogue = {
        "id": 9001,
        "turn_count": 1,
        "user_messages": ["今晚吃啥比较好？"],
    }
    extractor = (
        production_contract.create_langchain_constraint_extractor_from_environment()
    )

    service = production_contract.DialogueConstraintService(
        lambda: ingredient_session,
        extractor,
    )
    result = service.extract(dialogue)

    assert result["dialogue_id"] == 9001
    assert result["meal_periods"] == ["晚餐"]
    assert result["dishes"]
