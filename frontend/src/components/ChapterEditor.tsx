import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, App, Button, Card, Input, Space, Tag } from "antd";
import { SaveOutlined, ThunderboltOutlined, StopOutlined } from "@ant-design/icons";
import { api, sseEndpoints } from "../api";
import { ApiError } from "../api/client";
import type { Chapter } from "../api/types";
import { CHAPTER_STATUS_LABEL } from "../constants/chapterStatus";
import { useGenerationStream } from "../hooks/useGenerationStream";
import { chapterDraftKey, useAppStore, type ChapterDraftBase } from "../store";

interface Props {
  bookId: string;
  chapterId: string | null;
}

type MetaField = "title" | "outline";

export default function ChapterEditor({ bookId, chapterId }: Props) {
  const draftKey = chapterDraftKey(bookId, chapterId ?? "none");
  return <ChapterDraft key={draftKey} chapterId={chapterId} draftKey={draftKey} />;
}

function ChapterDraft({ chapterId, draftKey }: Pick<Props, "chapterId"> & { draftKey: string }) {
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [base, setBase] = useState<ChapterDraftBase | null>(null);
  const localDraft = useAppStore((s) => s.chapterDrafts[draftKey]);
  const pendingSave = useAppStore((s) => s.chapterSaves[draftKey]);
  const savingBody = Boolean(pendingSave);
  const setChapterDraft = useAppStore((s) => s.setChapterDraft);
  const confirmChapterDraft = useAppStore((s) => s.confirmChapterDraft);
  const body = localDraft?.body ?? base?.body ?? "";
  const contentVersion = base?.version ?? null;
  const [contentConflict, setContentConflict] = useState(false);
  const [confirmingContent, setConfirmingContent] = useState(false);
  const [reloadingContent, setReloadingContent] = useState(false);
  const [confirmationFailed, setConfirmationFailed] = useState(false);
  const [generationPreview, setGenerationPreview] = useState<string | null>(null);
  const [loadFailure, setLoadFailure] = useState<"missing" | "request" | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [savingFields, setSavingFields] = useState<MetaField[]>([]);
  const scope = useRef(0);
  const metaSaves = useRef(new Set<MetaField>());
  const requestRef = useRef<AbortController | null>(null);
  const generationVersion = useRef(0);
  const contentRead = useRef(false);
  const unconfirmedBody = useRef<string | null>(null);
  const { running: generating, isRunning, start, cancel } = useGenerationStream(chapterId);
  const { message, modal } = App.useApp();
  const reloadDialog = useRef<ReturnType<typeof modal.confirm> | null>(null);
  const refreshTree = useAppStore((s) => s.refreshTree);
  const markMissing = useCallback(
    (error: unknown) => {
      if (!(error instanceof ApiError) || error.status !== 404) return false;
      requestRef.current?.abort();
      useAppStore.getState().markChapterDraftsMissing([draftKey]);
      setChapter(null);
      setLoadFailure("missing");
      refreshTree();
      return true;
    },
    [draftKey, refreshTree]
  );

  useEffect(() => {
    const owner = ++scope.current;
    const request = new AbortController();
    requestRef.current = request;
    const pendingMetadata = metaSaves.current;
    async function loadChapter(id: string) {
      try {
        const pending = useAppStore.getState().chapterSaves[draftKey];
        if (pending) {
          // 即使上一次保存没有返回，已删除的章节也要能恢复本地草稿。
          await api.getChapter(id, request.signal);
          try {
            await pending.result;
          } catch (err: unknown) {
            if (!request.signal.aborted)
              message.error(err instanceof Error ? err.message : "正文保存失败");
          }
          if (request.signal.aborted) return;
        }
        // 两部分均成功后才开放编辑，避免把尚未读取的正文当成空稿提交。
        const [data, content] = await Promise.all([
          api.getChapter(id, request.signal),
          api.getContent(id, request.signal),
        ]);
        if (scope.current !== owner || request.signal.aborted) return;
        const draft = useAppStore.getState().chapterDrafts[draftKey];
        if (draft?.verification === "missing") return;
        const remoteBase = { body: content.body, version: content.version };
        const changed =
          draft &&
          draft.body !== remoteBase.body &&
          (draft.base.version === null
            ? draft.base.body !== remoteBase.body
            : draft.base.version !== remoteBase.version);
        setChapter(data);
        setBase(changed ? draft.base : remoteBase);
        setContentConflict(Boolean(changed));
        if (draft && !changed) {
          confirmChapterDraft(draftKey, remoteBase);
        }
        setLoadFailure(null);
      } catch (err: unknown) {
        if (scope.current !== owner || request.signal.aborted) return;
        request.abort();
        if (!markMissing(err)) {
          setLoadFailure("request");
          message.error(err instanceof Error ? err.message : "章节加载失败");
        }
      }
    }
    if (chapterId) void loadChapter(chapterId);
    return () => {
      scope.current += 1;
      request.abort();
      reloadDialog.current?.destroy();
      pendingMetadata.clear();
    };
  }, [chapterId, draftKey, message, loadAttempt, markMissing, confirmChapterDraft]);

  const copyDraft = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制正文");
    } catch {
      message.info("无法自动复制，请选中正文手动复制");
    }
  };

  const missing = loadFailure === "missing" || localDraft?.verification === "missing";
  if (!chapterId || chapter?.id !== chapterId || missing) {
    const loadingNote = !chapterId
      ? "从左侧选择一个章节"
      : missing
        ? "章节已不存在"
        : loadFailure
          ? "章节加载失败"
          : savingBody
            ? "正在确认上一次保存…"
            : "正在加载章节...";
    const recoveryBody = localDraft?.body ?? (missing ? undefined : pendingSave?.body);
    const retry = loadFailure === "request" && (
      <Button
        onClick={() => {
          setLoadFailure(null);
          setLoadAttempt((value) => value + 1);
        }}
      >
        重新加载章节
      </Button>
    );

    if (chapterId && recoveryBody !== undefined) {
      return (
        <div className="nf-editor">
          <Alert
            type={loadFailure ? "warning" : "info"}
            showIcon
            message={loadingNote}
            description={missing ? "正文草稿仍在本窗口。可以复制后粘贴到其他章节。" : undefined}
          />
          <Input.TextArea
            aria-label="本地正文草稿"
            className="nf-editor__body"
            value={recoveryBody}
            autoSize={{ minRows: 6, maxRows: 18 }}
            readOnly
          />
          <Space>
            <Button onClick={() => void copyDraft(recoveryBody)}>复制正文</Button>
            {missing && (
              <Button
                danger
                onClick={() => {
                  reloadDialog.current = modal.confirm({
                    title: "丢弃本窗口的正文草稿？",
                    content: "草稿清除后无法恢复，请先复制需要保留的内容。",
                    okText: "丢弃草稿",
                    cancelText: "保留草稿",
                    okButtonProps: { danger: true },
                    onOk: () => {
                      requestRef.current?.abort();
                      setChapter(null);
                      setLoadFailure("missing");
                      setChapterDraft(draftKey, null);
                    },
                  });
                }}
              >
                丢弃本地草稿
              </Button>
            )}
            {retry}
          </Space>
        </div>
      );
    }

    return (
      <div className="nf-empty nf-empty--fill">
        <span className="nf-empty__glyph">卷</span>
        <p>{loadingNote}</p>
        {retry}
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
      if (scope.current === owner && !markMissing(err))
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
      useAppStore.getState().chapterSaves[draftKey] ||
      contentRead.current ||
      isRunning() ||
      !base ||
      !contentVersion ||
      contentConflict
    )
      return;
    const owner = scope.current;
    try {
      const result = useAppStore
        .getState()
        .startChapterSave(draftKey, { body, base, title: chapter.title }, () =>
          api.saveContent(chapter.id, body, contentVersion)
        );
      if (!result) return;
      generationVersion.current += 1;
      const saved = await result;
      if (
        scope.current !== owner ||
        requestRef.current?.signal.aborted ||
        useAppStore.getState().chapterDrafts[draftKey]?.verification === "missing"
      )
        return;
      setBase({ body: saved.body, version: saved.version });
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
        if (markMissing(err)) return;
        if (err instanceof ApiError && err.status === 409) setContentConflict(true);
        message.error(err instanceof Error ? err.message : "正文保存失败");
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
      if (
        scope.current !== owner ||
        generationVersion.current !== version ||
        useAppStore.getState().chapterDrafts[draftKey]?.verification === "missing"
      )
        return;
      if (!reload && content.body !== unconfirmedBody.current) {
        setContentConflict(true);
        return;
      }
      const loadedBase = { body: content.body, version: content.version };
      setBase(loadedBase);
      if (reload) setChapterDraft(draftKey, null);
      else confirmChapterDraft(draftKey, loadedBase);
      setContentConflict(false);
      unconfirmedBody.current = null;
      setChapter((draft) =>
        draft?.id === updated.id
          ? { ...draft, status: updated.status, word_count: updated.word_count }
          : draft
      );
    } catch (err: unknown) {
      if (scope.current === owner && generationVersion.current === version) {
        if (markMissing(err)) return;
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
    reloadDialog.current = modal.confirm({
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
      useAppStore.getState().chapterSaves[draftKey] ||
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
          if (useAppStore.getState().chapterDrafts[draftKey]?.verification === "missing") return;
          const confirmedBody = generatedBody.trim();
          if (confirmedBody) setChapterDraft(draftKey, null);
          unconfirmedBody.current = confirmedBody;
          setBase({ body: confirmedBody || body, version: null });
          setGenerationPreview(null);
          refreshTree();
          void readContent(false);
        },
        onError: (err) => {
          setGenerationPreview(null);
          if (!markMissing(err)) message.error(err instanceof Error ? err.message : "生成失败");
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
          {localDraft && (
            <Tag color="warning" title="切换页面后仍在本窗口保留，保存后才会写入服务器">
              未保存草稿
            </Tag>
          )}
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
        onChange={(e) => {
          if (base) setChapterDraft(draftKey, { body: e.target.value, base, title: chapter.title });
        }}
        autoSize={{ minRows: 18 }}
        placeholder="点击“生成正文”让 AI 基于大纲与记忆写作,或直接手写。"
      />
    </div>
  );
}
