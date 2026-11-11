import { useContext, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { BookTree, GenerationTask } from "../api/types";
import { AuthSessionRevisionContext } from "../components/authContext";
import { isTaskActive } from "../constants/taskStatus";

/** 任务请求串行轮询；操作完成时的刷新会排在当前请求后，避免旧状态覆盖操作结果。 */
export function useTaskPanelData(bookId: string) {
  const sessionRevision = useContext(AuthSessionRevisionContext);
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [tree, setTree] = useState<BookTree | null>(null);
  const [tasksError, setTasksError] = useState("");
  const [treeError, setTreeError] = useState("");
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const refresh = useRef({ tasks: () => Promise.resolve(), tree: () => {} });

  useEffect(() => {
    const request = new AbortController();
    let tasksPending: Promise<void> | undefined;
    let tasksQueued: Promise<void> | undefined;
    let taskStatuses: Map<string, string> | undefined;
    let treePending = false;
    let treeQueued = false;
    let polling = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const fetchTasks = async () => {
      try {
        if (request.signal.aborted) return;
        const data = await api.listTasks(bookId, request.signal);
        if (!request.signal.aborted && !tasksQueued) {
          polling = data.some((task) => isTaskActive(task.status));
          const previous = taskStatuses;
          taskStatuses = new Map(data.map((task) => [task.id, task.status]));
          // Background generation can create volumes before the task stops.
          if (
            previous &&
            data.some((task) => !isTaskActive(task.status) && previous.get(task.id) !== task.status)
          ) {
            void loadTree();
          }
          setTasks(data);
          setTasksLoaded(true);
          setTasksError("");
        }
      } catch (err) {
        if (!request.signal.aborted && !tasksQueued)
          setTasksError(err instanceof Error ? err.message : "任务加载失败");
      } finally {
        tasksPending = undefined;
        if (!request.signal.aborted && !tasksQueued && polling) {
          timer = setTimeout(() => void loadTasks(), 2500);
        }
      }
    };

    const loadTasks = (): Promise<void> => {
      clearTimeout(timer);
      if (tasksQueued) return tasksQueued;
      if (tasksPending) {
        tasksQueued = tasksPending.then(() => {
          tasksQueued = undefined;
          if (!request.signal.aborted) return loadTasks();
        });
        return tasksQueued;
      }
      tasksPending = Promise.resolve().then(fetchTasks);
      return tasksPending;
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

    refresh.current = { tasks: loadTasks, tree: () => void loadTree() };
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
      refresh.current = { tasks: () => Promise.resolve(), tree: () => {} };
    };
  }, [bookId, sessionRevision]);

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
