from __future__ import annotations

from typing import Final, TypedDict


class DishFilteringValidationError(Exception):
    """菜品筛选的可预期接口错误（输入不符合 Spec_04 契约）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class DishFilteringExecutionError(Exception):
    """菜品筛选的不可预期接口错误（Neo4j 不可达或查询失败）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class RecipeMatch(TypedDict):
    """匹配到的菜谱（数据单位）。"""

    recipe_name: str
    recipe_type: str | None
    matched_tags: list[str]
    matched_groups: list[str]


class DishFilteringResult(TypedDict):
    """每组需求对应的菜谱候选列表。"""

    dishes: list[list[RecipeMatch]]
    unmatched_allergens: list[str]


# 集成约束输入字段（来自 Spec_03 输出）
INTEGRATED_TOP_LEVEL_FIELDS: Final = (
    "profile_id",
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "max_total_time_minutes",
    "available_ingredients",
    "allergens",
    "dishes",
    "has_conflicts",
    "conflicts",
)
INTEGRATED_DISH_FIELDS: Final = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredients",
)
INGREDIENT_REQUIREMENT_FIELDS: Final = ("kind", "value")

# 入组标签映射（5 组 23 个，来自 spec_02/03 枚举）
GROUP_TAGS: Final[dict[str, tuple[str, ...]]] = {
    "餐次": ("下午茶", "晚餐", "早餐", "午餐"),
    "口味": ("甜", "清淡", "辣", "咸", "酸"),
    "菜系": ("西餐风味", "东北菜", "粤菜", "川湘菜", "江浙菜"),
    "功效": ("助眠", "减脂", "养胃健胃消食", "贫血", "哺乳"),
    "人群": ("上班族", "儿童", "老人", "更年期"),
}
TAG_GROUPS: Final = tuple(GROUP_TAGS)

# 标签 → 所属组（反向映射，供结果推导）
TAG_TO_GROUP: Final[dict[str, str]] = {
    tag: group
    for group, tags in GROUP_TAGS.items()
    for tag in tags
}

# 口味键 → 口味标签
TASTE_KEY_TO_TAG: Final = {
    "is_sweet": "甜",
    "is_light": "清淡",
    "is_spicy": "辣",
    "is_salty": "咸",
    "is_sour": "酸",
}

# 过敏类目概念 → 成员（Ingredient 标准名，预置数据）
ALLERGEN_CONCEPT_MEMBERS: Final[dict[str, tuple[str, ...]]] = {
    "海鲜": (
        "基围虾", "大虾干", "对虾", "小河虾", "小龙虾", "明虾", "河虾",
        "河虾仁", "波士顿龙虾", "皮皮虾", "罗氏虾", "草虾", "虾", "虾丸",
        "虾仁", "虾头", "虾尾", "虾滑", "虾皮", "虾米", "虾肉", "虾饺",
        "虾黄", "鲜虾", "黑虎虾",
        "大闸蟹", "梭子蟹", "螃蟹", "蟹肉棒", "蟹黄", "蟹黄/蟹膏", "青蟹",
        "响螺", "干贝", "扇贝", "花蛤", "蛤蜊", "青口贝",
        "三文鱼", "咸鱼", "墨鱼", "墨鱼丝", "墨鱼丸", "墨鱼花", "多宝鱼",
        "大连鲍鱼", "大黄鱼", "小鱼干", "小黄鱼", "巴沙鱼", "带鱼",
        "明太鱼籽", "柴鱼片", "桂鱼", "武昌鱼", "比目鱼", "油发鱼肚",
        "海鲈鱼", "秋刀鱼", "章鱼干", "笋壳鱼", "罗非鱼", "芝麻鱼",
        "草鱼", "豆豉鲮鱼", "金枪鱼罐头",
    ),
    "蟹类": (
        "大闸蟹", "梭子蟹", "螃蟹", "蟹肉棒", "蟹黄", "蟹黄/蟹膏", "青蟹",
    ),
    "坚果": ("花生", "核桃", "杏仁", "腰果", "开心果", "榛子", "松子"),
    "蛋类": ("鸡蛋", "鸭蛋", "鹅蛋", "鹌鹑蛋"),
    "奶类": ("牛奶", "羊奶", "奶酪", "黄油"),
    "豆类": ("黄豆", "大豆", "豆腐", "豆浆"),
    "麸质": ("面粉", "面条", "挂面"),
    "面": ("面粉", "面条", "挂面"),
}

# 全部过敏概念成员（食材词直接排除时判断用）
ALL_ALLERGEN_MEMBERS: Final[frozenset[str]] = frozenset(
    member
    for concept_members in ALLERGEN_CONCEPT_MEMBERS.values()
    for member in concept_members
)

# 概念 kind 映射（过敏类目 vs 概念）
CONCEPT_KINDS: Final[dict[str, str]] = {
    name: ("concept" if name == "面" else "allergen")
    for name in ALLERGEN_CONCEPT_MEMBERS
}

# 辅料名单（非核心食材，跨类目）；名单外为核心食材
AUXILIARY_INGREDIENTS: Final[frozenset[str]] = frozenset(
    {
        "espresso", "乌龙茶", "伯爵红茶", "可乐", "咖啡粉", "咖啡豆",
        "啤酒", "大葱", "大蒜", "姜", "小葱", "无酒精饮料", "朗姆酒",
        "果蔬汁", "桃汁", "椰子水", "樱桃酒", "橄榄油", "橙汁", "气泡水",
        "水", "波特酒", "淀粉", "清水", "清酒", "温开水", "温水", "热水",
        "猪油", "玫瑰露酒", "甜酒酿", "生姜", "生粉", "白兰地", "白糖",
        "白葡萄酒", "米酒", "红茶", "红茶包", "红葡萄酒", "红薯淀粉",
        "红酒", "绍酒", "绿茶", "胡萝卜汁", "花雕酒", "苏打水", "苹果酒",
        "茉莉花茶", "茶叶", "菜籽油", "菠萝醋", "葡萄酒", "葱", "蒜",
        "蒜末", "辣椒", "青麦汁", "面粉", "食用油", "黑啤酒", "龙井茶叶",
    }
)


__all__ = [
    "ALL_ALLERGEN_MEMBERS",
    "ALLERGEN_CONCEPT_MEMBERS",
    "AUXILIARY_INGREDIENTS",
    "CONCEPT_KINDS",
    "DishFilteringExecutionError",
    "DishFilteringResult",
    "DishFilteringValidationError",
    "GROUP_TAGS",
    "INGREDIENT_REQUIREMENT_FIELDS",
    "INTEGRATED_DISH_FIELDS",
    "INTEGRATED_TOP_LEVEL_FIELDS",
    "RecipeMatch",
    "TAG_GROUPS",
    "TAG_TO_GROUP",
    "TASTE_KEY_TO_TAG",
]
