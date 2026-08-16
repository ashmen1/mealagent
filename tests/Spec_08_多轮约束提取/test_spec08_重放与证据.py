from __future__ import annotations

from .spec08_support import (
    build_dinner_for_two_dishes,
    build_dish_action,
    build_first_dinner_for_two,
    build_inherited_dinner_for_two,
    build_top_action,
    build_turn_result,
)


def test_未声明的标量改动_502_重试一次仍失败(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(session_id, diner_count=3),
        build_inherited_dinner_for_two(session_id, diner_count=3),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "好的"),
        502,
    )
    assert llm_client.call_count == 3


def test_声明与输出不一致_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        # 声明 remove diner_count,但输出仍为2,重放(null)与输出(2)不一致
        build_inherited_dinner_for_two(
            session_id,
            change_actions=[
                build_top_action("diner_count", "remove", "人数不限")
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            change_actions=[
                build_top_action("diner_count", "remove", "人数不限")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "人数不限"),
        502,
    )


def test_标量add新值不大于旧值_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        # add 但输出1 <= 旧值2
        build_inherited_dinner_for_two(
            session_id,
            diner_count=1,
            evidence={"diner_count": "减一个人"},
            change_actions=[
                build_top_action("diner_count", "add", "减一个人")
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=1,
            evidence={"diner_count": "减一个人"},
            change_actions=[
                build_top_action("diner_count", "add", "减一个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "减一个人"),
        502,
    )


def test_同一顶层字段多条声明_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "三个人"),
                build_top_action("diner_count", "replace", "三个人"),
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "三个人"),
                build_top_action("diner_count", "replace", "三个人"),
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_同一Dish多条声明_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
            change_actions=[
                build_dish_action(0, "add", "加菜"),
                build_dish_action(0, "add", "加菜"),
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
            change_actions=[
                build_dish_action(0, "add", "加菜"),
                build_dish_action(0, "add", "加菜"),
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "加菜"),
        502,
    )


def test_未声明的Dish改动_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
        ),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "加菜"),
        502,
    )


def test_首轮change_actions非空_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            diner_count=2,
            evidence={"diner_count": "两个人"},
            change_actions=[
                build_top_action("diner_count", "add", "两个人")
            ],
        ),
        build_turn_result(
            session_id,
            diner_count=2,
            evidence={"diner_count": "两个人"},
            change_actions=[
                build_top_action("diner_count", "add", "两个人")
            ],
        ),
    ]

    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )


def test_首轮evidence路径多出_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            diner_count=2,
            evidence={
                "diner_count": "两个人",
                "多余路径": "两个人",
            },
        ),
        build_turn_result(
            session_id,
            diner_count=2,
            evidence={
                "diner_count": "两个人",
                "多余路径": "两个人",
            },
        ),
    ]

    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )


def test_标量add旧值为空_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        # 首轮无人数约束,旧值为 null
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
        # 旧值为 null 时声明 add,应使用 replace
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            diner_count=2,
            evidence={"diner_count": "两个人"},
            change_actions=[
                build_top_action("diner_count", "add", "两个人")
            ],
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            diner_count=2,
            evidence={"diner_count": "两个人"},
            change_actions=[
                build_top_action("diner_count", "add", "两个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "今晚吃啥")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )


def test_变更声明action非法值_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "delete", "三个人")
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "delete", "三个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_变更声明field与dish_index都为空_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": None,
                    "dish_index": None,
                    "action": "replace",
                    "evidence": "三个人",
                }
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": None,
                    "dish_index": None,
                    "action": "replace",
                    "evidence": "三个人",
                }
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_变更声明field与dish_index同时填写_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": "diner_count",
                    "dish_index": 0,
                    "action": "replace",
                    "evidence": "三个人",
                }
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": "diner_count",
                    "dish_index": 0,
                    "action": "replace",
                    "evidence": "三个人",
                }
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_变更声明evidence非字符串_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": "diner_count",
                    "dish_index": None,
                    "action": "replace",
                    "evidence": 123,
                }
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                {
                    "field": "diner_count",
                    "dish_index": None,
                    "action": "replace",
                    "evidence": 123,
                }
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_重放校验失败重试一次后成功(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(session_id, diner_count=3),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "再加一个人"},
            change_actions=[
                build_top_action("diner_count", "add", "再加一个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "再加一个人")

    assert result["merged_constraints"]["diner_count"] == 3
    assert llm_client.call_count == 3


def test_变更字段证据必须命中本轮原文_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        # 本轮原文"好的"不含证据片段"三个人"
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "三个人")
            ],
        ),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "三个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "好的"),
        502,
    )


def test_动作证据必须命中本轮原文_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
            change_actions=[build_dish_action(0, "add", "加菜")],
        ),
        build_inherited_dinner_for_two(
            session_id,
            dishes=build_dinner_for_two_dishes(session_id, first_count=3),
            evidence={"dishes[0].count": "加菜"},
            change_actions=[build_dish_action(0, "add", "加菜")],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "好的"),
        502,
    )


def test_首轮证据缺失_502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    # 首轮 diner_count=2 但 evidence 为空
    llm_client.responses = [
        build_turn_result(session_id, diner_count=2),
        build_turn_result(session_id, diner_count=2),
    ]

    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )
    assert llm_client.call_count == 2


def test_继承字段保留原轮证据_忽略LLM重新给出的片段(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            # LLM 对未变更的 meal_periods 重新给了错误片段,应被忽略
            evidence={
                "diner_count": "再加一个人",
                "meal_periods[0]": "瞎写的片段",
            },
            change_actions=[
                build_top_action("diner_count", "add", "再加一个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "再加一个人")

    evidence = result["merged_constraints"]["evidence"]
    assert evidence["meal_periods[0]"] == "晚上"
    assert evidence["diner_count"] == "再加一个人"
    assert evidence["dishes[0].count"] == "两菜"
