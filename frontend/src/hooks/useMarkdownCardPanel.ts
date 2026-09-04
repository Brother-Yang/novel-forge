import { useCallback, useEffect, useState } from "react";
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

/**
 * 人物卡 / 设定卡面板的共用逻辑:列表加载、增删改、AI 生成(SSE)。
 * 两个面板 UI 展示的字段不同(name/title 等),因此渲染部分仍由各自组件负责。
 */
export function useMarkdownCardPanel<T extends MarkdownCard>({
  bookId,
  list,
  create,
  update,
  remove,
  generateEndpoint,
  generateBody,
}: UseMarkdownCardPanelOptions<T>) {
  const [items, setItems] = useState<T[]>([]);
  const [current, setCurrent] = useState<T | null>(null);
  const [md, setMd] = useState("");
  const [generating, setGenerating] = useState(false);
  const { message } = App.useApp();

  const load = useCallback(() => list(bookId).then(setItems), [bookId, list]);
  useEffect(() => {
    load();
  }, [load]);

  const select = (item: T) => {
    setCurrent(item);
    setMd(item.md_content);
  };

  const add = async () => {
    const item = await create(items.length);
    await load();
    select(item);
  };

  const save = async () => {
    if (!current) return;
    const updated = await update(current.id, { md_content: md } as Partial<T>);
    setCurrent(updated);
    message.success("已保存");
    load();
  };

  const removeItem = async (id: string) => {
    await remove(id);
    if (current?.id === id) {
      setCurrent(null);
      setMd("");
    }
    load();
  };

  const generate = () => {
    setGenerating(true);
    sseFetch(generateEndpoint, generateBody(bookId), {
      onEvent: (event, data) => {
        if (event === "done") {
          setGenerating(false);
          load();
        } else if (event === "error") {
          message.error(data || "生成失败");
          setGenerating(false);
        }
      },
      onError: (err) => {
        message.error(err instanceof Error ? err.message : "生成失败");
        setGenerating(false);
      },
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
    remove: removeItem,
    generate,
  };
}
