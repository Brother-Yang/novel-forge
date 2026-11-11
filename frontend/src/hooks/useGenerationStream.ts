import { useLayoutEffect, useRef, useState } from "react";
import { sseFetch } from "../api/client";

interface GenerationStreamCallbacks {
  onEvent?: (event: string, data: string) => void;
  onDone: () => void;
  onError: (error: unknown) => void;
}

interface StreamRun {
  scope: string | null;
  controller: AbortController;
}

export function useGenerationStream(scope: string | null) {
  const active = useRef<StreamRun | null>(null);
  const [running, setRunning] = useState<StreamRun | null>(null);
  if (running && running.scope !== scope) setRunning(null);

  useLayoutEffect(
    () => () => {
      const run = active.current;
      active.current = null;
      run?.controller.abort();
    },
    [scope]
  );

  function isRunning() {
    return active.current !== null;
  }

  function cancel() {
    const run = active.current;
    if (!run) return;
    active.current = null;
    setRunning(null);
    run.controller.abort();
  }

  function start(path: string, body: unknown, callbacks: GenerationStreamCallbacks) {
    if (active.current) return;
    const run = { scope, controller: new AbortController() };
    active.current = run;
    setRunning(run);

    function finish(): boolean {
      if (active.current !== run) return false;
      active.current = null;
      setRunning(null);
      return true;
    }

    function fail(error: unknown) {
      if (finish()) callbacks.onError(error);
    }

    async function consume() {
      try {
        await sseFetch(path, body, {
          signal: run.controller.signal,
          onEvent: (event, data) => {
            if (active.current !== run) return;
            if (event === "done") {
              if (!finish()) return;
              try {
                callbacks.onDone();
              } catch (error) {
                callbacks.onError(error);
              }
            } else if (event === "error") {
              fail(new Error(data || "生成失败"));
            } else {
              callbacks.onEvent?.(event, data);
            }
          },
          onError: fail,
        });
      } catch (error) {
        fail(error);
        return;
      }
      if (active.current === run) fail(new Error("生成连接已中断，未收到结束事件"));
    }

    void consume();
  }

  return { running: running !== null && running.scope === scope, isRunning, start, cancel };
}
