import { create } from "zustand";
import type { ChapterContentSaved } from "./api/types";

export interface ChapterDraftBase {
  body: string;
  version: string | null;
}

interface ChapterDraft {
  body: string;
  base: ChapterDraftBase;
  title?: string;
  verification?: "unconfirmed" | "missing";
}

interface ChapterSave {
  body: string;
  result: Promise<ChapterContentSaved>;
}

type ByChapter<T> = Partial<Record<string, T>>;

export function chapterDraftKey(bookId: string, chapterId: string): string {
  return `${bookId}:${chapterId}`;
}

function withoutChapter<T>(records: ByChapter<T>, key: string): ByChapter<T> {
  if (!records[key]) return records;
  const remaining = { ...records };
  delete remaining[key];
  return remaining;
}

function withoutBook<T>(records: ByChapter<T>, bookId: string): ByChapter<T> {
  const prefix = chapterDraftKey(bookId, "");
  return Object.fromEntries(Object.entries(records).filter(([key]) => !key.startsWith(prefix)));
}

function withDraft(
  records: ByChapter<ChapterDraft>,
  key: string,
  draft: ChapterDraft | null,
  saving = false
): ByChapter<ChapterDraft> {
  if (draft && (saving || draft.verification || draft.body !== draft.base.body)) {
    return { ...records, [key]: draft };
  }
  return withoutChapter(records, key);
}

interface AppState {
  treeVersion: number;
  refreshTree: () => void;
  chapterDrafts: ByChapter<ChapterDraft>;
  chapterSaves: ByChapter<ChapterSave>;
  setChapterDraft: (key: string, draft: ChapterDraft | null) => void;
  confirmChapterDraft: (key: string, base: ChapterDraftBase) => void;
  markChapterDraftsMissing: (keys: readonly string[]) => void;
  startChapterSave: (
    key: string,
    draft: ChapterDraft,
    submit: () => Promise<ChapterContentSaved>
  ) => Promise<ChapterContentSaved> | undefined;
  clearChapterDraft: (bookId: string, chapterId: string) => void;
  clearBookDrafts: (bookId: string) => void;
  clearChapterDrafts: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  treeVersion: 0,
  refreshTree: () => set((s) => ({ treeVersion: s.treeVersion + 1 })),
  chapterDrafts: {},
  chapterSaves: {},
  setChapterDraft: (key, draft) =>
    set((state) => {
      const verification = state.chapterDrafts[key]?.verification;
      const next =
        draft && verification && !draft.verification ? { ...draft, verification } : draft;
      return {
        chapterDrafts: withDraft(state.chapterDrafts, key, next, Boolean(state.chapterSaves[key])),
      };
    }),
  confirmChapterDraft: (key, base) =>
    set((state) => {
      const draft = state.chapterDrafts[key];
      if (!draft || draft.verification === "missing") return state;
      return {
        chapterDrafts: withDraft(state.chapterDrafts, key, {
          ...draft,
          base,
          verification: undefined,
        }),
      };
    }),
  markChapterDraftsMissing: (keys) =>
    set((state) => {
      let chapterDrafts = state.chapterDrafts;
      for (const key of keys) {
        const draft = chapterDrafts[key];
        if (!draft || draft.verification === "missing") continue;
        if (chapterDrafts === state.chapterDrafts) chapterDrafts = { ...chapterDrafts };
        chapterDrafts[key] = { ...draft, verification: "missing" };
      }
      return chapterDrafts === state.chapterDrafts ? state : { chapterDrafts };
    }),
  startChapterSave: (key, submitted, submit) => {
    if (get().chapterSaves[key]) return;

    function finish(saved?: ChapterContentSaved) {
      set((state) => {
        if (state.chapterSaves[key]?.result !== result) return state;
        const draft = state.chapterDrafts[key];
        const chapterSaves = withoutChapter(state.chapterSaves, key);
        if (
          !draft ||
          draft.verification === "missing" ||
          draft.base.version !== submitted.base.version
        ) {
          return { chapterSaves };
        }
        // A failed response can follow a committed save. Equality against the
        // old base is only meaningful again after an authoritative response.
        const next: ChapterDraft = saved
          ? {
              ...draft,
              base: { body: saved.body, version: saved.version },
              verification: undefined,
            }
          : { ...draft, verification: "unconfirmed" };
        return {
          chapterSaves,
          chapterDrafts: withDraft(state.chapterDrafts, key, next),
        };
      });
    }

    const result = submit().then(
      (saved) => {
        finish(saved);
        return saved;
      },
      (error: unknown) => {
        finish();
        throw error;
      }
    );
    set((state) => ({
      chapterSaves: { ...state.chapterSaves, [key]: { body: submitted.body, result } },
    }));
    return result;
  },
  clearChapterDraft: (bookId, chapterId) => {
    const key = chapterDraftKey(bookId, chapterId);
    set((state) => {
      return {
        chapterDrafts: withoutChapter(state.chapterDrafts, key),
        chapterSaves: withoutChapter(state.chapterSaves, key),
      };
    });
  },
  clearBookDrafts: (bookId) =>
    set((state) => ({
      chapterDrafts: withoutBook(state.chapterDrafts, bookId),
      chapterSaves: withoutBook(state.chapterSaves, bookId),
    })),
  clearChapterDrafts: () => set({ chapterDrafts: {}, chapterSaves: {} }),
}));
