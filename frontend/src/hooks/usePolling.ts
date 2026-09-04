import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * 按固定间隔重复执行 fn,挂载时立即执行一次;enabled=false 时停止轮询
 * (用于 agent 任务结束后不再无意义地请求)。fn 用 ref 存储,避免调用方
 * 每次渲染传入新的闭包导致定时器被重建。
 */
export function usePolling(fn: () => void, intervalMs: number, enabled = true): void {
  const fnRef = useRef(fn);
  useLayoutEffect(() => {
    fnRef.current = fn;
  });

  useEffect(() => {
    if (!enabled) return;
    fnRef.current();
    const timer = setInterval(() => fnRef.current(), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, enabled]);
}
