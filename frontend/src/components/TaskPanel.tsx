import { useEffect, useRef, useState } from "react";
import {
  App,
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
import { api, sseEndpoints } from "../api";
import { sseFetch } from "../api/client";
import type { BookTree, GenerationTask } from "../api/types";
import { useAppStore } from "../store";
import { TASK_STATUS_TAG, TASK_TYPE_LABEL, taskStageLabel } from "../constants/taskStatus";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

export default function TaskPanel({ bookId }: { bookId: string }) {
  const [log, setLog] = useState("");
  const [stage, setStage] = useState<string | null>(null);
  const [tree, setTree] = useState<BookTree | null>(null);
  const [volumeId, setVolumeId] = useState<number | undefined>();
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const { message } = App.useApp();
  const refreshTree = useAppStore((s) => s.refreshTree);

  const loadTasks = () =>
    api
      .listTasks(bookId)
      .then(setTasks)
      .catch((err: unknown) => message.error(errMessage(err)));
  const loadTree = () =>
    api
      .getTree(bookId)
      .then(setTree)
      .catch((err: unknown) => message.error(errMessage(err)));

  useEffect(() => {
    loadTree();
    loadTasks();
    const t = setInterval(loadTasks, 2500);
    return () => clearInterval(t);
    // message 来自 antd context,轮询只跟 bookId 走。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [log]);

  const busy = tasks.some((t) => t.status === "running" || t.status === "paused");

  const runStage = (name: string, path: string, body: unknown) => {
    setStage(name);
    setLog(`【${name}】开始...\n`);
    sseFetch(path, body, {
      onEvent: (event, data) => {
        if (event === "token") setLog((p) => p + data);
        else if (event === "log") setLog((p) => p + `\n${data}\n`);
        else if (event === "done") {
          setLog((p) => p + `\n【${name}】完成。\n`);
          setStage(null);
          refreshTree();
          loadTree();
        } else if (event === "error") {
          setLog((p) => p + `\n错误:${data}\n`);
          setStage(null);
        }
      },
      onError: (err) => {
        setLog((p) => p + `\n错误:${err instanceof Error ? err.message : String(err)}\n`);
        setStage(null);
      },
    });
  };

  const startNovel = async () => {
    try {
      await api.startNovelFromBook(bookId);
      message.success("已启动全书自动生成");
      loadTasks();
    } catch (err) {
      message.error(errMessage(err));
    }
  };

  const startBatch = async () => {
    try {
      await api.startBookGeneration(bookId);
      message.success("已启动全书批量生成正文");
      loadTasks();
    } catch (err) {
      message.error(errMessage(err));
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <Card title="一键全自动生成" style={{ marginBottom: 16 }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          按世界观 → 人物 → 大纲 → 章纲 →
          正文自动跑完全书。已有设定/人物/卷/章会跳过,中断后可继续或重试。
        </Typography.Paragraph>
        <Button type="primary" disabled={busy} onClick={startNovel}>
          一键全自动生成
        </Button>
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
                  <Button disabled={busy} onClick={startBatch}>
                    5. 全书批量生成正文(后台任务)
                  </Button>
                </Space>
              </>
            ),
          },
        ]}
      />

      <Card title="实时日志" style={{ marginBottom: 16 }}>
        <div
          ref={logRef}
          style={{
            height: 220,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            background: "#0d1117",
            color: "#d1d5da",
            padding: 12,
            borderRadius: 6,
            fontFamily: "monospace",
            fontSize: 13,
          }}
        >
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
                  t.status === "running" && (
                    <Button
                      key="p"
                      size="small"
                      onClick={() =>
                        api
                          .pauseTask(t.id)
                          .then(loadTasks)
                          .catch((err: unknown) => message.error(errMessage(err)))
                      }
                    >
                      暂停
                    </Button>
                  ),
                  t.status === "paused" && (
                    <Button
                      key="r"
                      size="small"
                      onClick={() =>
                        api
                          .resumeTask(t.id)
                          .then(loadTasks)
                          .catch((err: unknown) => message.error(errMessage(err)))
                      }
                    >
                      继续
                    </Button>
                  ),
                  t.status === "error" && (
                    <Button
                      key="rt"
                      size="small"
                      onClick={() =>
                        api
                          .retryTask(t.id)
                          .then(loadTasks)
                          .catch((err: unknown) => message.error(errMessage(err)))
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
