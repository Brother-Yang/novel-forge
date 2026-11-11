export interface Book {
  id: string;
  title: string;
  intro: string;
  brief: string;
  genre: string;
  tone: string;
  target_words: number;
  key_elements: string;
  created_at: string;
}

export interface Volume {
  id: string;
  book_id: string;
  title: string;
  order_index: number;
  summary: string;
}

export interface Chapter {
  id: string;
  book_id: string;
  volume_id: string | null;
  title: string;
  order_index: number;
  outline: string;
  status: string;
  word_count: number;
}

export interface ChapterContent {
  chapter_id: string;
  body: string;
  version: string;
}

export interface ChapterContentSaved extends ChapterContent {
  status: string;
  word_count: number;
}

export interface TreeChapter {
  id: string;
  title: string;
  order_index: number;
  status: string;
  word_count: number;
}

export interface TreeVolume {
  id: string;
  title: string;
  order_index: number;
  chapters: TreeChapter[];
}

export interface BookTree {
  volumes: TreeVolume[];
  loose_chapters: TreeChapter[];
}

export interface Character {
  id: string;
  book_id: string;
  name: string;
  order_index: number;
  md_content: string;
  state_json: string;
  derived_state?: CharacterState;
}

export interface CharacterState {
  location?: string;
  condition?: string;
  goal?: string;
  relationship?: string;
}

export interface Worldbuilding {
  id: string;
  book_id: string;
  category: string;
  title: string;
  md_content: string;
}

export interface PlotThread {
  id: string;
  book_id: string;
  name: string;
  status: string;
  description: string;
  derived_status?: "active" | "closed" | null;
}

export interface GenerationTask {
  id: string;
  book_id: string;
  type: string;
  target_id: string | null;
  status: string;
  progress: number;
  error: string;
  checkpoint_json: string;
  created_at: string;
}

export interface NovelGenerationResult {
  book: Book;
  task: GenerationTask;
}

export interface AgentTraceEntry {
  id: string;
  task_id: string;
  step_index: number;
  kind: string;
  tool_name: string | null;
  payload: string;
  created_at: string;
}

export interface AgentTracePageEntry extends AgentTraceEntry {
  sequence: number;
}

export interface AgentTracePage {
  entries: AgentTracePageEntry[];
  has_more: boolean;
}

export interface MemorySyncItem {
  id: string;
  source_type: "worldbuilding" | "chapter_summary" | "volume_summary" | "chapter_facts";
  source_id: string;
  status: "pending" | "running" | "error";
  attempts: number;
  error: string;
  updated_at: string;
}

export interface MemoryProgress {
  pending: number;
  running: number;
  failed: number;
  ready: number;
  total: number;
  items: MemorySyncItem[];
}

export interface MemoryStatus extends MemoryProgress {
  missing: number;
}
