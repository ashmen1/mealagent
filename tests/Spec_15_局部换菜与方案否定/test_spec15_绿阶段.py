from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.services.menu_planning import MenuPlanningService
from tests.Spec_06_菜单规划.spec06_support import (
    build_candidate,
    build_dish,
    build_planning_input,
)
from tests.Spec_14_对外服务接口.conftest import (
    FakeRecommendationService,
    build_dish_recommendation,
    build_generation_result,
    build_recommendation_reason,
)
from tests.Spec_14_对外服务接口.test_API层 import build_app, parse_sse


OLD_NAMES = ["菜A", "菜B", "菜C", "菜D"]
NEW_NAMES = ["菜E", "菜F", "菜G", "菜H", "菜I"]
RECOMMENDABLE = set(OLD_NAMES + NEW_NAMES)


def build_snapshot(names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "position": index,
            "dish_constraint_index": 0,
            "recipe_name": name,
        }
        for index, name in enumerate(names, start=1)
    ]


def build_change(
    mode: str,
    *,
    replace_count: int | None = None,
    target_positions: list[int] | None = None,
    target_recipe_names: list[str] | None = None,
    replacement_recipe_name: str | None = None,
    unresolved_target: str | None = None,
    evidence: str | None = "换菜",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "replace_count": replace_count,
        "target_positions": list(target_positions or []),
        "target_recipe_names": list(target_recipe_names or []),
        "replacement_recipe_name": replacement_recipe_name,
        "unresolved_target": unresolved_target,
        "evidence": evidence,
    }


def build_none_change() -> dict[str, Any]:
    return build_change("none", evidence=None)


def solve_with_policy(
    policy: dict[str, Any],
    *,
    old_names: list[str] | None = None,
) -> list[str]:
    previous = old_names or OLD_NAMES
    all_names = list(
        dict.fromkeys(
            previous + NEW_NAMES + policy["required_recipe_names"]
        )
    )
    planning_input = build_planning_input(
        total_dish_count=len(previous),
        dishes=[
            build_dish(
                count=len(previous),
                candidates=[build_candidate(name) for name in all_names],
            )
        ],
    )
    planning_input["menu_change_policy"] = policy
    result = MenuPlanningService().plan(planning_input)
    return [item["recipe_name"] for item in result["selected_dishes"]]


def build_api_change_result() -> dict[str, Any]:
    reasons = build_recommendation_reason(
        dishes=(
            build_dish_recommendation("菜A"),
            build_dish_recommendation("菜E"),
        )
    )
    return build_generation_result(
        recommendation_reason_result=reasons,
        menu_change_result={
            "mode": "replace_specific",
            "retained_recipe_names": ["菜A"],
            "removed_recipe_names": ["菜B"],
            "added_recipe_names": ["菜E"],
        },
    )


