# Spec_00_基础数据存储

## 一句话目标

> 将处理完成的菜品、食材营养、用户健康档案和 DRI 数据存入 6 张基础及派生表，为后续功能提供统一数据源。

## 数据模型

### recipes

| 字段                           | 类型    | 约束（必填？范围？默认？）           |
| ------------------------------ | ------- | ------------------------------------ |
| id                             | bigint  | 主键，数据库生成                     |
| name                           | string  | 必填，菜品名称唯一                   |
| total_time_lower_bound_minutes | integer | 必填，大于等于0                      |
| dish_type                      | string/null | 可选；菜/汤/主食/小菜/甜品，LLM打标；缺失为null |
| atomic_steps                   | JSON    | 必填，原子步骤数组                   |
| labels                         | JSON    | 必填，归一化Label数组；无Label时为[] |
| difficulty                     | string  | 必填；只允许简单、中等、复杂；按本 Spec 的确定性规则派生 |
| is_recommendable               | boolean | 必填；推荐资格，只允许显式布尔值，不得用字符串、null或默认值代替；规则见 Spec_13 |

菜谱难度仅由已有结构化数据派生。食材种类数等于该菜谱去重后的标准食材数：

- 简单：`total_time_lower_bound_minutes <= 20`、原子步骤数 `<= 8`、食材种类数 `<= 9`，三项同时满足。
- 复杂：`total_time_lower_bound_minutes > 60`、原子步骤数 `> 15`、食材种类数 `> 20`，任一满足。
- 中等：其余菜谱。

判定顺序为先简单、再复杂、最后中等；不调用 LLM，不根据食材是否难买、技法名称或主观经验调整结果。

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
| resolved_quantity_g | decimal | 必填；正式营养计算采用的最终克重 |
| is_quantity_estimated | boolean | 必填；是否经过估算或单位换算 |
| is_nutrition_excluded | boolean | 必填；是否从营养汇总中排除 |

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
| is_menstruating     | boolean/null | 女性 50–64 岁必填，其他用户为 null；完整约束见 Spec_05 |
| taste_preference    | string       | 必填，归一化口味值               |
| allergens           | JSON         | 必填，数组；无过敏食材时为[]     |
| health_goals        | JSON         | 必填，数组；无健康需求时为[]     |
| height_cm           | decimal      | 必填，大于0                      |
| weight_kg           | decimal      | 必填，大于0                      |
| bmi                 | decimal      | 必填，大于0                      |
| medical_metrics     | JSON         | 必填，体检指标对象；无指标时为{} |

### recipe_nutrition 与 profile_dri_targets

`recipe_nutrition` 保存每道菜的 9 项整份配方营养，`profile_dri_targets` 保存用户、餐次和营养素维度的预计算目标；字段及计算约束统一由 Spec_05 定义。

## 端点 / 接口

| 动作              | 输入                                                               | 成功返回            | 失败情况（状态码）                                              |
| ----------------- | ------------------------------------------------------------------ | ------------------- | --------------------------------------------------------------- |
| import_basic_data | RecipeComplete.json、Ingredients2Nutrition.csv、归一化健康档案 JSON、DRI CSV | recipes、ingredients、recipe_ingredients、user_profiles、recipe_nutrition、profile_dri_targets 的写入数量；recipes.difficulty 在导入时确定性派生 | 400：格式或字段错误；409：主键、唯一键或外键冲突；500：写入失败 |
| create_database_engine | 非空数据库URL字符串 | SQLAlchemy同步Engine | URL类型、空值或格式错误时抛出DatabaseConfigurationError |
| create_session_factory | SQLAlchemy同步Engine | 与该Engine绑定的Session工厂 | Engine类型错误时抛出TypeError |

## 边界（每条之后会变成一条测试）

- RecipeComplete.json中的每道菜写入一行recipes，多种食材分别写入多行recipe_ingredients。
- RecipeComplete.json中每道菜必须显式提供布尔推荐资格；缺失、非布尔或字符串返回400，整批不写入，不应用默认值。
- recipes.is_recommendable 为非空布尔列，导入值必须与源JSON逐菜一致。
- difficulty 的边界严格按原值比较：20 分钟、8 步、9 种食材仍可为简单；60 分钟、15 步、20 种食材本身不触发复杂，分别增加 1 才触发复杂。
- 当前 1912 道 RecipeComplete 数据按本规则应得到简单 373 道、中等 976 道、复杂 563 道；分布变化表示源数据或派生逻辑发生变化，必须显式确认。
- ingredients必须包含全部菜品使用的归一化食材；没有营养数据的食材仍需写入，营养字段为null。
- quantity_g只换算能够明确确定的质量值；个、勺、片、毫升等不能猜测换算，填写null。
- recipe_ingredients引用不存在的recipe_id或ingredient_id时失败。
- 菜名、归一化食材名或用户ID重复时失败，不静默覆盖。
- labels、atomic_steps、aliases及用户数组字段为空时保存[]，不能保存null。
- medical_metrics为空时保存{}，不能保存null。
- 任一数据写入失败时整批回滚，不留下部分数据。
- 既有数据库升级在同一 PostgreSQL 事务中完成：先增加可空 difficulty 列，按 recipes 的时间、atomic_steps 数组长度和 recipe_ingredients 去重行数回填，确认每行均命中合法枚举后再设置 NOT NULL 与枚举 CHECK；任一步失败时整笔回滚，不删除或重建既有业务数据。
- PostgreSQL 回填完成后，图导入按 recipes.name 将 difficulty 同步为 Neo4j Recipe 节点属性；同步必须幂等且不得重新计算难度。
- 数据库Engine只使用调用方显式传入的URL创建，并启用连接存活检查；不读取环境变量或内置默认地址。
- Session工厂只负责创建相互独立且绑定到指定Engine的Session，不自动提交或回滚事务。
- 调用方负责关闭Session、显式提交或回滚事务，并在不再使用时释放Engine。

## 明确不做（划出范围，防 AI 自由发挥）

- 不建立版本表、历史表或运行记录表。
- 不在入库时重新归一化食材、Label或健康档案。
- 不调用LLM补全或修正数据。
- 不修改 RecipeComplete.json 增加难度标签，不自动执行既有数据库升级或破坏性重建。
- 数据库工厂不自动连接验证，不创建或删除表，不执行数据查询。
- 不建立全局Engine、全局Session或模块级数据库缓存。
