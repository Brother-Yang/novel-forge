import { http } from "./client";
import type {
  Book,
  BookTree,
  Chapter,
  Character,
  GenerationTask,
  PlotThread,
  Volume,
  Worldbuilding,
} from "./types";

export const api = {
  // books
  listBooks: () => http.get<Book[]>("/books"),
  getBook: (id: number) => http.get<Book>(`/books/${id}`),
  createBook: (b: Partial<Book>) => http.post<Book>("/books", b),
  updateBook: (id: number, b: Partial<Book>) => http.patch<Book>(`/books/${id}`, b),
  deleteBook: (id: number) => http.del(`/books/${id}`),
  getTree: (id: number) => http.get<BookTree>(`/books/${id}/tree`),
  listPlotThreads: (id: number) => http.get<PlotThread[]>(`/books/${id}/plot_threads`),

  // volumes
  createVolume: (v: Partial<Volume>) => http.post<Volume>("/volumes", v),
  updateVolume: (id: number, v: Partial<Volume>) =>
    http.patch<Volume>(`/volumes/${id}`, v),
  deleteVolume: (id: number) => http.del(`/volumes/${id}`),

  // chapters
  getChapter: (id: number) => http.get<Chapter>(`/chapters/${id}`),
  createChapter: (c: Partial<Chapter>) => http.post<Chapter>("/chapters", c),
  updateChapter: (id: number, c: Partial<Chapter>) =>
    http.patch<Chapter>(`/chapters/${id}`, c),
  deleteChapter: (id: number) => http.del(`/chapters/${id}`),
  reorderChapters: (items: { id: number; order_index: number; volume_id?: number | null }[]) =>
    http.post("/chapters/reorder", { items }),
  getContent: (id: number) =>
    http.get<{ chapter_id: number; body: string }>(`/chapters/${id}/content`),
  saveContent: (id: number, body: string) =>
    http.put(`/chapters/${id}/content`, { body }),

  // characters
  listCharacters: (bookId: number) =>
    http.get<Character[]>(`/books/${bookId}/characters`),
  createCharacter: (c: Partial<Character>) => http.post<Character>("/characters", c),
  updateCharacter: (id: number, c: Partial<Character>) =>
    http.patch<Character>(`/characters/${id}`, c),
  deleteCharacter: (id: number) => http.del(`/characters/${id}`),

  // worldbuilding
  listWorldbuilding: (bookId: number) =>
    http.get<Worldbuilding[]>(`/books/${bookId}/worldbuilding`),
  createWorldbuilding: (w: Partial<Worldbuilding>) =>
    http.post<Worldbuilding>("/worldbuilding", w),
  updateWorldbuilding: (id: number, w: Partial<Worldbuilding>) =>
    http.patch<Worldbuilding>(`/worldbuilding/${id}`, w),
  deleteWorldbuilding: (id: number) => http.del(`/worldbuilding/${id}`),

  // tasks
  listTasks: (bookId: number) => http.get<GenerationTask[]>(`/books/${bookId}/tasks`),
  getTask: (id: number) => http.get<GenerationTask>(`/tasks/${id}`),
  pauseTask: (id: number) => http.post(`/tasks/${id}/pause`),
  resumeTask: (id: number) => http.post(`/tasks/${id}/resume`),
  retryTask: (id: number) => http.post(`/tasks/${id}/retry`),
  startBookGeneration: (bookId: number) =>
    http.post<GenerationTask>("/generate/book", { book_id: bookId }),
};
