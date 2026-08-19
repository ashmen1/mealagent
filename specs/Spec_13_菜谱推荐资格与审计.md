# Spec_13 菜谱推荐资格与审计

## 一句话目标

> 为全部正式菜谱建立经过双提示审计的显式推荐资格，只允许 `is_recommendable=true` 的完整成品进入菜单候选，并从源JSON空库重建后验证 PostgreSQL 与 Neo4j 一致。

## 数据模型

### 正式菜谱

`RecipeComplete.json` 中每道菜新增必填字段：

| 字段 | 类型 | 约束 |
|---|---|---|
| is_recommendable | boolean | 必填且不得用字符串、null或默认值代替 |

- `true`：名称、食材和步骤共同描述可作为菜单条目的完整成品，包括菜、汤、主食、小菜或甜品。
- `false`：仅描述清洗、切配、浸泡、焯水、解冻等准备操作，或只是食材/步骤片段、非成品流程、非食物内容。
- 资格只控制菜单候选；`false` 菜谱仍完整导入 PostgreSQL、营养数据和 Neo4j，不删除原始记录。

### AuditDecision

每个提示版本对每道菜返回一项：

| 字段 | 类型 | 约束 |
|---|---|---|
| recipe_name | string | 必须等于输入菜名 |
| is_recommendable | boolean | 推荐资格判定 |
| reason_code | string | finished_item、preparation_only、ingredient_only、fragment或non_food |
| confidence | string | high、medium或low |
| reason | string | 非空简短说明 |

`finished_item`必须对应`true`，其余reason_code必须对应`false`。

每个模型批次只接收20道菜的`name、ingredients、atomic_steps、dish_type`；不接收现有标签或既有资格，避免用标签替代成品判断。提示A从“是否构成完整成品”判断，提示B从“是否命中准备操作或片段排除项”反向判断。

### AuditResolution

| 字段 | 类型 | 约束 |
|---|---|---|
| recipe_name | string | 全部1912道正式菜谱各一项，顺序与源JSON一致 |
| status | string | auto_approved或manual_review |
| is_recommendable | boolean/null | auto_approved时为最终值；manual_review时为null |
| prompt_a | AuditDecision | 提示版本A结果 |
| prompt_b | AuditDecision | 提示版本B结果 |

仅当两份结果均为high，且`is_recommendable`与`reason_code`完全一致时自动通过；其他情况全部进入人工审核。

### 人工审核CSV

仅包含`manual_review`项，固定列为：

`recipe_name,prompt_a_value,prompt_a_code,prompt_a_reason,prompt_b_value,prompt_b_code,prompt_b_reason,reviewer_value,reviewer_note`

`reviewer_value`只允许`true`或`false`；应用前每行必须填写，菜名必须与待审项精确一一对应。`reviewer_note`允许为空。

## 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| `generate` | 正式菜谱路径、审计目录、batch_size=20、两个LLM提示版本 | 完整检查点、AuditResolution、人工审核CSV及汇总 | 400输入非法；409输出目录与源基线冲突；502模型结构/覆盖非法；503模型不可用；500文件失败 |
| `validate` | 正式菜谱、审计目录、人工审核CSV | 1912项最终资格映射及计数 | 400结构非法；409缺项、重复、额外项、基线变化或人工项未完成；500文件失败 |
| `apply` | 与validate相同 | 原子写回全部1912项`is_recommendable` | validate全部错误；写入失败返回500且正式文件不变 |

`generate`固定以源顺序每20道一批，最后一批12道；提示A、B独立覆盖全部菜谱，共96×2=192次模型调用。中断时保留已完成批次，`--resume`只继续缺失批次；不重跑或覆盖已完成结果。

审计允许输入尚无`is_recommendable`的初始源文件；源基线哈希覆盖除该字段外的全部内容。基础数据导入只接受`apply`后的正式文件。

## 数据流与筛选

1. 基础数据导入要求每道菜显式提供布尔资格；缺失或非法返回400，整批不写入。
2. PostgreSQL `recipes.is_recommendable` 为非空布尔列；导入值与源JSON一致。
3. Neo4j `Recipe.is_recommendable` 为布尔属性；图导入值与PostgreSQL一致。
4. 菜品筛选的每个查询都必须包含 `r.is_recommendable = true`；`false` 菜谱即使满足全部标签、食材、时间、难度和过敏条件也不得返回。
5. 营养查询和基础数据计数仍覆盖全部菜谱，推荐资格不改变营养计算结果。

## 空库重建验收

- 只允许在新建的隔离 Docker PostgreSQL 数据库和隔离 Neo4j 实例执行，不清空或修改现有业务容器。
- 从应用后的正式JSON及既有食材、档案、DRI源文件导入，不复制现有数据库数据。
- PostgreSQL与Neo4j菜谱总数均为1912；两端按菜名一一对应，`is_recommendable`值完全一致。
- 两端`true/false`计数分别一致；所有`false`菜谱通过真实筛选服务均不可见，至少一条`true`菜谱仍可命中正常查询。
- 重建完成后运行基础导入、图导入、筛选及统一推荐链路回归；验收失败不得替换现有业务数据。

## 边界

- 空菜谱数组、数量不是1912、重复菜名、缺少资格或资格非布尔均拒绝。
- 任一提示批次缺项、重复项、额外菜名、顺序错位、未知枚举或资格与reason_code冲突均返回502。
- 两提示结论相同但任一置信度不是high时仍进入人工审核。
- 两提示资格相同但reason_code不同仍进入人工审核。
- 人工CSV缺项、重复、额外菜名、未知值或源菜谱基线变化时不得应用。
- 应用只新增或更新`is_recommendable`，移除该字段后每道菜必须与应用前逐字段深度相等；相同审核结果重复应用得到相同文件内容。
- `false`菜谱不进入候选，但不得从数据库、图谱或营养表删除。
- 图中缺失或非布尔资格属于数据错误，不按`true`处理。

## 明确不做

- 不修改菜谱名称、食材、用量、步骤、标签、类型、难度或营养。
- 不让LLM直接修改正式JSON，不自动通过分歧或中低置信度结果。
- 不把推荐资格用于放宽过敏、健康、数量、时间或其他硬约束。
- 不在本阶段解决空候选、低营养分或标签覆盖不足；只先排除非成品菜谱。
- 不清空、覆盖或切换现有业务 PostgreSQL/Neo4j 数据。

---

**三条自检**

1. 通过：字段、双提示合并、人工审核、导入、筛选和空库验收规则都有唯一可断言结果。
2. 通过：两分钟内只需审核资格定义、自动通过门槛、筛选门禁和隔离重建四部分。
3. 通过：范围只新增推荐资格并贯通存储与筛选；标签、营养、约束和现有业务数据均明确不改。
