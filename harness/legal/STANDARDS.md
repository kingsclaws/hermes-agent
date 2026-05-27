# Legal Document Production Standards (SOP)

> 法律文件制作标准操作流程。所有 HPSwarm Agent 必须遵守本标准。
> 本标准适用于：合同、尽调报告、法律意见书、备忘录、诉讼文件。

## 0. 铁律

1. **逐字审阅，不可跳读。** 每一个字都必须经过 Reviewer 审阅，不允许只看关键词或首尾段。
2. **Track Changes 全程。** 从 Draft 到 Final，所有修改必须保留 TC 痕迹，直到 Coordinator 下发 `clean` 指令。
3. **段落编号不可变。** 审阅和修改过程中，段落索引（0-based paragraph index）是定位依据。不要在审阅完成前插入或删除整段。
4. **先结构后内容。** 任何文件操作必须先 `stats` → `export_structure` 理解全貌，再逐段处理。

## 1. 文档创建标准

### 1.1 骨架结构
每个法律文件必须包含以下结构元素（通过 `lex_docx_create` + `set_outline_level` 实现）：

| 级别 | 必需内容 |
|------|---------|
| Title | 文档标题 |
| Heading 1 | 一、背景 / 引言 |
| Heading 1 | 二、审阅范围 / 工作范围 |
| Heading 1 | 三、主要发现 / 法律分析 |
| Heading 2 | （按主题细分） |
| Heading 1 | 四、结论 / 建议 |
| Heading 1 | 附件（如有） |

### 1.2 元数据要求
- 文档属性中必须包含：案号/项目号、日期、版本号、作者
- 页眉：文档标题（左）+ 日期（右）
- 页脚：页码（居中）+ 保密声明（如需要）

## 2. 格式标准

### 2.1 字体
| 元素 | 中文字体 | 拉丁字体 | 字号 |
|------|---------|---------|------|
| 正文 | 宋体 | Times New Roman | 11.5pt |
| 标题 1 | 黑体 | Arial | 16pt Bold |
| 标题 2 | 黑体 | Arial | 14pt Bold |
| 标题 3 | 楷体 | Arial | 12pt Bold |
| 表格正文 | 宋体 | Times New Roman | 10.5pt |
| 页眉页脚 | 宋体 | Times New Roman | 9pt |

### 2.2 段落
- 行间距：正文 1.5 倍，表格 1.0 倍
- 段前间距：0pt（标题除外：标题 1 段前 12pt，标题 2 段前 6pt）
- 段后间距：0pt
- 首行缩进：2 字符（中文正文）
- 对齐方式：两端对齐（JUSTIFY）

### 2.3 表格
- 边框：单线，0.5pt，黑色
- 表头：深蓝底色 (#1F4E79)，白色加粗文字
- 数据行：交替浅灰底色 (#F2F2F2 / 无底色)
- 列宽：按内容比例分配

## 3. 审阅标准

### 3.1 内容审阅底线（Reviewer-Content）
以下问题必须检出，检出率 100%：
- [ ] 主体名称全篇一致（用 `lex_docx_lint` 的 entity_name_consistency 规则）
- [ ] 关键定义术语已加粗（用 `lex_docx_bold_terms --scan`）
- [ ] 引用条款号正确存在
- [ ] 金额/日期/比例等数据交叉一致
- [ ] 无草稿残留文字（用 `lex_docx_lint` 的 no_forbidden_text 规则）
- [ ] 法律依据引用准确
- [ ] 逻辑链条完整无断点

### 3.2 格式审阅底线（Reviewer-Format）
以下问题必须检出，检出率 100%：
- [ ] 全文字体统一（`lex_docx_doctor check` D01/D02）
- [ ] 大纲层级正确（`lex_docx_doctor check` D04）
- [ ] 编号连续无断号（`lex_docx_doctor check` D05）
- [ ] TOC 字段无 \\u 开关（`lex_docx_doctor check` D07）
- [ ] 标题同级别字体字号一致（`lex_docx_doctor check` D08）
- [ ] 页脚无模板残留（`lex_docx_doctor check` D09）
- [ ] 行间距统一（用 `lex_docx_para_query` 检查）
- [ ] 首行缩进统一
- [ ] 表格格式统一（用 `lex_docx_table_inspect` 逐表检查）
- [ ] 无多余空段落（用 `lex_docx_cleanup mode=report`）
- [ ] 中英文/数字间适当空格

## 4. 工作流程

### 4.1 起草流程
```
Coordinator 接收任务
  → 读取 CONTEXT.md
  → lex_docx_stats + export_structure（如果是修改现有文档）
  → delegate_task → Drafter
      → Drafter: stats → structure → 逐段起草/修改
      → Drafter 完成报告
  → delegate_task → Reviewer-Content（并行可以同时发给 Reviewer-Format）
      → Content: 全文档逐段审阅 → 审阅报告
  → delegate_task → Reviewer-Format
      → Format: doctor → para_query → table_inspect → footer_audit → 审阅报告
  → Coordinator 汇总报告 → 决定是否退回修改
  → 如通过：lex_docx_clean（定稿）
```

### 4.2 修改迭代
- 第一轮：Drafter 起草 → Reviewer 审阅 → 退回修改（如有重大问题）
- 第二轮：Drafter 修改 → Reviewer 再审 → 确认通过
- 终版：Coordinator 执行 `lex_docx_clean` → 交付

### 4.3 质量门
- 内容审阅：重大问题数 = 0 才能通过
- 格式审阅：所有 D01-D09 问题已修复或已记录
- 定稿前必须执行 `lex_docx_review_stats` 确认 TC 和 comment 数量

## 5. 文件命名规范

```
<项目简称>_<文档类型>_<版本号>_<日期>.docx
```

示例：
- `SJ_尽调报告_draft1_20260527.docx` — 第一轮草稿
- `SJ_尽调报告_review1_20260528.docx` — 第一轮审阅版
- `SJ_尽调报告_final_20260530.docx` — 终版（clean 后）
