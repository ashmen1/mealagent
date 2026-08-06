# Spec_04_菜品筛选

## 一句话目标

> 使用 Neo4j 图数据库，按整合约束（餐次、口味、菜系、功效、人群、菜品类型、必需食材、过敏原、可用食材）为每组菜品筛选出可选候选集，供后续菜单编排使用。

## 数据模型

### 输入

**IntegratedConstraints**（唯一输入结构）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| profile_id | integer | 必填，正整数 |
| dialogue_id | integer | 必填，正整数 |
| meal_periods | string[] | 必填；只允许下午茶、晚餐、早餐、午餐；无餐次时为 [] |
| diner_count | integer/null | 必填；明确人数时为正整数，否则为 null |
| max_total_time_minutes | integer/null | 必填；明确最长时间时为正整数分钟，否则为 null |
| available_ingredients | string[] | 必填；无限制时为 [] |
| allergens | string[] | 必填；全餐硬排除词；无过敏时为 [] |
| dishes | IntegratedDish[] | 必填，至少一项 |
| has_conflicts | boolean | 必填；true 时本 Spec 直接拒绝，不查询 |
| conflicts | ConstraintConflict[] | 必填；无冲突时为 [] |

其中 `dishes` 的元素为 **IntegratedDish**：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| count | integer/null | 必填；明确数量时为正整数，否则为 null |
| dish_type | string | 必填；菜、汤、主食、小菜、未指定；未指定=不过滤该维度，其余精确匹配菜谱 dish_type |
| taste_preferences | object<string, boolean> | 必填；键只允许 is_sweet、is_light、is_spicy、is_salty、is_sour；无要求时为 {} |
| cuisines | string[] | 必填；只允许西餐风味、东北菜、粤菜、川湘菜、江浙菜；无要求时为 [] |
| effects | string[] | 必填；只允许助眠、减脂、养胃健胃消食、贫血、哺乳；无要求时为 [] |
| special_populations | string[] | 必填；可含档案人群值（孕妇等无标签对应，过滤时忽略）；无要求时为 [] |
| required_ingredients | IngredientRequirement[] | 必填；无要求时为 [] |

其中 `required_ingredients` 的元素为 **IngredientRequirement**：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| kind | string | 必填；只允许 ingredient、category、concept |
| value | string | 必填；分别命中标准食材名、食材类目或已配置概念名 |

`conflicts` 的元素仅用于校验拒绝（has_conflicts=true 时直接拒绝），其字段约束见边界。

### 输出

**DishFilteringResult**

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| dishes | `list[list[RecipeMatch]]` | 外层与输入 dishes（需求组）顺序一致；每组一个候选列表，无候选时为 [] |
| unmatched_allergens | string[] | 无法在 ontology 中展开的过敏词；无则 [] |

**RecipeMatch**（匹配到的菜谱，数据单位）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| recipe_name | string | 唯一键，来自 recipes.name |
| recipe_type | string/null | 菜谱的 dish_type（菜/汤/主食/小菜/甜品）；未打标时为 null |
| matched_tags | string[] | 该菜谱命中的入组标签名 |
| matched_groups | string[] | 该菜谱命中的组名（餐次/口味/菜系/功效/人群） |

### Neo4j 图结构（由导入脚本建立）

**实体（节点）**

| 实体 | 属性 | 类型 | 约束 |
| --- | --- | --- | --- |
| Recipe | name | string | 必填，唯一 |
| Recipe | dish_type | string/null | 必填；菜/汤/主食/小菜/甜品，来自 LLM 打标 |
| Recipe | tags | string[] | 必填；只含入组标签 |
| Recipe | total_time_lower_bound_minutes | integer | 必填；来自 PG recipes |
| Ingredient | name | string | 必填，唯一 |
| Ingredient | category | string/null | 必填；来自 PG 食材类目 |
| Ingredient | is_core_ingredient | boolean | 必填；辅料名单内 false，名单外 true |
| Concept | name | string | 必填，唯一 |
| Concept | kind | string | 必填；枚举 allergen/concept |

**关系（边）**

| 关系 | 方向 | 语义 |
| --- | --- | --- |
| part_of | (Ingredient)→(Recipe) | 该食材是该菜的一部分 |
| is_a | (Ingredient)→(Concept) | 该食材属于该过敏类目/概念 |

```
Ingredient ──part_of──> Recipe
Ingredient ──is_a──> Concept
```

**预置数据（数据文件维护，非自动归纳）**