def test_对话端点正常返回换菜说明且不增加顶层字段() -> None:
    recommendation = FakeRecommendationService(
        results=[build_api_change_result()]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "session_id": 101,
                "messages": [{"role": "user", "content": "换掉第二道"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "object", "choices", "session_id", "status"}
    answer = body["choices"][0]["message"]["content"]
    assert "换出：菜B" in answer
    assert "换入：菜E" in answer


def test_流式端点返回换菜说明且SSE协议不变() -> None:
    recommendation = FakeRecommendationService(
        results=[build_api_change_result()]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "session_id": 101,
                "messages": [{"role": "user", "content": "换掉第二道"}],
                "stream": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    chunks = parse_sse(response)
    answer = "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks
    )
    assert response.status_code == 200
    assert response.headers["x-session-id"] == "101"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert "换出：菜B" in answer and "换入：菜E" in answer


def test_没有历史菜单时换菜要求确认且不生成策略(
    menu_change_contract,
) -> None:
    result = menu_change_contract.resolve(
        None,
        [],
        build_change("replace_all", evidence="全部换掉"),
        RECOMMENDABLE,
    )

    assert result["status"] == "needs_confirmation"
    assert result["policy"] is None
    assert "还没有可调整的历史菜单" in result["message"]


def test_全部换掉时新旧菜单零重叠(menu_change_contract) -> None:
    result = menu_change_contract.resolve(
        build_snapshot(OLD_NAMES),
        [],
        build_change("replace_all", evidence="全部换掉"),
        RECOMMENDABLE,
    )

    selected = solve_with_policy(result["policy"])
    assert result["status"] == "ready"
    assert set(selected).isdisjoint(OLD_NAMES)


def test_明确换两道时严格保留N减2道(menu_change_contract) -> None:
    result = menu_change_contract.resolve(
        build_snapshot(OLD_NAMES),
        [],
        build_change("replace_partial", replace_count=2, evidence="换两道"),
        RECOMMENDABLE,
    )

    selected = solve_with_policy(result["policy"])
    assert len(set(selected) & set(OLD_NAMES)) == 2


def test_模糊换一部分时默认替换向上取整的一半(
    menu_change_contract,
) -> None:
    old_names = ["菜A", "菜B", "菜C", "菜D", "菜E"]
    result = menu_change_contract.resolve(
        build_snapshot(old_names),
        [],
        build_change("replace_partial", evidence="这个方案不行，换个方案"),
        set(old_names + NEW_NAMES),
    )

    selected = solve_with_policy(result["policy"], old_names=old_names)
    assert result["effective_replace_count"] == 3
    assert len(set(selected) & set(old_names)) == 2


def test_指定目标时只换目标且其余菜全部保留(
    menu_change_contract,
) -> None:
    changes = [
        build_change(
            "replace_specific",
            target_positions=[2],
            evidence="换第二道",
        ),
        build_change(
            "replace_specific",
            target_recipe_names=["菜B"],
            evidence="换掉菜B",
        ),
    ]

    for change in changes:
        result = menu_change_contract.resolve(
            build_snapshot(OLD_NAMES), [], change, RECOMMENDABLE
        )
        selected = solve_with_policy(result["policy"])
        assert set(OLD_NAMES) - {"菜B"} <= set(selected)
        assert "菜B" not in selected


def test_指定A换B时B必须正式可推荐并被选中(
    menu_change_contract,
) -> None:
    valid = menu_change_contract.resolve(
        build_snapshot(OLD_NAMES),
        [],
        build_change(
            "replace_specific",
            target_recipe_names=["菜A"],
            replacement_recipe_name="菜E",
            evidence="把菜A换成菜E",
        ),
        RECOMMENDABLE,
    )
    unavailable = menu_change_contract.resolve(
        build_snapshot(OLD_NAMES),
        [],
        build_change(
            "replace_specific",
            target_recipe_names=["菜A"],
            replacement_recipe_name="不存在的菜",
            evidence="把菜A换成不存在的菜",
        ),
        RECOMMENDABLE,
    )

    selected = solve_with_policy(valid["policy"])
    assert "菜A" not in selected and "菜E" in selected
    assert unavailable["status"] == "needs_confirmation"
    assert unavailable["policy"] is None


def test_多个目标带不同条件时要求确认(menu_change_contract) -> None:
    result = menu_change_contract.resolve(
        build_snapshot(OLD_NAMES),
        [],
        build_change(
            "replace_specific",
            unresolved_target="多个目标提出了不同换菜条件",
            evidence="第二道换清淡，第三道换辣的",
        ),
        RECOMMENDABLE,
    )

    assert result["status"] == "needs_confirmation"
    assert result["policy"] is None
    assert "多个目标提出了不同换菜条件" in result["message"]


def test_目标无效时要求确认并保留待确认请求(
    menu_change_contract,
) -> None:
    changes = [
        build_change("replace_specific", target_positions=[9]),
        build_change("replace_specific", target_recipe_names=["菜Z"]),
        build_change("replace_specific", unresolved_target="目标描述有歧义"),
    ]

    for change in changes:
        result = menu_change_contract.resolve(
            build_snapshot(OLD_NAMES), [], change, RECOMMENDABLE
        )
        assert result["status"] == "needs_confirmation"
        assert result["policy"] is None
        assert result["pending_menu_change"] == change


def test_换菜无可行解时菜单和排除集合都不变(
    menu_change_contract,
) -> None:
    old_menu = build_snapshot(OLD_NAMES)
    excluded = ["菜X"]
    result = menu_change_contract.finalize(
        old_menu,
        excluded,
        build_change("replace_all", evidence="全部换掉"),
        "planning_infeasible",
        None,
    )

    assert result["last_menu"] == old_menu
    assert result["excluded_recipe_names"] == excluded
    assert result["menu_change_result"] is None


def test_换菜成功后换出菜加入排除集合(menu_change_contract) -> None:
    result = menu_change_contract.finalize(
        build_snapshot(OLD_NAMES),
        ["菜X"],
        build_change("replace_partial", replace_count=2),
        "recommended",
        build_snapshot(["菜A", "菜C", "菜E", "菜F"]),
    )

    assert result["excluded_recipe_names"] == ["菜X", "菜B", "菜D"]
    assert result["menu_change_result"]["removed_recipe_names"] == [
        "菜B",
        "菜D",
    ]
    assert result["menu_change_result"]["added_recipe_names"] == [
        "菜E",
        "菜F",
    ]


def test_明确换回来后A必选且只在成功时解除排除(
    menu_change_contract,
) -> None:
    change = build_change(
        "restore_specific",
        replacement_recipe_name="菜B",
        evidence="把菜B换回来",
    )
    resolution = menu_change_contract.resolve(
        build_snapshot(["菜A", "菜C", "菜D", "菜E"]),
        ["菜B", "菜X"],
        change,
        RECOMMENDABLE | {"菜X"},
    )
    failed = menu_change_contract.finalize(
        build_snapshot(["菜A", "菜C", "菜D", "菜E"]),
        ["菜B", "菜X"],
        change,
        "planning_infeasible",
        None,
    )
    succeeded = menu_change_contract.finalize(
        build_snapshot(["菜A", "菜C", "菜D", "菜E"]),
        ["菜B", "菜X"],
        change,
        "recommended",
        build_snapshot(["菜A", "菜B", "菜D", "菜E"]),
    )

    selected = solve_with_policy(
        resolution["policy"], old_names=["菜A", "菜C", "菜D", "菜E"]
    )
    assert "菜B" in selected
    assert failed["excluded_recipe_names"] == ["菜B", "菜X"]
    assert succeeded["excluded_recipe_names"] == ["菜X", "菜C"]


def test_普通重规划过滤排除菜但不把自然落选菜加入排除(
    menu_change_contract,
) -> None:
    candidates = [[{"recipe_name": name} for name in OLD_NAMES]]
    filtered = menu_change_contract.filter_candidates(candidates, ["菜B"])
    state = menu_change_contract.finalize(
        build_snapshot(OLD_NAMES),
        ["菜B"],
        build_none_change(),
        "recommended",
        build_snapshot(["菜C", "菜D", "菜E", "菜F"]),
    )

    assert [item["recipe_name"] for item in filtered[0]] == [
        "菜A",
        "菜C",
        "菜D",
    ]
    assert state["excluded_recipe_names"] == ["菜B"]


def test_不同会话的菜单状态互不修改(menu_change_contract) -> None:
    session_one_menu = build_snapshot(OLD_NAMES)
    session_two_menu = build_snapshot(["菜E", "菜F"])
    session_two_before = copy.deepcopy(session_two_menu)
    session_two_excluded = ["菜G"]

    menu_change_contract.finalize(
        session_one_menu,
        [],
        build_change("replace_specific", target_recipe_names=["菜B"]),
        "recommended",
        build_snapshot(["菜A", "菜C", "菜D", "菜E"]),
    )

    assert session_two_menu == session_two_before
    assert session_two_excluded == ["菜G"]
