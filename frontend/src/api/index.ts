import { API_BASE, http } from "./client";
import type {
  Book,
  BookTree,
  Chapter,
  Character,
  GenerationTask,
  NovelGenerationResult,
  PlotThread,
  Volume,
  Worldbuilding,
} from "./types";

export const api = {
  // books
  listBooks: () => http.get<Book[]>("/books"),
  getBook: (id: string) => http.get<Book>(`/books/${id}`),
  createBook: (b: Partial<Book>) => http.post<Book>("/books", b),
  updateBook: (id: string, b: Partial<Book>) => http.patch<Book>(`/books/${id}`, b),
  deleteBook: (id: string) => http.del(`/books/${id}`),
  getTree: (id: string) => http.get<BookTree>(`/books/${id}/tree`),
  listPlotThreads: (id: string) => http.get<PlotThread[]>(`/books/${id}/plot_threads`),

  // volumes
  createVolume: (v: Partial<Volume>) => http.post<Volume>("/volumes", v),
  updateVolume: (id: number, v: Partial<Volume>) => http.patch<Volume>(`/volumes/${id}`, v),
  deleteVolume: (id: number) => http.del(`/volumes/${id}`),

  // chapters
  getChapter: (id: number) => http.get<Chapter>(`/chapters/${id}`),
  createChapter: (c: Partial<Chapter>) => http.post<Chapter>("/chapters", c),
  updateChapter: (id: number, c: Partial<Chapter>) => http.patch<Chapter>(`/chapters/${id}`, c),
  deleteChapter: (id: number) => http.del(`/chapters/${id}`),
  reorderChapters: (items: { id: number; order_index: number; volume_id?: number | null }[]) =>
    http.post("/chapters/reorder", { items }),
  getContent: (id: number) =>
    http.get<{ chapter_id: number; body: string }>(`/chapters/${id}/content`),
  saveContent: (id: number, body: string) => http.put(`/chapters/${id}/content`, { body }),

  // characters
  listCharacters: (bookId: string) => http.get<Character[]>(`/books/${bookId}/characters`),
  createCharacter: (c: Partial<Character>) => http.post<Character>("/characters", c),
  updateCharacter: (id: number, c: Partial<Character>) =>
    http.patch<Character>(`/characters/${id}`, c),
  deleteCharacter: (id: number) => http.del(`/characters/${id}`),

  // worldbuilding
  listWorldbuilding: (bookId: string) =>
    http.get<Worldbuilding[]>(`/books/${bookId}/worldbuilding`),
  createWorldbuilding: (w: Partial<Worldbuilding>) => http.post<Worldbuilding>("/worldbuilding", w),
  updateWorldbuilding: (id: number, w: Partial<Worldbuilding>) =>
    http.patch<Worldbuilding>(`/worldbuilding/${id}`, w),
  deleteWorldbuilding: (id: number) => http.del(`/worldbuilding/${id}`),

  // tasks
  listTasks: (bookId: string) => http.get<GenerationTask[]>(`/books/${bookId}/tasks`),
  getTask: (id: number) => http.get<GenerationTask>(`/tasks/${id}`),
  pauseTask: (id: number) => http.post(`/tasks/${id}/pause`),
  resumeTask: (id: number) => http.post(`/tasks/${id}/resume`),
  retryTask: (id: number) => http.post(`/tasks/${id}/retry`),
  startBookGeneration: (bookId: string) =>
    http.post<GenerationTask>("/generate/book", { book_id: bookId }),
  startNovelGeneration: (payload: {
    brief: string;
    title?: string;
    genre?: string;
    tone?: string;
    target_words?: number;
  }) => http.post<NovelGenerationResult>("/generate/novel", payload),
  startNovelFromBook: (bookId: string) =>
    http.post<GenerationTask>("/generate/novel/from-book", { book_id: bookId }),

  exportUrl: (bookId: string, format: "txt" | "epub") =>
    `${API_BASE}/books/${bookId}/export?format=${format}`,
};

// 供 sseFetch 使用的流式生成接口路径,集中管理避免各组件各自硬编码字符串
export const sseEndpoints = {
  worldbuilding: "/generate/worldbuilding",
  characters: "/generate/characters",
  outline: "/generate/outline",
  chapterOutlines: "/generate/chapter-outlines",
  chapter: "/generate/chapter",
} as const;
