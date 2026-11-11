import { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import { useGenerationStream } from "./useGenerationStream";

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
  const scope = useRef(0);
  const listRequest = useRef(0);
  const pending = useRef(new Set<string>());
  const savingFields = useRef(new Map<string, Set<keyof T>>());
  const stream = useGenerationStream(bookId);
  const selectionVersion = useRef(0);
  const { message } = App.useApp();

  if (editingBookId !== bookId) {
    setEditingBookId(bookId);
    setItems([]);
    setCurrent(null);
    setMd("");
  }

  useEffect(() => {
    scope.current += 1;
    pending.current.clear();
    savingFields.current.clear();
    return () => {
      scope.current += 1;
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
    if (pending.current.has("create") || stream.isRunning()) return;
    const owner = scope.current;
    const selection = selectionVersion.current;
    pending.current.add("create");
    try {
      const item = await create(items.length);
      if (scope.current !== owner) return;
      setItems((existing) => [...existing, item]);
      // 创建完成时不抢走用户后来选择的卡片。
      if (selectionVersion.current === selection) select(item);
      void load();
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "新增失败");
    } finally {
      if (scope.current === owner) pending.current.delete("create");
    }
  };

  const persist = async (patch: Partial<T>, announce: boolean) => {
    if (!current) return;
    const fields = Object.keys(patch) as (keyof T)[];
    const activeFields = savingFields.current.get(current.id) ?? new Set<keyof T>();
    if (
      stream.isRunning() ||
      pending.current.has(current.id) ||
      fields.some((field) => activeFields.has(field))
    ) {
      message.info("请等待当前操作完成后重试保存");
      return;
    }
    const owner = scope.current;
    const id = current.id;
    // 标题失焦后紧接着点击保存正文，是两个独立字段的 PATCH。
    // 只互斥重叠字段，不能因为标题请求尚未返回就丢弃正文保存。
    for (const field of fields) activeFields.add(field);
    savingFields.current.set(id, activeFields);
    try {
      await update(id, patch);
      if (scope.current !== owner) return;
      // 保存的响应只刷新列表，编辑器始终保留当前选择及最新草稿。
      if (announce) message.success("已保存");
      setItems((existing) =>
        existing.map((item) => (item.id === id ? { ...item, ...patch } : item))
      );
      // 写入已经完成；慢列表刷新不应继续占用写锁。
      void load();
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      if (scope.current === owner) {
        for (const field of fields) activeFields.delete(field);
        if (activeFields.size === 0) savingFields.current.delete(id);
      }
    }
  };

  const save = () => persist({ md_content: md } as Partial<T>, true);
  const saveMetadata = (patch: Partial<T>) => persist(patch, false);

  const removeItem = async (id: string) => {
    if (pending.current.has(id) || savingFields.current.has(id) || stream.isRunning()) {
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
      setItems((existing) => existing.filter((item) => item.id !== id));
      void load();
    } catch (err: unknown) {
      if (scope.current === owner) message.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      if (scope.current === owner) pending.current.delete(id);
    }
  };

  const generate = () => {
    if (stream.isRunning() || pending.current.size > 0 || savingFields.current.size > 0) {
      message.info("请等待当前操作完成");
      return;
    }
    stream.start(generateEndpoint, generateBody(bookId), {
      onDone: () => {
        void load();
      },
      onError: (err) => message.error(err instanceof Error ? err.message : "生成失败"),
    });
  };

  return {
    items,
    current,
    setCurrent,
    md,
    setMd,
    generating: stream.running,
    load,
    select,
    add,
    save,
    saveMetadata,
    remove: removeItem,
    generate,
  };
}
