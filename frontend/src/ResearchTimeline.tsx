import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Modal, Tag } from "antd";
import type { components } from "./api/schema";

type Run = components["schemas"]["ResearchRunRead"];
type ResearchEvent = Run["events"][number];
type RecordValue = Record<string, unknown>;

const TOOL: Record<string, string> = {
  read_product_evidence: "读取商品证据",
  ask_user: "确认关键条件",
  submit_report: "整理研究结果",
  capture_product_page: "浏览器重新采集商品页",
  collect_product_page: "后台打开并采集商品页",
  inspect_product_variants: "读取可选规格",
  select_product_variant: "选择规格并核验价格",
};

const EVENT_KIND: Record<string, string> = {
  model: "模型",
  tool: "工具",
  backend_browser: "后台浏览器",
  browser: "扩展",
  question: "确认",
  answer: "补充",
};

function asRecord(value: unknown): RecordValue | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : null;
}

function parseJson(value: string): { parsed: boolean; value?: unknown } {
  try {
    return { parsed: true, value: JSON.parse(value) as unknown };
  } catch {
    return { parsed: false };
  }
}

function getToolName(event: ResearchEvent): string | undefined {
  const data = event.data as RecordValue;
  if (typeof data.tool === "string") return data.tool;
  const output = asRecord(data.output);
  const calls = output?.tool_calls;
  const firstCall = Array.isArray(calls) ? asRecord(calls[0]) : null;
  const fn = asRecord(firstCall?.function);
  return typeof fn?.name === "string" ? fn.name : undefined;
}

function eventTitle(event: ResearchEvent): string {
  const tool = getToolName(event);
  return tool ? TOOL[tool] || tool : event.label;
}

function eventStatus(event: ResearchEvent): { label: string; color: string } {
  const data = event.data as RecordValue;
  const output = asRecord(data.output);
  const verification = asRecord(output?.variant_verification);
  const error = typeof data.error === "string" ? data.error : output?.error;
  if (typeof error === "string" && error) return { label: "失败", color: "red" };
  if (verification?.status === "selected") return { label: "规格已核对", color: "green" };
  if (verification && verification.status !== "selected") return { label: "需核实", color: "orange" };
  if (["blocked", "failed", "unavailable"].includes(String(output?.status))) {
    return { label: "受阻", color: "orange" };
  }
  if (output?.status === "opening" || event.label === "模型正在决定下一步") {
    return { label: "进行中", color: "blue" };
  }
  if (event.kind === "question") return { label: "等待确认", color: "orange" };
  if (event.kind === "answer") return { label: "已记录", color: "green" };
  return { label: "已完成", color: "green" };
}

function longTextLabel(value: string): string {
  return `展开文本 · ${value.length.toLocaleString()} 字`;
}

function ValueView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="research-record-muted">无</span>;
  if (typeof value === "string") {
    if (value.length > 280) {
      return <details className="research-record-nested">
        <summary>{longTextLabel(value)}</summary>
        <pre className="research-record-text">{value}</pre>
      </details>;
    }
    return <span className="research-record-value">{value}</span>;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="research-record-value">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    const list = <ul className="research-record-list">
      {value.map((entry, index) => <li key={index}><ValueView value={entry} depth={depth + 1} /></li>)}
    </ul>;
    return value.length > 4 || depth > 0
      ? <details className="research-record-nested"><summary>{value.length} 项</summary>{list}</details>
      : list;
  }

  const entries = Object.entries(value as RecordValue);
  const fields = <dl className="research-record-fields">
    {entries.map(([key, child]) => <div className="research-record-field" key={key}>
      <dt>{key}</dt><dd><ValueView value={child} depth={depth + 1} /></dd>
    </div>)}
  </dl>;
  return depth > 0 && entries.length > 5
    ? <details className="research-record-nested"><summary>{entries.length} 个字段</summary>{fields}</details>
    : fields;
}

function MessageContent({ value }: { value: unknown }) {
  if (typeof value !== "string") return <ValueView value={value} />;
  const parsed = parseJson(value);
  if (parsed.parsed && parsed.value !== null && typeof parsed.value === "object") {
    return <ValueView value={parsed.value} />;
  }
  return <ValueView value={value} />;
}

function RecordSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="research-record-section">
    <h4>{title}</h4>
    <div>{children}</div>
  </section>;
}

function messageRole(role: unknown): string {
  if (role === "system") return "系统";
  if (role === "assistant") return "助手";
  if (role === "tool") return "工具结果";
  if (role === "user") return "用户";
  return typeof role === "string" ? role : "消息";
}

function summarizeOutput(output: RecordValue): RecordValue {
  const keys = [
    "status", "capture_status", "freshness", "mode", "requires_open_product_page",
    "retrieval_note", "error_code", "error", "snapshot_id", "variant_verification",
    "selected_variant",
  ];
  const summary = Object.fromEntries(keys.filter((key) => key in output).map((key) => [key, output[key]]));
  return Object.keys(summary).length > 0 ? summary : output;
}

function ResearchEventDialog({
  selection,
  onClose,
}: {
  selection: { event: ResearchEvent; number: number } | null;
  onClose: () => void;
}) {
  const [showRaw, setShowRaw] = useState(false);
  if (!selection) return null;

  const { event, number } = selection;
  const data = event.data as RecordValue;
  const output = asRecord(data.output) ?? {};
  const tool = getToolName(event);
  const callList = output.tool_calls;
  const firstCall = Array.isArray(callList) ? asRecord(callList[0]) : null;
  const fn = asRecord(firstCall?.function);
  const rawArguments = fn?.arguments;
  const parsedArguments = typeof rawArguments === "string" ? parseJson(rawArguments) : null;
  const argumentsValue = parsedArguments?.parsed ? parsedArguments.value : rawArguments;
  const messages = Array.isArray(data.input)
    ? data.input.map(asRecord).filter((message): message is RecordValue => message !== null)
    : [];
  const usage = asRecord(data.usage);
  const error = typeof data.error === "string" ? data.error : output.error;
  const status = eventStatus(event);

  return <Modal
    open
    title={`调用记录 · 第 ${number} 步 · ${eventTitle(event)}`}
    onCancel={onClose}
    footer={null}
    width={820}
    className="research-event-modal"
  >
    <div className="research-record-meta">
      <Tag>{EVENT_KIND[event.kind] || event.kind}</Tag>
      <Tag color={status.color}>{status.label}</Tag>
      {event.elapsed_ms != null && <span>耗时 {(event.elapsed_ms / 1000).toFixed(1)} 秒</span>}
      <time>{new Date(event.created_at).toLocaleString()}</time>
    </div>

    {typeof error === "string" && <Alert className="research-record-alert" type="error" showIcon message={error} />}

    {event.kind === "model" ? <>
      <RecordSection title="模型动作">
        {typeof fn?.name === "string"
          ? <div className="research-record-choice"><Tag color="blue">选择工具</Tag><strong>{TOOL[fn.name] || fn.name}</strong><code>{fn.name}</code></div>
          : <p className="research-record-muted">{event.label}</p>}
        {argumentsValue !== undefined && <div className="research-record-arguments"><span>工具参数</span><ValueView value={argumentsValue} />
          {!parsedArguments?.parsed && typeof rawArguments === "string" && <p className="research-record-muted">参数未能解析为 JSON，以上显示原始文本。</p>}
        </div>}
      </RecordSection>

      {usage && <RecordSection title="模型用量">
        <div className="research-record-usage">{Object.entries(usage).map(([key, value]) => <div key={key}><span>{key}</span><strong>{String(value)}</strong></div>)}</div>
      </RecordSection>}

      {messages.length > 0 && <details className="research-record-context">
        <summary>请求上下文 · {messages.length} 条消息</summary>
        <div className="research-record-messages">{messages.map((message, index) => <article className="research-record-message" key={index}>
          <div className="research-record-message-heading"><Tag>{messageRole(message.role)}</Tag>
            {typeof message.name === "string" && <code>{message.name}</code>}
            {typeof message.tool_call_id === "string" && <small>调用 ID：{message.tool_call_id}</small>}
          </div>
          {message.content !== undefined && message.content !== null && <MessageContent value={message.content} />}
          {message.tool_calls !== undefined && <ValueView value={{ 工具调用: message.tool_calls }} />}
        </article>)}</div>
      </details>}
    </> : event.kind === "tool" || event.kind === "backend_browser" || event.kind === "browser" ? <>
      <RecordSection title="调用信息">
        <div className="research-record-choice"><Tag color="blue">{event.kind === "tool" ? "工具" : "执行通道"}</Tag>
          <strong>{tool ? TOOL[tool] || tool : event.label}</strong>{tool && <code>{tool}</code>}
        </div>
        {data.input !== undefined && <div className="research-record-arguments"><span>输入参数</span><ValueView value={data.input} /></div>}
      </RecordSection>
      <RecordSection title="执行结果">
        <ValueView value={summarizeOutput(output)} />
        {Object.keys(output).some((key) => !(key in summarizeOutput(output))) && <details className="research-record-context">
          <summary>展开完整结果</summary><ValueView value={output} />
        </details>}
      </RecordSection>
    </> : <RecordSection title="事件内容"><ValueView value={data} /></RecordSection>}

    <div className="research-record-raw-toggle">
      <Button type="link" onClick={() => setShowRaw((value) => !value)}>
        {showRaw ? "收起原始 JSON" : "查看原始 JSON"}
      </Button>
    </div>
    {showRaw && <pre className="research-record-json">{JSON.stringify(event.data, null, 2)}</pre>}
  </Modal>;
}

