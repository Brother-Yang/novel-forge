import { useCallback, useEffect, useMemo, useState } from "react";
import { App, Button, Dropdown, Space, Tree, Typography } from "antd";
import type { TreeDataNode, TreeProps } from "antd";
import { PlusOutlined, FileAddOutlined, MoreOutlined } from "@ant-design/icons";
import { api } from "../api";
import type { BookTree } from "../api/types";
import { useAppStore } from "../store";
import { CHAPTER_STATUS_COLOR } from "../constants/chapterStatus";

interface Props {
  bookId: number;
  selectedChapter: number | null;
  onSelectChapter: (id: number) => void;
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

  const addChapter = async (volumeId: number | null) => {
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

  const rename = (kind: "volume" | "chapter", id: number, current: string) => {
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

  const remove = async (kind: "volume" | "chapter", id: number) => {
    if (kind === "volume") await api.deleteVolume(id);
    else await api.deleteChapter(id);
    if (kind === "chapter" && id === selectedChapter) onSelectChapter(0);
    message.success("已删除");
    load();
  };

  const nodeMenu = (kind: "volume" | "chapter", id: number, title: string) => ({
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
      id: number;
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

  // 拖拽:本地重建结构后,批量持久化章节 order_index 与 volume_id
  const onDrop: TreeProps["onDrop"] = async (info) => {
    if (!tree) return;
    const dragId = Number((info.dragNode.key as string).slice(2));
    const dragIsChapter = (info.dragNode.key as string).startsWith("c-");
    if (!dragIsChapter) return; // 简化:仅支持章节拖拽

    const dropKey = info.node.key as string;
    let targetVolume: number | null = null;
    if (dropKey.startsWith("v-")) targetVolume = Number(dropKey.slice(2));
    else if (dropKey.startsWith("c-")) {
      const cid = Number(dropKey.slice(2));
      const owner = tree.volumes.find((v) => v.chapters.some((c) => c.id === cid));
      targetVolume = owner ? owner.id : null;
    }

    const siblings =
      targetVolume == null
        ? tree.loose_chapters.map((c) => c.id)
        : tree.volumes.find((v) => v.id === targetVolume)!.chapters.map((c) => c.id);

    const filtered = siblings.filter((x) => x !== dragId);
    let insertAt = filtered.length;
    if (dropKey.startsWith("c-")) {
      const dropId = Number(dropKey.slice(2));
      const idx = filtered.indexOf(dropId);
      insertAt = info.dropPosition < 0 ? idx : idx + 1;
    }
    filtered.splice(insertAt, 0, dragId);

    await api.reorderChapters(
      filtered.map((cid, i) => ({ id: cid, order_index: i, volume_id: targetVolume }))
    );
    load();
  };

  return (
    <div style={{ padding: 8 }}>
      <Space style={{ marginBottom: 8, paddingLeft: 4 }}>
        <Button size="small" icon={<PlusOutlined />} onClick={addVolume}>
          卷
        </Button>
        <Button size="small" icon={<FileAddOutlined />} onClick={() => addChapter(null)}>
          章
        </Button>
      </Space>
      <Tree
        blockNode
        draggable
        showLine
        height={window.innerHeight - 160}
        treeData={treeData}
        selectedKeys={selectedChapter ? [`c-${selectedChapter}`] : []}
        onDrop={onDrop}
        onSelect={(keys) => {
          const k = keys[0] as string | undefined;
          if (k && k.startsWith("c-")) onSelectChapter(Number(k.slice(2)));
        }}
      />
    </div>
  );
}
