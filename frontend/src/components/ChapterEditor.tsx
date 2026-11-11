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

type MetaField = "title" | "outline";

export default function ChapterEditor({ chapterId }: Props) {
  const [editingId, setEditingId] = useState(chapterId);
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [body, setBody] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [savingBody, setSavingBody] = useState(false);
  const [savingFields, setSavingFields] = useState<MetaField[]>([]);
  const scope = useRef(0);
  const bodySave = useRef(false);
  const metaSaves = useRef(new Set<MetaField>());
  const abortRef = useRef<AbortController | null>(null);
  const generationVersion = useRef(0);
  const { message } = App.useApp();
  const refreshTree = useAppStore((s) => s.refreshTree);

  if (editingId !== chapterId) {
    setEditingId(chapterId);
    setChapter(null);
    setBody("");
    setLoadFailed(false);
    setGenerating(false);
    setSavingBody(false);
    setSavingFields([]);
  }

  useEffect(() => {
    const owner = ++scope.current;
    const pendingMetadata = metaSaves.current;
    if (chapterId) {
      // 两部分均成功后才开放编辑，避免把尚未读取的正文当成空稿提交。
      void Promise.all([api.getChapter(chapterId), api.getContent(chapterId)])
        .then(([data, content]) => {
          if (scope.current !== owner) return;
          setChapter(data);
          setBody(content.body);
          setLoadFailed(false);
        })
        .catch((err: unknown) => {
          if (scope.current !== owner) return;
          setLoadFailed(true);
          message.error(err instanceof Error ? err.message : "章节加载失败");
        });
    }
    return () => {
      scope.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      bodySave.current = false;
      pendingMetadata.clear();
    };
  }, [chapterId, message]);

  if (!chapterId || chapter?.id !== chapterId) {
    return (
      <div className="nf-empty nf-empty--fill">
        <span className="nf-empty__glyph">卷</span>
        <p>
          {!chapterId
            ? "从左侧选择一个章节"
            : loadFailed
              ? "章节加载失败，请重新选择章节重试"
              : "正在加载章节..."}
        </p>
      </div>
    );
  }

  const saveMeta = async (field: MetaField) => {
    if (abortRef.current || metaSaves.current.has(field)) return;
    const owner = scope.current;
    metaSaves.current.add(field);
    setSavingFields([...metaSaves.current]);
    try {
      await api.updateChapter(chapter.id, { [field]: chapter[field] });
      // 本地字段已经是草稿，不用整条服务器响应覆盖其他正在编辑的字段。
      if (scope.current === owner) refreshTree();
    } catch (err: unknown) {
      if (scope.current === owner)
        message.error(err instanceof Error ? err.message : "章节信息保存失败");
    } finally {
      if (scope.current === owner) {
        metaSaves.current.delete(field);
        setSavingFields([...metaSaves.current]);
      }
    }
  };

  const saveBody = async () => {
    if (bodySave.current || abortRef.current) return;
    const owner = scope.current;
    bodySave.current = true;
    setSavingBody(true);
    try {
      await api.saveContent(chapter.id, body);
      if (scope.current !== owner) return;
      message.success("已保存正文");
      refreshTree();
    } catch (err: unknown) {
      if (scope.current === owner)
        message.error(err instanceof Error ? err.message : "正文保存失败");
    } finally {
      if (scope.current === owner) {
        bodySave.current = false;
        setSavingBody(false);
      }
    }
  };

  const generate = () => {
    if (abortRef.current || bodySave.current || metaSaves.current.size > 0) return;
    const owner = scope.current;
    const version = ++generationVersion.current;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setGenerating(true);
    let generatedBody = "";
    const active = () => scope.current === owner && abortRef.current === ctrl;
    const finish = () => {
      abortRef.current = null;
      setGenerating(false);
    };
    void sseFetch(
      sseEndpoints.chapter,
      { chapter_id: chapter.id },
      {
        signal: ctrl.signal,
        onEvent: (event, data) => {
          if (!active()) return;
          if (event === "token") {
            generatedBody += data;
            // 无有效输出的失败必须保留原稿，避免把空白生成结果再次保存。
            if (generatedBody.trim()) setBody(generatedBody);
          } else if (event === "done") {
            finish();
            refreshTree();
            void api
              .getChapter(chapter.id)
              .then((updated) => {
                if (scope.current !== owner || generationVersion.current !== version) return;
                // 生成只改变状态、字数；标题和大纲仍由本地编辑状态拥有。
                setChapter((draft) =>
                  draft?.id === updated.id
                    ? { ...draft, status: updated.status, word_count: updated.word_count }
                    : draft
                );
              })
              .catch((err: unknown) => {
                if (scope.current === owner)
                  message.error(err instanceof Error ? err.message : "章节状态刷新失败");
              });
          } else if (event === "error") {
            message.error(data || "生成失败");
            finish();
          }
        },
        onError: (err) => {
          if (!active()) return;
          message.error(err instanceof Error ? err.message : "生成失败");
          finish();
        },
      }
    ).then(() => {
      if (active()) {
        message.error("生成连接已结束，请重新加载章节确认结果");
        finish();
      }
    });
  };

  const stop = () => {
    const ctrl = abortRef.current;
    abortRef.current = null;
    ctrl?.abort();
    setGenerating(false);
  };

  return (
    <div className="nf-editor">
      <div className="nf-editor__toolbar">
        <Input
          className="nf-editor__title"
          value={chapter.title}
          disabled={generating || savingFields.includes("title")}
          onChange={(e) => setChapter({ ...chapter, title: e.target.value })}
          onBlur={() => void saveMeta("title")}
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
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              disabled={savingBody || savingFields.length > 0}
              onClick={generate}
            >
              生成正文
            </Button>
          )}
          <Button
            icon={<SaveOutlined />}
            disabled={generating || savingBody}
            onClick={() => void saveBody()}
          >
            保存
          </Button>
        </Space>
      </div>

      <Card className="nf-editor__outline" size="small" title="本章大纲">
        <Input.TextArea
          value={chapter.outline}
          disabled={generating || savingFields.includes("outline")}
          onChange={(e) => setChapter({ ...chapter, outline: e.target.value })}
          onBlur={() => void saveMeta("outline")}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="本章梗概:涉及人物、地点、推进的剧情线程..."
          variant="borderless"
        />
      </Card>

      <span className="nf-editor__count">正文 {body.length} 字</span>
      <Input.TextArea
        className={`nf-editor__body${generating ? " nf-editor__body--live" : ""}`}
        value={body}
        disabled={generating}
        onChange={(e) => setBody(e.target.value)}
        autoSize={{ minRows: 18 }}
        placeholder="点击“生成正文”让 AI 基于大纲与记忆写作,或直接手写。"
      />
    </div>
  );
}
