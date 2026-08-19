from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypedDict, TypeAlias


SCORED_NUTRIENT_SPECS = (
    ("energy_kcal", "能量", "kcal"),
    ("protein_g", "蛋白质", "g"),
    ("fat_g", "脂肪", "g"),
    ("carbohydrate_g", "碳水化合物", "g"),
    ("fiber_g", "膳食纤维", "g"),
    ("sodium_mg", "钠", "mg"),
    ("calcium_mg", "钙", "mg"),
    ("iron_mg", "铁", "mg"),
)
MAX_NUTRITION_SCORE = 16
GRADE_SCORES = {
    "excellent": 2,
    "normal": 1,
    "bad": 0,
}
GRADE_LABELS = {
    "excellent": "优秀区间",
    "normal": "正常区间",
    "bad": "正常区间外",
}


class ReasonSource(TypedDict):
    """一条推荐依据在上游结果中的位置。"""

    component: Literal["dish_filtering", "menu_planning"]
    paths: list[str]


class TagMatchReason(TypedDict):
    """单道菜在一个标签组内的命中理由。"""

    reason_type: Literal["tag_match"]
    matched_group: str
    matched_tags: list[str]
    sources: list[ReasonSource]
    text: str


class DishRecommendation(TypedDict):
    """一份最终菜品及其标签推荐理由。"""

    dish_constraint_index: int
    recipe_name: str
    reasons: list[TagMatchReason]


class NutrientDetail(TypedDict):
    """整桌一项计分营养的实际值与等级。"""

    nutrient: str
    label: str
    menu_total_value: Decimal
    unit: str
    grade: Literal["excellent", "normal", "bad"]
    grade_label: str
    score: int
    source: ReasonSource


class HealthConstraintReason(TypedDict):
    """菜单规划实际应用的一项健康硬约束。"""

    reason_type: Literal["health_constraint"]
    constraint: str
    rule: Literal["sodium_upper_bound", "macronutrient_energy_ratio"]
    sources: list[ReasonSource]
    text: str


class NutritionSummaryReason(TypedDict):
    """整桌八项营养的结构化得分和正向摘要。"""

    reason_type: Literal["nutrition_summary"]
    nutrition_score: int
    max_score: int
    nutrient_details: list[NutrientDetail]
    sources: list[ReasonSource]
    text: str


MenuReason: TypeAlias = HealthConstraintReason | NutritionSummaryReason


class RecommendationReasonResult(TypedDict):
    """逐菜与整桌推荐理由。"""

    profile_id: int
    dialogue_id: int
    dish_recommendations: list[DishRecommendation]
    menu_reasons: list[MenuReason]


class RecommendationReasonError(Exception):
    """推荐理由组装的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "DishRecommendation",
    "GRADE_LABELS",
    "GRADE_SCORES",
    "HealthConstraintReason",
    "MAX_NUTRITION_SCORE",
    "MenuReason",
    "NutrientDetail",
    "NutritionSummaryReason",
    "ReasonSource",
    "RecommendationReasonError",
    "RecommendationReasonResult",
    "SCORED_NUTRIENT_SPECS",
    "TagMatchReason",
]
