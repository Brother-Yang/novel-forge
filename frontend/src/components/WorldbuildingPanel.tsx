import { Button, Input, List, Popconfirm, Space, Typography } from "antd";
import { PlusOutlined, DeleteOutlined, SaveOutlined, ThunderboltOutlined } from "@ant-design/icons";
import MDEditor from "@uiw/react-md-editor";
import { api, sseEndpoints } from "../api";
import type { Worldbuilding } from "../api/types";
import { useMarkdownCardPanel } from "../hooks/useMarkdownCardPanel";

export default function WorldbuildingPanel({ bookId }: { bookId: number }) {
  const {
    items: list,
    current,
    setCurrent,
    md,
    setMd,
    generating,
    load,
    select,
    add,
    save,
    remove,
    generate,
  } = useMarkdownCardPanel<Worldbuilding>({
    bookId,
    list: api.listWorldbuilding,
    create: () =>
      api.createWorldbuilding({
        book_id: bookId,
        title: "新设定",
        category: "general",
        md_content: "# 新设定\n\n",
      }),
    update: (id, patch) => api.updateWorldbuilding(id, patch),
    remove: (id) => api.deleteWorldbuilding(id),
    generateEndpoint: sseEndpoints.worldbuilding,
    generateBody: (id) => ({ book_id: id }),
  });

  return (
    <div style={{ display: "flex", height: "100%" }}>
      <div style={{ width: 240, borderRight: "1px solid #f0f0f0", padding: 12, overflow: "auto" }}>
        <Space style={{ marginBottom: 8 }}>
          <Button size="small" icon={<PlusOutlined />} onClick={add}>
            新增
          </Button>
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            loading={generating}
            onClick={generate}
          >
            AI 生成
          </Button>
        </Space>
        <List
          size="small"
          dataSource={list}
          renderItem={(w) => (
            <List.Item
              onClick={() => select(w)}
              style={{
                cursor: "pointer",
                background: current?.id === w.id ? "#e6f4ff" : undefined,
                paddingInline: 8,
              }}
              actions={[
                <Popconfirm key="d" title="删除?" onConfirm={() => remove(w.id)}>
                  <DeleteOutlined onClick={(e) => e.stopPropagation()} />
                </Popconfirm>,
              ]}
            >
              {w.title}
            </List.Item>
          )}
        />
      </div>

      <div style={{ flex: 1, padding: 16, overflow: "auto" }} data-color-mode="light">
        {current ? (
          <>
            <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
              <Input
                value={current.title}
                onChange={(e) => setCurrent({ ...current, title: e.target.value })}
                onBlur={() =>
                  api.updateWorldbuilding(current.id, { title: current.title }).then(load)
                }
                style={{ width: 240, fontWeight: 600 }}
              />
              <Button type="primary" icon={<SaveOutlined />} onClick={save}>
                保存设定
              </Button>
            </Space>
            <MDEditor
              value={md}
              onChange={(v) => setMd(v ?? "")}
              height={window.innerHeight - 220}
            />
          </>
        ) : (
          <Typography.Text type="secondary">
            维护世界观、地理、势力、物品等设定,生成正文时按需注入。
          </Typography.Text>
        )}
      </div>
    </div>
  );
}
