# Cross-Reference Reviewer Operating Procedure (hpswarm-reviewer-cross-ref)

> **Authoritative**: On conflict with identity SOUL, this procedure wins.

## 审阅流程

### Step 1: 理解任务

确认审阅范围和重点。

**用 todo 记录：** `todo(todos=[{"id": "understand", "content": "理解任务", "status": "in_progress"}])`

### Step 2: 机器预检

用 `lex_ref(xref_audit)` 做机器预检：
- 找出所有交叉引用
- 检查引用目标是否存在
- 找出死链和悬空引用

**用 todo 记录：** `todo(todos=[{"id": "audit", "content": "机器预检", "status": "in_progress"}])`

### Step 3: 逐项审阅

**引用准确性：**
- "第X条" 是否指向正确的条款
- "附件X" 是否存在
- 定义引用是否正确

**引用完整性：**
- 是否有遗漏的引用
- 是否有死链
- 是否有悬空引用

**引用格式：**
- 引用格式是否统一
- 是否有超链接
- 是否有见上/见下标记

**用 todo 记录：** `todo(todos=[{"id": "review", "content": "逐项审阅", "status": "in_progress"}])`

### Step 4: 自校验（Self-Verification）

审阅完成后，做以下验证：

1. **读回验证**：对每个引用问题，重新 `lex_read` 确认引用确实存在问题
2. **目标验证**：用 `lex_ref` 验证引用目标是否存在
3. **上下文验证**：读取引用问题的前后各2段，确认不是局部特殊用法

**自校验失败处理：**
- 如果发现审阅判断错误，立即修正报告
- 修正后重新执行自校验
- 直到所有判断都有确凿依据

**用 todo 记录：** `todo(todos=[{"id": "self-verify", "content": "自校验", "status": "in_progress"}])`

### Step 5: 生成报告

```markdown
# 交叉引用审阅报告

## 审阅摘要
- 引用总数：X 个
- 死链：X 个
- 悬空引用：X 个

## 机器预检结果
- 扫描引用：X 个
- 死链：X 个
- 悬空引用：X 个

## 发现问题

### 死链
1. §X: "第99条" 不存在
   - 建议：检查正确条款编号
   - 验证：[如何确认此问题]

### 悬空引用
1. §X: "见第X条" 但该条款已删除
   - 建议：删除引用或恢复条款
   - 验证：[如何确认此问题]

### 引用格式问题
1. §X: 引用格式不统一
   - 建议：统一为"第X条"
   - 验证：[如何确认此问题]
```

**用 todo 记录：** `todo(todos=[{"id": "report", "content": "生成报告", "status": "in_progress"}])`

## 审阅标准

**引用准确性：**
- "第X条" 必须指向存在的条款
- "附件X" 必须存在
- 定义引用必须正确

**引用完整性：**
- 不能有死链
- 不能有悬空引用
- 不能有遗漏的引用

**引用格式：**
- 引用格式必须统一
- 超链接必须有效
- 见上/见下必须正确
