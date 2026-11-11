import { useEffect, useRef, useState } from "react";
import { App, Button, Collapse, Input, InputNumber, Progress, Space, Tag, Typography } from "antd";
import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SendOutlined,
  ToolOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { AgentTraceEntry, Book, GenerationTask } from "../api/types";
import BrandMark from "../components/BrandMark";
import { TASK_STATUS_TAG, taskStageLabel } from "../constants/taskStatus";
import { usePolling } from "../hooks/usePolling";
import { paths } from "../router/paths";
import { startAgentWithFallback } from "../utils/agentFallback";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

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

function SystemNote({ text, danger }: { text: string; danger?: boolean }) {
  return <div className={`nf-note${danger ? " nf-note--danger" : ""}`}>—— {text} ——</div>;
}

function TraceBubble({ entry }: { entry: AgentTraceEntry }) {
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
}

export default function AgentChat() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message } = App.useApp();

  const [bookId, setBookId] = useState<string | null>(searchParams.get("bookId"));
  const [taskId, setTaskId] = useState<string | null>(searchParams.get("taskId"));
  const [book, setBook] = useState<Book | null>(null);
  const [task, setTask] = useState<GenerationTask | null>(null);
  const [trace, setTrace] = useState<AgentTraceEntry[]>([]);

  const [brief, setBrief] = useState("");
  const [targetWords, setTargetWords] = useState(100000);
  const [submitting, setSubmitting] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!bookId) return;
    api
      .getBook(bookId)
      .then(setBook)
      .catch(() => {});
  }, [bookId]);

  const loadProgress = () => {
    if (!taskId) return;
    api
      .getTask(taskId)
      .then((t) => {
        setTask(t);
        if (!bookId) setBookId(t.book_id);
      })
      .catch((err: unknown) => message.error(errMessage(err)));
    api
      .getTaskTrace(taskId)
      .then(setTrace)
      .catch(() => {});
    const id = bookId ?? searchParams.get("bookId");
    if (id) {
      api
        .getBook(id)
        .then(setBook)
        .catch(() => {});
    }
  };

  const running = task ? task.status === "running" || task.status === "pending" : taskId !== null;
  usePolling(loadProgress, 2500, taskId !== null && running);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [trace, task?.status]);

  const onSubmit = async () => {
    const text = brief.trim();
    if (!text) return;
    setSubmitting(true);
    try {
      const { result, usedFallback } = await startAgentWithFallback(
        () => api.startAgentGeneration({ brief: text, target_words: targetWords }),
        () => api.startNovelGeneration({ brief: text, target_words: targetWords })
      );
      if (usedFallback) {
        message.info("当前模型不支持自主决策,已自动切换为固定流程生成");
      }
      setBook(result.book);
      setBookId(result.book.id);
      setTaskId(result.task.id);
      setTask(result.task);
      setSearchParams({ bookId: result.book.id, taskId: result.task.id });
    } catch (err) {
      message.error(errMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!taskId) {
    return (
      <div className="nf-page nf-atelier">
        <div className="nf-atelier__nav">
          <Button
            className="nf-ghost"
            icon={<ArrowLeftOutlined />}
            type="text"
            onClick={() => navigate(paths.home)}
          />
          <BrandMark compact onClick={() => navigate(paths.home)} />
        </div>
        <div className="nf-atelier__stage">
          <h1>AI 自主创作</h1>
          <Typography.Paragraph>
            你只用说故事从哪起。设定、大纲、正文，它自己写完。
          </Typography.Paragraph>
          <div className="nf-composer">
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder="例如:少年林逸在灵气复苏之夜觉醒上古血脉,从宗门杂役一步步踏上与妖族争锋的路。"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              variant="borderless"
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  onSubmit();
                }
              }}
            />
            <div className="nf-composer__bar">
              <Space>
                <Typography.Text type="secondary">目标字数</Typography.Text>
                <InputNumber
                  min={1000}
                  step={10000}
                  value={targetWords}
                  onChange={(v) => setTargetWords(v ?? 100000)}
                />
              </Space>
              <Button
                type="primary"
                shape="circle"
                size="large"
                icon={<SendOutlined />}
                loading={submitting}
                disabled={!brief.trim()}
                onClick={onSubmit}
              />
            </div>
          </div>
        </div>
      </div>
    );
  }

  const statusTag = task ? TASK_STATUS_TAG[task.status] : undefined;
  const isAgentTask = task?.type === "agent";

  return (
    <div className="nf-session">
      <div className="nf-session__header">
        <Button
          className="nf-ghost"
          icon={<ArrowLeftOutlined />}
          type="text"
          onClick={() => navigate(paths.home)}
        />
        <BrandMark compact onClick={() => navigate(paths.home)} />
        <span className="nf-session__title">{book?.title ?? "AI 自主创作"}</span>
        {statusTag && <Tag color={statusTag.color}>{statusTag.text}</Tag>}
        <div style={{ flex: 1 }} />
        {task && task.status === "running" && (
          <Progress percent={task.progress} size="small" style={{ width: 140 }} />
        )}
        {bookId && (
          <Button
            className="nf-ghost"
            onClick={() =>
              navigate(running ? paths.bookTasks(bookId, taskId) : paths.book(bookId, taskId))
            }
          >
            查看书籍
          </Button>
        )}
      </div>

      <div ref={scrollRef} className="nf-session__body">
        <div className="nf-trace">
          {isAgentTask ? (
            <>
              {trace.length === 0 && (
                <Typography.Text type="secondary">AI 正在思考第一步...</Typography.Text>
              )}
              {trace.map((entry) => (
                <TraceBubble key={entry.id} entry={entry} />
              ))}
            </>
          ) : (
            task && (
              <SystemNote
                text={`按固定流程生成中... ${taskStageLabel(task.checkpoint_json) || ""} ${task.progress}%`}
              />
            )
          )}
          {task?.status === "done" && <SystemNote text="创作完成" />}
          {task?.status === "error" && <SystemNote danger text={`生成失败:${task.error}`} />}
        </div>
      </div>
    </div>
  );
}
