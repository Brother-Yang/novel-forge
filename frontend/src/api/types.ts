export interface Book {
  id: string;
  title: string;
  intro: string;
  brief: string;
  genre: string;
  tone: string;
  target_words: number;
  created_at: string;
}

export interface Volume {
  id: number;
  book_id: string;
  title: string;
  order_index: number;
  summary: string;
}

export interface Chapter {
  id: number;
  book_id: string;
  volume_id: number | null;
  title: string;
  order_index: number;
  outline: string;
  status: string;
  word_count: number;
}

export interface TreeChapter {
  id: number;
  title: string;
  order_index: number;
  status: string;
  word_count: number;
}

export interface TreeVolume {
  id: number;
  title: string;
  order_index: number;
  chapters: TreeChapter[];
}

export interface BookTree {
  volumes: TreeVolume[];
  loose_chapters: TreeChapter[];
}

export interface Character {
  id: number;
  book_id: string;
  name: string;
  order_index: number;
  md_content: string;
  state_json: string;
}

export interface Worldbuilding {
  id: number;
  book_id: string;
  category: string;
  title: string;
  md_content: string;
}

export interface PlotThread {
  id: number;
  book_id: string;
  name: string;
  status: string;
  description: string;
}

export interface GenerationTask {
  id: number;
  book_id: string;
  type: string;
  target_id: number | null;
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
