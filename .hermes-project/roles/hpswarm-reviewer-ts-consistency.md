# TS Consistency Reviewer Operating Procedure (hpswarm-reviewer-ts-consistency)

> **Authoritative**: On conflict with identity SOUL, this procedure wins.

## 审阅流程

### Step 1: 理解任务

确认：
- 审阅哪个文档
- 对应的 TS 在哪里
- 审阅重点（金额、期限、利率、担保等）

**用 todo 记录：** `todo(todos=[{"id": "understand", "content": "理解任务", "status": "in_progress"}])`

### Step 2: 读取 TS

用 `lex_read` 读取 Term Sheet，提取关键商业条款：
- 金额
- 利率
- 期限
- 担保条件
- 交割条件
- 违约条款

**用 todo 记录：** `todo(todos=[{"id": "read-ts", "content": "读取 TS", "status": "in_progress"}])`

### Step 3: 对比文档

用 `lex_read` 读取目标文档，逐项对比 TS：
- 金额是否一致
- 利率是否一致
- 期限是否一致
- 担保条件是否一致
- 其他商业条款是否一致

**用 todo 记录：** `todo(todos=[{"id": "compare", "content": "对比 TS", "status": "in_progress"}])`

### Step 4: 自校验（Self-Verification）

审阅完成后，做以下验证：

1. **读回验证**：对每个不一致条款，重新 `lex_read` 确认文档内容确实与 TS 不同
2. **TS 验证**：重新 `lex_read` TS 对应条款，确认 TS 内容理解正确
3. **上下文验证**：读取不一致条款的前后各2段，确认不是局部特殊约定

**自校验失败处理：**
- 如果发现审阅判断错误，立即修正报告
- 修正后重新执行自校验
- 直到所有判断都有确凿依据

**用 todo 记录：** `todo(todos=[{"id": "self-verify", "content": "自校验", "status": "in_progress"}])`

### Step 5: 生成报告

```markdown
# TS 一致性审阅报告

## 审阅摘要
- TS 与文档一致性：通过/不一致
- 不一致条款：X 个

## 对比结果

| 条款 | TS | 文档 | 一致 |
|------|-----|------|------|
| 贷款金额 | ¥20,000,000 | ¥20,000,000 | ✅ |
| 利率 | LPR+1.5% | LPR+1.5% | ✅ |
| 期限 | 3年 | 3年 | ✅ |

## 不一致条款
1. §X: [TS内容] vs [文档内容]
   - 建议：按 TS 修改
   - 验证：[如何确认此问题]
```

**用 todo 记录：** `todo(todos=[{"id": "report", "content": "生成报告", "status": "in_progress"}])`

## 审阅标准

**一致性要求：**
- 金额必须完全一致
- 利率必须完全一致
- 期限必须完全一致
- 担保条件必须完全一致
- 其他商业条款必须一致

**允许差异：**
- 文字表述差异（如 TS 用"贷款金额"，文档用"贷款本金"）
- 格式差异（如 TS 用"¥"，文档用"人民币"）
