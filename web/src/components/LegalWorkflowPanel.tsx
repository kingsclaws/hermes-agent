import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  CheckSquare,
  FileSearch,
  GitBranch,
  GripVertical,
  Plus,
  PackageCheck,
  PenLine,
  Play,
  ScrollText,
  Trash2,
} from "lucide-react";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";

interface LegalWorkflowPanelProps {
  cwd?: string;
  disabled?: boolean;
  onRun: (prompt: string) => void;
}

const REVIEW_TYPES = [
  ["review_content", "内容"],
  ["review_format", "格式"],
  ["review_xref", "交叉引用"],
  ["review_ts", "TS一致性"],
] as const;

type WorkflowAtom = {
  id: string;
  role: string;
  title: string;
  type: string;
};

const DEFAULT_WORKFLOW_ATOMS: WorkflowAtom[] = [
  { id: "read-template", role: "reader", title: "通读合同模板", type: "lex_read" },
  { id: "read-ts", role: "reader", title: "读取 TS/支持资料", type: "source_read" },
  { id: "clause-map", role: "planner", title: "建立条款地图", type: "analysis" },
  { id: "ts-matrix", role: "planner", title: "TS-合同矩阵", type: "analysis" },
  { id: "revision-plan", role: "planner", title: "修订计划/人审确认", type: "approval_gate" },
  { id: "execute", role: "drafter", title: "执行已确认修订", type: "lex_edit" },
  { id: "xref", role: "xref", title: "交叉引用审计", type: "lex_ref" },
  { id: "review", role: "reviewer", title: "格式/TS复核", type: "review" },
  { id: "deliver", role: "coordinator", title: "交付检查", type: "delivery" },
];

function q(value: string): string {
  return value.trim();
}

