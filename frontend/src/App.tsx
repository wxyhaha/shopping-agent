import { useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Input,
  Layout,
  Popconfirm,
  Select,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import { Link, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  addItem,
  ApiError,
  createIdempotencyKey,
  createTask,
  getTask,
  getReadiness,
  listTasks,
  listCaptureSnapshots,
  removeItem,
  removeTask,
  updateItemTitle,
  updateTaskTitle,
} from "./api/client";
import type { components } from "./api/schema";
import { ResearchPanel } from "./ResearchPanel";

const { Content, Sider } = Layout;
const { TextArea } = Input;
const { Text, Title } = Typography;

type Task = components["schemas"]["TaskRead"];
type TaskSummary = components["schemas"]["TaskSummary"];
type CreateTaskRequest = components["schemas"]["CreateTaskRequest"];
type ItemInput = components["schemas"]["ItemInput"];
type ProductItem = components["schemas"]["ItemRead"];
type CaptureSnapshot = components["schemas"]["CaptureSnapshotRead"];

const TASK_STATUS: Record<string, string> = {
  open: "研究任务",
  queued: "排队中",
  running: "处理中",
  needs_input: "需要你确认",
  completed: "已完成",
  failed: "遇到问题",
};

const ITEM_STATUS: Record<string, string> = {
  not_started: "链接已加入 · 尚未采集 · 尚未分析",
  queued: "等待采集",
  collecting: "正在采集",
  needs_attention: "需要你确认",
  complete: "已采集",
  partial: "部分采集 · 存在缺失字段",
  blocked: "需要人工处理 · 页面访问受阻",
  failed: "采集失败",
};

function BrandMark() {
  return <span className="brand-mark" aria-hidden="true">SA</span>;
}

function Workspace() {
  const tasksQuery = useQuery({ queryKey: ["tasks"], queryFn: listTasks });
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: getReadiness,
    refetchInterval: 10_000,
    retry: false,
  });

  return (
    <Layout className="workspace">
      <Sider width={252} className="workspace-sider" theme="light" breakpoint="lg" collapsedWidth="0">
        <Link to="/" className="brand-lockup" aria-label="Shopping Agent 首页">
          <BrandMark />
          <span className="brand-copy">
            <strong>Shopping Agent</strong>
            <small>本地商品比较</small>
          </span>
        </Link>
        <div className="sider-section-label">工作区</div>
        <NavLink to="/" end className={({ isActive }) => `side-link${isActive ? " active" : ""}`}>
          <span className="side-link-icon">⌂</span> 新建比较
        </NavLink>
        <div className="sider-list-heading">
          <span>最近任务</span>
          <span className="subtle-count">{tasksQuery.data?.length ?? "—"}</span>
        </div>
        <nav className="task-navigation" aria-label="最近比较任务">
          {tasksQuery.isLoading ? (
            <div className="side-loading"><Skeleton active title={false} paragraph={{ rows: 3 }} /></div>
          ) : tasksQuery.isError ? (
            <button className="side-empty" onClick={() => void tasksQuery.refetch()}>
              任务暂时无法读取，点击重试
            </button>
          ) : tasksQuery.data?.length ? (
            tasksQuery.data.slice(0, 12).map((task) => <TaskNavItem key={task.id} task={task} />)
          ) : (
            <p className="side-empty">创建的任务会保存在这里</p>
          )}
        </nav>
        <div className="sider-bottom">
          <span className="online-dot" />
          <span>数据保存在本机</span>
        </div>
      </Sider>
      <Layout className="main-layout">
        <header className="topbar">
          <div className="topbar-context">商品研究 <span>/</span> 任务工作台</div>
          <div className={`topbar-status${healthQuery.isError ? " service-offline" : ""}`}>
            <span className={healthQuery.data ? "online-dot" : "offline-dot"} />
            {healthQuery.data ? "本地服务正常" : healthQuery.isLoading ? "正在检查服务" : "服务暂不可用"}
          </div>
        </header>
        <Content className="page-content">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/tasks/:taskId" element={<TaskPage />} />
            <Route path="/capture" element={<CapturePage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

function TaskNavItem({ task }: { task: TaskSummary }) {
  const label = task.title.trim() || "商品比较";
  return (
    <NavLink to={`/tasks/${task.id}`} className={({ isActive }) => `task-nav-item${isActive ? " active" : ""}`}>
      <span className="task-nav-title" title={label}>{label}</span>
      <span className="task-nav-meta">{task.item_count} 个链接</span>
    </NavLink>
  );
}

function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [urls, setUrls] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const submissionRef = useRef({ signature: "", key: "" });
  const createMutation = useMutation({
    mutationFn: ({ payload, key }: { payload: CreateTaskRequest; key: string }) => createTask(payload, key),
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate(`/tasks/${task.id}`);
    },
  });
  const urlLines = urls.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
  const nonProductLineNumbers = urlLines.flatMap((candidate, index) =>
    getNonProductPageNotice(candidate) ? [index + 1] : [],
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!urlLines.length) {
      setFormError("先粘贴至少一个商品链接。每行一个，最多 20 个。");
      return;
    }
    if (urlLines.length > 20) {
      setFormError("单个任务最多添加 20 个链接，请分成多个任务。");
      return;
    }
    const payload: CreateTaskRequest = {
      title: title.trim() || null,
      items: urlLines.map((url) => ({ url, page_title: null, source: "web", browser: null })),
    };
    const signature = JSON.stringify(payload);
    if (submissionRef.current.signature !== signature) {
      submissionRef.current = { signature, key: createIdempotencyKey() };
    }
    createMutation.mutate({ payload, key: submissionRef.current.key });
  }

  const apiError = createMutation.error instanceof Error ? createMutation.error.message : null;
  const totalLinks = urlLines.length;

  return (
    <div className="page-frame">
      <div className="page-kicker"><span className="kicker-line" /> 比较工作台</div>
      <div className="page-heading-row">
        <div>
          <Title level={1}>从链接开始，做一次有依据的比较</Title>
          <Text className="page-subtitle">先把商品放进同一个任务。每条链接都会保存到本机，采集和分析状态会如实显示。</Text>
        </div>
        <div className="heading-mark" aria-hidden="true">01</div>
      </div>

      <div className="home-grid">
        <Card className="form-card" bordered={false}>
          <div className="card-heading">
            <div>
              <span className="step-label">第一步 · 建立比较任务</span>
              <Title level={3}>添加商品链接</Title>
            </div>
            <span className="card-icon" aria-hidden="true">↗</span>
          </div>
          <form onSubmit={submit} className="new-task-form">
            <label className="field-label" htmlFor="task-title">任务名称</label>
            <Input
              id="task-title"
              size="large"
              maxLength={120}
              placeholder="例如：卧室空气净化器"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
            {!title.trim() && (
              <Text className="field-help">
                直接粘贴链接时尚未读取商品标题，请填写名称。从商品详情页通过扩展新建时，留空可由已配置的 AI 根据页面标题命名；只发送标题，不发送链接。
              </Text>
            )}
            <div className="field-label-row">
              <label className="field-label" htmlFor="product-urls">商品链接</label>
              <span className="field-hint">每行一个 · 最多 20 个</span>
            </div>
            <TextArea
              id="product-urls"
              className="url-input"
              rows={6}
              value={urls}
              onChange={(event) => setUrls(event.target.value)}
              placeholder={'粘贴淘宝、京东或其他商品页面链接\nhttps://...'}
              spellCheck={false}
              aria-describedby="url-help"
            />
            <div className="input-footer" id="url-help">
              <span>支持淘宝、京东及其他商品页</span>
              <span className={totalLinks > 20 ? "count-error" : ""}>{totalLinks} / 20</span>
            </div>
            {nonProductLineNumbers.length > 0 && (
              <Alert
                className="form-alert"
                type="warning"
                showIcon
                message={`第 ${nonProductLineNumbers.join("、")} 条链接可能是购物车、订单或账号页面，请核对是否为商品详情页。`}
              />
            )}
            {(formError || apiError) && <Alert className="form-alert" type="error" showIcon message={formError || apiError} />}
            <div className="form-actions">
              <Text className="privacy-note"><span className="lock-icon">▣</span> 链接本机保存；AI 命名只发送页面标题</Text>
              <Button type="primary" htmlType="submit" size="large" loading={createMutation.isPending}>
                创建比较任务 <span aria-hidden="true">→</span>
              </Button>
            </div>
          </form>
        </Card>

        <aside className="guide-column">
          <div className="guide-card guide-primary">
            <div className="guide-card-top"><span className="guide-index">A</span><span>现在可以做</span></div>
            <h2>先整理候选</h2>
            <p>把准备比较的商品链接放进同一个任务，也可以在商品页面点扩展图标加入。</p>
            <div className="guide-flow"><span>商品链接</span><i>→</i><span>比较任务</span></div>
          </div>
          <div className="guide-card guide-muted">
            <div className="guide-card-top"><span className="guide-index">B</span><span>后续阶段</span></div>
            <h2>采集与分析</h2>
            <p>当前版本只登记链接，不会读取商品页或生成结论。采集与分析能力将在后续阶段逐步接入。</p>
            <div className="pending-line"><span className="pending-dot" /> 尚未开始采集</div>
          </div>
        </aside>
      </div>

      <RecentTasks />
    </div>
  );
}

