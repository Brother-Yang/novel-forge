import AsyncContent from "./AsyncContent";

const loadEditor = () => import("@uiw/react-md-editor");

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string | undefined) => void;
  height: number;
}

export default function MarkdownEditor(props: MarkdownEditorProps) {
  return <AsyncContent load={loadEditor} componentProps={props} label="Markdown 编辑器" />;
}