- 入组标签映射（Python 常量，5 组 23 个）：餐次（下午茶/晚餐/早餐/午餐）、口味（甜/清淡/辣/咸/酸）、菜系（西餐风味/东北菜/粤菜/川湘菜/江浙菜）、功效（助眠/减脂/养胃健胃消食/贫血/哺乳）、人群（上班族/儿童/老人/更年期）。其余标签为噪声，不写入 Recipe.tags。
- 过敏类目 Concept（kind=allergen）：海鲜（66 个真实标准食材名：基围虾/大闸蟹/三文鱼等）、坚果（花生/核桃/杏仁等 7 项）、蛋类（4 项）、奶类（4 项）、豆类（4 项）、麸质（3 项）；成员为 Ingredient 标准名。名字含"海鲜"但不是海鲜食材的（海鲜酱/海鲜菇/海鲜捞汁）不建 is_a 边。
- 概念 Concept（kind=concept）：面（面粉/面条/挂面 3 项成员）。
- 辅料名单（62 项，跨类目）：is_core_ingredient 反向标记——名单内 Ingredient 为 false，名单外为 true。
- Recipe.dish_type 由 LLM 打标脚本生成（tag_dish_types.py），数据落在 RecipeComplete.json 的 dish_type 字段。

## 过滤语义（确定性规则）

| 约束 | 语义 | 说明 |
| --- | --- | --- |
| 餐次 meal_periods | 任一命中 | 空数组不过滤 |
| 口味正向 taste_preferences=true | **全部命中** | 多个口味同时要求；空 dict 不过滤 |
| 口味否定 taste_preferences=false | 硬排除 | 命中任一即排除 |
| 菜系/功效/人群 | 任一命中 | 空数组不过滤；人群只取有标签对应的（上班族/儿童/老人/更年期），档案人群（孕妇等）忽略 |
| dish_type | 精确匹配 | 未指定=不过滤；菜/汤/主食/小菜精确匹配菜谱 dish_type（甜品由数据侧打标） |
| 最长时间 max_total_time_minutes | 上限过滤 | total_time_lower_bound_minutes <= max_total_time_minutes 才通过；null 不过滤 |
| 必需食材 required_ingredients | 全部满足 | ingredient=Ingredient.name 匹配；category=Ingredient.category 匹配；concept=经 is_a 展开的成员任一匹配 |
| 过敏原 allergens | 任一命中即排除 | 概念词经 is_a 路径排除；食材词按 Ingredient.name 匹配；unmatched 词不参与排除，输出到 unmatched_allergens |
| 可用食材 available_ingredients | 核心食材全部 ∈ 可用 | is_core_ingredient=false 的辅料不参与；可用词无法归一到食材标准名时忽略该词 |

候选排序：命中标签数降序；同数时保持 Neo4j 返回顺序（确定性）；候选数量不截断，选择留给菜单编排。matched_groups 按组常量顺序（餐次/口味/菜系/功效/人群）输出。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
| --- | --- | --- | --- |
| DishFilteringService.filter | IntegratedConstraints | DishFilteringResult | 400：输入不符合 Spec_03 契约，或 has_conflicts=true（冲突必须先用户确认再过滤）；500：Neo4j 不可达或查询失败 |

Service 构造时注入 Neo4j Driver（长期复用），方法只传约束。Cypher 全部参数化，禁止字符串拼接。每次调用自动打开和关闭 Session。

## 边界（每条之后会变成一条测试）

- 空约束（无餐次/口味/菜系/功效/人群/必需食材/过敏/可用食材）返回全部候选。
- count 不参与过滤：候选数量不因 count 截断，count 只由菜单编排阶段消费；无候选时返回空列表（不报错）。
- 多餐次任一命中；空 meal_periods 不过滤。
- 口味多值全部命中（如甜+清淡须同时命中）；否定口味（如不辣）硬排除。
- 菜系/功效/人群任一命中；空数组不过滤；档案人群（孕妇等）无标签对应，不参与过滤。
- dish_type 精确匹配：菜组只返回菜、汤组只返回汤；未指定不过滤。
- 最长时间 max_total_time_minutes：仅保留 total_time_lower_bound_minutes <= max 的菜；null 不过滤。
- 三类必需食材各自生效；多项 requirement 全部满足。
- concept 命中"面"（is_a 路径）；海鲜过敏展开后含任一海鲜食材的菜被排除；食材型过敏词按标准名匹配。
- unmatched 过敏词（非 Concept 名、非 Ingredient 名）进报告且不参与排除。
- 可用食材：核心食材全部 ∈ 可用、辅料不限制；可用词无法归一时忽略。
- 无候选返回空列表（不报错）。
- has_conflicts=true 返回 400，不查询 Neo4j。
- 噪声标签（节日、LLM 残留等）不参与过滤。
- 候选排序确定（命中标签数降序，同数保持图返回顺序）；结果顺序与输入 dishes 一致。
- Neo4j 不可达或查询失败返回 500。

## 明确不做

- 特殊人群（高血脂等）营养约束过滤——留待 cp-sat 营养线。
- LLM ontology 展开、unmatched 过敏词处理——后续 spec。
- 菜单编排、份量换算、候选选择与数量截断、推荐排序偏好、分页。
- 多轮对话、约束更新、历史约束合并。
- 不引入新依赖（Neo4j 已确认；Ontology 数据文件与导入脚本在实现阶段落地）。

---
**三条自检**：①每条规则能否写成测试？能——边界已逐条对应。②人能在 2 分钟内审完吗？能——约 50 行。③范围划死了吗？划死——明确不做清单完整。
