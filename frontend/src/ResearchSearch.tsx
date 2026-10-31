import { Tag } from "antd";
import type { components } from "./api/schema";

type Run = components["schemas"]["ResearchRunRead"];
type Requirements = components["schemas"]["SearchRequirementsArgs"];
type SearchOutput = {
  status?: string; platform?: string; query?: string; search_url?: string;
  observed_at?: string; note?: string;
  candidates?: { id: string; url: string; title: string }[];
};
const CHECK_LABEL: Record<string, string> = { matched: "满足", unmatched: "不满足", unknown: "待核实" };
const STATUS_LABEL: Record<string, string> = { qualified: "满足当前硬条件", rejected: "不符合替代条件", needs_evidence: "资料不足" };

export function ResearchSearch({ run }: { run: Run }) {
  const searches = run.events.filter((event) => event.kind === "search");
  const latestRequirements = [...run.events].reverse().find((event) => event.data.tool === "set_search_requirements" &&
    (event.data.output as { status?: string })?.status === "recorded");
  const requirements = run.report?.search_requirements ??
    (latestRequirements?.data.output as { requirements?: Requirements } | undefined)?.requirements;
  const adopted = run.events.filter((event) => event.kind === "candidate");
  const titles = new Map(adopted.map((event) => {
    const output = event.data.output as { item_id: string; title: string };
    return [output.item_id, output.title];
  }));
  const evidence = new Map<string, string>();
  for (const event of run.events) {
    const output = event.data.output as { evidence?: { ref: string; excerpt?: string }[] } | undefined;
    for (const entry of output?.evidence ?? []) evidence.set(entry.ref, entry.excerpt || "无文字摘录");
  }
  if (!requirements && !searches.length) return null;
  return <section className="research-search" aria-label="替代商品搜索">
    <h3>替代商品搜索</h3>
    {requirements && <div className="research-search-requirements">
      <strong>本次硬条件</strong>
      <ul>{requirements.hard_conditions.map((condition) => <li key={condition.id}>{condition.description}</li>)}</ul>
      {(requirements.preferences ?? []).length > 0 && <p>偏好：{requirements.preferences?.join("；")}</p>}
      <small>条件有误时可停止研究后修改需求重新开始；助手向你确认时，也可在回答中修正。</small>
    </div>}
    {searches.map((event) => {
      const output = event.data.output as SearchOutput;
      const input = event.data.input as { platform: string; query: string };
      return <details className="research-search-attempt" key={event.id} open={output.status === "blocked"}>
        <summary>{input.platform === "jd" ? "京东" : "淘宝 / 天猫"} · {input.query} · {output.status === "opening" ? "搜索中" : output.status === "results" ? `${output.candidates?.length ?? 0} 个线索` : output.status === "blocked" ? "访问受阻" : output.status === "no_results" ? "当前查询无结果" : "未获得有效结果"}</summary>
        {output.note && <p>{output.note}</p>}
        {output.observed_at && <small>读取时间：{new Date(output.observed_at).toLocaleString()}</small>}
        {output.search_url && <p><a href={output.search_url} target="_blank" rel="noreferrer">查看本次搜索页 ↗</a></p>}
        <ul>{(output.candidates ?? []).map((candidate) => <li key={candidate.id}>
          <a href={candidate.url} target="_blank" rel="noreferrer">{candidate.title} ↗</a>
        </li>)}</ul>
      </details>;
    })}
    {(run.report?.alternatives ?? []).map((alternative) => <article className="research-alternative" key={alternative.item_id}>
      <strong>{titles.get(alternative.item_id) || "候选商品"}</strong>
      <Tag color={alternative.status === "qualified" ? "green" : "orange"}>{STATUS_LABEL[alternative.status]}</Tag>
      <p>品牌关系：{alternative.brand_relation === "different" ? "不同品牌" : alternative.brand_relation === "same" ? "同品牌" : "尚未核实"}</p>
      <ul>{alternative.checks.map((check) => <li key={check.condition_id}>
        <strong>{CHECK_LABEL[check.result]} · {requirements?.hard_conditions.find((condition) => condition.id === check.condition_id)?.description ?? check.condition_id}</strong>
        <p>{check.explanation}</p>
        {(check.evidence_refs ?? []).length > 0 && <details><summary>查看条件依据</summary>
          {check.evidence_refs?.map((ref) => <blockquote key={ref}>{evidence.get(ref) || "对应证据见运行记录。"}</blockquote>)}
        </details>}
      </li>)}</ul>
      <p>取舍与限制：{alternative.tradeoffs}</p>
      {(alternative.brand_evidence_refs ?? []).length > 0 && <details><summary>查看品牌依据</summary>
        {alternative.brand_evidence_refs?.map((ref) => <blockquote key={ref}>{evidence.get(ref) || "对应证据见运行记录。"}</blockquote>)}
      </details>}
    </article>)}
  </section>;
}
