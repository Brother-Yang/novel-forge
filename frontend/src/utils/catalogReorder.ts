import type { BookTree } from "../api/types";

export type ChapterDropTarget =
  | { kind: "volume"; id: string | null }
  | { kind: "chapter"; id: string; position: "before" | "after" };

export interface ChapterOrder {
  id: string;
  order_index: number;
  volume_id: string | null;
}

/** 按卷及卷内顺序重排整本书，保持正文生成和前情检索使用的全局章序一致。 */
export function reorderCatalog(
  tree: BookTree,
  chapterId: string,
  target: ChapterDropTarget
): ChapterOrder[] {
  if (target.kind === "chapter" && target.id === chapterId) return [];
  const groups = [
    ...tree.volumes.map((volume) => ({
      id: volume.id as string | null,
      chapters: volume.chapters.map((chapter) => chapter.id),
    })),
    { id: null, chapters: tree.loose_chapters.map((chapter) => chapter.id) },
  ];
  const source = groups.find((group) => group.chapters.includes(chapterId));
  const destination = groups.find((group) =>
    target.kind === "volume" ? group.id === target.id : group.chapters.includes(target.id)
  );
  if (!source || !destination) return [];
  source.chapters.splice(source.chapters.indexOf(chapterId), 1);
  const position =
    target.kind === "volume"
      ? destination.chapters.length
      : destination.chapters.indexOf(target.id) + (target.position === "after" ? 1 : 0);
  destination.chapters.splice(position, 0, chapterId);
  return groups
    .flatMap((group) => group.chapters.map((id) => ({ id, volume_id: group.id })))
    .map((chapter, order_index) => ({ ...chapter, order_index }));
}
