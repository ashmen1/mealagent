# Spec_00_基础数据存储

## 一句话目标

> 将处理完成的菜品、食材营养和用户健康档案存入4张基础表，为后续功能提供统一数据源。

## 数据模型

### recipes

| 字段                           | 类型    | 约束（必填？范围？默认？）           |
| ------------------------------ | ------- | ------------------------------------ |
| id                             | bigint  | 主键，数据库生成                     |
| name                           | string  | 必填，菜品名称唯一                   |
| total_time_lower_bound_minutes | integer | 必填，大于等于0                      |
| atomic_steps                   | JSON    | 必填，原子步骤数组                   |
| labels                         | JSON    | 必填，归一化Label数组；无Label时为[] |

### ingredients

| 字段           | 类型         | 约束（必填？范围？默认？）   |
| -------------- | ------------ | ---------------------------- |
| id             | bigint       | 主键，数据库生成             |
| name           | string       | 必填，归一化食材名唯一       |
| english_name   | string/null  | 可为空                       |
| category       | string/null  | 可为空                       |
| energy_kcal    | decimal/null | 每100g，可为空               |
| protein_g      | decimal/null | 每100g，可为空               |
| fat_g          | decimal/null | 每100g，可为空               |
| carbohydrate_g | decimal/null | 每100g，可为空               |
| fiber_g        | decimal/null | 每100g，可为空               |
| sodium_mg      | decimal/null | 每100g，可为空               |
| calcium_mg     | decimal/null | 每100g，可为空               |
| iron_mg        | decimal/null | 每100g，可为空               |
| cholesterol_mg | decimal/null | 每100g，可为空               |
| aliases        | JSON         | 必填，别名数组；无别名时为[] |

### recipe_ingredients

一份菜品的食材清单由多行组成：同一个recipe_id对应多个ingredient_id及其数量。

| 字段          | 类型         | 约束（必填？范围？默认？）       |
| ------------- | ------------ | -------------------------------- |
| recipe_id     | bigint       | 联合主键，外键关联recipes.id     |
| ingredient_id | bigint       | 联合主键，外键关联ingredients.id |
| quantity_text | string       | 必填，最终数量文本，如5g、1个    |
| quantity_g    | decimal/null | 可确定克数时填写，否则为null     |

联合主键为 `recipe_id + ingredient_id`。

### user_profiles

| 字段                | 类型         | 约束（必填？范围？默认？）       |
| ------------------- | ------------ | -------------------------------- |
| id                  | bigint       | 主键，使用健康档案原ID           |
| sex                 | string       | 必填，男或女                     |
| age                 | integer      | 必填，正整数                     |
| activity_level      | string       | 必填，低、中或高                 |
| special_populations | JSON         | 必填，数组；无特殊人群时为[]     |
| gestational_week    | integer/null | 孕妇填写孕周，其他人为null       |
| taste_preference    | string       | 必填，归一化口味值               |
| allergens           | JSON         | 必填，数组；无过敏食材时为[]     |
| health_goals        | JSON         | 必填，数组；无健康需求时为[]     |
| height_cm           | decimal      | 必填，大于0                      |
| weight_kg           | decimal      | 必填，大于0                      |
| bmi                 | decimal      | 必填，大于0                      |
| medical_metrics     | JSON         | 必填，体检指标对象；无指标时为{} |

## 端点 / 接口

| 动作              | 输入                                                               | 成功返回            | 失败情况（状态码）                                              |
| ----------------- | ------------------------------------------------------------------ | ------------------- | --------------------------------------------------------------- |
| import_basic_data | RecipeComplete.json、Ingredients2Nutrition.csv、归一化健康档案JSON | 4张基础表的写入数量 | 400：格式或字段错误；409：主键、唯一键或外键冲突；500：写入失败 |
| create_database_engine | 非空数据库URL字符串 | SQLAlchemy同步Engine | URL类型、空值或格式错误时抛出DatabaseConfigurationError |
| create_session_factory | SQLAlchemy同步Engine | 与该Engine绑定的Session工厂 | Engine类型错误时抛出TypeError |

## 边界（每条之后会变成一条测试）

- RecipeComplete.json中的每道菜写入一行recipes，多种食材分别写入多行recipe_ingredients。
- ingredients必须包含全部菜品使用的归一化食材；没有营养数据的食材仍需写入，营养字段为null。
- quantity_g只换算能够明确确定的质量值；个、勺、片、毫升等不能猜测换算，填写null。
- recipe_ingredients引用不存在的recipe_id或ingredient_id时失败。
- 菜名、归一化食材名或用户ID重复时失败，不静默覆盖。
- labels、atomic_steps、aliases及用户数组字段为空时保存[]，不能保存null。
- medical_metrics为空时保存{}，不能保存null。
- 任一数据写入失败时整批回滚，不留下部分数据。
- 数据库Engine只使用调用方显式传入的URL创建，并启用连接存活检查；不读取环境变量或内置默认地址。
- Session工厂只负责创建相互独立且绑定到指定Engine的Session，不自动提交或回滚事务。
- 调用方负责关闭Session、显式提交或回滚事务，并在不再使用时释放Engine。

## 明确不做（划出范围，防 AI 自由发挥）

- 不建立版本表、历史表或运行记录表。
- 不在入库时重新归一化食材、Label或健康档案。
- 不调用LLM补全或修正数据。
- 数据库工厂不自动连接验证，不创建或删除表，不执行数据查询。
- 不建立全局Engine、全局Session或模块级数据库缓存。
