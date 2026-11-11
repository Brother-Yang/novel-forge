import { useContext, useState } from "react";
import { App, Button } from "antd";
import { AuthContext } from "./authContext";

export default function AuthSignOut() {
  const logout = useContext(AuthContext);
  const { message } = App.useApp();
  const [pending, setPending] = useState(false);
  if (!logout) return null;

  return (
    <Button
      className="nf-ghost"
      loading={pending}
      onClick={async () => {
        setPending(true);
        try {
          await logout();
        } catch (error) {
          void message.error(error instanceof Error ? error.message : "退出登录失败，请重试");
        } finally {
          setPending(false);
        }
      }}
    >
      退出登录
    </Button>
  );
}
