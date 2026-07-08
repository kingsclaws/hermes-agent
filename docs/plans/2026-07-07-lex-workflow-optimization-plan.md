# Lex Workflow 优化计划

Date: 2026-07-07

## 核心问题

当前 document_drafting workflow 的问题：
1. **Coordinator 独自读完整份文档** — context 被塞满，推理能力下降
2. **文本润色在注入之后** — 应该先润色模板再注入内容
3. **没有 split-read** — 大文档（100+ 段）单 agent 读不完

## 优化后的 workflow

```
1. 结构扫描（轻量）
2. Split-read（并行）
3. 文本润色
4. 内容注入
5. 校对
6. 交付
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

### Phase 3: 文本润色

在注入内容之前，先润色模板文本：

- 统一术语（如 "甲方"/"乙方"/"丙方" 统一）
- 修正错别字
- 清理空段落
- 统一格式（字体、字号、行间距）
- 删除无用的占位符

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
2. 新增 `text-polish` 步骤（在 drafting 之前）
3. 重新排序依赖关系

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

## 完成标准

- [ ] workflow tool 支持 split-read phase
- [ ] 文本润色在 drafting 之前
- [ ] 颐保银团文档测试通过
- [ ] kanban 任务正确创建和完成
- [ ] coordinator 能正确聚合 reader 摘要
