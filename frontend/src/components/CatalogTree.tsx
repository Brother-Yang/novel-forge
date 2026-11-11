import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { Alert, App, Button, Dropdown, Space, Tree, Typography } from "antd";
import type { TreeDataNode, TreeProps } from "antd";
import { PlusOutlined, FileAddOutlined, MoreOutlined } from "@ant-design/icons";
import { api } from "../api";
import type { BookTree } from "../api/types";
import { chapterDraftKey, useAppStore } from "../store";
import { CHAPTER_STATUS_COLOR } from "../constants/chapterStatus";
import { reorderCatalog, type ChapterDropTarget } from "../utils/catalogReorder";
import { AuthSessionRevisionContext } from "./authContext";

interface Props {
  bookId: string;
  selectedChapter: string | null;
  onSelectChapter: (id: string | null) => void;
}

// key 规则:卷 "v-<id>",章 "c-<id>",未归卷根 "loose"
export default function CatalogTree({ bookId, selectedChapter, onSelectChapter }: Props) {
  const [snapshot, setSnapshot] = useState<{ bookId: string; tree: BookTree } | null>(null);
  const tree = snapshot?.bookId === bookId ? snapshot.tree : null;
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const directoryUnavailable = !tree || Boolean(loadError);
  const [collapsedKeys, setCollapsedKeys] = useState<string[]>([]);
  const pending = useRef(false);
  const scope = useRef(0);
  const requestVersion = useRef(0);
  const { message, modal } = App.useApp();
  const treeVersion = useAppStore((s) => s.treeVersion);
  const sessionRevision = useContext(AuthSessionRevisionContext);

  useEffect(() => {
    scope.current += 1;
    pending.current = false;
    return () => {
      scope.current += 1;
      requestVersion.current += 1;
    };
  }, [bookId]);

  const load = useCallback(() => {
    const request = ++requestVersion.current;
    const owner = scope.current;
    return api
      .getTree(bookId)
      .then((data) => {
        if (scope.current !== owner || request !== requestVersion.current) return;
        setSnapshot({ bookId, tree: data });
        setLoadError("");
      })
      .catch((err: unknown) => {
        if (scope.current !== owner || request !== requestVersion.current) return;
        setLoadError(err instanceof Error ? err.message : "加载目录失败");
      });
  }, [bookId]);
  useEffect(() => {
    void load();
  }, [load, treeVersion, sessionRevision]);

  const mutate = async (action: () => Promise<unknown>): Promise<boolean> => {
    if (pending.current) return false;
    const owner = scope.current;
    pending.current = true;
    setBusy(true);
    // 作废写入前的目录请求，避免它在写入后把旧节点放回界面。
    requestVersion.current += 1;
    try {
      await action();
      if (scope.current !== owner) return false;
      await load();
      return true;
    } catch (err: unknown) {
      if (scope.current === owner)
        message.error(err instanceof Error ? err.message : "目录操作失败");
      return false;
    } finally {
      if (scope.current === owner) {
        pending.current = false;
        setBusy(false);
      }
    }
  };

  const addVolume = async () => {
    if (!tree || loadError) return;
    const order =
      tree.volumes.reduce((highest, volume) => Math.max(highest, volume.order_index), -1) + 1;
    await mutate(() =>
      api.createVolume({ book_id: bookId, title: `第 ${order + 1} 卷`, order_index: order })
    );
  };

  const addChapter = async (volumeId: string | null) => {
    if (!tree || loadError) return;
    const count =
      volumeId == null
        ? tree.loose_chapters.length
        : (tree.volumes.find((v) => v.id === volumeId)?.chapters.length ?? 0);
    const chapters = [...tree.volumes.flatMap((volume) => volume.chapters), ...tree.loose_chapters];
    const nextOrder = Math.max(-1, ...chapters.map((chapter) => chapter.order_index)) + 1;
    await mutate(() =>
      api.createChapter({
        book_id: bookId,
        volume_id: volumeId,
        title: `第 ${count + 1} 章`,
        order_index: nextOrder,
      })
    );
  };

  const rename = async (kind: "volume" | "chapter", id: string, current: string) => {
    let value = current;
    await modal.confirm({
      title: `重命名${kind === "volume" ? "卷" : "章节"}`,
      okText: "保存",
      cancelText: "取消",
      content: (
        <input
          defaultValue={current}
          style={{ width: "100%", padding: 6, marginTop: 8 }}
          onChange={(e) => (value = e.target.value)}
        />
      ),
      onOk: async () => {
        if (!value.trim()) {
          message.error("名称不能为空");
          throw new Error("名称不能为空");
        }
        const saved = await mutate(() =>
          kind === "volume"
            ? api.updateVolume(id, { title: value.trim() })
            : api.updateChapter(id, { title: value.trim() })
        );
        if (!saved) throw new Error("目录操作未完成");
      },
    });
  };

  const remove = async (kind: "volume" | "chapter", id: string, title: string) => {
    const isVolume = kind === "volume";
    const draftKey = chapterDraftKey(bookId, id);
    const hasDraft = !isVolume && useAppStore.getState().chapterDrafts[draftKey];
    await modal.confirm({
      title: `删除${isVolume ? "卷" : "章节"}《${title}》？`,
      content: isVolume
        ? "仅删除卷，卷内章节及正文会保留并移到“未归卷”。"
        : `该章节的正文和摘要将一并删除，此操作无法撤销。${hasDraft ? "本窗口的未保存草稿也会清除。" : ""}`,
      okText: isVolume ? "删除卷" : "删除章节",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        const saved = await mutate(async () => {
          if (isVolume) {
            await api.deleteVolume(id);
          } else {
            await api.deleteChapter(id);
            useAppStore.getState().clearChapterDraft(bookId, id);
          }
        });
        if (!saved) throw new Error("目录操作未完成");
        if (!isVolume && id === selectedChapter) onSelectChapter(null);
        message.success(isVolume ? "已删除卷，章节及正文已保留" : "已删除章节");
      },
    });
  };

  const nodeMenu = (kind: "volume" | "chapter", id: string, title: string) => ({
    items: [
      { key: "rename", label: "重命名", disabled: busy || directoryUnavailable },
      ...(kind === "volume"
        ? [{ key: "addChapter", label: "新增章节", disabled: busy || directoryUnavailable }]
        : []),
      { key: "delete", label: "删除", danger: true, disabled: busy || directoryUnavailable },
    ],
    onClick: ({ key, domEvent }: { key: string; domEvent: React.SyntheticEvent }) => {
      domEvent.stopPropagation();
      if (key === "rename") rename(kind, id, title);
      else if (key === "delete") remove(kind, id, title);
      else if (key === "addChapter") addChapter(id);
    },
  });

  const onDrop: TreeProps["onDrop"] = async (info) => {
    if (!tree || loadError || pending.current) return;
    const sourceKey = String(info.dragNode.key);
    const targetKey = String(info.node.key);
    if (!sourceKey.startsWith("c-")) return;
    let target: ChapterDropTarget;
    if (targetKey.startsWith("c-") && info.dropToGap) {
      const position = info.dropPosition - Number(info.node.pos.split("-").pop());
      target = {
        kind: "chapter",
        id: targetKey.slice(2),
        position: position < 0 ? "before" : "after",
      };
    } else if (!info.dropToGap && (targetKey.startsWith("v-") || targetKey === "loose")) {
      target = { kind: "volume", id: targetKey === "loose" ? null : targetKey.slice(2) };
    } else return;
    const orders = reorderCatalog(tree, sourceKey.slice(2), target);
    if (orders.length) await mutate(() => api.reorderChapters(orders));
  };

  const treeData: TreeDataNode[] = [];
  if (tree) {
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
    treeData.push(...volumeNodes, { key: "loose", title: "未归卷", children: looseNodes });
  }

  const groupKeys = [...(tree?.volumes.map((volume) => `v-${volume.id}`) ?? []), "loose"];

  return (
    <div className="nf-catalog">
      <div className="nf-catalog__bar">
        <span className="nf-catalog__label">目录</span>
        <Space size={6}>
          <Button
            size="small"
            aria-label="新增卷"
            disabled={busy || directoryUnavailable}
            icon={<PlusOutlined />}
            onClick={addVolume}
          >
            卷
          </Button>
          <Button
            size="small"
            aria-label="新增章节"
            disabled={busy || directoryUnavailable}
            icon={<FileAddOutlined />}
            onClick={() => addChapter(null)}
          >
            章
          </Button>
        </Space>
      </div>
      {loadError && (
        <Alert
          type="error"
          message={loadError}
          action={
            <Button size="small" onClick={() => void load()}>
              重试
            </Button>
          }
        />
      )}
      <Tree
        key={tree ? "loaded" : "empty"}
        blockNode
        showLine={false}
        expandedKeys={groupKeys.filter((key) => !collapsedKeys.includes(key))}
        onExpand={(keys) => setCollapsedKeys(groupKeys.filter((key) => !keys.includes(key)))}
        draggable={
          !busy &&
          !directoryUnavailable && { nodeDraggable: (node) => String(node.key).startsWith("c-") }
        }
        allowDrop={({ dropNode, dropPosition }) =>
          String(dropNode.key).startsWith("c-") ? dropPosition !== 0 : dropPosition === 0
        }
        onDrop={onDrop}
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
