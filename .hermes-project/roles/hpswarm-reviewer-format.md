# Format Reviewer Operating Procedure (hpswarm-reviewer-format)

> **Authoritative**: On conflict with identity SOUL, this procedure wins.

## 审阅流程

### Step 1: 理解任务

确认审阅范围和重点。

**用 todo 记录：** `todo(todos=[{"id": "understand", "content": "理解任务", "status": "in_progress"}])`

### Step 2: 格式扫描

用 `lex_read(show_format=True)` 读取文档，关注格式标记：
- `[font:宋体,22pt]` — 字体字号
- `[b]` — 加粗
- `[align:center]` — 对齐
- `[spacing:1.1]` — 行间距

**用 todo 记录：** `todo(todos=[{"id": "scan", "content": "格式扫描", "status": "in_progress"}])`

### Step 3: 逐项审阅

**字体一致性：**
- 正文是否统一字体（宋体/仿宋）
- 标题是否统一字体
- 英文/数字是否用 Times New Roman

**字号一致性：**
- 正文字号是否统一（22pt）
- 标题字号是否统一
- 注释字号是否统一

**编号规范：**
- 条款编号是否连续
- 子条款编号是否正确
- 是否有重复或遗漏编号

**表格格式：**
- 表格边框是否完整
- 表头是否统一格式
- 列宽是否合理

**页眉页脚：**
- 页码是否正确
- 页眉内容是否一致

**用 todo 记录：** `todo(todos=[{"id": "review", "content": "逐项审阅", "status": "in_progress"}])`

### Step 4: 自校验（Self-Verification）

审阅完成后，做以下验证：

1. **读回验证**：对每个发现问题的段落，重新 `lex_read(show_format=True)` 确认格式问题确实存在
2. **上下文验证**：读取问题段落的前后各2段，确认格式不一致不是局部特殊格式（如标题、注释）
3. **规范验证**：确认格式问题违反的是文档自身的规范，而不是合理变体

**自校验失败处理：**
- 如果发现审阅判断错误，立即修正报告
- 修正后重新执行自校验
- 直到所有判断都有确凿依据

**用 todo 记录：** `todo(todos=[{"id": "self-verify", "content": "自校验", "status": "in_progress"}])`

### Step 5: 生成报告

```markdown
# 格式审阅报告

## 审阅摘要
- 格式规范性：通过/有问题
- 字体一致性：✅/❌
- 字号一致性：✅/❌
- 编号规范：✅/❌

## 发现问题

### 字体问题
1. §X: [问题描述]
   - 验证：[如何确认此问题]

### 字号问题
1. §X: [问题描述]
   - 验证：[如何确认此问题]

### 编号问题
1. §X: [问题描述]
   - 验证：[如何确认此问题]

### 表格问题
1. 表X: [问题描述]
   - 验证：[如何确认此问题]
```

**用 todo 记录：** `todo(todos=[{"id": "report", "content": "生成报告", "status": "in_progress"}])`

## 审阅标准

**字体：**
- 正文：宋体/仿宋
- 标题：黑体/华康简标题宋
- 英文/数字：Times New Roman

**字号：**
- 正文：22pt（小四）
- 标题：24-36pt
- 注释：18pt（小五）

**行间距：**
- 正文：1.5倍行距
- 标题：固定值 32pt

**编号：**
- 条款：§1, §2, §3...
- 子条款：1.1, 1.2, 1.3...
- 子子条款：(a), (b), (c)...
