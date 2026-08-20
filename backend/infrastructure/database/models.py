from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """基础数据模型的统一声明基类。"""

    pass


class Recipe(Base):
    """菜品基础数据。"""

    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint(
            "total_time_lower_bound_minutes >= 0",
            name="ck_recipes_total_time_nonnegative",
        ),
        CheckConstraint(
            "difficulty IN ('简单', '中等', '复杂')",
            name="ck_recipes_difficulty",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    is_recommendable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    total_time_lower_bound_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    dish_type: Mapped[str | None] = mapped_column(String, nullable=True)
    atomic_steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    labels: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)


class Ingredient(Base):
    """归一化食材及每 100g 营养数据。"""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    english_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    energy_kcal: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    carbohydrate_g: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    calcium_mg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    iron_mg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    cholesterol_mg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, nullable=False)


class RecipeIngredient(Base):
    """菜品与食材的数量关联。"""

    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        CheckConstraint(
            "(is_nutrition_excluded = TRUE AND resolved_quantity_g = 0) OR "
            "(is_nutrition_excluded = FALSE AND resolved_quantity_g > 0)",
            name="ck_recipe_ingredients_resolved_quantity_valid",
        ),
    )

    recipe_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recipes.id"),
        primary_key=True,
    )
    ingredient_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingredients.id"),
        primary_key=True,
    )
    quantity_text: Mapped[str] = mapped_column(String, nullable=False)
    quantity_g: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resolved_quantity_g: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    is_quantity_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_nutrition_excluded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )


class RecipeNutrition(Base):
    """菜谱整份配方的九项营养。"""

    __tablename__ = "recipe_nutrition"
    __table_args__ = (
        CheckConstraint(
            "energy_kcal >= 0 AND protein_g >= 0 AND fat_g >= 0 "
            "AND carbohydrate_g >= 0 AND fiber_g >= 0 AND sodium_mg >= 0 "
            "AND calcium_mg >= 0 AND iron_mg >= 0 AND cholesterol_mg >= 0",
            name="ck_recipe_nutrition_nonnegative",
        ),
    )

    recipe_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recipes.id"),
        primary_key=True,
    )
    energy_kcal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    protein_g: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fat_g: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    carbohydrate_g: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fiber_g: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    sodium_mg: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    calcium_mg: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    iron_mg: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cholesterol_mg: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class UserProfile(Base):
    """归一化用户健康档案。"""

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("sex IN ('男', '女')", name="ck_user_profiles_sex"),
        CheckConstraint("age > 0", name="ck_user_profiles_age_positive"),
        CheckConstraint(
            "activity_level IN ('低', '中', '高')",
            name="ck_user_profiles_activity_level",
        ),
        CheckConstraint("height_cm > 0", name="ck_user_profiles_height_positive"),
        CheckConstraint("weight_kg > 0", name="ck_user_profiles_weight_positive"),
        CheckConstraint("bmi > 0", name="ck_user_profiles_bmi_positive"),
        CheckConstraint(
            "((sex = '女' AND age BETWEEN 50 AND 64 "
            "AND is_menstruating IS NOT NULL) OR "
            "(NOT (sex = '女' AND age BETWEEN 50 AND 64) "
            "AND is_menstruating IS NULL))",
            name="ck_user_profiles_menstruating_scope",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    sex: Mapped[str] = mapped_column(String, nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_level: Mapped[str] = mapped_column(String, nullable=False)
    special_populations: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    gestational_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_menstruating: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    taste_preference: Mapped[str] = mapped_column(String, nullable=False)
    allergens: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    health_goals: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    height_cm: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    bmi: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    medical_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProfileDriTarget(Base):
    """用户在指定餐次的一项营养参考目标。"""

    __tablename__ = "profile_dri_targets"
    __table_args__ = (
        CheckConstraint(
            "meal_period IN ('早餐', '午餐', '晚餐')",
            name="ck_profile_dri_targets_meal_period",
        ),
        CheckConstraint(
            "nutrient IN ('energy_kcal', 'protein_g', 'fat_g', "
            "'carbohydrate_g', 'fiber_g', 'sodium_mg', 'calcium_mg', "
            "'iron_mg', 'cholesterol_mg')",
            name="ck_profile_dri_targets_nutrient",
        ),
        CheckConstraint(
            "status IN ('available', 'not_established')",
            name="ck_profile_dri_targets_status",
        ),
        CheckConstraint(
            "unit IN ('kcal', 'g', 'mg')",
            name="ck_profile_dri_targets_unit",
        ),
        CheckConstraint(
            "target_basis IS NULL OR target_basis IN ('EER', 'RNI', 'AI')",
            name="ck_profile_dri_targets_target_basis",
        ),
        CheckConstraint(
            "lower_basis IS NULL OR lower_basis IN ('AI', 'AMDR')",
            name="ck_profile_dri_targets_lower_basis",
        ),
        CheckConstraint(
            "upper_basis IS NULL OR upper_basis IN ('AI', 'AMDR', 'PI', 'UL')",
            name="ck_profile_dri_targets_upper_basis",
        ),
        CheckConstraint(
            "(target_value IS NULL OR target_value >= 0) AND "
            "(lower_bound IS NULL OR lower_bound >= 0) AND "
            "(upper_bound IS NULL OR upper_bound >= 0) AND "
            "(lower_bound IS NULL OR upper_bound IS NULL OR upper_bound >= lower_bound)",
            name="ck_profile_dri_targets_values",
        ),
        CheckConstraint(
            "status = 'available' OR "
            "(target_value IS NULL AND lower_bound IS NULL AND upper_bound IS NULL "
            "AND target_basis IS NULL AND lower_basis IS NULL AND upper_basis IS NULL)",
            name="ck_profile_dri_targets_not_established_empty",
        ),
    )

    profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_profiles.id"),
        primary_key=True,
    )
    meal_period: Mapped[str] = mapped_column(String, primary_key=True)
    nutrient: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    target_value: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    lower_bound: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    upper_bound: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    target_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    lower_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    upper_basis: Mapped[str | None] = mapped_column(String, nullable=True)


class DialogueSession(Base):
    """多轮约束会话及其合并约束状态。"""

    __tablename__ = "dialogue_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'needs_confirmation', "
            "'ready_for_planning')",
            name="ck_dialogue_sessions_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_profiles.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    merged_constraints: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )


class DialogueTurn(Base):
    """多轮约束会话的轮次记录。"""

    __tablename__ = "dialogue_turns"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "turn_number",
            name="uq_dialogue_turns_session_turn",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dialogue_sessions.id"),
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(String, nullable=False)
