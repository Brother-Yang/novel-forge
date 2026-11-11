import type { CharacterState } from "../api/types";

export const characterStateFields = [
  { key: "location", label: "所在位置" },
  { key: "condition", label: "身体与处境" },
  { key: "goal", label: "当前目标" },
  { key: "relationship", label: "关系变化" },
] satisfies { key: keyof CharacterState; label: string }[];

export const plotThreadStatuses: Record<string, { label: string; color: string }> = {
  active: { label: "推进中", color: "processing" },
  closed: { label: "已收束", color: "success" },
};
