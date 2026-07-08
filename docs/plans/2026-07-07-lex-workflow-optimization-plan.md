# Lex Workflow 优化计划

Date: 2026-07-07

## 核心问题

当前 document_drafting workflow 的问题：
1. **Coordinator 独自读完整份文档** — context 被塞满，推理能力下降
2. **文本润色在注入之后** — 应该先润色模板再注入内容
3. **没有 split-read** — 大文档（100+ 段）单 agent 读不完

## 优化后的 workflow

**两条路径，根据文档复杂度选择：**

| 文档类型 | 流程 | Token 效率 |
|---------|------|-----------|
| 复杂文档（100+ 段） | 结构扫描 → split-read → grill-me → clean inject → quicompare | ~4500 tokens/轮 |
| 简单文档（<50 段） | 直接 TC 注入 | ~2000 tokens/轮 |

### 复杂文档路径
```
1. 结构扫描（轻量）
2. Split-read（并行 kanban）
3. 文本润色确认（grill-me）
4. 文本润色执行（精确修改）
5. 内容注入（clean inject）
6. 对比验证（quicompare）
7. 校对（lex_preview）
8. 交付
```

### 简单文档路径
```
1. 直接 TC 注入（insert_text/replace_text/delete_text）
2. 校对（lex_preview）
3. 交付
```

### Phase 1: 结构扫描（轻量）

- 用 `lex_read(mode="structure")` 只读标题
- 用 `lex_stats` 获取段落/表格/TC 统计
- Coordinator 拿到大纲，确定分块策略

**工具：** `lex_read(mode="structure")`, `lex_stats`

### Phase 2: Split-read（并行）

把文档按逻辑分块，发布 kanban 任务并行读：

```
Coordinator 读到大纲后，拆成 3-5 块：
  - 定义 + 鉴于（通常 §1-30）
  - 正文条款（§31-100）
  - 费用 + 期限 + 解除（§101-150）
  - 附件 + 签署页（§151+）

每块发布一个 kanban task：
  swarm_task_create(
    title="读取 §1-30 定义和鉴于",
    assignee="lex-reader",  # 或 coordinator 自己读
    body="用 lex_read 读取指定段落，输出结构化摘要"
  )
```

**Reader 输出格式：**
```markdown
## 段落摘要 §1-30

### 关键条款
- §5: 甲方信息（交通银行上海闵行支行）
- §6: 乙方信息（中国银行上海黄浦支行）
- §15: 服务范围（合同审查、法律意见书、其他法律咨询）

### 格式问题
- §3: 空段落，可删除
- §8: 缺少编号

### 需要关注的术语
- "被担保债务" — 定义在 §25
- "法律服务框架协议" — 定义在 §26
```

**Coordinator 收到所有 reader 的摘要后：**
- 建立整体理解（不需要读原文）
- 识别需要修改的段落
- 生成修订计划

### Phase 3: 文本润色确认（grill-me）

**不自动润色 — 先跟你确认。**

Coordinator 读完 reader 摘要后，生成结构化问题清单，每个问题带建议答案：

```
## 润色确认清单

### 术语选择
1. §6 "丙方" → 用简称还是全称？
   建议：正文用"丙方"，附件用全称。
   你的选择？

### 格式偏好
2. 正文字体：宋体还是仿宋？
   建议：宋体 22pt（合同常用）。
   你的选择？

### 风险提示
3. §15 "其他法律咨询服务" 为开放式兜底
   建议：限定为"就本次银团贷款的相关事项"。
   你的选择？

### 金额校验
4. 费用 70,000 元，是否正确？
```

**设计原则：**
- 一次一个问题，不一次抛多个
- Agent 先猜再问 — 能推断的不问，只问真正需要确认的
- 给建议选项，不是开放问题
- 你随意回答，Agent 负责精确化：
  - "用简称" → "丙方"
  - "太宽了" → "限定服务范围为本次银团贷款相关事项"
  - "默认" → "宋体 22pt，保持原文格式"
  - "金额对的" → 确认 70,000 元
  - "删掉" → Agent 判断是删除空段落还是多余条款
