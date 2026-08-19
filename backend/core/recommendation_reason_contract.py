from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal, NamedTuple, TypedDict, TypeAlias


GradeName: TypeAlias = Literal["excellent", "normal", "bad"]
HealthRule: TypeAlias = Literal[
    "sodium_upper_bound",
    "macronutrient_energy_ratio",
]
SourceComponent: TypeAlias = Literal["dish_filtering", "menu_planning"]


class NutrientSpec(NamedTuple):
    """计分营养的字段名、显示名和单位。"""

    field: str
    label: str
    unit: str


SCORED_NUTRIENT_SPECS: Final[tuple[NutrientSpec, ...]] = (
    NutrientSpec("energy_kcal", "能量", "kcal"),
    NutrientSpec("protein_g", "蛋白质", "g"),
    NutrientSpec("fat_g", "脂肪", "g"),
    NutrientSpec("carbohydrate_g", "碳水化合物", "g"),
    NutrientSpec("fiber_g", "膳食纤维", "g"),
    NutrientSpec("sodium_mg", "钠", "mg"),
    NutrientSpec("calcium_mg", "钙", "mg"),
    NutrientSpec("iron_mg", "铁", "mg"),
)
MAX_NUTRITION_SCORE: Final = 16
GRADE_SCORES: Final[dict[GradeName, int]] = {
    "excellent": 2,
    "normal": 1,
    "bad": 0,
}
GRADE_LABELS: Final[dict[GradeName, str]] = {
    "excellent": "优秀区间",
    "normal": "正常区间",
    "bad": "正常区间外",
}


class ReasonSource(TypedDict):
    """一条推荐依据在上游结果中的位置。"""

    component: SourceComponent
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
    grade: GradeName
    grade_label: str
    score: int
    source: ReasonSource


class HealthConstraintReason(TypedDict):
    """菜单规划实际应用的一项健康硬约束。"""

    reason_type: Literal["health_constraint"]
    constraint: str
    rule: HealthRule
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
    "GradeName",
    "HealthRule",
    "HealthConstraintReason",
    "MAX_NUTRITION_SCORE",
    "MenuReason",
    "NutrientDetail",
    "NutrientSpec",
    "NutritionSummaryReason",
    "ReasonSource",
    "RecommendationReasonError",
    "RecommendationReasonResult",
    "SCORED_NUTRIENT_SPECS",
    "SourceComponent",
    "TagMatchReason",
]
