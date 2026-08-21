from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AnswerComposerError(Exception):
    """回答组装的接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class AnswerComposerService:
    """将统一推荐结果组装为面向用户的自然语言回答。

    菜名逐字取自推荐理由结果,模板不自由发挥,保证菜谱真实性。
    """

    def compose(self, generation_result: object) -> str:
        """按推荐终态组装回答文本,所有终态都返回非空文本。"""

        result = _require_mapping(generation_result, "推荐结果无效")
        status = result.get("status")
        if status == "recommended":
            return self._compose_recommended(result)
        if status == "needs_confirmation":
            return self._compose_confirmation(result)
        if status == "in_progress":
            return "会话尚无内容，请先描述您的用餐需求。"
        if status == "constraint_conflict":
            return self._compose_conflict(result)
        if status == "unmatched_allergen":
            return self._compose_unmatched_allergen(result)
        if status == "empty_candidate":
            return "没有符合条件的候选菜品，请放宽约束后重试。"
        if status == "planning_infeasible":
            return "无法排出满足约束的菜单，请放宽约束后重试。"
        raise AnswerComposerError(500, f"未知推荐状态:{status}")

    def _compose_recommended(self, result: Mapping[str, Any]) -> str:
        """推荐成功:餐次、人数、菜品清单、逐菜理由与整桌理由。"""

        confirmation = _require_mapping(
            result.get("confirmation_state"),
            "确认状态无效",
        )
        context = _require_mapping(
            confirmation.get("planning_context"),
            "规划上下文无效",
        )
        reasons = _require_mapping(
            result.get("recommendation_reason_result"),
            "推荐理由无效",
        )
        dishes = reasons.get("dish_recommendations")
        if not isinstance(dishes, list):
            raise AnswerComposerError(500, "推荐菜品无效")

        parts: list[str] = []
        meal_period = context.get("meal_period")
        diner_count = context.get("diner_count")
        prefix = "已为您安排"
        if isinstance(meal_period, str) and meal_period:
            prefix += meal_period
        if isinstance(diner_count, int) and diner_count > 0:
            prefix += f"，{diner_count}人份"
        parts.append(prefix + "菜单：")

        for index, dish in enumerate(dishes, start=1):
            dish_mapping = _require_mapping(dish, "推荐菜品无效")
            lines = [f"{index}. {dish_mapping.get('recipe_name')}"]
            dish_reasons = dish_mapping.get("reasons")
            if isinstance(dish_reasons, list):
                for reason in dish_reasons:
                    reason_mapping = _require_mapping(
                        reason,
                        "菜品推荐理由无效",
                    )
                    text = reason_mapping.get("text")
                    if isinstance(text, str) and text:
                        lines.append(f"   - {text}")
            parts.append("\n".join(lines))

        menu_reasons = reasons.get("menu_reasons")
        if isinstance(menu_reasons, list):
            for reason in menu_reasons:
                reason_mapping = _require_mapping(reason, "整桌推荐理由无效")
                text = reason_mapping.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)

        warnings = result.get("quality_warnings")
        if isinstance(warnings, list):
            for warning in warnings:
                if warning.get("code") == "nutrition_score_below_target":
                    parts.append(
                        f"提示：本桌营养得分{warning.get('nutrition_score')}分，"
                        f"低于目标{warning.get('target_score')}分。"
                    )
        return "\n".join(parts)

    def _compose_confirmation(self, result: Mapping[str, Any]) -> str:
        """需要确认餐次:直接输出确认状态已有的固定消息。"""

        confirmation = _require_mapping(
            result.get("confirmation_state"),
            "确认状态无效",
        )
        message = confirmation.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AnswerComposerError(500, "确认消息无效")
        return message

    def _compose_conflict(self, result: Mapping[str, Any]) -> str:
        """约束冲突:列出冲突项,不强行推荐。"""

        conflicts = result.get("conflicts")
        if not isinstance(conflicts, list):
            raise AnswerComposerError(500, "约束冲突结果无效")
        lines = ["检测到约束冲突，无法推荐菜单："]
        for conflict in conflicts:
            if isinstance(conflict, Mapping):
                detail = conflict.get("detail") or conflict.get("message")
                if detail is not None:
                    lines.append(f"- {detail}")
                    continue
            lines.append(f"- {conflict}")
        return "\n".join(lines)

    def _compose_unmatched_allergen(
        self,
        result: Mapping[str, Any],
    ) -> str:
        """过敏词未识别:为保障零违反拒绝推荐并列出未识别词。"""

        allergens = result.get("unmatched_allergens")
        if not isinstance(allergens, list):
            raise AnswerComposerError(500, "未识别过敏词结果无效")
        return (
            "存在无法识别的过敏词："
            + "、".join(str(item) for item in allergens)
            + "，为保障安全无法推荐菜单。"
        )


def _require_mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnswerComposerError(500, message)
    return value


def compose_with_llm(
    chat_model: object,
    generation_result: object,
) -> str:
    """模板组装后用LLM润色回答；菜名必须全部保留，缺失时回退模板。

    仅用于对比实验，默认不接入API请求路径。
    """

    result = _require_mapping(generation_result, "推荐结果无效")
    template = AnswerComposerService().compose(result)
    recipe_names = _collect_recipe_names(result)
    invoke = getattr(chat_model, "invoke", None)
    if not callable(invoke):
        raise AnswerComposerError(500, "LLM润色客户端无效")
    prompt = (
        "以下是膳食推荐回答的草稿和其中必须保留的真实菜名清单。"
        "请把回答润色为更自然、更口语化的中文，保持原有全部信息"
        "（菜名、份量、推荐理由、营养与健康说明），"
        "不得修改任何菜名、不得增删菜品，直接输出润色后的回答文本，"
        "不要输出任何解释。\n"
        f"必须保留的菜名：{'、'.join(recipe_names)}\n"
        "草稿：\n"
        f"{template}"
    )
    try:
        message = invoke(prompt)
    except Exception as exc:
        raise AnswerComposerError(503, f"LLM润色调用失败：{exc}") from exc
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        content = message if isinstance(message, str) else ""
    polished = content.strip()
    if not polished or any(name not in polished for name in recipe_names):
        return template
    return polished


def _collect_recipe_names(result: Mapping[str, Any]) -> list[str]:
    """收集推荐结果中的全部真实菜名。"""

    reasons = _require_mapping(
        result.get("recommendation_reason_result"),
        "推荐理由无效",
    )
    dishes = reasons.get("dish_recommendations")
    if not isinstance(dishes, list):
        raise AnswerComposerError(500, "推荐菜品无效")
    names: list[str] = []
    for dish in dishes:
        dish_mapping = _require_mapping(dish, "推荐菜品无效")
        name = dish_mapping.get("recipe_name")
        if not isinstance(name, str) or not name:
            raise AnswerComposerError(500, "推荐菜品缺少菜名")
        names.append(name)
    return names


__all__ = ["AnswerComposerError", "AnswerComposerService", "compose_with_llm"]
