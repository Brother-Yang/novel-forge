import AsyncContent from "./AsyncContent";

// 小说设定以 Markdown 为主；保留常见语言高亮，避免加载整套编程语言语法。
const loadEditor = () => import("@uiw/react-md-editor/common");

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string | undefined) => void;
  height: number;
}

export default function MarkdownEditor(props: MarkdownEditorProps) {
  return <AsyncContent load={loadEditor} componentProps={props} label="Markdown 编辑器" />;
}