- 回答后 Agent 确认："好的，按你的意思：丙方用简称，已确认。"

**输出：** `confirmed_revision_plan` — 结构化的修订计划

**工具：** `clarify`（hermes 的问答工具）

### Phase 4: 文本润色执行

根据确认后的修订计划，执行润色：

- 统一术语（"丙方" → 统一简称）
- 修正错别字
- 清理空段落
- 统一格式（字体、字号、行间距）
- 删除无用的占位符（`【/】`、`【 】`）

**工具：** `lex_edit(tc=True, author="JT")`

### Phase 4: 内容注入

根据修订计划，逐段注入内容：

- 替换占位符
- 插入新条款
- 修改金额/日期/名称
- 添加签名页

**工具：** `lex_edit`, `lex_comment`, `lex_tc`

### Phase 5: 校对

- 逐段校对（用 lex_preview 看 HTML 渲染）
- 交叉引用检查（lex_ref）
- TS 一致性检查
- 格式检查

**工具：** `lex_preview`, `lex_ref`, `lex_proofread`

### Phase 6: 交付

- 生成 HTML 报告
- 生成 DOCX
- 更新项目状态

**工具：** `lex_deliver`, `project_status`

## 实现步骤

### Step 1: 修改 workflow tool

在 `legal_workflow_tool.py` 的 `_default_document_drafting_workflow_steps()` 中：

1. 将 `read-template-structure` 改为 `split-read-phase`
2. 新增 `text-polish-confirm` 步骤（grill-me 确认）
3. 新增 `text-polish-execute` 步骤（执行润色）
4. 重新排序依赖关系

### Step 2: 创建 coordinator 交接 skill

在 `skills/lex-workflow-coordinator/SKILL.md` 中定义 coordinator 的交接协议：

- split-read 时，按标题分块发布 kanban 任务
- 收到 reader 摘要后，生成 grill-me 问题清单
- 你回答后，把回答精确化为修订计划
- 修订计划确认后，执行润色 + 注入

### Step 3: 添加 lex-reader profile

在 `profiles/lex-reader.soul.md` 中定义 reader 角色：
- 只负责读取和输出结构化摘要
- 不做任何修改
- 输出格式固定

### Step 2: 添加 lex-reader profile

在 `profiles/lex-reader.soul.md` 中定义 reader 角色：
- 只负责读取和输出结构化摘要
- 不做任何修改
- 输出格式固定

### Step 3: 测试

用颐保银团的法律服务协议测试：
- split-read 是否正确分块
- reader 输出是否结构化
- coordinator 是否能正确聚合
- 文本润色是否有效

## 风险

- **分块逻辑**：不同文档结构不同，需要智能分块
- **Reader 摘要质量**：需要明确的输出格式约束
- **Kanban 开销**：每块一个 task，小文档可能不值得
- **Coordinator 聚合**：需要在 context 里放多个摘要，仍有开销

## TC 注入可靠性保证

TC 注入必须满足以下标准：

| 操作 | 要求 |
|------|------|
| `insert_text(tc=True)` | 插入标记有 `author` + `date`，Word 正常显示 |
| `replace_text(tc=True)` | 原文标记为删除，新文标记为插入，Word 正常显示 |
| `delete_text(tc=True)` | 删除标记完整，Word 正常显示 |
| `insert_paragraph_block(tc=True)` | 批量插入完整，段落顺序正确 |
| 不产生空标签 | `_clean_empty_tc_markers` 自动清理 |

**测试用例：**
- [ ] 插入 10 个字符，验证 TC 标记
- [ ] 替换一段话，验证删除+插入标记
- [ ] 删除一个段落，验证删除标记
- [ ] 批量插入 3 段，验证段落顺序
- [ ] 所有操作后 Word 正常打开（无"发现无法读取的内容"）

## 完成标准

- [ ] workflow tool 支持 split-read phase
- [ ] 文本润色在 drafting 之前（grill-me 确认）
- [ ] clean inject + quicompare 对比验证
- [ ] TC 注入可靠性测试通过
- [ ] 颐保银团文档测试通过（复杂文档路径）
- [ ] 简单文档测试通过（TC 注入路径）
