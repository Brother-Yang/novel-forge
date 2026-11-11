import { describe, expect, it } from "vitest";
import { chapterDraftKey, useAppStore } from "./store";
import type { ChapterContentSaved } from "./api/types";

describe("useAppStore", () => {
  it("refreshTree 会递增 treeVersion", () => {
    const before = useAppStore.getState().treeVersion;
    useAppStore.getState().refreshTree();
    expect(useAppStore.getState().treeVersion).toBe(before + 1);
  });

  it("会话已清理时，旧保存响应不能给新会话的同章草稿换版本", async () => {
    const store = useAppStore.getState();
    const key = chapterDraftKey("book", "chapter");
    const base = { body: "原稿", version: "original" };
    let complete!: (response: ChapterContentSaved) => void;
    const pending = new Promise<ChapterContentSaved>((resolve) => {
      complete = resolve;
    });
    const original = { body: "旧会话提交", base };
    const fresh = { body: "新会话草稿", base };
    store.setChapterDraft(key, original);
    const saved = store.startChapterSave(key, original, () => pending);
    store.clearChapterDrafts();
    store.setChapterDraft(key, fresh);
    try {
      complete({
        chapter_id: "chapter",
        body: original.body,
        version: "saved",
        status: "done",
        word_count: 5,
      });
      await saved;
      expect(useAppStore.getState().chapterDrafts[key]).toEqual(fresh);
    } finally {
      store.clearChapterDrafts();
    }
  });

  it("目录确认删章后，迟到的保存及正文确认均保留相同文本的本地草稿", async () => {
    const store = useAppStore.getState();
    const key = chapterDraftKey("book", "chapter");
    const draft = { body: "已经提交的正文", base: { body: "原稿", version: "original" } };
    const response = {
      chapter_id: "chapter",
      body: draft.body,
      version: "saved",
      status: "done",
      word_count: 7,
    };
    store.setChapterDraft(key, draft);
    const save = store.startChapterSave(key, draft, () => Promise.resolve(response));
    store.markChapterDraftsMissing([key]);
    try {
      await save;
      store.confirmChapterDraft(key, { body: response.body, version: response.version });
      expect(useAppStore.getState().chapterDrafts[key]).toEqual({
        ...draft,
        verification: "missing",
      });
      expect(useAppStore.getState().chapterSaves[key]).toBeUndefined();
      store.clearChapterDraft("book", "chapter");
      expect(useAppStore.getState().chapterDrafts[key]).toBeUndefined();
    } finally {
      store.clearChapterDrafts();
    }
  });
});
