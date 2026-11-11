import { useEffect, useRef, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Collapse,
  Divider,
  List,
  Progress,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { RobotOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api, sseEndpoints } from "../api";
import type { GenerationTask } from "../api/types";
import { paths } from "../router/paths";
import { useAppStore } from "../store";
import { startAgentWithFallback } from "../utils/agentFallback";
import {
  isTaskActive,
  TASK_START_ERROR,
  TASK_STATUS_TAG,
  TASK_TYPE_LABEL,
  taskStageLabel,
} from "../constants/taskStatus";
import MemoryPanel from "./MemoryPanel";
import { useGenerationStream } from "../hooks/useGenerationStream";
import { useTaskPanelData } from "../hooks/useTaskPanelData";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

interface TaskPanelProps {
  bookId: string;
  onGenerationActivityChange?: (active: boolean) => void;
}

export default function TaskPanel(props: TaskPanelProps) {
  return <BookTaskPanel key={props.bookId} {...props} />;
}

function BookTaskPanel({ bookId, onGenerationActivityChange }: TaskPanelProps) {
  const [log, setLog] = useState("");
  const [stageName, setStageName] = useState<string | null>(null);
  const [volumeId, setVolumeId] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const operationPending = useRef(false);
  const active = useRef(true);
  const stream = useGenerationStream(bookId);
  const stage = stream.running ? stageName : null;
  const {
    tasks,
    tree,
    tasksError,
    treeError,
    tasksLoaded,
    refreshTasks: loadTasks,
    refreshTree: loadTree,
  } = useTaskPanelData(bookId);
  const logRef = useRef<HTMLDivElement>(null);
  const { message } = App.useApp();
  const navigate = useNavigate();
  const refreshTree = useAppStore((s) => s.refreshTree);
  const taskActive = tasks.some((task) => isTaskActive(task.status));

  useEffect(() => {
    if (!taskActive || !onGenerationActivityChange) return;
    onGenerationActivityChange(true);
    return () => onGenerationActivityChange(false);
  }, [taskActive, onGenerationActivityChange]);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [log]);

  const busy =
    !tasksLoaded ||
    Boolean(tasksError) ||
    submitting ||
    stage !== null ||
    taskActive ||
    tasks.some((task) => task.status === "paused");

  const appendLog = (text: string) => setLog((previous) => (previous + text).slice(-50_000));

  const runStage = (name: string, path: string, body: unknown) => {
    if (busy || operationPending.current || stream.isRunning()) return;
    setStageName(name);
    setLog(`【${name}】开始...\n`);
    stream.start(path, body, {
      onEvent: (event, data) => {
        if (event === "token") appendLog(data);
        else if (event === "log") appendLog(`\n${data}\n`);
      },
      onDone: () => {
        appendLog(`\n【${name}】完成。\n`);
        void loadTasks();
        refreshTree();
        loadTree();
      },
      onError: (err) => {
        appendLog(`\n错误:${errMessage(err)}\n`);
        void loadTasks();
      },
    });
  };

  const submit = async (action: () => Promise<void>, controlTask = false) => {
    if ((!controlTask && busy) || operationPending.current || stream.isRunning()) return;
    operationPending.current = true;
    setSubmitting(true);
    try {
      await action();
    } catch (err) {
      if (active.current) message.error(errMessage(err));
    } finally {
      if (active.current) {
        operationPending.current = false;
        setSubmitting(false);
      }
    }
  };

  const reportStartFailure = (task: GenerationTask): boolean => {
    if (task.status !== "error") return false;
    message.error(task.error || TASK_START_ERROR);
    return true;
  };

  const startNovel = async () => {
    const task = await api.startNovelFromBook(bookId);
    if (!active.current) return;
    if (!reportStartFailure(task)) message.success("已启动全书自动生成");
    await loadTasks();
  };

  const startAgent = async () => {
    const { result: task, usedFallback } = await startAgentWithFallback(
      () => api.startAgentFromBook(bookId),
      () => api.startNovelFromBook(bookId)
    );
    if (!active.current) return;
    if (reportStartFailure(task)) {
      await loadTasks();
      return;
    }
    if (usedFallback) {
      message.info("当前模型不支持自主决策,已自动切换为固定流程生成");
    } else {
      message.success("已启动 AI 自主创作");
    }
    navigate(paths.agentChatFor(bookId, task.id));
  };

  const startBatch = async () => {
    const task = await api.startBookGeneration(bookId);
    if (!active.current) return;
    if (!reportStartFailure(task)) message.success("已启动全书批量生成正文");
    await loadTasks();
  };

  return (
    <div className="nf-tasks">
      <MemoryPanel bookId={bookId} />
      {tasksError && (
        <Alert
          type="error"
          message={tasksError}
          action={<Button onClick={loadTasks}>重试任务列表</Button>}
        />
      )}
      {treeError && (
        <Alert
          type="error"
          message={treeError}
          action={<Button onClick={loadTree}>重试卷目录</Button>}
        />
      )}
      <Card title="一键全自动生成" style={{ marginBottom: 16 }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          按世界观 → 人物 → 大纲 → 章纲 →
          正文自动跑完全书。完整阶段会保留，缺失内容会补齐；中断后可继续或重试。
        </Typography.Paragraph>
        <Space>
          <Button type="primary" disabled={busy} onClick={() => void submit(startNovel)}>
            一键全自动生成
          </Button>
          <Button disabled={busy} icon={<RobotOutlined />} onClick={() => void submit(startAgent)}>
            AI 自主创作(对话查看进度)
          </Button>
        </Space>
      </Card>

      <Collapse
        style={{ marginBottom: 16 }}
        items={[
          {
            key: "manual",
            label: "手工分层（生成后维护）",
            children: (
              <>
                <Space wrap>
                  <Button
                    disabled={busy}
                    loading={stage === "世界观"}
                    onClick={() =>
                      runStage("世界观", sseEndpoints.worldbuilding, { book_id: bookId })
                    }
                  >
                    1. 生成世界观
                  </Button>
                  <Button
                    disabled={busy}
                    loading={stage === "人物"}
                    onClick={() =>
                      runStage("人物", sseEndpoints.characters, { book_id: bookId, count: 5 })
                    }
                  >
                    2. 生成人物
                  </Button>
                  <Button
                    disabled={busy}
                    loading={stage === "全书大纲"}
                    onClick={() =>
                      runStage("全书大纲", sseEndpoints.outline, {
                        book_id: bookId,
                        volume_count: 3,
                      })
                    }
                  >
                    3. 生成全书大纲
                  </Button>
                </Space>
                <Divider style={{ margin: "16px 0" }} />
                <Space wrap>
                  <Select
                    placeholder="选择卷"
                    style={{ width: 200 }}
                    value={volumeId}
                    onChange={setVolumeId}
                    options={tree?.volumes.map((v) => ({ value: v.id, label: v.title }))}
                  />
                  <Button
                    disabled={!volumeId || busy}
                    loading={stage === "章节大纲"}
                    onClick={() =>
                      runStage("章节大纲", sseEndpoints.chapterOutlines, {
                        volume_id: volumeId,
                        chapter_count: 10,
                      })
                    }
                  >
                    4. 生成本卷章节大纲
                  </Button>
                  <Button disabled={busy} onClick={() => void submit(startBatch)}>
                    5. 全书批量生成正文(后台任务)
                  </Button>
                </Space>
              </>
            ),
          },
        ]}
      />

      <Card title="实时日志" extra="保留最近 50,000 字符" style={{ marginBottom: 16 }}>
        <div ref={logRef} className="nf-log">
          {log || "等待任务..."}
        </div>
      </Card>

      <Card title="生成任务">
        <List
          dataSource={tasks}
          locale={{ emptyText: "暂无任务" }}
          renderItem={(t) => {
            const s = TASK_STATUS_TAG[t.status] ?? { color: "default", text: t.status };
            const typeLabel = TASK_TYPE_LABEL[t.type] ?? t.type;
            const stageLabel = taskStageLabel(t.checkpoint_json);
            return (
              <List.Item
                actions={[
                  t.type === "agent" && (
                    <Button
                      key="chat"
                      size="small"
                      onClick={() => navigate(paths.agentChatFor(bookId, t.id))}
                    >
                      查看对话
                    </Button>
                  ),
                  t.status === "running" && (
                    <Button
                      key="p"
                      size="small"
                      disabled={submitting || stage !== null}
                      onClick={() =>
                        void submit(async () => {
                          await api.pauseTask(t.id);
                          if (active.current) await loadTasks();
                        }, true)
                      }
                    >
                      暂停
                    </Button>
                  ),
                  t.status === "paused" && (
                    <Button
                      key="r"
                      size="small"
                      disabled={submitting || stage !== null}
                      onClick={() =>
                        void submit(async () => {
                          await api.resumeTask(t.id);
                          if (active.current) await loadTasks();
                        }, true)
                      }
                    >
                      继续
                    </Button>
                  ),
                  t.status === "error" && (
                    <Button
                      key="rt"
                      size="small"
                      disabled={submitting || stage !== null}
                      onClick={() =>
                        void submit(async () => {
                          await api.retryTask(t.id);
                          if (active.current) await loadTasks();
                        }, true)
                      }
                    >
                      重试
                    </Button>
                  ),
                ].filter(Boolean)}
              >
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Space>
                    <Tag color={s.color}>{s.text}</Tag>
                    <Typography.Text>
                      #{t.id} {typeLabel}
                      {stageLabel ? ` · ${stageLabel}` : ""}
                    </Typography.Text>
                  </Space>
                  <Progress
                    percent={t.progress}
                    size="small"
                    status={t.status === "error" ? "exception" : undefined}
                  />
                  {t.error && <Typography.Text type="danger">{t.error}</Typography.Text>}
                </Space>
              </List.Item>
            );
          }}
        />
      </Card>
    </div>
  );
}
