import { Button, Input, Popconfirm, Space } from "antd";
import { PlusOutlined, DeleteOutlined, SaveOutlined, ThunderboltOutlined } from "@ant-design/icons";
import MarkdownEditor from "./MarkdownEditor";
import CharacterFacts from "./CharacterFacts";
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
    select,
    add,
    save,
    saveMetadata,
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
    <div className="nf-card-panel">
      <div className="nf-card-panel__list">
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
        <div className="nf-namecards">
          {list.map((c) => (
            <div
              key={c.id}
              className={`nf-namecard${current?.id === c.id ? " nf-namecard--active" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => select(c)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  select(c);
                }
              }}
            >
              <span className="nf-namecard__name">{c.name}</span>
              <Popconfirm title="删除?" onConfirm={() => remove(c.id)}>
                <DeleteOutlined onClick={(e) => e.stopPropagation()} />
              </Popconfirm>
            </div>
          ))}
        </div>
      </div>

      <div className="nf-card-panel__editor" data-color-mode="light">
        {current ? (
          <>
            <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
              <Input
                value={current.name}
                onChange={(e) => setCurrent({ ...current, name: e.target.value })}
                onBlur={() => void saveMetadata({ name: current.name })}
                style={{ width: 240, fontWeight: 600, fontFamily: "var(--font-serif)" }}
              />
              <Button type="primary" icon={<SaveOutlined />} onClick={save}>
                保存人物卡
              </Button>
            </Space>
            <CharacterFacts state={current.derived_state} />
            <MarkdownEditor
              value={md}
              onChange={(v) => setMd(v ?? "")}
              height={window.innerHeight - 220}
            />
          </>
        ) : (
          <div className="nf-empty nf-empty--fill">
            <span className="nf-empty__glyph">人</span>
            <p>选择或新增一个人物,每个人物维护一份 Markdown 人物卡。</p>
          </div>
        )}
      </div>
    </div>
  );
}
