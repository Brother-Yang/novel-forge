// 生成任务状态对应的 Tag 颜色与中文文案
export const TASK_STATUS_TAG: Record<string, { color: string; text: string }> = {
  pending: { color: "default", text: "等待" },
  running: { color: "processing", text: "进行中" },
  paused: { color: "warning", text: "已暂停" },
  done: { color: "success", text: "完成" },
  error: { color: "error", text: "错误" },
};
