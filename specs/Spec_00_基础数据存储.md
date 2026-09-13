# Spec_00_基础数据存储

## 一句话目标

> 将处理完成的菜品、食材营养、用户健康档案和 DRI 数据存入 6 张基础及派生表，并保存菜谱食材是否承担主食构成，为后续功能提供统一数据源。

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

- 简单：`total_time_lower_bound_minutes <= 20`、原子步骤数 `<= 8`、食材种类数 `<= 8`，三项同时满足。
- 复杂：`total_time_lower_bound_minutes > 60`、原子步骤数 `> 15`、食材种类数 `> 18`，任一满足。
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
| is_staple_component | boolean | 必填；该食材是否承担本菜谱的主食主体或共同主食主体；不得为null或依赖数据库默认值 |

联合主键为 `recipe_id + ingredient_id`。

### RecipeComplete.json 主食构成字段

每道菜必须包含 `staple_ingredients: string[]`。数组元素必须非空、不重复，并且逐项存在于同一道菜的 `ingredients` 键集合中。导入时仅按该数组确定 `recipe_ingredients.is_staple_component`：数组内为 true，数组外为 false，不按食材类别、克重或菜名再次推导。

主食构成表示食材承担成品中的主食主体或共同主食主体，允许一道菜包含多个主食构成；少量配料、馅料、点缀和调味料不属于主食构成。具体食材是否属于主食构成是人工审核数据，不是按克重、食材类别或菜名执行的运行时推导规则；自动校验只认审核通过的 `reviewed_staple_ingredients`。当前必须通过的审核锚点为：

- 培根披萨：高筋面粉是主食构成，玉米不是。
- 蜜汁烤玉米：玉米是主食构成。
- 红薯米饭：大米和红薯均是主食构成。

正式数据采用候选加人工审核流程。审核文件固定为 JSON 数组，每项结构如下：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| recipe_name | string | 非空且在当前 `dish_type=主食` 菜谱中唯一存在 |
| candidates | StapleCandidate[] | ingredient 不重复；允许为空，空数组表示模型未识别到真实主食构成，必须人工复核 |
| reviewed_staple_ingredients | string[]/null | 生成时必须为 null；人工审核后为不重复且均属于该菜谱 ingredients 的数组；审核确认菜谱没有真实主食构成时允许为空 |
| review_status | string | 生成时固定 pending；人工只能改为 approved 或 rejected |

`StapleCandidate` 固定包含 `ingredient: string` 和 `evidence: string`；ingredient 必须属于该菜谱 ingredients，evidence 必须为非空审核依据。候选服务只为当前 310 道主食菜谱生成记录，输出必须按源菜谱顺序完整覆盖、无重复，且所有记录初始均为 `pending + null`。候选允许为空，用于暴露“主食”历史分类下没有真实主食构成的数据并交由人工复核，不得自动补值、改变菜品类型或推荐资格。模型调用失败、记录遗漏、未知食材、空证据或结构非法时整批失败，不创建或覆盖审核文件。

人工审核发现候选语义错误或遗漏时必须将记录标为 rejected；写回服务遇到 pending 或 rejected 均整批失败。只有审核文件完整覆盖 310 道主食菜谱、每项为 approved，且 reviewed_staple_ingredients 合法时才可写回：主食菜谱使用人工审核数组；人工确认菜谱没有真实主食构成时允许使用空数组，不得因此改变菜品类型或推荐资格；非主食菜谱写入显式空数组。人工审核数组允许纠正候选结果，但仍只能选择该菜谱已有食材。

写回前必须在内存中完成全部校验；成功时只改变每道菜的 staple_ingredients，其他字段和值保持不变，并一次替换正式 RecipeComplete.json；任一步失败时正式文件内容保持不变。候选生成、人工编辑和正式写回是三个独立步骤，候选生成不得自动批准或触发写回。

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
| import_basic_data | 含staple_ingredients的RecipeComplete.json、Ingredients2Nutrition.csv、归一化健康档案 JSON、DRI CSV | recipes、ingredients、recipe_ingredients、user_profiles、recipe_nutrition、profile_dri_targets 的写入数量；recipes.difficulty 在导入时确定性派生 | 400：格式或字段错误；409：主键、唯一键或外键冲突；500：写入失败 |
| StapleReviewCandidateService.generate | source_path、review_path；结构化LLM在Service创建时注入 | 主食菜谱记录数与审核文件路径 | 400：源数据非法；502：模型调用或结构化结果非法；500：审核文件写入失败 |
| apply_staple_review | source_path、review_path | 写回后的菜谱数 | 400：源数据或审核文件不符合上述契约；500：正式文件替换失败 |
| create_database_engine | 非空数据库URL字符串 | SQLAlchemy同步Engine | URL类型、空值或格式错误时抛出DatabaseConfigurationError |
| create_session_factory | SQLAlchemy同步Engine | 与该Engine绑定的Session工厂 | Engine类型错误时抛出TypeError |