export function ResearchTimeline({ run }: { run: Run }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const previousRunId = useRef<string | null>(null);
  const followLatestRef = useRef(true);
  const [showLatestButton, setShowLatestButton] = useState(false);
  const [selection, setSelection] = useState<{ event: ResearchEvent; number: number } | null>(null);

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    if (previousRunId.current !== run.id) {
      previousRunId.current = run.id;
      followLatestRef.current = true;
      setShowLatestButton(false);
    }
    if (followLatestRef.current) scroller.scrollTop = scroller.scrollHeight;
  }, [run.id, run.events.length]);

  function updateFollowState() {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const atLatest = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 36;
    followLatestRef.current = atLatest;
    setShowLatestButton(!atLatest);
  }

  function scrollToLatest() {
    followLatestRef.current = true;
    setShowLatestButton(false);
    scrollerRef.current?.scrollTo({ top: scrollerRef.current.scrollHeight, behavior: "smooth" });
  }

  return <section className="research-process" aria-label="研究过程">
    <div className="research-process-heading">
      <div><h3>研究过程</h3><span>{run.events.length} 步 · 选择单步可查看调用详情</span></div>
      {showLatestButton && <Button size="small" onClick={scrollToLatest}>回到最新</Button>}
    </div>
    <div className="research-timeline-scroller" ref={scrollerRef} onScroll={updateFollowState}>
      <ol className="research-timeline" aria-live="polite">
        {run.events.map((event, index) => {
          const status = eventStatus(event);
          const data = event.data as RecordValue;
          const output = asRecord(data.output);
          const error = typeof data.error === "string" ? data.error : output?.error;
          const verification = asRecord(output?.variant_verification);
          const detail = typeof error === "string" ? error
            : verification?.status === "selected" ? String(verification.note || "已核对选中规格。")
              : (output?.freshness === "same_snapshot" ? "仍是同一份已保存资料，没有重新采集商品页。" : "");
          return <li className={`research-timeline-item is-${status.color}`} key={event.id}>
            <span className="research-timeline-index">{index + 1}</span>
            <div className="research-timeline-main">
              <div className="research-timeline-labels">
                <strong>{eventTitle(event)}</strong>
                <Tag>{EVENT_KIND[event.kind] || event.kind}</Tag>
                <Tag color={status.color}>{status.label}</Tag>
              </div>
              {detail && <span className={typeof error === "string" ? "research-timeline-detail is-error" : "research-timeline-detail"}>{detail}</span>}
            </div>
            {event.elapsed_ms != null && <small className="research-timeline-duration">{(event.elapsed_ms / 1000).toFixed(1)} 秒</small>}
            <Button type="link" size="small" onClick={() => setSelection({ event, number: index + 1 })}
              aria-label={`查看第 ${index + 1} 步调用记录`}>查看记录</Button>
          </li>;
        })}
      </ol>
    </div>
    <ResearchEventDialog key={selection?.event.id || "closed"} selection={selection} onClose={() => setSelection(null)} />
  </section>;
}
