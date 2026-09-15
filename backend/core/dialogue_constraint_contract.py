from __future__ import annotations

from typing import Annotated, Any, Final, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
)


MERGED_CONSTRAINT_FIELDS: Final = (
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
    "dishes",
    "evidence",
)
TOP_LEVEL_FIELDS: Final = MERGED_CONSTRAINT_FIELDS + (
    "change_actions",
    "menu_change",
)
DISH_FIELDS: Final = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredient_groups",
    "required_staple_ingredients",
    "excluded_staple_ingredients",
)
INGREDIENT_GROUP_FIELDS: Final = ("match", "items")
INGREDIENT_REQUIREMENT_FIELDS: Final = ("kind", "value")
CHANGE_ACTION_FIELDS: Final = ("field", "dish_index", "action", "evidence")
MENU_CHANGE_FIELDS: Final = (
    "mode",
    "replace_count",
    "target_positions",
    "target_recipe_names",
    "replacement_recipe_name",
    "unresolved_target",
    "evidence",
)

MEAL_PERIODS: Final = ("下午茶", "晚餐", "早餐", "午餐")
DISH_TYPES: Final = ("菜", "汤", "主食", "小菜", "未指定")
TASTE_PREFERENCES: Final = (
    "is_sweet",
    "is_light",
    "is_spicy",
    "is_salty",
    "is_sour",
)
CUISINES: Final = ("西餐风味", "东北菜", "粤菜", "川湘菜", "江浙菜")
EFFECTS: Final = ("助眠", "减脂", "养胃健胃消食", "贫血", "哺乳")
SPECIAL_POPULATIONS: Final = ("上班族", "儿童", "老人", "更年期")
INGREDIENT_GROUP_MATCHES: Final = ("all", "any")
INGREDIENT_REQUIREMENT_KINDS: Final = (
    "ingredient",
    "category",
    "concept",
)
INGREDIENT_CONCEPTS: Final = ("面",)
CHANGE_ACTIONS: Final = ("add", "replace", "remove")
MENU_CHANGE_MODES: Final = (
    "none",
    "replace_all",
    "replace_partial",
    "replace_specific",
    "restore_specific",
)
CHANGEABLE_TOP_FIELDS: Final = (
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
)
SCALAR_FIELDS: Final = (
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
)
SESSION_STATUSES: Final = (
    "in_progress",
    "needs_confirmation",
    "ready_for_planning",
)
MISSING_REQUIREMENTS: Final = ("人数", "明确菜品类型")


class DialogueConstraintExtractionError(Exception):
    """统一对话约束提取的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


_Item = TypeVar("_Item")


def _normalize_decimal_integer(value: object) -> object:
    """兼容工具协议返回的纯十进制整数字符串，并拒绝其他宽松转换。"""

    if type(value) is int:
        return value
    if (
        isinstance(value, str)
        and value
        and value.isascii()
        and value.isdecimal()
    ):
        return int(value)
    raise ValueError("必须是整数或纯十进制整数字符串")


def _ensure_unique(items: list[_Item]) -> list[_Item]:
    """拒绝数组中的重复项，保持原有工具契约。"""

    for index, item in enumerate(items):
        if item in items[:index]:
            raise ValueError("数组不允许重复项")
    return items


def _remove_optional_defaults(schema: dict[str, Any]) -> None:
    """移除可选口味键的展示默认值，避免工具误把 null 当成合法布尔值。"""

    for property_schema in schema.get("properties", {}).values():
        property_schema.pop("default", None)


PositiveToolInteger = Annotated[
    int,
    Field(ge=1),
    BeforeValidator(_normalize_decimal_integer),
]
NonNegativeToolInteger = Annotated[
    int,
    Field(ge=0),
    BeforeValidator(_normalize_decimal_integer),
]
MealPeriod = Literal[*MEAL_PERIODS]
DishType = Literal[*DISH_TYPES]
Cuisine = Literal[*CUISINES]
Effect = Literal[*EFFECTS]
SpecialPopulation = Literal[*SPECIAL_POPULATIONS]
IngredientMatch = Literal[*INGREDIENT_GROUP_MATCHES]
IngredientRequirementKind = Literal[*INGREDIENT_REQUIREMENT_KINDS]
Difficulty = Literal["简单", "中等"]
ChangeActionName = Literal[*CHANGE_ACTIONS]
ChangeableTopField = Literal[*CHANGEABLE_TOP_FIELDS]
MenuChangeMode = Literal[*MENU_CHANGE_MODES]


class StrictContractModel(BaseModel):
    """禁止模型输出契约之外的字段。"""

    model_config = ConfigDict(extra="forbid")


class TastePreferences(StrictContractModel):
    """只允许出现用户明确表达的受控口味布尔键。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_remove_optional_defaults,
    )

    # 字段缺省表示未提取；若模型显式返回该键，值必须为严格布尔值。
    is_sweet: StrictBool = Field(default=None, validate_default=False)  # type: ignore[assignment]
    is_light: StrictBool = Field(default=None, validate_default=False)  # type: ignore[assignment]
    is_spicy: StrictBool = Field(default=None, validate_default=False)  # type: ignore[assignment]
    is_salty: StrictBool = Field(default=None, validate_default=False)  # type: ignore[assignment]
    is_sour: StrictBool = Field(default=None, validate_default=False)  # type: ignore[assignment]