## 边界（每条之后会变成一条测试）

- RecipeComplete.json中的每道菜写入一行recipes，多种食材分别写入多行recipe_ingredients。
- RecipeComplete.json中的每道菜必须显式提供staple_ingredients；缺失、非数组、重复或含未知食材时返回400，整批不写入；经过完整审核的主食菜谱允许为空。
- 候选文件已存在时，生成失败不得覆盖原文件；生成成功才一次替换 review_path。
- 写回审核文件含 pending、rejected、重复或缺失菜谱、非法审核食材时返回400，正式 RecipeComplete.json 内容不变。
- 三个审核锚点必须得到：培根披萨=[高筋面粉]且不含玉米、蜜汁烤玉米包含玉米、红薯米饭同时包含大米和红薯；任一不符时写回返回400。
- recipe_ingredients.is_staple_component为非空布尔列，导入值必须与源JSON逐菜逐食材一致；不得按克重、类目或核心食材标记推导。
- RecipeComplete.json中每道菜必须显式提供布尔推荐资格；缺失、非布尔或字符串返回400，整批不写入，不应用默认值。
- recipes.is_recommendable 为非空布尔列，导入值必须与源JSON逐菜一致。
- difficulty 的边界严格按原值比较：20 分钟、8 步、8 种食材仍可为简单；60 分钟、15 步、18 种食材本身不触发复杂，分别增加 1 才触发复杂。
- 当前 1912 道 RecipeComplete 数据按本规则应得到简单 323 道、中等 1019 道、复杂 570 道；分布变化表示源数据或派生逻辑发生变化，必须显式确认。
- ingredients必须包含全部菜品使用的归一化食材；没有营养数据的食材仍需写入，营养字段为null。
- quantity_g只换算能够明确确定的质量值；个、勺、片、毫升等不能猜测换算，填写null。
- recipe_ingredients引用不存在的recipe_id或ingredient_id时失败。
- 菜名、归一化食材名或用户ID重复时失败，不静默覆盖。
- labels、atomic_steps、aliases及用户数组字段为空时保存[]，不能保存null。
- medical_metrics为空时保存{}，不能保存null。
- 任一数据写入失败时整批回滚，不留下部分数据。
- 既有数据库升级在同一 PostgreSQL 事务中完成：先增加可空 difficulty 列，按 recipes 的时间、atomic_steps 数组长度和 recipe_ingredients 去重行数回填，确认每行均命中合法枚举后再设置 NOT NULL 与枚举 CHECK；任一步失败时整笔回滚，不删除或重建既有业务数据。
- 推荐资格迁移（migrate_recommendability）在同一 PostgreSQL 事务中完成：先增加可空 is_recommendable 列（列已存在则跳过建列），按正式 RecipeComplete.json 的菜名逐菜回填布尔资格（资格为审计结果、不推导），校验表行数与 JSON 行数一致且每行均已回填后再设置 NOT NULL；数量不一致、回填不完整或任一步失败时整笔回滚，可重复执行（幂等）。
- 主食构成迁移在同一 PostgreSQL 事务中完成：列不存在时先增加可空 is_staple_component 列，再按正式 RecipeComplete.json 的菜名与食材名逐行回填；列已存在时仍重跑回填。迁移必须核对菜谱数、关联行数、源食材子集和全部非空布尔结果，全部通过后设置 NOT NULL；任一步失败整笔回滚且可重复执行。
- PostgreSQL 回填完成后，图导入按 recipes.name 将 difficulty 同步为 Neo4j Recipe 节点属性；同步必须幂等且不得重新计算难度。
- 图导入按菜名和食材名将is_staple_component同步到既有Neo4j part_of关系；每次同步均覆盖关系属性，不能保留缺失值或旧值。
- 数据库Engine只使用调用方显式传入的URL创建，并启用连接存活检查；不读取环境变量或内置默认地址。
- Session工厂只负责创建相互独立且绑定到指定Engine的Session，不自动提交或回滚事务。
- 调用方负责关闭Session、显式提交或回滚事务，并在不再使用时释放Engine。

## 明确不做（划出范围，防 AI 自由发挥）

- 不建立版本表、历史表或运行记录表。
- 不在入库时重新归一化食材、Label或健康档案。
- 基础导入和迁移不调用LLM补全或修正数据；LLM只允许在独立的主食构成审核候选生成阶段使用。
- 不修改 RecipeComplete.json 增加难度标签，不自动执行既有数据库升级或破坏性重建。
- 本次红绿实现只生成待审核主食候选，不自动调用apply_staple_review，不写回正式RecipeComplete.json，不执行正式PostgreSQL迁移、Neo4j同步或图重建；以上操作必须等待人工审核数据并再次明确确认。
- 不扩展primary、secondary或seasoning等完整食材角色枚举。
- 数据库工厂不自动连接验证，不创建或删除表，不执行数据查询。
- 不建立全局Engine、全局Session或模块级数据库缓存。
