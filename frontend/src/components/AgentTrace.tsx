import { memo, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Button, Collapse, Space, Spin, Typography } from "antd";
import {
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ToolOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { AgentTraceEntry } from "../api/types";
import { useAgentTrace } from "../hooks/useAgentTrace";

function tryParseJson(text: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function decisionArgs(payload: string): string {
  const idx = payload.indexOf("(");
  if (idx === -1) return "";
  const parsed = tryParseJson(payload.slice(idx + 1, payload.lastIndexOf(")")));
  return parsed
    ? JSON.stringify(parsed, null, 2)
    : payload.slice(idx + 1, payload.lastIndexOf(")"));
}

function observationOk(payload: string): boolean {
  const parsed = tryParseJson(payload);
  return parsed?.ok !== false;
}

function observationSummary(payload: string): string {
  const parsed = tryParseJson(payload);
  if (!parsed) return payload.slice(0, 60);
  if (typeof parsed.error === "string") return parsed.error;
  if (typeof parsed.summary === "string") return parsed.summary;
  return parsed.ok === false ? "执行失败" : "执行成功";
}

interface SystemNoteProps {
  text: string;
  danger?: boolean;
}

export function SystemNote({ text, danger }: SystemNoteProps) {
  return <div className={`nf-note${danger ? " nf-note--danger" : ""}`}>—— {text} ——</div>;
}

interface TraceBubbleProps {
  entry: AgentTraceEntry;
}

const TraceBubble = memo(function TraceBubble({ entry }: TraceBubbleProps) {
  if (entry.kind === "log") return <SystemNote text={entry.payload} />;

  if (entry.kind === "thought") {
    return (
      <div className="nf-bubble nf-bubble--thought">
        <Space size={6} style={{ marginBottom: 2 }}>
          <BulbOutlined />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            思考
          </Typography.Text>
        </Space>
        <div>{entry.payload}</div>
      </div>
    );
  }

  if (entry.kind === "decision") {
    return (
      <div className="nf-bubble nf-bubble--decision">
        <Space size={6} style={{ marginBottom: 4 }}>
          <ToolOutlined style={{ color: "#c23b22" }} />
          <Typography.Text strong>调用工具:{entry.tool_name ?? "unknown"}</Typography.Text>
        </Space>
        <pre>{decisionArgs(entry.payload)}</pre>
      </div>
    );
  }

  if (entry.kind === "observation") {
    const ok = observationOk(entry.payload);
    return (
      <div className="nf-obs">
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: "o",
              label: (
                <Space size={6}>
                  {ok ? (
                    <CheckCircleOutlined style={{ color: "#5a7d4e" }} />
                  ) : (
                    <CloseCircleOutlined style={{ color: "#c23b22" }} />
                  )}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {observationSummary(entry.payload)}
                  </Typography.Text>
                </Space>
              ),
              children: (
                <pre>{JSON.stringify(tryParseJson(entry.payload) ?? entry.payload, null, 2)}</pre>
              ),
            },
          ]}
        />
      </div>
    );
  }

  return (
    <div className="nf-bubble nf-bubble--error">
      <Space size={6}>
        <WarningOutlined style={{ color: "#c23b22" }} />
        <Typography.Text type="danger">{entry.payload}</Typography.Text>
      </Space>
    </div>
  );
});

interface AgentTraceProps {
  taskId: string;
  running: boolean;
  children: ReactNode;
}

export default function AgentTrace({ taskId, running, children }: AgentTraceProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const entriesRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<{ element: HTMLElement; top: number } | null>(null);
  const [following, setFollowing] = useState(true);
  const [lastSeen, setLastSeen] = useState(0);
  const {
    entries,
    initialized,
    hasEarlier,
    loadingEarlier,
    earlierError,
    latestError,
    loadEarlier,
    retryLatest,
  } = useAgentTrace(taskId, running, () => {
    const element = entriesRef.current?.firstElementChild;
    if (element instanceof HTMLElement) anchorRef.current = { element, top: element.offsetTop };
  });
  const lastSequence = entries[entries.length - 1]?.sequence ?? 0;

  useLayoutEffect(() => {
    const body = scrollRef.current;
    if (!body) return;
    const anchor = anchorRef.current;
    if (anchor) {
      body.scrollTop += anchor.element.offsetTop - anchor.top;
      anchorRef.current = null;
    }
    if (following) body.scrollTop = Math.max(0, body.scrollHeight - body.clientHeight);
  }, [entries, following, running]);

  function stopFollowing() {
    if (!following) return;
    setLastSeen(lastSequence);
    setFollowing(false);
  }

  function handleScroll() {
    const body = scrollRef.current;
    if (!body) return;
    const remaining = body.scrollHeight - body.clientHeight - body.scrollTop;
    if (remaining <= 24) setFollowing(true);
    else stopFollowing();
  }

  const hasNewer = !following && lastSequence > lastSeen;
  return (
    <>
      <div
        ref={scrollRef}
        className="nf-session__body nf-session__body--trace"
        role="region"
        aria-label="执行轨迹"
        tabIndex={0}
        onScroll={handleScroll}
      >
        <div className="nf-trace">
          {hasEarlier && (
            <div className="nf-trace__history">
              <Button
                loading={loadingEarlier}
                onClick={() => {
                  stopFollowing();
                  void loadEarlier();
                }}
              >
                {earlierError ? "重试加载更早轨迹" : "加载更早轨迹"}
              </Button>
              {earlierError && <span role="alert">加载更早轨迹失败：{earlierError}</span>}
            </div>
          )}
          {!initialized && !latestError && (
            <Space role="status">
              <Spin size="small" />
              正在加载轨迹…
            </Space>
          )}
          {initialized && entries.length === 0 && (
            <Typography.Text type="secondary">
              {running ? "AI 正在思考第一步..." : "暂无执行轨迹"}
            </Typography.Text>
          )}
          <div ref={entriesRef}>
            {entries.map((entry) => (
              <TraceBubble key={entry.id} entry={entry} />
            ))}
          </div>
          {children}
        </div>
      </div>
      {(latestError || hasNewer) && (
        <div className="nf-trace__footer">
          {latestError && (
            <Space wrap>
              <span role="alert">加载轨迹失败：{latestError}</span>
              <Button onClick={retryLatest}>重试加载轨迹</Button>
            </Space>
          )}
          {hasNewer && <Button onClick={() => setFollowing(true)}>有新轨迹，跳到最新</Button>}
        </div>
      )}
    </>
  );
}
