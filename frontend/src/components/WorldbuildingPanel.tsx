import { useEffect, useState } from "react";
import { App, Button, Input, List, Popconfirm, Space, Typography } from "antd";
import { PlusOutlined, DeleteOutlined, SaveOutlined, ThunderboltOutlined } from "@ant-design/icons";
import MDEditor from "@uiw/react-md-editor";
import { api } from "../api";
import { sseFetch } from "../api/client";
import type { Worldbuilding } from "../api/types";

export default function WorldbuildingPanel({ bookId }: { bookId: number }) {
  const [list, setList] = useState<Worldbuilding[]>([]);
  const [current, setCurrent] = useState<Worldbuilding | null>(null);
  const [md, setMd] = useState("");
  const [generating, setGenerating] = useState(false);
  const { message } = App.useApp();

  const load = () => api.listWorldbuilding(bookId).then(setList);
  useEffect(() => {
    load();
  }, [bookId]);

  const select = (w: Worldbuilding) => {
    setCurrent(w);
    setMd(w.md_content);
  };

  const add = async () => {
    const w = await api.createWorldbuilding({
      book_id: bookId,
      title: "新设定",
      category: "general",
      md_content: "# 新设定\n\n",
    });
    await load();
    select(w);
  };

  const save = async () => {
    if (!current) return;
    const updated = await api.updateWorldbuilding(current.id, { md_content: md });
    setCurrent(updated);
    message.success("已保存");
    load();
  };

  const remove = async (id: number) => {
    await api.deleteWorldbuilding(id);
    if (current?.id === id) {
      setCurrent(null);
      setMd("");
    }
    load();
  };

  const generate = () => {
    setGenerating(true);
    sseFetch(
      "/generate/worldbuilding",
      { book_id: bookId },
      {
        onEvent: (event, data) => {
          if (event === "done") {
            setGenerating(false);
            load();
          } else if (event === "error") {
            message.error(data || "生成失败");
            setGenerating(false);
          }
        },
        onError: () => setGenerating(false),
      }
    );
  };

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
                onBlur={() => api.updateWorldbuilding(current.id, { title: current.title }).then(load)}
                style={{ width: 240, fontWeight: 600 }}
              />
              <Button type="primary" icon={<SaveOutlined />} onClick={save}>
                保存设定
              </Button>
            </Space>
            <MDEditor value={md} onChange={(v) => setMd(v ?? "")} height={window.innerHeight - 220} />
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
