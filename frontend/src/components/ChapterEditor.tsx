import { useEffect, useRef, useState } from "react";
import { App, Button, Card, Input, Space, Tag } from "antd";
import { SaveOutlined, ThunderboltOutlined, StopOutlined } from "@ant-design/icons";
import { api, sseEndpoints } from "../api";
import { sseFetch } from "../api/client";
import type { Chapter } from "../api/types";
import { CHAPTER_STATUS_LABEL } from "../constants/chapterStatus";
import { useAppStore } from "../store";

interface Props {
  bookId: string;
  chapterId: string | null;
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
      <div className="nf-empty nf-empty--fill">
        <span className="nf-empty__glyph">卷</span>
        <p>从左侧选择一个章节</p>
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
    <div className="nf-editor">
      <div className="nf-editor__toolbar">
        <Input
          className="nf-editor__title"
          value={chapter.title}
          onChange={(e) => setChapter({ ...chapter, title: e.target.value })}
          onBlur={() => saveMeta({ title: chapter.title })}
          variant="borderless"
        />
        <Space>
          <Tag color={chapter.status === "done" ? "success" : "default"}>
            {CHAPTER_STATUS_LABEL[chapter.status] ?? chapter.status}
          </Tag>
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
      </div>

      <Card className="nf-editor__outline" size="small" title="本章大纲">
        <Input.TextArea
          value={chapter.outline}
          onChange={(e) => setChapter({ ...chapter, outline: e.target.value })}
          onBlur={() => saveMeta({ outline: chapter.outline })}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="本章梗概:涉及人物、地点、推进的剧情线程..."
          variant="borderless"
        />
      </Card>

      <span className="nf-editor__count">正文 {body.length} 字</span>
      <Input.TextArea
        className={`nf-editor__body${generating ? " nf-editor__body--live" : ""}`}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        autoSize={{ minRows: 18 }}
        placeholder="点击“生成正文”让 AI 基于大纲与记忆写作,或直接手写。"
      />
    </div>
  );
}
