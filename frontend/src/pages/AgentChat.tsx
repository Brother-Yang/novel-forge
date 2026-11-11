import { useEffect, useState } from "react";
import { App, Button, Input, InputNumber, Progress, Space, Tag, Typography } from "antd";
import { ArrowLeftOutlined, SendOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { NovelGenerationResult } from "../api/types";
import AgentTrace, { SystemNote } from "../components/AgentTrace";
import BrandMark from "../components/BrandMark";
import { isTaskActive, TASK_STATUS_TAG, taskStageLabel } from "../constants/taskStatus";
import { useBook } from "../hooks/useBook";
import { usePendingAction } from "../hooks/usePendingAction";
import { paths } from "../router/paths";
import { startAgentWithFallback } from "../utils/agentFallback";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

export default function AgentChat() {
  const [searchParams, setSearchParams] = useSearchParams();
  const taskId = searchParams.get("taskId");
  const [started, setStarted] = useState<NovelGenerationResult>();
  const [draft, setDraft] = useState({ brief: "", targetWords: 100000 });

  return taskId ? (
    <AgentSession
      key={taskId}
      taskId={taskId}
      initialResult={started?.task.id === taskId ? started : undefined}
    />
  ) : (
    <AgentComposer
      draft={draft}
      onDraftChange={setDraft}
      onStarted={(result) => {
        setStarted(result);
        setSearchParams({ bookId: result.book.id, taskId: result.task.id });
      }}
    />
  );
}

interface AgentComposerProps {
  draft: { brief: string; targetWords: number };
  onDraftChange: (draft: AgentComposerProps["draft"]) => void;
  onStarted: (result: NovelGenerationResult) => void;
}

function AgentComposer({ draft, onDraftChange, onStarted }: AgentComposerProps) {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { brief, targetWords } = draft;
  const { pending: submitting, run } = usePendingAction();

  const onSubmit = () => {
    const text = brief.trim();
    if (!text) return;
    void run(async (isCurrent) => {
      try {
        const { result, usedFallback } = await startAgentWithFallback(
          () => api.startAgentGeneration({ brief: text, target_words: targetWords }),
          () => api.startNovelGeneration({ brief: text, target_words: targetWords })
        );
        if (!isCurrent()) return;
        if (usedFallback) {
          message.info("当前模型不支持自主决策,已自动切换为固定流程生成");
        }
        onStarted(result);
      } catch (err) {
        if (isCurrent()) message.error(errMessage(err));
      }
    });
  };

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
            onChange={(e) => onDraftChange({ ...draft, brief: e.target.value })}
            variant="borderless"
            onPressEnter={(e) => {
              if (e.shiftKey || e.nativeEvent.isComposing) return;
              e.preventDefault();
              onSubmit();
            }}
          />
          <div className="nf-composer__bar">
            <Space>
              <Typography.Text type="secondary">目标字数</Typography.Text>
              <InputNumber
                min={1000}
                step={10000}
                value={targetWords}
                onChange={(v) => onDraftChange({ ...draft, targetWords: v ?? 100000 })}
              />
            </Space>
            <Button
              type="primary"
              shape="circle"
              size="large"
              icon={<SendOutlined />}
              loading={submitting}
              disabled={submitting || !brief.trim()}
              onClick={onSubmit}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

interface AgentSessionProps {
  taskId: string;
  initialResult?: NovelGenerationResult;
}

function AgentSession({ taskId, initialResult }: AgentSessionProps) {
  const navigate = useNavigate();
  const [task, setTask] = useState(initialResult?.task);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function loadProgress() {
      let running = true;
      try {
        const updatedTask = await api.getTask(taskId, request.signal);
        if (request.signal.aborted) return;
        setTask(updatedTask);
        setTaskError(null);
        running = isTaskActive(updatedTask.status);
      } catch (error) {
        if (request.signal.aborted) return;
        setTaskError(errMessage(error));
      }
      if (running && !request.signal.aborted) timer = setTimeout(loadProgress, 2500);
    }

    void loadProgress();
    return () => {
      request.abort();
      clearTimeout(timer);
    };
  }, [taskId, retryCount]);

  const bookId = task?.book_id;
  const running = isTaskActive(task?.status);
  const {
    book,
    error: bookError,
    loading: bookLoading,
    retry: retryBook,
  } = useBook(bookId, running, initialResult?.book);
  const statusTag = task ? TASK_STATUS_TAG[task.status] : undefined;
  const endNote =
    task?.status === "done" ? (
      <SystemNote text="创作完成" />
    ) : task?.status === "error" ? (
      <SystemNote danger text={`生成失败:${task.error}`} />
    ) : null;

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

      {bookError && (
        <div className="nf-trace__footer">
          <span role="alert">加载书籍失败：{bookError}</span>
          <Button aria-label="重试加载书籍" loading={bookLoading} onClick={retryBook}>
            重试加载书籍
          </Button>
        </div>
      )}
      {taskError && (
        <div className="nf-trace__footer">
          <span role="alert">加载任务失败：{taskError}</span>
          <Button
            onClick={() => {
              setTaskError(null);
              setRetryCount((count) => count + 1);
            }}
          >
            重试加载任务
          </Button>
        </div>
      )}
      {task?.type === "agent" ? (
        <AgentTrace taskId={taskId} running={running}>
          {endNote}
        </AgentTrace>
      ) : (
        <div className="nf-session__body">
          <div className="nf-trace">
            {task ? (
              <SystemNote
                text={`按固定流程生成中... ${taskStageLabel(task.checkpoint_json) || ""} ${task.progress}%`}
              />
            ) : (
              !taskError && <span role="status">正在加载任务…</span>
            )}
            {endNote}
          </div>
        </div>
      )}
    </div>
  );
}