function RecentTasks() {
  const tasksQuery = useQuery({ queryKey: ["tasks"], queryFn: listTasks });
  const tasks = tasksQuery.data ?? [];
  if (!tasksQuery.isLoading && !tasks.length) return null;

  return (
    <section className="recent-section">
      <div className="section-heading">
        <div><span className="step-label">继续上次的工作</span><h2>最近任务</h2></div>
        <span className="section-caption">自动保存 · 重启后仍可继续</span>
      </div>
      {tasksQuery.isLoading ? <Skeleton active paragraph={{ rows: 2 }} /> : (
        <div className="recent-grid">
          {tasks.slice(0, 3).map((task) => (
            <Link key={task.id} to={`/tasks/${task.id}`} className="recent-task-card">
              <span className="recent-task-icon">↗</span>
              <span className="recent-task-copy"><strong>{task.title}</strong><small>{task.item_count} 个商品链接 · {formatDate(task.updated_at)}</small></span>
              <span className="recent-arrow">→</span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function TaskPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const taskQuery = useQuery({ queryKey: ["task", taskId], queryFn: () => getTask(taskId), enabled: Boolean(taskId) });
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const requestRef = useRef({ url: "", key: "" });
  const addMutation = useMutation({
    mutationFn: ({ item, key }: { item: ItemInput; key: string }) => addItem(taskId, item, key),
    onSuccess: async () => {
      setUrl("");
      setSuccess(true);
      requestRef.current = { url: "", key: "" };
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => removeTask(taskId),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["task", taskId] });
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/", { replace: true });
    },
  });

  const add = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    const normalized = url.trim();
    if (!normalized) return setError("请先粘贴一个商品链接。");
    if (requestRef.current.url !== normalized) {
      requestRef.current = { url: normalized, key: createIdempotencyKey() };
    }
    addMutation.mutate({
      item: { url: normalized, page_title: null, source: "web", browser: null },
      key: requestRef.current.key,
    });
  };

  if (taskQuery.isLoading) return <div className="page-frame"><Skeleton active paragraph={{ rows: 6 }} /></div>;
  if (taskQuery.isError || !taskQuery.data) {
    return <div className="page-frame"><Alert showIcon type="error" message={taskQuery.error instanceof ApiError ? taskQuery.error.message : "无法读取此任务。"} action={<Button onClick={() => void taskQuery.refetch()}>重试</Button>} /></div>;
  }
  const task = taskQuery.data;
  const collectedCount = task.items.filter((item) => ["complete", "partial", "blocked", "failed"].includes(item.capture_status)).length;
  const captureIsInProgress = task.items.some((item) => ["queued", "collecting"].includes(item.capture_status));
  const hasCaptureSnapshot = task.items.some((item) => ["complete", "partial", "blocked", "failed"].includes(item.capture_status));
  const hasIncompleteCapture = task.items.some((item) => ["partial", "blocked", "failed"].includes(item.capture_status));
  const addItemNotice = url.trim() ? getNonProductPageNotice(url) : null;

  return (
    <div className="page-frame task-frame">
      <div className="page-kicker"><Link to="/">比较任务</Link><span>/</span>任务详情</div>
      <div className="task-title-row">
        <div>
          <div className="task-title-meta"><Tag bordered={false} color="green">{TASK_STATUS[task.status] ?? task.status}</Tag><span>创建于 {formatDate(task.created_at)}</span></div>
            <TaskTitleEditor taskId={task.id} title={task.title} />
          <Text className="page-subtitle">
            {captureIsInProgress
              ? "正在采集商品信息。你可以先说明用途与关注点。"
              : hasIncompleteCapture
                ? "已有部分商品资料。研究助手会根据需求判断哪些缺项需要确认。"
                : hasCaptureSnapshot
                  ? "商品资料已保存。向研究助手说明你的需求，开始核对与比较。"
                  : "先说明你的购物需求；助手会提示需要补充的商品资料。"}
          </Text>
        </div>
        <div className="task-header-actions">
          <div className="extension-hint"><span className="online-dot" /> 在淘宝、天猫或京东商品页，可用悬浮助手单击加入</div>
          <Popconfirm
            title="删除这个比较任务？"
            description={`任务中的 ${task.item_count} 条商品链接也会一起删除，此操作不可撤销。`}
            okText="删除任务"
            cancelText="保留任务"
            okButtonProps={{ danger: true, loading: deleteMutation.isPending }}
            onConfirm={() => deleteMutation.mutate()}
          >
            <Button danger loading={deleteMutation.isPending}>删除任务</Button>
          </Popconfirm>
        </div>
      </div>
      {deleteMutation.error instanceof Error && <Alert className="form-alert" type="error" showIcon message={deleteMutation.error.message} />}

      <div className="task-overview-strip">
        <div><span className="overview-value">{task.item_count}</span><span className="overview-label">商品链接</span></div>
        <div><span className="overview-value">{collectedCount} / {task.items.length}</span><span className="overview-label">已采集</span></div>
        <div className="overview-note"><span className="pending-dot" />
          {captureIsInProgress
            ? "商品采集处理中"
            : hasCaptureSnapshot
              ? "资料与缺项已记录，研究进度见下方"
              : "尚无采集快照，助手可帮助确认需要补充的信息"}
        </div>
      </div>

      <ResearchPanel key={task.id} taskId={task.id} />

      <section className="items-section">
        <div className="section-heading">
          <div><span className="step-label">商品资料与来源</span><h2>已添加商品 <span className="inline-count">{task.items.length}</span></h2></div>
          <span className="section-caption">网页和扩展添加的链接保存在同一个任务中</span>
        </div>
        <div className="product-list">
          {task.items.map((item, index) => <ProductRow key={item.id} taskId={task.id} item={item} index={index + 1} />)}
        </div>
        <Card className="add-item-card" bordered={false}>
          <div className="add-card-heading"><span className="add-plus">+</span><div><strong>再添加一条商品链接</strong><small>建议使用单个商品的详情页</small></div></div>
          <form onSubmit={add} className="add-item-form">
            <Input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https:// 商品页面链接" maxLength={8192} />
            <Button htmlType="submit" loading={addMutation.isPending}>添加链接</Button>
          </form>
          {addItemNotice && <Alert className="form-alert" type="warning" showIcon message={addItemNotice} />}
          {(error || (addMutation.error instanceof Error && addMutation.error.message)) && <Alert className="form-alert" type="error" showIcon message={error || addMutation.error?.message} />}
          {success && <div className="inline-success"><span className="online-dot" /> 链接已加入任务，尚未采集或分析。</div>}
        </Card>
      </section>

      <div className="evidence-placeholder">
        <span className="placeholder-mark">i</span>
        <div><strong>研究结论保留依据和未知项</strong><p>助手会优先核对影响选择的规格与价格条件，资料不足时给出阶段性结论或向你确认。</p></div>
      </div>
    </div>
  );
}

function TaskTitleEditor({ taskId, title }: { taskId: string; title: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const mutation = useMutation({
    mutationFn: (nextTitle: string) => updateTaskTitle(taskId, { title: nextTitle }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
      setEditing(false);
    },
  });

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draft.trim();
    if (value) mutation.mutate(value);
  }

  return (
    <div className="task-title-editor">
      {editing ? (
        <form className="task-title-edit-form" onSubmit={save}>
          <Input autoFocus maxLength={120} value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="任务名称" />
          <Button type="primary" htmlType="submit" loading={mutation.isPending} disabled={!draft.trim()}>保存</Button>
          <Button onClick={() => setEditing(false)} disabled={mutation.isPending}>取消</Button>
        </form>
      ) : (
        <div className="task-title-display">
          <Title level={1}>{title}</Title>
          <Button type="link" size="small" aria-label="编辑任务名称" onClick={() => { setDraft(title); setEditing(true); }}>编辑名称</Button>
        </div>
      )}
      {mutation.error instanceof Error && <Alert className="form-alert" type="error" showIcon message={mutation.error.message} />}
    </div>
  );
}

function ProductRow({ taskId, item, index }: { taskId: string; item: ProductItem; index: number }) {
  const host = useMemo(() => {
    try { return new URL(item.url).hostname; } catch { return "商品链接"; }
  }, [item.url]);
  const queryClient = useQueryClient();
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(item.page_title || host);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);
  const capturesQuery = useQuery({
    queryKey: ["captures", taskId, item.id],
    queryFn: () => listCaptureSnapshots(taskId, item.id, 5),
    enabled: captureOpen,
  });
  const updateMutation = useMutation({
    mutationFn: (pageTitle: string) => updateItemTitle(taskId, item.id, { page_title: pageTitle }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
      setEditingTitle(false);
      setTitleError(null);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => removeItem(taskId, item.id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
  });
  const warning = getNonProductPageNotice(item.url, item.page_title);

  function saveTitle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draftTitle.trim();
    if (!value) return setTitleError("商品标题不能为空。");
    updateMutation.mutate(value);
  }

  return (
    <Card className="product-row" bordered={false}>
      <span className="product-index">{String(index).padStart(2, "0")}</span>
      <span className="product-source-mark" aria-hidden="true">↗</span>
      <div className="product-main">
        {editingTitle ? (
          <form className="product-title-edit-form" onSubmit={saveTitle}>
            <Input autoFocus maxLength={512} value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} aria-label="商品标题" />
            <Button type="primary" size="small" htmlType="submit" loading={updateMutation.isPending} disabled={!draftTitle.trim()}>保存</Button>
            <Button size="small" onClick={() => { setEditingTitle(false); setTitleError(null); }} disabled={updateMutation.isPending}>取消</Button>
          </form>
        ) : (
          <div className="product-title-line">
            <strong title={item.page_title || host}>{item.page_title || host}</strong>
            <Tag bordered={false}>{item.source === "extension" ? "浏览器扩展" : "网页添加"}</Tag>
            {item.browser && <Tag bordered={false}>{item.browser === "edge" ? "Edge" : "Chrome"}</Tag>}
            <div className="product-actions">
              <Button type="link" size="small" onClick={() => { setDraftTitle(item.page_title || host); setEditingTitle(true); }}>编辑标题</Button>
              <Popconfirm
                title="从当前任务移除这条链接？"
                description="原商品页面不会受到影响。"
                okText="移除"
                cancelText="保留"
                okButtonProps={{ danger: true, loading: deleteMutation.isPending }}
                onConfirm={() => deleteMutation.mutate()}
              >
                <Button type="link" size="small" danger loading={deleteMutation.isPending}>移除</Button>
              </Popconfirm>
            </div>
          </div>
        )}
        {(titleError || updateMutation.error instanceof Error) && <Alert className="row-alert" type="error" showIcon message={titleError || updateMutation.error?.message} />}
        <div className="product-link-row">
          <a className="product-url" href={item.url} target="_blank" rel="noopener noreferrer">打开商品详情页 ↗</a>
          <span className="product-domain">{host}</span>
        </div>
        {warning && <div className="product-page-warning"><span className="pending-dot" />{warning}</div>}
        <span className="product-status"><span className="pending-dot" />{ITEM_STATUS[item.capture_status] ?? item.capture_status}</span>
        {item.capture_status !== "not_started" && (
          <Button className="capture-details-toggle" type="link" size="small" onClick={() => setCaptureOpen((open) => !open)}>
            {captureOpen ? "收起采集信息" : "查看采集信息"}
          </Button>
        )}
        {captureOpen && (
          <div className="capture-details-panel">
            {capturesQuery.isLoading ? <Skeleton active paragraph={{ rows: 3 }} /> : null}
            {capturesQuery.isError ? (
              <Alert type="error" showIcon message={capturesQuery.error instanceof Error ? capturesQuery.error.message : "无法读取采集快照。"} />
            ) : null}
            {capturesQuery.data?.length === 0 ? <Alert type="info" showIcon message="尚无采集快照。" /> : null}
            {capturesQuery.data?.map((snapshot) => <CaptureSnapshotDetails key={snapshot.id} snapshot={snapshot} />)}
          </div>
        )}
        {deleteMutation.error instanceof Error && <Alert className="row-alert" type="error" showIcon message={deleteMutation.error.message} />}
      </div>
      <span className="product-date">加入于 {formatDate(item.created_at)}</span>
    </Card>
  );
}

function CaptureSnapshotDetails({ snapshot }: { snapshot: CaptureSnapshot }) {
  const images = snapshot.images ?? [];
  const fields = snapshot.fields ?? [];
  const prices = snapshot.prices ?? [];
  const coverage = snapshot.coverage ?? [];
  const evidence = snapshot.evidence ?? [];
  const loadedImages = images.filter((image) => image.status === "observed" && image.loaded).length;
  const missingCoverage = coverage.filter((entry) => ["missing", "ambiguous", "blocked"].includes(entry.status));
  const platformLabel = snapshot.platform === "jd" ? "京东" : snapshot.platform === "tmall" ? "天猫" : "淘宝";
  const captureStatus = snapshot.status === "complete" ? "采集完整" : snapshot.status === "blocked" ? "页面受阻" : snapshot.status === "failed" ? "采集失败" : "部分采集";
  const accessEntry = evidence.find((entry) => entry.field_path === "page.access");
  const accessCode = accessEntry?.locator || "";

  return (
    <article className="capture-snapshot-card">
      <div className="capture-snapshot-header">
        <strong>{platformLabel} · {captureStatus}</strong>
        <span>{formatDate(snapshot.captured_at)}</span>
      </div>
      <div className="capture-snapshot-grid">
        <div><span>选中规格</span><strong>{snapshot.selected_variant.label || "未能确认规格"}</strong></div>
        <div><span>已加载商品图</span><strong>{loadedImages} 张 · 仅保存来源和尺寸</strong></div>
        <div><span>页面价格</span><strong>{prices.length ? prices.map((price) => price.raw_text || "未知价格").join("；") : "未识别"}</strong></div>
      </div>
      {["blocked", "failed"].includes(snapshot.status) && accessEntry && (
        <Alert className="form-alert" showIcon
          type={accessCode === "risk_control" ? "error" : "warning"}
          message={
            accessCode === "risk_control" ? `${platformLabel}风控拦截了后台访问`
              : accessCode === "login_required" ? `${platformLabel}要求先登录`
                : `${platformLabel}页面访问受阻`
          }
          description={
            accessCode === "risk_control"
              ? `${accessEntry.excerpt || "平台未返回商品内容"}。重新登录通常无效，请稍后重试或换用扩展接管。`
              : `${accessEntry.excerpt || "平台未返回商品内容"}。可完成独立登录，或使用扩展接管后重试。`
          } />
      )}
      {snapshot.variant_verification && <Alert className="form-alert" showIcon
        type={snapshot.variant_verification.status === "selected" ? "success" : "warning"}
        message={snapshot.variant_verification.status === "selected" ? "本次规格选择已核对" : "本次规格选择尚未核对成功"}
        description={`${snapshot.variant_verification.note}${snapshot.variant_verification.price_binding === "same_page_state" ? " 已记录选中后同页稳定显示的价格，仍以页面标价条件为准。" : " 尚未确认价格与本次选择的对应关系。"}`} />}
      {snapshot.variant_catalog?.groups?.length ? <details className="capture-evidence-details">
        <summary>可选规格 · {snapshot.variant_catalog.groups.length} 组{snapshot.variant_catalog.truncated ? "（部分选项）" : ""}</summary>
        {snapshot.variant_catalog.groups.map(group => <div key={group.id}><strong>{group.label}</strong><p>
          {group.options.map(option => <Tag key={option.id} color={option.disabled ? "default" : option.selected ? "green" : undefined}>
            {option.label}{option.selected ? " · 已选" : ""}{option.disabled ? " · 不可选" : ""}
          </Tag>)}
        </p></div>)}
      </details> : null}
      {fields.length > 0 && (
        <div className="capture-field-list">
          {fields.filter((field) => field.status !== "missing").map((field) => (
            <div key={field.observation_id}>
              <span>{field.label || field.field_path}</span>
              <strong>{field.normalized_value || field.raw_value || "未识别"}</strong>
            </div>
          ))}
        </div>
      )}
      {missingCoverage.length > 0 && (
        <div className="capture-missing-list">
          <span>需要核对</span>
          {missingCoverage.map((entry) => <Tag key={entry.field_path} color={entry.status === "blocked" ? "red" : "orange"}>{entry.field_path}: {entry.note || entry.status}</Tag>)}
        </div>
      )}
      <details className="capture-evidence-details">
        <summary>查看来源证据（{evidence.length} 条）</summary>
        <ul>
          {evidence.map((entry) => (
            <li key={entry.evidence_id}>
              <strong>{entry.field_path}</strong>
              <span>{entry.excerpt || "页面来源"}</span>
              <small>{entry.source} · {entry.locator || "未提供定位器"}</small>
            </li>
          ))}
        </ul>
        {images.length > 0 && <p>图片内容尚未下载或送入模型；当前记录 {images.length} 条来源引用。</p>}
        <p>字段证据来自页面 DOM、页面 JSON-LD 或图片元数据；商家展示信息仍需人工核对。</p>
      </details>
    </article>
  );
}

function CapturePage() {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const handoffParams = useMemo(() => {
    const hash = window.location.hash;
    return hash.startsWith("#handoff?") ? new URLSearchParams(hash.slice("#handoff?".length)) : search;
  }, [search]);
  const url = handoffParams.get("url") ?? "";
  const title = (handoffParams.get("title") ?? "").slice(0, 512);
  const source = handoffParams.get("source") === "extension" ? "extension" : "web";
  const browserValue = handoffParams.get("browser");
  const browser = browserValue === "chrome" || browserValue === "edge" ? browserValue : null;
  const taskFromQuery = handoffParams.get("task") ?? undefined;
  const [selectedTask, setSelectedTask] = useState(taskFromQuery ?? "new");
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const requestRef = useRef({ signature: "", key: "" });
  const tasksQuery = useQuery({ queryKey: ["tasks"], queryFn: listTasks });
  const validUrl = useMemo(() => {
    try {
      const parsed = new URL(url);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") && !parsed.username && !parsed.password;
    } catch {
      return false;
    }
  }, [url]);
  const options = tasksQuery.data?.map((task) => ({ label: `${task.title} · ${task.item_count} 个链接`, value: task.id })) ?? [];
  const pageNotice = getNonProductPageNotice(url, title);

  async function saveCapture() {
    setError(null);
    if (!validUrl) return setError("没有收到有效的网页链接。请回到商品页面后重新打开扩展。");
    if (tasksQuery.isError) return setError("无法读取已有任务。请检查本地服务后重试。");
    setSaving(true);
    const item: ItemInput = { url, page_title: title || null, source, browser };
    const signature = JSON.stringify({ selectedTask, newTaskTitle: newTaskTitle.trim(), item });
    if (requestRef.current.signature !== signature) {
      requestRef.current = { signature, key: createIdempotencyKey() };
    }
    try {
      let task: Task;
      if (selectedTask === "new" || !tasksQuery.data?.some((candidate) => candidate.id === selectedTask)) {
        task = await createTask(
          { title: newTaskTitle.trim() || null, items: [item] },
          requestRef.current.key,
        );
      } else {
        await addItem(selectedTask, item, requestRef.current.key);
        task = await getTask(selectedTask);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["task", task.id] }),
      ]);
      navigate(`/tasks/${task.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "添加失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page-frame capture-frame">
      <div className="page-kicker"><Link to="/">商品比较</Link><span>/</span>浏览器扩展</div>
      <div className="capture-heading"><BrandMark /><div><span className="step-label">来自浏览器当前标签页</span><Title level={1}>把这个商品加入比较</Title></div></div>
      <Card className="capture-preview" bordered={false}>
        <div className="capture-preview-icon">↗</div>
        <div className="capture-preview-copy">
          <strong>{title || (validUrl ? new URL(url).hostname : "当前网页")}</strong>
          <span>{url || "扩展未能读取当前页面地址"}</span>
          {browser && <Tag bordered={false}>{browser === "edge" ? "Microsoft Edge" : "Google Chrome"}</Tag>}
        </div>
        <span className="capture-explicit-label">由你确认后添加</span>
      </Card>
      {pageNotice && <Alert className="capture-page-warning" type="warning" showIcon message={pageNotice} />}
      <Card className="capture-action-card" bordered={false}>
        <label className="field-label" htmlFor="capture-task">加入到</label>
        <Select
          id="capture-task"
          size="large"
          className="capture-select"
          value={selectedTask}
          loading={tasksQuery.isLoading}
          options={[{ label: "创建一个新比较任务", value: "new" }, ...options]}
          onChange={setSelectedTask}
          aria-label="选择要加入的任务"
        />
        {selectedTask === "new" && (
          <div className="capture-title-field">
            <label className="field-label" htmlFor="capture-new-task-title">任务名称 <span>选填</span></label>
            <Input
              id="capture-new-task-title"
              size="large"
              maxLength={120}
              placeholder="留空时由 AI 根据商品页标题生成"
              value={newTaskTitle}
              onChange={(event) => setNewTaskTitle(event.target.value)}
            />
          </div>
        )}
        <div className="capture-disclosure"><span className="pending-dot" /><span>不会读取页面内容或生成商品分析。新任务名称留空时，会把页面标题发送给已配置的 AI 网关生成名称；商品链接不会发送。</span></div>
        {error && <Alert className="form-alert" type="error" showIcon message={error} />}
        <div className="capture-actions">
          <Button href="/" disabled={saving}>取消</Button>
          <Button type="primary" size="large" loading={saving} disabled={!validUrl || tasksQuery.isLoading} onClick={() => void saveCapture()}>
            确认加入任务 <span aria-hidden="true">→</span>
          </Button>
        </div>
      </Card>
      <p className="capture-footnote">数据保存在运行 Shopping Agent 服务的设备上。请勿提交支付、结算或账号设置页面。</p>
    </div>
  );
}

function NotFound() {
  return <div className="page-frame empty-state"><h1>找不到这个页面</h1><Link className="back-link" to="/">返回工作台 →</Link></div>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function getNonProductPageNotice(url: string, title?: string | null): string | null {
  let route = "";
  try {
    const parsed = new URL(url);
    route = `${parsed.hostname}${parsed.pathname}`.toLocaleLowerCase();
  } catch {
    route = url.toLocaleLowerCase();
  }
  const pageTitle = (title ?? "").toLocaleLowerCase();
  const cartOrOrderRoute = /(?:^|[./_-])(?:shopping.?cart|cart|basket|checkout|orders?)(?:$|[./_-])/i.test(route);
  const accountRoute = /(?:^|[./_-])(?:login|sign.?in|account.?settings|profile)(?:$|[./_-])/i.test(route);
  const nonProductTitle = /我的购物车|购物车|购物袋|商品结算|结算中心|我的订单|订单列表|订单详情|账号设置|账户设置|个人中心|登录页面?/i.test(pageTitle);
  if (cartOrOrderRoute || accountRoute || nonProductTitle) {
    return "这个页面可能是购物车、订单或账号页面，不是单商品详情页；请核对后再添加。";
  }
  return null;
}

export default function App() {
  return <Workspace />;
}
