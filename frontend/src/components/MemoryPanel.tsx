import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, List, Space, Spin, Tag, Typography } from "antd";
import { api } from "../api";
import type { MemoryProgress, MemoryStatus } from "../api/types";

interface MemoryPanelProps {
  bookId: string;
}

const sourceLabels = {
  worldbuilding: "世界观设定",
  chapter_summary: "章节摘要",
  volume_summary: "卷摘要",
  chapter_facts: "章节事实",
};
const statusLabels = { pending: "等待同步", running: "正在同步", error: "同步失败" };

function BookMemoryPanel({ bookId }: MemoryPanelProps) {
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [progress, setProgress] = useState<MemoryProgress | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState({ pollAfterRepair: false });
  const [repairing, setRepairing] = useState(false);
  const repairingRef = useRef(false);
  const requestVersion = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fail = (version: number, err: unknown) => {
      if (cancelled || version !== requestVersion.current) return;
      setError(err instanceof Error ? err.message : "记忆状态加载失败");
    };
    const loadProgress = () => {
      if (repairingRef.current) return;
      const version = ++requestVersion.current;
      api.getMemoryProgress(bookId, request.signal).then(
        (data) => {
          if (cancelled || version !== requestVersion.current) return;
          setProgress(data);
          setError("");
          if (data.pending + data.running > 0) timer = setTimeout(loadProgress, 2500);
          else loadStatus();
        },
        (err: unknown) => fail(version, err)
      );
    };
    const loadStatus = () => {
      if (repairingRef.current) return;
      const version = ++requestVersion.current;
      api.getMemoryStatus(bookId, request.signal).then(
        (data) => {
          if (cancelled || version !== requestVersion.current) return;
          setStatus(data);
          setProgress(null);
          setError("");
          if (data.pending + data.running > 0) timer = setTimeout(loadProgress, 2500);
        },
        (err: unknown) => fail(version, err)
      );
    };
    if (!repairingRef.current) {
      if (refresh.pollAfterRepair) timer = setTimeout(loadProgress, 2500);
      else loadStatus();
    }
    return () => {
      cancelled = true;
      request.abort();
      clearTimeout(timer);
      requestVersion.current += 1;
    };
  }, [bookId, refresh]);

  const repair = async () => {
    if (repairingRef.current) return;
    repairingRef.current = true;
    setRepairing(true);
    // 使修复前已发出的查询失效,避免旧结果把新状态覆盖。
    const version = ++requestVersion.current;
    let submitted: MemoryProgress | undefined;
    try {
      const result = await api.repairMemory(bookId);
      if (version !== requestVersion.current) return;
      setProgress(result);
      setError("");
      submitted = result;
    } catch (err) {
      if (version === requestVersion.current) {
        setError(err instanceof Error ? err.message : "提交记忆修复失败");
      }
    } finally {
      if (version === requestVersion.current) {
        repairingRef.current = false;
        setRepairing(false);
        if (submitted) {
          setRefresh({ pollAfterRepair: submitted.pending + submitted.running > 0 });
        }
      }
    }
  };

  const activity = progress ?? status;
  const missingKnown = progress === null || !refresh.pollAfterRepair;

  return (
    <Card title="创作记忆" style={{ marginBottom: 16 }}>
      <Typography.Paragraph type="secondary">
        记忆用于后续章节检索。修复会补齐摘要、人物与剧情事实和索引，保留已保存的正文。
      </Typography.Paragraph>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button disabled={repairing} onClick={() => setRefresh({ pollAfterRepair: false })}>
              重试查询
            </Button>
          }
          style={{ marginBottom: 12 }}
        />
      )}
      {!status && !error && <Spin aria-label="加载记忆状态" />}
      {status && activity && (
        <>
          <Space wrap>
            <Tag color="green">
              可用 {status.ready} / {status.total}
            </Tag>
            <Tag>等待 {activity.pending}</Tag>
            <Tag color="processing">处理中 {activity.running}</Tag>
            <Tag color={activity.failed ? "error" : "default"}>失败 {activity.failed}</Tag>
            <Tag color={status.missing ? "warning" : "default"}>缺失 {status.missing}</Tag>
            <Button
              loading={repairing}
              disabled={repairing || (activity.failed === 0 && (!missingKnown || !status.missing))}
              onClick={() => void repair()}
            >
              修复缺失或失败的记忆
            </Button>
            <Button disabled={repairing} onClick={() => setRefresh({ pollAfterRepair: false })}>
              刷新状态
            </Button>
          </Space>
          {activity.pending + activity.running > 0 && (
            <Typography.Paragraph type="secondary">
              正在同步，完成后会重新校验可用数量。
            </Typography.Paragraph>
          )}
          {activity.items.length > 0 && (
            <List
              size="small"
              dataSource={activity.items}
              rowKey="id"
              renderItem={(item) => (
                <List.Item>
                  <Space wrap>
                    <Typography.Text>{sourceLabels[item.source_type]}</Typography.Text>
                    <Tag>{statusLabels[item.status]}</Tag>
                    {item.error && <Typography.Text type="danger">{item.error}</Typography.Text>}
                  </Space>
                </List.Item>
              )}
            />
          )}
        </>
      )}
    </Card>
  );
}

export default function MemoryPanel({ bookId }: MemoryPanelProps) {
  return <BookMemoryPanel key={bookId} bookId={bookId} />;
}
