import { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import { sseFetch } from "../api/client";

interface MarkdownCard {
  id: string;
  md_content: string;
}

interface UseMarkdownCardPanelOptions<T extends MarkdownCard> {
  bookId: string;
  list: (bookId: string) => Promise<T[]>;
  create: (currentCount: number) => Promise<T>;
  update: (id: string, patch: Partial<T>) => Promise<T>;
  remove: (id: string) => Promise<unknown>;
  generateEndpoint: string;
  generateBody: (bookId: string) => unknown;
}

/** 人物卡 / 设定卡共用的列表、编辑与生成逻辑。 */
export function useMarkdownCardPanel<T extends MarkdownCard>({
  bookId,
  list,
  create,
  update,
  remove,
  generateEndpoint,
  generateBody,
}: UseMarkdownCardPanelOptions<T>) {
  const [editingBookId, setEditingBookId] = useState(bookId);
  const [items, setItems] = useState<T[]>([]);
  const [current, setCurrent] = useState<T | null>(null);
  const [md, setMd] = useState("");
  const [generating, setGenerating] = useState(false);
  const scope = useRef(0);
  const listRequest = useRef(0);
  const pending = useRef(new Set<string>());
  const generation = useRef<AbortController | null>(null);
  const selectionVersion = useRef(0);
  const { message } = App.useApp();

  if (editingBookId !== bookId) {
    setEditingBookId(bookId);
    setItems([]);
    setCurrent(null);
    setMd("");
    setGenerating(false);
  }

  useEffect(() => {
    scope.current += 1;
    pending.current.clear();
    return () => {
      scope.current += 1;
      generation.current?.abort();
      generation.current = null;
    };
  }, [bookId]);

  const load = useCallback(() => {
    const owner = scope.current;
    const request = ++listRequest.current;
    return list(bookId)
      .then((data) => {
        if (scope.current === owner && request === listRequest.current) {
          setItems(Array.isArray(data) ? data : []);
        }
      })
      .catch((err: unknown) => {
        if (scope.current === owner && request === listRequest.current) {
          message.error(err instanceof Error ? err.message : "加载失败");
        }
      });
  }, [bookId, list, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const select = (item: T) => {
    selectionVersion.current += 1;
    setCurrent(item);
    setMd(item.md_content);
  };

  const add = async () => {
    if (pending.current.has("create") || generation.current) return;
    const owner = scope.current;
    const selection = selectionVersion.current;
    pending.current.add("create");
    try {
      const item = await create(items.length);
      if (scope.current !== owner) return;
      // 只更新列表，创建完成时不抢走用户后来选择的卡片。
      await load();
      if (scope.current === owner && selectionVersion.current === selection) select(item);
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "新增失败");
    } finally {
      if (scope.current === owner) pending.current.delete("create");
    }
  };

  const persist = async (patch: Partial<T>, announce: boolean) => {
    if (!current) return;
    if (generation.current || pending.current.has(current.id)) {
      message.info("请等待当前操作完成后重试保存");
      return;
    }
    const owner = scope.current;
    const id = current.id;
    pending.current.add(id);
    try {
      await update(id, patch);
      if (scope.current !== owner) return;
      // 保存的响应只刷新列表，编辑器始终保留当前选择及最新草稿。
      if (announce) message.success("已保存");
      await load();
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      if (scope.current === owner) pending.current.delete(id);
    }
  };

  const save = () => persist({ md_content: md } as Partial<T>, true);
  const saveMetadata = (patch: Partial<T>) => persist(patch, false);

  const removeItem = async (id: string) => {
    if (pending.current.has(id) || generation.current) {
      message.info("请等待当前操作完成");
      return;
    }
    const owner = scope.current;
    pending.current.add(id);
    try {
      await remove(id);
      if (scope.current !== owner) return;
      // 使用当下的选择，删除旧卡片的响应不能清空新卡片草稿。
      setCurrent((selected) => {
        if (selected?.id !== id) return selected;
        return null;
      });
      await load();
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      if (scope.current === owner) pending.current.delete(id);
    }
  };

  const generate = () => {
    if (generation.current || pending.current.size > 0) {
      message.info("请等待当前操作完成");
      return;
    }
    const owner = scope.current;
    const ctrl = new AbortController();
    generation.current = ctrl;
    setGenerating(true);
    const active = () => scope.current === owner && generation.current === ctrl;
    const finish = () => {
      generation.current = null;
      setGenerating(false);
    };
    void sseFetch(generateEndpoint, generateBody(bookId), {
      signal: ctrl.signal,
      onEvent: (event, data) => {
        if (!active()) return;
        if (event === "done") {
          finish();
          void load();
        } else if (event === "error") {
          message.error(data || "生成失败");
          finish();
        }
      },
      onError: (err) => {
        if (!active()) return;
        message.error(err instanceof Error ? err.message : "生成失败");
        finish();
      },
    }).then(() => {
      if (active()) {
        message.error("生成连接已结束，请刷新列表确认结果");
        finish();
      }
    });
  };

  return {
    items,
    current,
    setCurrent,
    md,
    setMd,
    generating,
    load,
    select,
    add,
    save,
    saveMetadata,
    remove: removeItem,
    generate,
  };
}
