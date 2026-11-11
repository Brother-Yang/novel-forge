import { fetchWithSession, requireOk } from "../api/client";

function filenameFrom(response: Response, fallback: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return fallback;
    }
  }
  return /filename="([^"]+)"/i.exec(disposition)?.[1] ?? fallback;
}

/** 用当前会话请求导出，避免把鉴权或服务错误保存成文件。 */
export async function downloadFile(url: string, fallbackFilename: string): Promise<void> {
  const response = await fetchWithSession(url);
  await requireOk(response);
  const objectUrl = URL.createObjectURL(await response.blob());
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filenameFrom(response, fallbackFilename);
    anchor.click();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}
