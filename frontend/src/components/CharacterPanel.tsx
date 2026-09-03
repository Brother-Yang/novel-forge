import { Button, Input, List, Popconfirm, Space, Typography } from "antd";
import { PlusOutlined, DeleteOutlined, SaveOutlined, ThunderboltOutlined } from "@ant-design/icons";
import MDEditor from "@uiw/react-md-editor";
import { api, sseEndpoints } from "../api";
import type { Character } from "../api/types";
import { useMarkdownCardPanel } from "../hooks/useMarkdownCardPanel";

export default function CharacterPanel({ bookId }: { bookId: string }) {
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
  } = useMarkdownCardPanel<Character>({
    bookId,
    list: api.listCharacters,
    create: (count) =>
      api.createCharacter({
        book_id: bookId,
        name: "新人物",
        md_content: "# 新人物\n\n- 身份:\n- 性格:\n- 外貌:\n- 目标:",
        order_index: count,
      }),
    update: (id, patch) => api.updateCharacter(id, patch),
    remove: (id) => api.deleteCharacter(id),
    generateEndpoint: sseEndpoints.characters,
    generateBody: (id) => ({ book_id: id, count: 5 }),
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
          renderItem={(c) => (
            <List.Item
              onClick={() => select(c)}
              style={{
                cursor: "pointer",
                background: current?.id === c.id ? "#e6f4ff" : undefined,
                paddingInline: 8,
              }}
              actions={[
                <Popconfirm key="d" title="删除?" onConfirm={() => remove(c.id)}>
                  <DeleteOutlined onClick={(e) => e.stopPropagation()} />
                </Popconfirm>,
              ]}
            >
              {c.name}
            </List.Item>
          )}
        />
      </div>

      <div style={{ flex: 1, padding: 16, overflow: "auto" }} data-color-mode="light">
        {current ? (
          <>
            <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
              <Input
                value={current.name}
                onChange={(e) => setCurrent({ ...current, name: e.target.value })}
                onBlur={() => api.updateCharacter(current.id, { name: current.name }).then(load)}
                style={{ width: 240, fontWeight: 600 }}
              />
              <Button type="primary" icon={<SaveOutlined />} onClick={save}>
                保存人物卡
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
            选择或新增一个人物,每个人物维护一份 Markdown 人物卡。
          </Typography.Text>
        )}
      </div>
    </div>
  );
}