class IngredientRequirement(StrictContractModel):
    """单个普通食材、动态类别或受控概念要求。"""

    kind: IngredientRequirementKind
    value: str


class IngredientGroup(StrictContractModel):
    """一个普通食材 AND/OR 组。"""

    match: IngredientMatch
    items: Annotated[
        list[IngredientRequirement],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]


class StapleIngredientGroup(StrictContractModel):
    """一个主食来源 AND/OR 组。"""

    match: IngredientMatch
    items: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]


class Dish(StrictContractModel):
    """一个独立的菜品查询组。"""

    count: PositiveToolInteger | None
    dish_type: DishType
    taste_preferences: TastePreferences
    cuisines: Annotated[
        list[Cuisine],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    effects: Annotated[
        list[Effect],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    special_populations: Annotated[
        list[SpecialPopulation],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    required_ingredient_groups: Annotated[
        list[IngredientGroup],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    required_staple_ingredients: StapleIngredientGroup | None
    excluded_staple_ingredients: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]


class ChangeAction(StrictContractModel):
    """当前轮相对上一状态的一项可重放变化。"""

    field: ChangeableTopField | None
    dish_index: NonNegativeToolInteger | None
    action: ChangeActionName
    evidence: str


class MenuChange(StrictContractModel):
    """当前轮针对最近成功菜单的操作意图。"""

    mode: MenuChangeMode
    replace_count: PositiveToolInteger | None
    target_positions: Annotated[
        list[PositiveToolInteger],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    target_recipe_names: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    replacement_recipe_name: Annotated[str, Field(min_length=1)] | None
    unresolved_target: Annotated[str, Field(min_length=1)] | None
    evidence: Annotated[str, Field(min_length=1)] | None


class DialogueConstraintsTurnOutput(StrictContractModel):
    """当前轮次提取出的完整新约束和相对上一状态的变更声明。"""

    dialogue_id: PositiveToolInteger
    meal_periods: Annotated[
        list[MealPeriod],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    diner_count: PositiveToolInteger | None
    total_dish_count: PositiveToolInteger | None
    max_total_time_minutes: PositiveToolInteger | None
    max_difficulty: Difficulty | None
    available_ingredients: Annotated[
        list[str],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    dishes: Annotated[
        list[Dish],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_ensure_unique),
    ]
    evidence: dict[str, str]
    change_actions: list[ChangeAction]
    menu_change: MenuChange


CONSTRAINT_OUTPUT_SCHEMA: Final[dict[str, Any]] = (
    DialogueConstraintsTurnOutput.model_json_schema()
)
_SCHEMA_DEFINITIONS = CONSTRAINT_OUTPUT_SCHEMA["$defs"]
INGREDIENT_REQUIREMENT_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS[
    "IngredientRequirement"
]
INGREDIENT_GROUP_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS[
    "IngredientGroup"
]
STAPLE_INGREDIENT_GROUP_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS[
    "StapleIngredientGroup"
]
DISH_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS["Dish"]
CHANGE_ACTION_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS[
    "ChangeAction"
]
MENU_CHANGE_SCHEMA: Final[dict[str, Any]] = _SCHEMA_DEFINITIONS[
    "MenuChange"
]


__all__ = [
    "CHANGEABLE_TOP_FIELDS",
    "ChangeAction",
    "CHANGE_ACTIONS",
    "CHANGE_ACTION_FIELDS",
    "CHANGE_ACTION_SCHEMA",
    "CONSTRAINT_OUTPUT_SCHEMA",
    "CUISINES",
    "DISH_FIELDS",
    "DISH_SCHEMA",
    "DISH_TYPES",
    "Dish",
    "DialogueConstraintsTurnOutput",
    "DialogueConstraintExtractionError",
    "EFFECTS",
    "INGREDIENT_CONCEPTS",
    "IngredientGroup",
    "IngredientRequirement",
    "INGREDIENT_GROUP_FIELDS",
    "INGREDIENT_GROUP_MATCHES",
    "INGREDIENT_GROUP_SCHEMA",
    "INGREDIENT_REQUIREMENT_FIELDS",
    "INGREDIENT_REQUIREMENT_KINDS",
    "INGREDIENT_REQUIREMENT_SCHEMA",
    "MEAL_PERIODS",
    "MENU_CHANGE_FIELDS",
    "MENU_CHANGE_MODES",
    "MENU_CHANGE_SCHEMA",
    "MenuChange",
    "MERGED_CONSTRAINT_FIELDS",
    "MISSING_REQUIREMENTS",
    "SCALAR_FIELDS",
    "SESSION_STATUSES",
    "SPECIAL_POPULATIONS",
    "StapleIngredientGroup",
    "STAPLE_INGREDIENT_GROUP_SCHEMA",
    "TastePreferences",
    "TASTE_PREFERENCES",
    "TOP_LEVEL_FIELDS",
]
