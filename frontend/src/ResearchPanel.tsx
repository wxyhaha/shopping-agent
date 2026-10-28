import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Select, Tag } from "antd";
import { cancelResearch, createIdempotencyKey, getBrowserConnections, getCollectionStatus, listResearch, resumeResearch, startResearch } from "./api/client";
import type { components } from "./api/schema";
import { BrowserLoginPanel } from "./BrowserLoginPanel";
import { ResearchTimeline } from "./ResearchTimeline";

type Run = components["schemas"]["ResearchRunRead"];
const STATUS: Record<string, string> = {
  queued: "等待开始", running: "正在研究", needs_input: "需要你确认", interrupted: "运行已中断",
  completed: "本次研究已结束", failed: "研究遇到问题", cancelled: "已停止",
};
function evidenceLookup(run: Run): Map<string, string> {
  const refs = new Map<string, string>();
  for (const event of run.events) {
    const data = event.data as Record<string, unknown>;
    const output = data.output as { evidence?: { ref: string; excerpt?: string }[] } | undefined;
    if (Array.isArray(output?.evidence)) {
      for (const evidence of output.evidence) refs.set(evidence.ref, evidence.excerpt || "无文字摘录");
    }
    if (event.kind === "answer" && typeof data.ref === "string") refs.set(data.ref, `你的补充：${String(data.answer)}`);
  }
  return refs;
}

function normalizeReportText(text: string): string {
  return text.replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\\r/g, "\n");
}

function ReportText({ text, className }: { text: string; className?: string }) {
  return <div className={className}>{normalizeReportText(text)}</div>;
}

function RunResult({ run }: { run: Run }) {
  const refs = evidenceLookup(run);
  if (!run.report) return null;
  const hasRecommendation = run.report.outcome === "recommendation";
  return <section className="research-result" aria-label="研究结果">
    <div className="research-result-heading">
      <div><span className="step-label">{hasRecommendation ? "基于本次采集证据" : "当前证据尚不足以确定推荐"}</span>
        <h3>{hasRecommendation ? "研究结论" : "阶段性研究结论"}</h3></div>
      <Tag color={hasRecommendation ? "blue" : "orange"}>{hasRecommendation ? "有条件的建议" : "资料不足"}</Tag>
    </div>
    <div className="research-result-summary">
      <h4>结论</h4>
      <ReportText className="research-summary" text={run.report.summary} />
    </div>
    {run.report.findings.length > 0 && <div className="research-findings-section">
      <div className="research-result-section-heading"><h4>关键发现与依据</h4><span>{run.report.findings.length} 条</span></div>
      <div className="research-findings">
        {run.report.findings.map((finding, index) => <article className="research-finding" key={`${index}:${finding.text.slice(0, 24)}`}>
          <span className="research-finding-number">{String(index + 1).padStart(2, "0")}</span>
          <div className="research-finding-body">
            <ReportText className="research-finding-copy" text={finding.text} />
            <details><summary>查看依据 · {finding.evidence_refs.length} 条</summary>
              {finding.evidence_refs.map((ref) => <blockquote key={ref}>{normalizeReportText(refs.get(ref) || "对应证据见运行记录。")}</blockquote>)}
            </details>
          </div>
        </article>)}
      </div>
    </div>}
    {run.report.unknowns.length > 0 && <div className="research-unknowns"><strong>仍需核实</strong>
      <ul>{run.report.unknowns.map((text, index) => <li key={index}>{normalizeReportText(text)}</li>)}</ul>
    </div>}
  </section>;
}

