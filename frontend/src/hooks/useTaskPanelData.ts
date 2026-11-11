import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { BookTree, GenerationTask } from "../api/types";
import { isTaskActive } from "../constants/taskStatus";

/** 任务请求串行轮询；操作完成时的刷新会排在当前请求后，避免旧状态覆盖操作结果。 */
export function useTaskPanelData(bookId: string) {
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [tree, setTree] = useState<BookTree | null>(null);
  const [tasksError, setTasksError] = useState("");
  const [treeError, setTreeError] = useState("");
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const refresh = useRef({ tasks: () => {}, tree: () => {} });

  useEffect(() => {
    const request = new AbortController();
    let tasksPending = false;
    let treePending = false;
    let tasksQueued = false;
    let treeQueued = false;
    let polling = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const loadTasks = async () => {
      clearTimeout(timer);
      if (tasksPending) {
        tasksQueued = true;
        return;
      }
      tasksPending = true;
      try {
        const data = await api.listTasks(bookId, request.signal);
        if (!request.signal.aborted && !tasksQueued) {
          polling = data.some((task) => isTaskActive(task.status));
          setTasks(data);
          setTasksLoaded(true);
          setTasksError("");
        }
      } catch (err) {
        if (!request.signal.aborted && !tasksQueued)
          setTasksError(err instanceof Error ? err.message : "任务加载失败");
      } finally {
        tasksPending = false;
        if (!request.signal.aborted) {
          if (tasksQueued) {
            tasksQueued = false;
            void loadTasks();
          } else if (polling) timer = setTimeout(() => void loadTasks(), 2500);
        }
      }
    };

    const loadTree = async () => {
      if (treePending) {
        treeQueued = true;
        return;
      }
      treePending = true;
      try {
        const data = await api.getTree(bookId, request.signal);
        if (!request.signal.aborted && !treeQueued) {
          setTree(data);
          setTreeError("");
        }
      } catch (err) {
        if (!request.signal.aborted && !treeQueued)
          setTreeError(err instanceof Error ? err.message : "目录加载失败");
      } finally {
        treePending = false;
        if (!request.signal.aborted && treeQueued) {
          treeQueued = false;
          void loadTree();
        }
      }
    };

    refresh.current = { tasks: () => void loadTasks(), tree: () => void loadTree() };
    void loadTasks();
    void loadTree();
    const refreshOnFocus = () => {
      if (!tasksPending) void loadTasks();
    };
    window.addEventListener("focus", refreshOnFocus);
    return () => {
      request.abort();
      clearTimeout(timer);
      window.removeEventListener("focus", refreshOnFocus);
      refresh.current = { tasks: () => {}, tree: () => {} };
    };
  }, [bookId]);

  return {
    tasks,
    tree,
    tasksError,
    treeError,
    tasksLoaded,
    refreshTasks: () => refresh.current.tasks(),
    refreshTree: () => refresh.current.tree(),
  };
}
