# Spec_15 局部换菜与方案否定

## 一句话目标

用户否定当前菜单或要求换菜时，只替换指定范围；换出的菜在同一会话中不再推荐，除非用户明确要求换回。

## 数据模型

| 字段 | 类型 | 约束 |
|---|---|---|
| last_menu | 菜单列表 | 仅保存最近一次成功推荐，包含顺序和菜名 |
| excluded_recipe_names | 菜名集合 | 成功换出的菜加入；同一会话内不得再次推荐 |
| pending_menu_change | 换菜请求或空 | 目标不明确时保存，确认或成功后清空 |

| 对象 | 必需字段 |
|---|---|
| menu_change | `mode`、`replace_count`、`target_positions`、`target_recipe_names`、`replacement_recipe_name`、`unresolved_target`、`evidence` |
| menu_change_policy | `previous_recipe_names`、`required_previous_count`、`required_recipe_names`、`forbidden_recipe_names` |
| menu_change_result | `mode`、`retained_recipe_names`、`removed_recipe_names`、`added_recipe_names` |

`mode` 只允许 `none、replace_all、replace_partial、replace_specific、restore_specific`；菜名和 1-based 序号统一解析为历史菜单中的菜名。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 继续使用 `/v1/chat/completions` | 原请求字段及换菜文本 | HTTP 200，`recommended`，回答包含新菜单及换菜说明 | 目标不清为 `needs_confirmation`；无可行菜单为 `planning_infeasible` |

不新增请求字段、HTTP响应字段或端点，流式与非流式格式保持不变。成功回答继续四段输出，并在“规划依据”说明保留、换出和换入。

## 边界（每条对应一条测试）

- 没有历史菜单却要求换菜：返回 `needs_confirmation`，不生成菜单。
- “全部换掉／整套重做”：新旧菜单不得有重复菜品。
- “换两道”：必须保留原菜单中的 `N-2` 道。
- “换一部分／这个方案不行，换个方案”：默认替换 `ceil(N/2)` 道。
- “换第二道／换掉菜名A”：只替换目标菜，其余菜保留；序号从 1 开始。
- “把A换成B”：B必须在 1912 道正式菜谱中且 `is_recommendable=true`；不在可推荐集时返回 `needs_confirmation`，存在但违反硬约束时返回 `planning_infeasible`，两者都不修改菜单。
- 多个目标只能共用一个换菜要求；不同目标提出不同要求时返回 `needs_confirmation`。
- 菜名不存在、序号越界或目标有歧义：返回 `needs_confirmation`，下一轮可补充目标。
- 换菜无可行解：只返回 `planning_infeasible`，不展示旧菜单，不修改会话状态。
- 换菜成功：被换出的菜加入排除集合，后续普通推荐和继续换菜均不得选中。
- 用户明确说“换回来A／重新考虑A”：成功后A移出排除集合；失败时排除集合不变。
- 普通约束修改不触发局部保留，仍按现有逻辑重新规划，但必须遵守排除集合。
- 不同会话的历史菜单和排除集合互不影响。
- 只有 `recommended` 才原子更新 `last_menu`、`excluded_recipe_names`和`pending_menu_change`；`needs_confirmation`、`planning_infeasible`和其他失败不改动原状态。
- 同一 session 内的提交、规划、状态更新和回答组装按请求到达顺序串行；不同 session 保持并发。
- 新数据库直接建立会话的 `last_menu、excluded_recipe_names、pending_menu_change` 和轮次的 `menu_change` 列；已有数据库启动时幂等补列，重复执行不丢失已有数据。

## 明确不做

- 不扩展健康档案或体检指标；搭配软目标不改变本规格的换菜硬约束。
- 不支持为多个目标分别指定不同换菜条件。
- 不生成菜谱库外的新菜。
- 不新增接口或改变现有 SSE 格式。
