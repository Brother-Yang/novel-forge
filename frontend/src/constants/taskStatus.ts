// 生成任务状态对应的 Tag 颜色与中文文案
export const TASK_STATUS_TAG: Record<string, { color: string; text: string }> = {
  pending: { color: "default", text: "等待" },
  running: { color: "processing", text: "进行中" },
  paused: { color: "warning", text: "已暂停" },
  done: { color: "success", text: "完成" },
  error: { color: "error", text: "错误" },
};

export const TASK_START_ERROR = "生成任务启动失败，请在任务面板重试";

export function isTaskActive(status: string | undefined): boolean {
  return status === "pending" || status === "running";
}

export const TASK_TYPE_LABEL: Record<string, string> = {
  novel: "全书自动生成",
  book: "全书正文生成",
  agent: "AI 自主创作",
};

export const TASK_STAGE_LABEL: Record<string, string> = {
  planner: "规划书目",
  worldbuilding: "生成世界观",
  characters: "生成人物",
  outline: "生成全书大纲",
  chapter_outlines: "生成章节大纲",
  chapter_bodies: "生成正文",
  done: "完成",
};

export function taskStageLabel(checkpointJson: string | undefined): string {
  if (!checkpointJson) return "";
  try {
    const ckpt = JSON.parse(checkpointJson) as { stage?: string };
    if (!ckpt.stage) return "";
    return TASK_STAGE_LABEL[ckpt.stage] ?? ckpt.stage;
  } catch {
    return "";
  }
}
