import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Space } from "antd";
import { controlBrowserLogin, getBrowserLogin, openBrowserLogin } from "./api/client";

export function BrowserLoginPanel({ taskId, itemId, onSaved }: {
  taskId: string; itemId: string; onSaved?: () => Promise<unknown>;
}) {
  const client = useQueryClient();
  const queryKey = ["browser-login", taskId, itemId];
  const query = useQuery({ queryKey, queryFn: () => getBrowserLogin(taskId, itemId),
    refetchInterval: (query) => ["opening", "awaiting_user", "saving"].includes(query.state.data?.status || "") ? 1000 : false,
  });
  const open = useMutation({ mutationFn: () => openBrowserLogin(taskId, itemId),
    onSuccess: (session) => { client.setQueryData(queryKey, session); },
  });
  const save = useMutation({ mutationFn: async () => {
    if (!query.data) throw new Error("请先打开登录窗口。");
    const session = await controlBrowserLogin(taskId, itemId, query.data.id, "save");
    client.setQueryData(queryKey, session);
    if (session.status === "saved") await onSaved?.();
  }, onSettled: () => client.invalidateQueries({ queryKey }) });
  const cancel = useMutation({ mutationFn: () => controlBrowserLogin(taskId, itemId, query.data!.id, "cancel"),
    onSuccess: (session) => { client.setQueryData(queryKey, session); },
  });
  const status = query.data?.status;
  const active = ["opening", "awaiting_user", "saving"].includes(status || "");
  const error = open.error || save.error || cancel.error || query.error;
  return <Alert type="info" showIcon className="form-alert" message="后台采集需要登录？在独立窗口完成接管"
    description={<div>
      <p>点击后会打开后台所在电脑的独立浏览器窗口。请在窗口中手动登录或完成验证，再回到这里保存会话。账号信息不会发送给 AI。</p>
      <Space wrap>
        {!active && <Button onClick={() => open.mutate()} loading={open.isPending} disabled={query.isLoading}>打开登录窗口</Button>}
        {status === "opening" && <span>正在打开窗口，请稍候……</span>}
        {status === "awaiting_user" && <span>窗口已打开，等待你完成登录。</span>}
        {(status === "awaiting_user" || status === "saved") && <Button type="primary"
          loading={save.isPending} onClick={() => save.mutate()}>
          {onSaved ? (status === "saved" ? "使用已保存会话继续研究" : "已完成登录，保存并继续研究") : "已完成登录，保存会话"}
        </Button>}
        {active && <Button onClick={() => cancel.mutate()} loading={cancel.isPending}
          disabled={save.isPending || status === "saving"}>关闭登录窗口</Button>}
      </Space>
      {status === "saved" && <p>会话已私下保存；是否登录有效将由下一次商品访问核实。</p>}
      {status === "expired" && <p>登录窗口已等待 10 分钟并关闭，可重新打开。</p>}
      {status === "cancelled" && <p>登录窗口已关闭，未保存新会话。</p>}
      {query.data?.error && <p role="alert">{query.data.error}</p>}
      {error instanceof Error && <p role="alert">{error.message}</p>}
    </div>} />;
}
