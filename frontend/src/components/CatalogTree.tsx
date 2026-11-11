import { useCallback, useEffect, useMemo, useState } from "react";
import { App, Button, Dropdown, Space, Tree, Typography } from "antd";
import type { TreeDataNode } from "antd";
import { PlusOutlined, FileAddOutlined, MoreOutlined } from "@ant-design/icons";
import { api } from "../api";
import type { BookTree } from "../api/types";
import { useAppStore } from "../store";
import { CHAPTER_STATUS_COLOR } from "../constants/chapterStatus";

interface Props {
  bookId: string;
  selectedChapter: string | null;
  onSelectChapter: (id: string | null) => void;
}

// key 规则:卷 "v-<id>",章 "c-<id>",未归卷根 "loose"
export default function CatalogTree({ bookId, selectedChapter, onSelectChapter }: Props) {
  const [tree, setTree] = useState<BookTree | null>(null);
  const { message, modal } = App.useApp();
  const treeVersion = useAppStore((s) => s.treeVersion);

  const load = useCallback(() => api.getTree(bookId).then(setTree), [bookId]);
  useEffect(() => {
    load();
  }, [load, treeVersion]);

  const addVolume = async () => {
    const order = tree ? tree.volumes.length : 0;
    await api.createVolume({ book_id: bookId, title: `第 ${order + 1} 卷`, order_index: order });
    load();
  };

  const addChapter = async (volumeId: string | null) => {
    const count =
      volumeId == null
        ? (tree?.loose_chapters.length ?? 0)
        : (tree?.volumes.find((v) => v.id === volumeId)?.chapters.length ?? 0);
    await api.createChapter({
      book_id: bookId,
      volume_id: volumeId,
      title: `第 ${count + 1} 章`,
      order_index: count,
    });
    load();
  };

  const rename = (kind: "volume" | "chapter", id: string, current: string) => {
    let value = current;
    modal.confirm({
      title: `重命名${kind === "volume" ? "卷" : "章节"}`,
      content: (
        <input
          defaultValue={current}
          style={{ width: "100%", padding: 6, marginTop: 8 }}
          onChange={(e) => (value = e.target.value)}
        />
      ),
      onOk: async () => {
        if (kind === "volume") await api.updateVolume(id, { title: value });
        else await api.updateChapter(id, { title: value });
        load();
      },
    });
  };

  const remove = async (kind: "volume" | "chapter", id: string) => {
    if (kind === "volume") await api.deleteVolume(id);
    else await api.deleteChapter(id);
    if (kind === "chapter" && id === selectedChapter) onSelectChapter(null);
    message.success("已删除");
    load();
  };

  const nodeMenu = (kind: "volume" | "chapter", id: string, title: string) => ({
    items: [
      { key: "rename", label: "重命名" },
      ...(kind === "volume" ? [{ key: "addChapter", label: "新增章节" }] : []),
      { key: "delete", label: "删除", danger: true },
    ],
    onClick: ({ key, domEvent }: { key: string; domEvent: React.SyntheticEvent }) => {
      domEvent.stopPropagation();
      if (key === "rename") rename(kind, id, title);
      else if (key === "delete") remove(kind, id);
      else if (key === "addChapter") addChapter(id);
    },
  });

  const treeData: TreeDataNode[] = useMemo(() => {
    if (!tree) return [];
    const renderChapter = (c: {
      id: string;
      title: string;
      status: string;
      word_count: number;
    }): TreeDataNode => ({
      key: `c-${c.id}`,
      isLeaf: true,
      title: (
        <Space size={4} style={{ justifyContent: "space-between", width: "100%" }}>
          <span>
            <span
              style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: CHAPTER_STATUS_COLOR[c.status] ?? "#bfbfbf",
                marginRight: 6,
              }}
            />
            {c.title}
            <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
              {c.word_count > 0 ? `${c.word_count}字` : ""}
            </Typography.Text>
          </span>
          <Dropdown menu={nodeMenu("chapter", c.id, c.title)} trigger={["click"]}>
            <MoreOutlined onClick={(e) => e.stopPropagation()} />
          </Dropdown>
        </Space>
      ),
    });

    const volumeNodes: TreeDataNode[] = tree.volumes.map((v) => ({
      key: `v-${v.id}`,
      title: (
        <Space size={4} style={{ justifyContent: "space-between", width: "100%" }}>
          <strong>{v.title}</strong>
          <Dropdown menu={nodeMenu("volume", v.id, v.title)} trigger={["click"]}>
            <MoreOutlined onClick={(e) => e.stopPropagation()} />
          </Dropdown>
        </Space>
      ),
      children: v.chapters.map(renderChapter),
    }));

    const looseNodes = tree.loose_chapters.map(renderChapter);
    return [...volumeNodes, ...looseNodes];
  }, [tree]);

  return (
    <div className="nf-catalog">
      <div className="nf-catalog__bar">
        <span className="nf-catalog__label">目录</span>
        <Space size={6}>
          <Button size="small" icon={<PlusOutlined />} onClick={addVolume}>
            卷
          </Button>
          <Button size="small" icon={<FileAddOutlined />} onClick={() => addChapter(null)}>
            章
          </Button>
        </Space>
      </div>
      <Tree
        key={tree ? "loaded" : "empty"}
        blockNode
        showLine={false}
        defaultExpandAll
        height={window.innerHeight - 160}
        treeData={treeData}
        selectedKeys={selectedChapter ? [`c-${selectedChapter}`] : []}
        onSelect={(keys) => {
          const k = keys[0] as string | undefined;
          if (k && k.startsWith("c-")) onSelectChapter(k.slice(2));
        }}
      />
    </div>
  );
}
