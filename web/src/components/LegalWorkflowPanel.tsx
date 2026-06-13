import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@nous-research/ui/ui/components/card";
import { api, type LegalWorkflowRun, type LegalWorkflowStep, type ProjectInfo } from "@/lib/api";
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
  ScanText,
  ScrollText,
  Users,
} from "lucide-react";
import type { PointerEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  depends_on?: string[];
  id: string;
  instructions?: string;
  requires_approval?: boolean;
  role: string;
  status?: string;
  stepId?: string;
  title: string;
  type: string;
  x: number;
  y: number;
};

const DEFAULT_WORKFLOW_ATOMS: WorkflowAtom[] = [
  { id: "read-template", role: "reader", title: "通读合同模板", type: "lex_read", x: 12, y: 18 },
  { id: "read-ts", role: "reader", title: "读取 TS/支持资料", type: "source_read", x: 180, y: 18 },
  { id: "clause-map", role: "planner", title: "建立条款地图", type: "analysis", x: 12, y: 100 },
  { id: "ts-matrix", role: "planner", title: "TS-合同矩阵", type: "analysis", x: 180, y: 100 },
  { id: "revision-plan", role: "planner", title: "修订计划/人审确认", type: "approval_gate", x: 12, y: 182, requires_approval: true },
  { id: "execute", role: "drafter", title: "执行已确认修订", type: "lex_edit", x: 180, y: 182 },
  { id: "xref", role: "xref", title: "交叉引用审计", type: "lex_ref", x: 12, y: 264 },
  { id: "review", role: "reviewer", title: "格式/TS复核", type: "review", x: 180, y: 264 },
  { id: "deliver", role: "coordinator", title: "交付检查", type: "delivery", x: 96, y: 346 },
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
  const [selectedAtomIndex, setSelectedAtomIndex] = useState(0);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<LegalWorkflowRun[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<LegalWorkflowRun | null>(null);
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [dragging, setDragging] = useState<{
    index: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const effectiveProjectDir = q(projectDir || cwd || "");
  const canRunProject = !!effectiveProjectDir && !disabled;
  const canRunDocument = !!q(documentPath) && !disabled;

  const reviewTypeText = useMemo(
    () => reviewTypes.map((v) => `"${v}"`).join(", "),
    [reviewTypes],
  );
  const run = (prompt: string) => {
    if (disabled) return;
    onRun(prompt.trim());
  };

  const atomFromStep = (step: LegalWorkflowStep, index: number): WorkflowAtom => {
    const input = step.input ?? {};
    return {
      id: step.step_key || step.id || `step-${index + 1}`,
      stepId: step.id,
      title: step.title,
      type: step.type,
      role: step.role,
      status: step.status,
      depends_on: step.depends_on ?? [],
      requires_approval: !!step.requires_approval,
      instructions: typeof input.instructions === "string" ? input.instructions : "",
      x: typeof input.x === "number" ? input.x : 24 + (index % 2) * 156,
      y: typeof input.y === "number" ? input.y : 24 + Math.floor(index / 2) * 82,
    };
  };

  const loadProjectWorkflows = useCallback(async (
    targetProjectId: string,
    autoSelect = false,
  ) => {
    setWorkflowError(null);
    const res = await api.fetchProjectWorkflows(targetProjectId, 20);
    setWorkflows(res.workflows ?? []);
    if (autoSelect && res.workflows?.[0]) {
      const workflow = await api.getWorkflow(res.workflows[0].id);
      setCurrentWorkflow(workflow.workflow);
      setWorkflowAtoms((workflow.workflow.steps ?? []).map(atomFromStep));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function resolveProject() {
      if (!effectiveProjectDir) return;
      try {
        const res = await api.fetchProjects();
        if (cancelled) return;
        const normalise = (value?: string) => (value || "").replace(/\/+$/, "");
        const target = normalise(effectiveProjectDir);
        const matched = (res.projects ?? []).find((project: ProjectInfo) =>
          [project.id, project.name, project.cwd, project.directory]
            .map(normalise)
            .includes(target),
        );
        if (matched?.id) {
          setProjectId(matched.id);
          void loadProjectWorkflows(matched.id, true);
        }
      } catch (e) {
        if (!cancelled) setWorkflowError(e instanceof Error ? e.message : String(e));
      }
    }
    void resolveProject();
    return () => {
      cancelled = true;
    };
  }, [effectiveProjectDir, loadProjectWorkflows]);

  const selectWorkflow = async (runId: string) => {
    if (!runId) {
      setCurrentWorkflow(null);
      setWorkflowAtoms(DEFAULT_WORKFLOW_ATOMS);
      return;
    }
    setWorkflowBusy(true);
    setWorkflowError(null);
    try {
      const res = await api.getWorkflow(runId);
      setCurrentWorkflow(res.workflow);
      setWorkflowAtoms((res.workflow.steps ?? []).map(atomFromStep));
      setSelectedAtomIndex(0);
    } catch (e) {
      setWorkflowError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkflowBusy(false);
    }
  };

  const createPersistentWorkflow = async () => {
    if (!projectId) {
      setWorkflowError("Project is not registered in DB yet; register/select the project first.");
      return;
    }
    setWorkflowBusy(true);
    setWorkflowError(null);
    try {
      const res = await api.createProjectWorkflow(projectId, {
        name: "法律文书 workflow",
        document_path: q(documentPath),
        term_sheet_path: q(termSheetPath),
        instructions: q(instructions),
        steps: workflowAtoms.map((atom) => ({
          id: atom.id,
          title: atom.title,
          type: atom.type,
          role: atom.role,
          depends_on: atom.depends_on ?? [],
          instructions: atom.instructions ?? "",
          requires_approval: !!atom.requires_approval,
          x: atom.x,
          y: atom.y,
        })),
      });
      setCurrentWorkflow(res.workflow);
      setWorkflowAtoms((res.workflow.steps ?? []).map(atomFromStep));
      if (projectId) await loadProjectWorkflows(projectId);
    } catch (e) {
      setWorkflowError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkflowBusy(false);
    }
  };

  const runSelectedAtom = async () => {
    const atom = selectedAtom;
    if (!atom) return;
    if (currentWorkflow?.id && atom.stepId) {
      setWorkflowBusy(true);
      setWorkflowError(null);
      try {
        const res = await api.prepareWorkflowStepRun(currentWorkflow.id, atom.stepId);
        setCurrentWorkflow(res.workflow);
        setWorkflowAtoms((res.workflow.steps ?? []).map(atomFromStep));
        run(res.prompt);
        return;
      } catch (e) {
        setWorkflowError(e instanceof Error ? e.message : String(e));
      } finally {
        setWorkflowBusy(false);
      }
    }
    run(`
请运行 UI 选中的 workflow 原子，不要自由发挥。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath) || "按 workflow 上下文"}
TS/支持文件：${q(termSheetPath) || "无"}
选中原子：
${JSON.stringify(atom, null, 2)}
要求：
1. 如果该原子 requires_approval=true，先汇报计划并等待确认，不得修改文档。
2. 如果 type=lex_read/source_read/analysis，只读取和产出结构化结果，不修改文档。
3. 如果 type=lex_edit，必须先 lex_read 定位，再 lex_edit，最后验证。
4. 如已有 workflow run_id，请用 legal_workflow(action="update_step") 回写状态和结果。
    `);
  };

  const saveSelectedAtom = async () => {
    const atom = selectedAtom;
    if (!currentWorkflow?.id || !atom?.stepId) {
      setWorkflowError("Create or select a persistent workflow before saving a step.");
      return;
    }
    setWorkflowBusy(true);
    setWorkflowError(null);
    try {
      const res = await api.updateWorkflowStep(currentWorkflow.id, atom.stepId, {
        title: atom.title,
        type: atom.type,
        role: atom.role,
        depends_on: atom.depends_on ?? [],
        requires_approval: !!atom.requires_approval,
        input: {
          ...(currentWorkflow.steps ?? [])
            .find((step) => step.id === atom.stepId)
            ?.input,
          instructions: atom.instructions ?? "",
          x: atom.x,
          y: atom.y,
        },
      });
      setCurrentWorkflow(res.workflow);
      setWorkflowAtoms((res.workflow.steps ?? []).map(atomFromStep));
    } catch (e) {
      setWorkflowError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkflowBusy(false);
    }
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
    setSelectedAtomIndex((prev) => Math.max(0, Math.min(prev, workflowAtoms.length - 2)));
  };

  const addAtom = () => {
    setWorkflowAtoms((prev) => [
      ...prev,
      {
        id: `custom-${prev.length + 1}`,
        role: "coordinator",
        title: "新增 workflow 原子",
        type: "manual",
        x: 24 + (prev.length % 2) * 156,
        y: 24 + Math.floor(prev.length / 2) * 82,
      },
    ]);
    setSelectedAtomIndex(workflowAtoms.length);
  };

  const selectedAtom = workflowAtoms[selectedAtomIndex] ?? workflowAtoms[0];

  const startDrag = (index: number, ev: PointerEvent<HTMLButtonElement>) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    setSelectedAtomIndex(index);
    setDragging({
      index,
      offsetX: ev.clientX - rect.left,
      offsetY: ev.clientY - rect.top,
    });
    ev.currentTarget.setPointerCapture(ev.pointerId);
  };

  const dragNode = (ev: PointerEvent<HTMLDivElement>) => {
    if (!dragging || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    updateAtom(dragging.index, {
      x: Math.max(0, Math.min(260, ev.clientX - rect.left - dragging.offsetX)),
      y: Math.max(0, Math.min(390, ev.clientY - rect.top - dragging.offsetY)),
    });
  };

  const finishDrag = () => setDragging(null);

  const clickNode = (index: number) => {
    const target = workflowAtoms[index];
    if (connectFrom && connectFrom !== target.id) {
      const deps = new Set(target.depends_on ?? []);
      deps.add(connectFrom);
      updateAtom(index, { depends_on: [...deps] });
      setConnectFrom(null);
    }
    setSelectedAtomIndex(index);
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
              persistent workflow
            </span>
            <Badge tone={projectId ? "success" : "secondary"}>
              {projectId ? "db linked" : "not registered"}
            </Badge>
          </div>
          <select
            value={currentWorkflow?.id ?? ""}
            disabled={!projectId || workflowBusy}
            onChange={(e) => void selectWorkflow(e.target.value)}
            className="w-full rounded border border-current/15 bg-black/10 px-2 py-1.5 text-xs outline-none focus:border-primary/60 disabled:opacity-50"
          >
            <option value="">本地草稿 canvas</option>
            {workflows.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name} · {workflow.status} · {workflow.id}
              </option>
            ))}
          </select>
          <div className="flex gap-1">
            <button
              type="button"
              disabled={!projectId || workflowBusy || workflowAtoms.length === 0}
              onClick={() => void createPersistentWorkflow()}
              className="rounded border border-primary/30 px-2 py-0.5 text-[0.65rem] text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              {currentWorkflow ? "另存为新计划" : "创建持久计划"}
            </button>
            {projectId && (
              <button
                type="button"
                disabled={workflowBusy}
                onClick={() => void loadProjectWorkflows(projectId)}
                className="rounded border border-current/15 px-2 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                刷新
              </button>
            )}
          </div>
          {currentWorkflow && (
            <div className="truncate font-mono-ui text-[0.65rem] text-muted-foreground">
              run_id: {currentWorkflow.id}
            </div>
          )}
          {workflowError && (
            <div className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1 text-[0.65rem] text-destructive">
              {workflowError}
            </div>
          )}
        </div>

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
          <div
            ref={canvasRef}
            onPointerMove={dragNode}
            onPointerUp={finishDrag}
            onPointerCancel={finishDrag}
            className="relative h-[430px] overflow-hidden rounded border border-current/10 bg-[radial-gradient(circle_at_1px_1px,currentColor_1px,transparent_0)] bg-[length:18px_18px] text-muted-foreground/25"
          >
            <svg className="pointer-events-none absolute inset-0 h-full w-full">
              <defs>
                <marker
                  id="workflow-arrow"
                  markerHeight="6"
                  markerWidth="6"
                  orient="auto"
                  refX="5"
                  refY="3"
                >
                  <path d="M0,0 L0,6 L6,3 z" fill="currentColor" />
                </marker>
              </defs>
              {workflowAtoms.flatMap((atom) =>
                (atom.depends_on ?? []).map((sourceId) => {
                  const source = workflowAtoms.find((candidate) => candidate.id === sourceId);
                  if (!source) return null;
                  return (
                    <line
                      key={`${sourceId}-${atom.id}`}
                      x1={source.x + 62}
                      y1={source.y + 28}
                      x2={atom.x + 62}
                      y2={atom.y + 28}
                      stroke="currentColor"
                      strokeWidth="1.5"
                      markerEnd="url(#workflow-arrow)"
                    />
                  );
                }),
              )}
            </svg>
            {workflowAtoms.map((atom, index) => (
              <button
                type="button"
                key={`${atom.id}-${index}`}
                onClick={() => clickNode(index)}
                onPointerDown={(ev) => startDrag(index, ev)}
                className={cn(
                  "absolute z-10 w-[126px] rounded border p-1.5 text-left shadow-sm",
                  "bg-background-base/95 text-foreground backdrop-blur-sm",
                  selectedAtomIndex === index
                    ? "border-primary/70 ring-1 ring-primary/30"
                    : "border-current/15 hover:border-current/30",
                  connectFrom === atom.id && "border-warning/70 ring-1 ring-warning/40",
                  atom.status === "running" && "border-warning/70",
                  atom.status === "done" && "border-success/60",
                  atom.status === "error" && "border-destructive/70",
                )}
                style={{ left: atom.x, top: atom.y }}
              >
                <div className="flex items-start gap-1">
                  <GripVertical className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="truncate text-[0.7rem] font-medium">{atom.title}</div>
                    <div className="truncate text-[0.6rem] text-muted-foreground">
                      {atom.role} · {atom.type}
                      {atom.status ? ` · ${atom.status}` : ""}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
          {selectedAtom && (
            <div className="space-y-1 rounded border border-primary/20 bg-primary/5 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
                  选中原子 #{selectedAtomIndex + 1}
                </span>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => void runSelectedAtom()}
                  className="rounded border border-primary/30 px-2 py-0.5 text-[0.65rem] text-primary hover:bg-primary/10 disabled:opacity-50"
                >
                  {currentWorkflow ? "运行选中 step" : "运行选中草稿"}
                </button>
                {currentWorkflow && selectedAtom.stepId && (
                  <button
                    type="button"
                    disabled={workflowBusy}
                    onClick={() => void saveSelectedAtom()}
                    className="rounded border border-current/15 px-2 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground disabled:opacity-50"
                  >
                    保存选中
                  </button>
                )}
              </div>
              <input
                value={selectedAtom.title}
                onChange={(e) => updateAtom(selectedAtomIndex, { title: e.target.value })}
                className="w-full rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.7rem] outline-none focus:border-primary/60"
                placeholder="title"
              />
              <div className="grid grid-cols-2 gap-1">
                <input
                  value={selectedAtom.id}
                  onChange={(e) => updateAtom(selectedAtomIndex, { id: e.target.value })}
                  className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                  placeholder="id"
                />
                <input
                  value={(selectedAtom.depends_on ?? []).join(", ")}
                  onChange={(e) =>
                    updateAtom(selectedAtomIndex, {
                      depends_on: e.target.value
                        .split(",")
                        .map((v) => v.trim())
                        .filter(Boolean),
                    })
                  }
                  className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                  placeholder="depends_on"
                />
              </div>
              <div className="grid grid-cols-2 gap-1">
                <input
                  value={selectedAtom.role}
                  onChange={(e) => updateAtom(selectedAtomIndex, { role: e.target.value })}
                  className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                  placeholder="role"
                />
                <input
                  value={selectedAtom.type}
                  onChange={(e) => updateAtom(selectedAtomIndex, { type: e.target.value })}
                  className="rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                  placeholder="type"
                />
              </div>
              <textarea
                value={selectedAtom.instructions ?? ""}
                onChange={(e) =>
                  updateAtom(selectedAtomIndex, { instructions: e.target.value })
                }
                rows={2}
                className="w-full resize-none rounded border border-current/10 bg-black/10 px-1.5 py-1 text-[0.65rem] outline-none focus:border-primary/60"
                placeholder="这个原子的具体执行要求"
              />
              <label className="flex items-center gap-1.5 text-[0.65rem] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={!!selectedAtom.requires_approval}
                  onChange={(e) =>
                    updateAtom(selectedAtomIndex, {
                      requires_approval: e.target.checked,
                    })
                  }
                />
                执行前需要人工确认
              </label>
              <div className="flex flex-wrap gap-1">
                <button
                  type="button"
                  onClick={() => setConnectFrom(selectedAtom.id)}
                  className="rounded border border-current/15 px-2 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground"
                >
                  从此节点连线
                </button>
                <button
                  type="button"
                  onClick={() => moveAtom(selectedAtomIndex, -1)}
                  className="rounded border border-current/15 px-2 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground"
                >
                  上移顺序
                </button>
                <button
                  type="button"
                  onClick={() => moveAtom(selectedAtomIndex, 1)}
                  className="rounded border border-current/15 px-2 py-0.5 text-[0.65rem] text-muted-foreground hover:text-foreground"
                >
                  下移顺序
                </button>
                <button
                  type="button"
                  onClick={() => removeAtom(selectedAtomIndex)}
                  className="rounded border border-destructive/30 px-2 py-0.5 text-[0.65rem] text-destructive hover:bg-destructive/10"
                >
                  删除
                </button>
              </div>
              {connectFrom && (
                <div className="text-[0.65rem] text-warning">
                  连线中：点击目标节点，将 `{connectFrom}` 设为其依赖。
                </div>
              )}
            </div>
          )}
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
            disabled={disabled}
            icon={<Users />}
            label="角色Profile"
            onClick={() =>
              run(`
请初始化 lex 法律多 profile worker 池。
要求：
1. 调用 legal_profiles(action="bootstrap", prefix="lex")。
2. 初始化后调用 legal_profiles(action="list", prefix="lex") 展示 coordinator、drafter、content/format/xref/translation reviewers。
3. 后续 workflow 分发任务时优先使用这些真实 Hermes profiles，而不是只在 prompt 里模拟角色。
              `)
            }
          />
          <WorkflowButton
            disabled={!canRunDocument}
            icon={<PenLine />}
            label="创建计划"
            onClick={() => void createPersistentWorkflow()}
          />
          <WorkflowButton
            disabled={!canRunDocument}
            icon={<ScrollText />}
            label="逐段制作"
            onClick={() =>
              run(`
请执行法律文书逐段制作 workflow，禁止一次性粗略替换。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath)}
TS/支持文件：${q(termSheetPath) || "无"}
指令：${q(instructions) || "按项目上下文、TS和模板逐段制作。"}
要求：
1. 先调用 legal_workflow(action="create_plan", workflow_type="document_drafting", chunk_size=12) 创建逐段制作计划。
2. 执行时必须调用 legal_orchestrate(task_type="draft_iterative", chunk_size=12)，不要自由调用一连串 lex_edit。
3. draft_iterative 必须按段落 chunk 顺序执行：lex_read 目标范围 → 判断是否需改 → lex_edit → lex_read 读回 → verification_report。
4. 每个 chunk 未读回核对前不得进入下一 chunk；任一 chunk 失败即停止并报告。
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
            icon={<ScanText />}
            label="逐段校对"
            onClick={() =>
              run(`
请创建并执行法律文书逐段校对 workflow，不要直接全篇粗略审阅。
项目目录：${effectiveProjectDir || "使用当前 active project"}
主文档：${q(documentPath)}
TS/支持文件：${q(termSheetPath) || "无"}
校对类型：[${reviewTypeText}]
额外要求：${q(instructions) || "逐段输出结构化 findings，包含严重程度、位置、问题、修改建议。"}
要求：
1. 先调用 legal_workflow(action="create_plan", workflow_type="proofread_review", review_types=[${reviewTypeText}], chunk_size=180)。
2. 计划必须包含：读取全文结构、建立段落分块、分段并行校对、汇总去重、人审确认、执行确认修改、修改段落复核、最终门禁。
3. 未经确认前不得调用 lex_edit。
4. 如只执行校对不改文档，可调用 legal_orchestrate(task_type="proofread", review_types=[${reviewTypeText}], chunk_size=180)。
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
