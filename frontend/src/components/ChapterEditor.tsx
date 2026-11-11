import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Card, Input, Space, Tag } from "antd";
import { SaveOutlined, ThunderboltOutlined, StopOutlined } from "@ant-design/icons";
import { api, sseEndpoints } from "../api";
import { ApiError } from "../api/client";
import type { Chapter } from "../api/types";
import { CHAPTER_STATUS_LABEL } from "../constants/chapterStatus";
import { useGenerationStream } from "../hooks/useGenerationStream";
import { useAppStore } from "../store";

interface Props {
  bookId: string;
  chapterId: string | null;
}

type MetaField = "title" | "outline";

export default function ChapterEditor({ bookId, chapterId }: Props) {
  return <ChapterDraft key={`${bookId}:${chapterId ?? "none"}`} chapterId={chapterId} />;
}

function ChapterDraft({ chapterId }: Pick<Props, "chapterId">) {
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [body, setBody] = useState("");
  const [contentVersion, setContentVersion] = useState<string | null>(null);
  const [contentConflict, setContentConflict] = useState(false);
  const [confirmingContent, setConfirmingContent] = useState(false);
  const [reloadingContent, setReloadingContent] = useState(false);
  const [confirmationFailed, setConfirmationFailed] = useState(false);
  const [generationPreview, setGenerationPreview] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [savingBody, setSavingBody] = useState(false);
  const [savingFields, setSavingFields] = useState<MetaField[]>([]);
  const scope = useRef(0);
  const bodySave = useRef(false);
  const metaSaves = useRef(new Set<MetaField>());
  const requestRef = useRef<AbortController | null>(null);
  const generationVersion = useRef(0);
  const contentRead = useRef(false);
  const unconfirmedBody = useRef<string | null>(null);
  const { running: generating, isRunning, start, cancel } = useGenerationStream(chapterId);
  const { message, modal } = App.useApp();
  const refreshTree = useAppStore((s) => s.refreshTree);

  useEffect(() => {
    const owner = ++scope.current;
    const request = new AbortController();
    requestRef.current = request;
    const pendingMetadata = metaSaves.current;
    if (chapterId) {
      // 两部分均成功后才开放编辑，避免把尚未读取的正文当成空稿提交。
      void Promise.all([
        api.getChapter(chapterId, request.signal),
        api.getContent(chapterId, request.signal),
      ])
        .then(([data, content]) => {
          if (scope.current !== owner || request.signal.aborted) return;
          setChapter(data);
          setBody(content.body);
          setContentVersion(content.version);
          setLoadFailed(false);
        })
        .catch((err: unknown) => {
          if (scope.current !== owner || request.signal.aborted) return;
          request.abort();
          setLoadFailed(true);
          message.error(err instanceof Error ? err.message : "章节加载失败");
        });
    }
    return () => {
      scope.current += 1;
      request.abort();
      bodySave.current = false;
      pendingMetadata.clear();
    };
  }, [chapterId, message, loadAttempt]);

  if (!chapterId || chapter?.id !== chapterId) {
    return (
      <div className="nf-empty nf-empty--fill">
        <span className="nf-empty__glyph">卷</span>
        <p>{!chapterId ? "从左侧选择一个章节" : loadFailed ? "章节加载失败" : "正在加载章节..."}</p>
        {chapterId && loadFailed && (
          <Button
            onClick={() => {
              setLoadFailed(false);
              setLoadAttempt((value) => value + 1);
            }}
          >
            重新加载章节
          </Button>
        )}
      </div>
    );
  }

  const saveMeta = async (field: MetaField) => {
    if (isRunning() || metaSaves.current.has(field)) return;
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
    if (
      bodySave.current ||
      contentRead.current ||
      isRunning() ||
      !contentVersion ||
      contentConflict
    )
      return;
    const owner = scope.current;
    generationVersion.current += 1;
    bodySave.current = true;
    setSavingBody(true);
    try {
      const saved = await api.saveContent(chapter.id, body, contentVersion);
      if (scope.current !== owner) return;
      setContentVersion(saved.version);
      // 保存确认只更新服务端元信息,保留请求期间继续输入的草稿。
      setChapter((draft) =>
        draft?.id === saved.chapter_id
          ? { ...draft, status: saved.status, word_count: saved.word_count }
          : draft
      );
      message.success("已保存正文");
      refreshTree();
    } catch (err: unknown) {
      if (scope.current === owner) {
        if (err instanceof ApiError && err.status === 409) setContentConflict(true);
        message.error(err instanceof Error ? err.message : "正文保存失败");
      }
    } finally {
      if (scope.current === owner) {
        bodySave.current = false;
        setSavingBody(false);
      }
    }
  };

  // 生成完成后重新读取版本，但不能把另一个窗口的新稿当成本地草稿的基线。
  // 显式重新加载才替换编辑区；确认生成版本时允许继续输入并保留这些输入。
  const readContent = async (reload: boolean) => {
    if (contentRead.current) return;
    const owner = scope.current;
    const version = ++generationVersion.current;
    contentRead.current = true;
    setConfirmingContent(true);
    setReloadingContent(reload);
    setConfirmationFailed(false);
    try {
      const [updated, content] = await Promise.all([
        api.getChapter(chapter.id, requestRef.current?.signal),
        api.getContent(chapter.id, requestRef.current?.signal),
      ]);
      if (scope.current !== owner || generationVersion.current !== version) return;
      if (!reload && content.body !== unconfirmedBody.current) {
        setContentConflict(true);
        return;
      }
      if (reload) setBody(content.body);
      setContentVersion(content.version);
      setContentConflict(false);
      unconfirmedBody.current = null;
      setChapter((draft) =>
        draft?.id === updated.id
          ? { ...draft, status: updated.status, word_count: updated.word_count }
          : draft
      );
    } catch (err: unknown) {
      if (scope.current === owner && generationVersion.current === version) {
        setConfirmationFailed(true);
        message.error(err instanceof Error ? err.message : "正文版本确认失败");
      }
    } finally {
      if (scope.current === owner && generationVersion.current === version) {
        contentRead.current = false;
        setConfirmingContent(false);
        setReloadingContent(false);
      }
    }
  };

  const reloadContent = () => {
    modal.confirm({
      title: "重新加载服务器正文？",
      content: "这会替换当前编辑区的草稿。请先复制需要保留的文字，再重新加载并合并修改。",
      okText: "替换并重新加载",
      cancelText: "保留本地草稿",
      onOk: () => readContent(true),
    });
  };

  const generate = () => {
    if (
      isRunning() ||
      bodySave.current ||
      metaSaves.current.size > 0 ||
      contentRead.current ||
      contentConflict ||
      !contentVersion
    )
      return;
    generationVersion.current += 1;
    let generatedBody = "";
    setGenerationPreview(null);
    start(
      sseEndpoints.chapter,
      { chapter_id: chapter.id, expected_version: contentVersion },
      {
        onEvent: (event, data) => {
          if (event === "content_conflict") setContentConflict(true);
          if (event !== "token") return;
          generatedBody += data;
          // 流中的文本只是预览；服务端确认落库后才替换编辑草稿。
          if (generatedBody.trim()) setGenerationPreview(generatedBody);
        },
        onDone: () => {
          const confirmedBody = generatedBody.trim();
          if (confirmedBody) setBody(confirmedBody);
          unconfirmedBody.current = confirmedBody;
          setContentVersion(null);
          setGenerationPreview(null);
          refreshTree();
          void readContent(false);
        },
        onError: (err) => {
          setGenerationPreview(null);
          message.error(err instanceof Error ? err.message : "生成失败");
        },
      }
    );
  };

  const stop = () => {
    cancel();
    setGenerationPreview(null);
  };
  const displayedBody = generationPreview ?? body;

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
              disabled={savingBody || savingFields.length > 0 || !contentVersion || contentConflict}
              onClick={generate}
            >
              生成正文
            </Button>
          )}
          <Button
            icon={<SaveOutlined />}
            disabled={generating || savingBody || !contentVersion || contentConflict}
            onClick={() => void saveBody()}
          >
            保存
          </Button>
        </Space>
      </div>

      {contentConflict ? (
        <Alert
          type="warning"
          showIcon
          message="服务器正文已有新版本，本地草稿已保留"
          description="请先复制需要保留的文字，再重新加载服务器正文并合并修改。"
          action={
            <Button loading={confirmingContent} onClick={reloadContent}>
              重新加载正文
            </Button>
          }
        />
      ) : confirmationFailed ? (
        <Alert
          type="warning"
          showIcon
          message="尚未确认生成后的正文版本，当前草稿已保留"
          action={
            <Button loading={confirmingContent} onClick={() => void readContent(false)}>
              重新确认版本
            </Button>
          }
        />
      ) : confirmingContent ? (
        <span role="status">正在确认正文版本…</span>
      ) : null}

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

      <span className="nf-editor__count">
        {generating ? "生成预览" : "正文"} {displayedBody.length} 字
      </span>
      <Input.TextArea
        className={`nf-editor__body${generating ? " nf-editor__body--live" : ""}`}
        value={displayedBody}
        disabled={generating || reloadingContent}
        onChange={(e) => setBody(e.target.value)}
        autoSize={{ minRows: 18 }}
        placeholder="点击“生成正文”让 AI 基于大纲与记忆写作,或直接手写。"
      />
    </div>
  );
}
