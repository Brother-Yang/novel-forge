import { Button, Input, Popconfirm, Space } from "antd";
import { PlusOutlined, DeleteOutlined, SaveOutlined, ThunderboltOutlined } from "@ant-design/icons";
import MDEditor from "@uiw/react-md-editor";
import { api, sseEndpoints } from "../api";
import type { Worldbuilding } from "../api/types";
import { useMarkdownCardPanel } from "../hooks/useMarkdownCardPanel";

export default function WorldbuildingPanel({ bookId }: { bookId: string }) {
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
          {list.map((w) => (
            <div
              key={w.id}
              className={`nf-namecard${current?.id === w.id ? " nf-namecard--active" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => select(w)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  select(w);
                }
              }}
            >
              <span className="nf-namecard__name">{w.title}</span>
              <Popconfirm title="删除?" onConfirm={() => remove(w.id)}>
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
                value={current.title}
                onChange={(e) => setCurrent({ ...current, title: e.target.value })}
                onBlur={() =>
                  api.updateWorldbuilding(current.id, { title: current.title }).then(load)
                }
                style={{ width: 240, fontWeight: 600, fontFamily: "var(--font-serif)" }}
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
            <div className="nf-empty nf-empty--fill">
              <span className="nf-empty__glyph">界</span>
              <p>维护世界观、地理、势力、物品等设定,生成正文时按需注入。</p>
            </div>
        )}
      </div>
    </div>
  );
}