export function LegalWorkflowPanel({
  cwd,
  disabled,
  onRun,
}: LegalWorkflowPanelProps) {
  const [projectDir, setProjectDir] = useState(cwd ?? "");
  const [documentPath, setDocumentPath] = useState("");
  const [termSheetPath, setTermSheetPath] = useState("");
  const [instructions, setInstructions] = useState("");
  const [reviewTypes, setReviewTypes] = useState<string[]>([
    "review_content",
    "review_format",
    "review_xref",
  ]);
  const [workflowAtoms, setWorkflowAtoms] = useState<WorkflowAtom[]>(DEFAULT_WORKFLOW_ATOMS);

  const effectiveProjectDir = q(projectDir || cwd || "");
  const canRunProject = !!effectiveProjectDir && !disabled;
  const canRunDocument = !!q(documentPath) && !disabled;

  const reviewTypeText = useMemo(
    () => reviewTypes.map((v) => `"${v}"`).join(", "),
    [reviewTypes],
  );
  const workflowAtomText = useMemo(
    () => JSON.stringify(workflowAtoms, null, 2),
    [workflowAtoms],
  );

  const run = (prompt: string) => {
    if (disabled) return;
    onRun(prompt.trim());
  };

  const toggleReview = (type: string) => {
    setReviewTypes((prev) =>
      prev.includes(type)
        ? prev.filter((v) => v !== type)
        : [...prev, type],
    );
  };

  const updateAtom = (index: number, patch: Partial<WorkflowAtom>) => {
    setWorkflowAtoms((prev) =>
      prev.map((atom, i) => (i === index ? { ...atom, ...patch } : atom)),
    );
  };

  const moveAtom = (index: number, direction: -1 | 1) => {
    setWorkflowAtoms((prev) => {
      const next = [...prev];
      const target = index + direction;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const removeAtom = (index: number) => {
    setWorkflowAtoms((prev) => prev.filter((_, i) => i !== index));
  };

  const addAtom = () => {
    setWorkflowAtoms((prev) => [
      ...prev,
      {
        id: `custom-${prev.length + 1}`,
        role: "coordinator",
        title: "新增 workflow 原子",
        type: "manual",
      },
    ]);
  };

  return (
    <Card className="overflow-hidden border-primary/20 bg-background-base/70 p-0">
      <div className="border-b border-current/10 bg-primary/5 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              legal workflow
            </div>
            <div className="truncate text-sm font-medium">
              法律文书工作台
            </div>
          </div>
          <Badge tone={disabled ? "secondary" : "success"}>
            {disabled ? "offline" : "ready"}
          </Badge>
        </div>
      </div>

      <div className="space-y-2 px-3 py-3">
        <label className="block space-y-1">
          <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            项目目录
          </span>
          <input
            value={projectDir}
            onChange={(e) => setProjectDir(e.target.value)}
            placeholder={cwd || "/workingfile/项目目录"}
            className={cn(
              "w-full rounded border border-current/15 bg-black/10 px-2 py-1.5",
              "text-xs outline-none focus:border-primary/60",
            )}
          />
        </label>

        <div className="space-y-1 rounded border border-current/10 bg-black/5 p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
              workflow 原子
            </span>
            <button
              type="button"
              onClick={addAtom}
              className="inline-flex items-center gap-1 rounded border border-current/15 px-1.5 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground"
            >
              <Plus className="h-3 w-3" />
              增加
            </button>
          </div>
          <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
            {workflowAtoms.map((atom, index) => (
              <div
                key={`${atom.id}-${index}`}
                className="grid grid-cols-[auto_1fr_auto] items-start gap-1 rounded border border-current/10 bg-background-base/60 p-1"
              >
                <GripVertical className="mt-1 h-3.5 w-3.5 text-muted-foreground" />
                <div className="min-w-0 space-y-1">
                  <input
                    value={atom.title}
                    onChange={(e) => updateAtom(index, { title: e.target.value })}
                    className="w-full rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.7rem] outline-none focus:border-primary/60"
                  />
                  <div className="grid grid-cols-2 gap-1">
                    <input
                      value={atom.role}
                      onChange={(e) => updateAtom(index, { role: e.target.value })}
                      className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                    />
                    <input
                      value={atom.type}
                      onChange={(e) => updateAtom(index, { type: e.target.value })}
                      className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <button
                    type="button"
                    onClick={() => moveAtom(index, -1)}
                    className="rounded border border-current/10 px-1 text-[0.6rem] text-muted-foreground hover:text-foreground"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    onClick={() => moveAtom(index, 1)}
                    className="rounded border border-current/10 px-1 text-[0.6rem] text-muted-foreground hover:text-foreground"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    onClick={() => removeAtom(index)}
                    className="rounded border border-current/10 px-1 py-0.5 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <WorkflowButton
            disabled={!canRunProject}
            icon={<ScrollText />}
            label="注册/选择"
            onClick={() =>
              run(`
请使用原生工具处理项目：
1. 如项目未注册，调用 project_create(name="颐保银团", path="${effectiveProjectDir}", select=true)。
2. 调用 project_select(name="${effectiveProjectDir}") 或选择刚创建的项目。
3. 调用 project_status 验证当前 active project。
不要使用 terminal、execute_code 或 SQLite 直接操作项目数据库。
              `)
            }
          />
          <WorkflowButton
            disabled={!canRunProject}
            icon={<FileSearch />}
            label="读取资料"
            onClick={() =>
              run(`
请完整读取并整理项目基础资料。项目目录：${effectiveProjectDir}
要求：
1. 使用 search_files/read_file 列出并读取已有 md。
2. 对 PDF/扫描件使用原生 lex_ocr。
3. 对 DOCX 使用原生 lex_read。
4. 调用 lex_project_init 建立或刷新项目上下文。
5. 汇总主体、交易结构、融资条件、担保、批复条件、缺失资料和风险点。
禁止用 shell 调 lex-ocr，禁止 import lexitool。
              `)
            }
          />
        </div>

        <label className="block space-y-1">
          <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            主文档
          </span>
          <input
            value={documentPath}
            onChange={(e) => setDocumentPath(e.target.value)}
            placeholder="/workingfile/.../D01.docx"
            className={cn(
              "w-full rounded border border-current/15 bg-black/10 px-2 py-1.5",
              "text-xs outline-none focus:border-primary/60",
            )}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            TS / 支持文件
          </span>
          <input
            value={termSheetPath}
            onChange={(e) => setTermSheetPath(e.target.value)}
            placeholder="/workingfile/.../Term Sheet.md"
            className={cn(
              "w-full rounded border border-current/15 bg-black/10 px-2 py-1.5",
              "text-xs outline-none focus:border-primary/60",
            )}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            指令
          </span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={3}
            placeholder="例如：根据 TS 修订贷款金额、还款安排、担保条款，并开启 Track Changes。"
            className={cn(
              "w-full resize-none rounded border border-current/15 bg-black/10 px-2 py-1.5",
              "text-xs outline-none focus:border-primary/60",
            )}
          />
        </label>

        <div className="grid grid-cols-2 gap-1.5">
          <WorkflowButton
            disabled={!canRunDocument}
            icon={<PenLine />}
            label="创建计划"
            onClick={() =>
              run(`
请先创建法律文书 workflow 计划，不要直接修改文件。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath)}
TS/支持文件：${q(termSheetPath) || "无"}
指令：${q(instructions) || "按项目上下文和用户要求处理。"}
要求：
1. 调用 legal_orchestrate(task_type="plan") 或 legal_workflow(action="create_plan")。
2. workflow 必须包含：通读模板、读取 TS、条款地图、TS-合同矩阵、修订计划、人审确认、执行修订、交叉引用、格式复核、交付检查。
3. 在用户确认修订计划前，禁止调用 lex_edit 修改主文档。
4. 创建后展示 workflow run_id 和每个 step_id，等待 Master 选择、修改或确认。
5. UI 指定的 workflow 原子如下，优先按该顺序建计划：
${workflowAtomText}
              `)
            }
          />
          <WorkflowButton
            disabled={!canRunDocument}
            icon={<CheckSquare />}
            label="多维审阅"
            onClick={() =>
              run(`
请执行多维法律审阅 workflow。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath)}
TS：${q(termSheetPath) || "无"}
审阅类型：[${reviewTypeText}]
额外要求：${q(instructions) || "输出结构化 findings，包含严重程度、位置、问题、修改建议。"}
请调用 legal_orchestrate(task_type="review", review_types=[${reviewTypeText}])。
              `)
            }
          />
          <WorkflowButton
            disabled={!canRunDocument}
            icon={<GitBranch />}
            label="交叉引用"
            onClick={() =>
              run(`
请审计并修复交叉引用。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath)}
要求：
1. 先调用 lex_ref 做 cross-reference/xref audit。
2. 不要把可点击的 [ref] 直接视为断链。
3. 静态条款号转自动引用时必须匹配准确条款和标题。
4. 修改后重新审计并报告 remaining issues。
              `)
            }
          />
          <WorkflowButton
            disabled={!canRunProject}
            icon={<PackageCheck />}
            label="交付检查"
            onClick={() =>
              run(`
请执行交付前检查和交付包 workflow。
项目目录：${effectiveProjectDir || "使用当前 active project"}
要求：
1. 调用 lex_gate_check。
2. 如需要红线，调用 lex_diff。
3. 调用 lex_deliver 生成交付包。
4. 汇报交付文件、未解决风险和需要 Master 确认的事项。
              `)
            }
          />
        </div>

        <div className="flex flex-wrap gap-1 pt-1">
          {REVIEW_TYPES.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => toggleReview(value)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[0.65rem]",
                reviewTypes.includes(value)
                  ? "border-primary/50 bg-primary/10 text-primary"
                  : "border-current/15 text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}

function WorkflowButton({
  disabled,
  icon,
  label,
  onClick,
}: {
  disabled?: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      outlined
      disabled={disabled}
      onClick={onClick}
      className="justify-start gap-1.5 px-2 py-1.5 text-xs normal-case tracking-normal"
    >
      <span className="h-3.5 w-3.5 [&>svg]:h-3.5 [&>svg]:w-3.5">
        {icon}
      </span>
      <span className="truncate">{label}</span>
      <Play className="ml-auto h-3 w-3 opacity-50" />
    </Button>
  );
}