export function ResearchPanel({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const [answer, setAnswer] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const requestRef = useRef({ goal: "", key: "" });
  const queryKey = ["research", taskId];
  const collectorQuery = useQuery({ queryKey: ["collection-status"], queryFn: getCollectionStatus });
  const connectionsQuery = useQuery({ queryKey: ["browser-connections", taskId],
    queryFn: () => getBrowserConnections(taskId), refetchInterval: 4000,
  });
  const runsQuery = useQuery({ queryKey, queryFn: () => listResearch(taskId),
    refetchInterval: (query) => query.state.data?.some((run) => ["queued", "running"].includes(run.status)) ? 2000 : false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey });
  const start = useMutation({ mutationFn: ({ goal, key }: { goal: string; key: string }) => startResearch(taskId, goal, key),
    onSuccess: async (run) => { setSelectedId(run.id); requestRef.current = { goal: "", key: "" }; await refresh(); },
  });
  const resume = useMutation({ mutationFn: ({ id, answer }: { id: string; answer?: string }) => resumeResearch(taskId, id, answer),
    onSuccess: async () => { setAnswer(""); await refresh(); },
  });
  const cancel = useMutation({ mutationFn: (id: string) => cancelResearch(taskId, id), onSuccess: refresh });
  const runs = runsQuery.data ?? [];
  const run = runs.find((item) => item.id === selectedId) ?? runs[0];
  const waitingBrowser = run?.status === "running" && run.events.at(-1)?.kind === "browser";
  const openingBackend = run?.status === "running" && run.events.at(-1)?.kind === "backend_browser" &&
    (run.events.at(-1)?.data.output as { status?: string })?.status === "opening";
  const capturedSnapshots = runs.flatMap((item) => item.events.filter((event) => {
    const output = event.data.output as { status?: string } | undefined;
    return event.kind === "tool" && ["capture_product_page", "collect_product_page", "inspect_product_variants", "select_product_variant"].includes(String(event.data.tool)) &&
      Boolean((output as { snapshot_id?: string })?.snapshot_id);
  }).map((event) => (event.data.output as { snapshot_id: string }).snapshot_id)).join(",");
  useEffect(() => {
    if (!capturedSnapshots) return;
    void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["captures", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  }, [capturedSnapshots, queryClient, taskId]);
  const hasActive = runs.some((item) => ["queued", "running", "needs_input", "interrupted"].includes(item.status));
  const error = start.error || resume.error || cancel.error || runsQuery.error;
  const blockedItemIds = [...new Set((run?.events ?? []).filter((event) => {
    const output = event.data.output as { capture_status?: string; mode?: string } | undefined;
    return event.kind === "tool" && ["collect_product_page", "inspect_product_variants", "select_product_variant"].includes(String(event.data.tool)) &&
      output?.mode === "headless" && ["blocked", "failed"].includes(output.capture_status || "");
  }).map((event) => String((event.data.input as { item_id: string }).item_id)))];

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = goal.trim();
    if (text.length < 2) return;
    if (requestRef.current.goal !== text) requestRef.current = { goal: text, key: createIdempotencyKey() };
    start.mutate(requestRef.current);
  }

  return <section className="research-panel" aria-label="购物研究助手">
    <div className="section-heading"><div><span className="step-label">围绕需求研究商品</span><h2>购物研究助手</h2></div></div>
    <form onSubmit={submit}>
      <label htmlFor="research-goal">你想解决什么问题？</label>
      <Input.TextArea id="research-goal" value={goal} onChange={(event) => setGoal(event.target.value)}
        placeholder="例如：这两款网线哪款适合家用？请先核对长度、材质和所选规格，信息不足时向我确认。"
        autoSize={{ minRows: 3, maxRows: 7 }} maxLength={4000} disabled={hasActive} />
      <div className="research-start-row"><small>开始后，需求及商品文字证据会发送到你配置的 AI 网关。助手会按需在后台打开链接、读取并选择可核对的规格，等待页面价格更新。遇到登录、验证或规格歧义时会请你接管；图片阅读和替代品搜索尚未接入。</small>
        <Button type="primary" htmlType="submit" loading={start.isPending} disabled={hasActive || goal.trim().length < 2 || runsQuery.isLoading || runsQuery.isError}>开始研究</Button>
      </div>
    </form>
    {collectorQuery.data && <Alert className="form-alert" showIcon
      type={collectorQuery.data.available ? "info" : "warning"}
      message={collectorQuery.data.available ? "后台采集已配置 · 无需打开商品页" : "后台采集环境尚未就绪"}
      description={collectorQuery.data.available ? "研究时按需启动独立浏览器，不共享日常浏览器登录态。实际访问结果会显示在研究记录中。" : "请按运行说明安装后台 Chromium 和共享采集器，或使用扩展接管。"} />}
    {collectorQuery.isError && <Alert className="form-alert" type="warning" showIcon message="无法核对后台采集环境，请确认后台已更新。" />}
    {connectionsQuery.data && <details><summary>扩展接管通道（可选）</summary>
      <div>{connectionsQuery.data.map((item, index) => <div key={item.item_id}>
        商品 {index + 1}：{item.connected
          ? `${item.browser === "edge" ? "Edge" : "Chrome"} 已连接${item.extension_version ? ` · v${item.extension_version}` : " · 版本未知"}`
          : "未连接。如需扩展接管，可打开商品页并刷新。"}
        {item.page_url && <a href={item.page_url} target="_blank" rel="noreferrer" style={{ marginLeft: 8 }}>打开商品页 ↗</a>}
      </div>)}<small>仅扩展接管时需要保持商品页打开；后台采集不依赖此连接。</small></div></details>}
    {error instanceof Error && <Alert type="error" showIcon message={error.message} />}
    {runs.length > 1 && <Select className="research-history" value={run?.id} onChange={(id) => { setSelectedId(id); setAnswer(""); }}
      aria-label="选择研究记录" options={runs.map((item) => ({ value: item.id, label: `${STATUS[item.status]} · ${item.goal.slice(0, 40)}` }))} />}
    {run && <div className="research-run">
      <div className="research-run-heading"><Tag color={run.status === "needs_input" || run.report?.outcome === "needs_evidence" ? "orange" : run.status === "failed" ? "red" : run.status === "completed" ? "green" : "blue"}>{run.status === "completed" && run.report?.outcome === "needs_evidence" ? "阶段性结果 · 资料不足" : STATUS[run.status]}</Tag>
        <span>{run.goal}</span>
        {!["completed", "cancelled"].includes(run.status) && <Button size="small" onClick={() => cancel.mutate(run.id)} loading={cancel.isPending}>停止研究</Button>}
      </div>
      {run.error && <Alert type="warning" showIcon message={run.error} />}
      {openingBackend && <Alert type="info" showIcon message="后台浏览器正在加载商品页并采集"
        description="你可以留在工作台。采集结束后会自动继续研究，访问受阻也会保留明确原因。" />}
      {waitingBrowser && <Alert type="info" showIcon message="正在等待浏览器补采集"
        description="请保持对应商品页打开，并确保 Chrome 或 Edge 已重新加载新版扩展。收到新快照后，研究会自动继续；超时会显示原因。" />}
      {blockedItemIds.map((itemId) => <BrowserLoginPanel key={`${run.id}:${itemId}`} taskId={taskId} itemId={itemId}
        onSaved={run.status === "needs_input" || ["failed", "interrupted"].includes(run.status) ? async () => {
          await resume.mutateAsync({ id: run.id, answer: "我已在后台独立浏览器中完成登录并保存会话。请使用 collect_product_page 重新打开商品链接核实，不要使用扩展；尚未确认任何商品规格或价格。" });
        } : undefined} />)}
      {run.question && ["needs_input", "interrupted", "failed"].includes(run.status) && <form className="research-question" onSubmit={(event) => {
        event.preventDefault(); if (answer.trim()) resume.mutate({ id: run.id, answer: answer.trim() });
      }}><strong>{run.question.question}</strong><p>{run.question.reason}</p>
        <Input.TextArea aria-label="补充信息" value={answer} onChange={(event) => setAnswer(event.target.value)} maxLength={4000} autoSize={{ minRows: 2, maxRows: 6 }} />
        <Button type="primary" htmlType="submit" loading={resume.isPending} disabled={!answer.trim()}>提交并继续</Button>
      </form>}
      {!run.question && ["failed", "interrupted"].includes(run.status) && <Button onClick={() => resume.mutate({ id: run.id })} loading={resume.isPending}>从检查点继续</Button>}
      {run.events.length > 0 && <ResearchTimeline run={run} />}
      <RunResult run={run} />
    </div>}
  </section>;
}
