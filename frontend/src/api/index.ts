import { API_BASE, http } from "./client";
import type {
  AgentTraceEntry,
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
  updateVolume: (id: string, v: Partial<Volume>) => http.patch<Volume>(`/volumes/${id}`, v),
  deleteVolume: (id: string) => http.del(`/volumes/${id}`),

  // chapters
  getChapter: (id: string) => http.get<Chapter>(`/chapters/${id}`),
  createChapter: (c: Partial<Chapter>) => http.post<Chapter>("/chapters", c),
  updateChapter: (id: string, c: Partial<Chapter>) => http.patch<Chapter>(`/chapters/${id}`, c),
  deleteChapter: (id: string) => http.del(`/chapters/${id}`),
  reorderChapters: (items: { id: string; order_index: number; volume_id?: string | null }[]) =>
    http.post("/chapters/reorder", { items }),
  getContent: (id: string) =>
    http.get<{ chapter_id: string; body: string }>(`/chapters/${id}/content`),
  saveContent: (id: string, body: string) => http.put(`/chapters/${id}/content`, { body }),

  // characters
  listCharacters: (bookId: string) => http.get<Character[]>(`/books/${bookId}/characters`),
  createCharacter: (c: Partial<Character>) => http.post<Character>("/characters", c),
  updateCharacter: (id: string, c: Partial<Character>) =>
    http.patch<Character>(`/characters/${id}`, c),
  deleteCharacter: (id: string) => http.del(`/characters/${id}`),

  // worldbuilding
  listWorldbuilding: (bookId: string) =>
    http.get<Worldbuilding[]>(`/books/${bookId}/worldbuilding`),
  createWorldbuilding: (w: Partial<Worldbuilding>) => http.post<Worldbuilding>("/worldbuilding", w),
  updateWorldbuilding: (id: string, w: Partial<Worldbuilding>) =>
    http.patch<Worldbuilding>(`/worldbuilding/${id}`, w),
  deleteWorldbuilding: (id: string) => http.del(`/worldbuilding/${id}`),

  // tasks
  listTasks: (bookId: string) => http.get<GenerationTask[]>(`/books/${bookId}/tasks`),
  getTask: (id: string) => http.get<GenerationTask>(`/tasks/${id}`),
  pauseTask: (id: string) => http.post(`/tasks/${id}/pause`),
  resumeTask: (id: string) => http.post(`/tasks/${id}/resume`),
  retryTask: (id: string) => http.post(`/tasks/${id}/retry`),
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
  startAgentGeneration: (payload: {
    brief: string;
    title?: string;
    genre?: string;
    tone?: string;
    target_words?: number;
  }) => http.post<NovelGenerationResult>("/generate/agent", payload),
  startAgentFromBook: (bookId: string) =>
    http.post<GenerationTask>("/generate/agent/from-book", { book_id: bookId }),
  getTaskTrace: (taskId: string) => http.get<AgentTraceEntry[]>(`/tasks/${taskId}/trace`),

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
