import { useEffect, useRef, useState } from "react";
import { App, Button, Card, Empty, Input, Space, Tag, Typography } from "antd";
import { SaveOutlined, ThunderboltOutlined, StopOutlined } from "@ant-design/icons";
import { api, sseEndpoints } from "../api";
import { sseFetch } from "../api/client";
import type { Chapter } from "../api/types";
import { useAppStore } from "../store";

interface Props {
  bookId: number;
  chapterId: number | null;
}

export default function ChapterEditor({ chapterId }: Props) {
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [body, setBody] = useState("");
  const [generating, setGenerating] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const { message } = App.useApp();
  const refreshTree = useAppStore((s) => s.refreshTree);

  useEffect(() => {
    // 父组件用 key={chapterId} 挂载本组件,切换章节时会整体重建,
    // 这里只需要处理"有 chapterId"的取数逻辑,不需要再手动重置状态。
    if (!chapterId) return;
    api.getChapter(chapterId).then(setChapter);
    api.getContent(chapterId).then((c) => setBody(c.body));
  }, [chapterId]);

  if (!chapterId || !chapter) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
        <Empty description="从左侧选择一个章节" />
      </div>
    );
  }

  const saveMeta = async (patch: Partial<Chapter>) => {
    const updated = await api.updateChapter(chapter.id, patch);
    setChapter(updated);
    refreshTree();
  };

  const saveBody = async () => {
    await api.saveContent(chapter.id, body);
    message.success("已保存正文");
    refreshTree();
  };

  const generate = () => {
    setGenerating(true);
    setBody("");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    sseFetch(
      sseEndpoints.chapter,
      { chapter_id: chapter.id },
      {
        signal: ctrl.signal,
        onEvent: (event, data) => {
          if (event === "token") {
            setBody((prev) => prev + data);
          } else if (event === "done") {
            setGenerating(false);
            api.getChapter(chapter.id).then(setChapter);
            refreshTree();
          } else if (event === "error") {
            message.error(data || "生成失败");
            setGenerating(false);
          }
        },
        onError: (err) => {
          if ((err as { name?: string })?.name !== "AbortError") {
            message.error(err instanceof Error ? err.message : "生成失败");
          }
          setGenerating(false);
        },
      }
    );
  };

  const stop = () => {
    abortRef.current?.abort();
    setGenerating(false);
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Input
          value={chapter.title}
          onChange={(e) => setChapter({ ...chapter, title: e.target.value })}
          onBlur={() => saveMeta({ title: chapter.title })}
          style={{ fontSize: 18, fontWeight: 600, width: 400 }}
          variant="borderless"
        />
        <Space>
          <Tag color="blue">{chapter.status}</Tag>
          {generating ? (
            <Button danger icon={<StopOutlined />} onClick={stop}>
              停止
            </Button>
          ) : (
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={generate}>
              生成正文
            </Button>
          )}
          <Button icon={<SaveOutlined />} onClick={saveBody}>
            保存
          </Button>
        </Space>
      </Space>

      <Card size="small" title="本章大纲" style={{ marginBottom: 16 }}>
        <Input.TextArea
          value={chapter.outline}
          onChange={(e) => setChapter({ ...chapter, outline: e.target.value })}
          onBlur={() => saveMeta({ outline: chapter.outline })}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="本章梗概:涉及人物、地点、推进的剧情线程..."
        />
      </Card>

      <Typography.Text type="secondary">正文({body.length} 字)</Typography.Text>
      <Input.TextArea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        autoSize={{ minRows: 18 }}
        style={{ marginTop: 8, fontSize: 15, lineHeight: 1.9 }}
        placeholder="点击“生成正文”让 AI 基于大纲与记忆写作,或直接手写。"
      />
    </div>
  );
}
