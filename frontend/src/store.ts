import { create } from "zustand";

interface AppState {
  treeVersion: number;
  refreshTree: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  treeVersion: 0,
  refreshTree: () => set((s) => ({ treeVersion: s.treeVersion + 1 })),
}));
