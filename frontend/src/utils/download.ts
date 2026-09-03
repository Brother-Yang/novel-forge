/** 触发浏览器下载指定 URL 的文件(通过临时 <a> 元素模拟点击)。 */
export function downloadFile(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.click();
}
