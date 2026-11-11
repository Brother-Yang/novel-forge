import { create } from "zustand";

export interface ChapterDraftBase {
  body: string;
  version: string | null;
}

interface ChapterDraft {
  body: string;
  base: ChapterDraftBase;
}

export function chapterDraftKey(bookId: string, chapterId: string): string {
  return `${bookId}:${chapterId}`;
}

interface AppState {
  treeVersion: number;
  refreshTree: () => void;
  chapterDrafts: Partial<Record<string, ChapterDraft>>;
  setChapterDraft: (key: string, draft: ChapterDraft | null) => void;
  clearChapterDraft: (bookId: string, chapterId: string) => void;
  clearBookDrafts: (bookId: string) => void;
  clearChapterDrafts: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  treeVersion: 0,
  refreshTree: () => set((s) => ({ treeVersion: s.treeVersion + 1 })),
  chapterDrafts: {},
  setChapterDraft: (key, draft) =>
    set((state) => {
      if (draft && draft.body !== draft.base.body) {
        return { chapterDrafts: { ...state.chapterDrafts, [key]: draft } };
      }
      if (!state.chapterDrafts[key]) return state;
      const chapterDrafts = { ...state.chapterDrafts };
      delete chapterDrafts[key];
      return { chapterDrafts };
    }),
  clearChapterDraft: (bookId, chapterId) =>
    get().setChapterDraft(chapterDraftKey(bookId, chapterId), null),
  clearBookDrafts: (bookId) =>
    set((state) => ({
      chapterDrafts: Object.fromEntries(
        Object.entries(state.chapterDrafts).filter(([key]) => !key.startsWith(`${bookId}:`))
      ),
    })),
  clearChapterDrafts: () => set({ chapterDrafts: {} }),
}));
