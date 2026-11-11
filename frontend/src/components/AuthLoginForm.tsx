import { Alert, Button, Card, Form, Input } from "antd";

interface AuthLoginFormProps {
  error?: string;
  submitting: boolean;
  onLogin: (password: string) => Promise<void>;
}

export default function AuthLoginForm({ error, submitting, onLogin }: AuthLoginFormProps) {
  return (
    <Card title="登录小说工作台" style={{ width: "min(380px, 92vw)" }}>
      <Form layout="vertical" onFinish={({ password }: { password: string }) => onLogin(password)}>
        {error && <Alert type="error" showIcon message={error} />}
        <Form.Item
          label="访问密码"
          name="password"
          rules={[{ required: true, message: "请输入访问密码" }]}
        >
          <Input.Password autoComplete="current-password" autoFocus maxLength={1024} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={submitting} block>
          登录
        </Button>
      </Form>
    </Card>
  );
}
