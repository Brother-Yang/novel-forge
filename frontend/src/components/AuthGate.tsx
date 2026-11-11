import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Modal, Spin } from "antd";
import { authApi } from "../api/auth";
import { onUnauthorized } from "../api/client";
import AsyncContent from "./AsyncContent";
import { AuthContext, AuthSessionRevisionContext } from "./authContext";

const loadLoginForm = () => import("./AuthLoginForm");

interface AuthGateProps {
  children: ReactNode;
}

type State =
  | { kind: "loading" }
  | { kind: "unavailable"; error: string }
  | { kind: "login"; error?: string; keepWorkspace?: boolean }
  | { kind: "ready"; protected: boolean };

export default function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [sessionRevision, setSessionRevision] = useState(0);

  useEffect(() => {
    let current = true;
    void authApi.session().then(
      (session) => {
        if (!current) return;
        setState(
          session.authenticated ? { kind: "ready", protected: session.enabled } : { kind: "login" }
        );
      },
      (error: unknown) => {
        if (current) {
          setState({
            kind: "unavailable",
            error: error instanceof Error ? error.message : "无法连接到服务",
          });
        }
      }
    );
    const unsubscribe = onUnauthorized(() => {
      // Confirmation dialogs render through portals outside the hidden workspace.
      Modal.destroyAll();
      setState((previous) =>
        previous.kind === "ready"
          ? { kind: "login", error: "登录已失效，请重新输入访问密码。", keepWorkspace: true }
          : previous
      );
    });
    return () => {
      current = false;
      unsubscribe();
    };
  }, [attempt]);

  async function login(password: string) {
    setSubmitting(true);
    try {
      await authApi.login(password);
      const session = await authApi.session();
      if (!session.authenticated) {
        throw new Error("浏览器未保存会话，请通过工作台地址访问并允许 Cookie。");
      }
      setSessionRevision((revision) => revision + 1);
      setState({ kind: "ready", protected: session.enabled });
    } catch (error) {
      setState((previous) => ({
        kind: "login",
        error: error instanceof Error ? error.message : "登录失败，请重试",
        keepWorkspace: previous.kind === "login" && previous.keepWorkspace,
      }));
    } finally {
      setSubmitting(false);
    }
  }

  const logout = useCallback(async () => {
    await authApi.logout();
    Modal.destroyAll();
    setState({ kind: "login" });
  }, []);

  const ready = state.kind === "ready";
  const keepWorkspace = ready || (state.kind === "login" && state.keepWorkspace);

  return (
    <AuthContext.Provider value={ready && state.protected ? logout : null}>
      {keepWorkspace && (
        <AuthSessionRevisionContext.Provider value={sessionRevision}>
          <div hidden={!ready} style={{ display: ready ? "contents" : "none" }}>
            {children}
          </div>
        </AuthSessionRevisionContext.Provider>
      )}
      {!ready && (
        <main className="nf-boot">
          {state.kind === "loading" && <Spin aria-label="正在检查访问权限" />}
          {state.kind === "unavailable" && (
            <Alert
              type="error"
              showIcon
              message="无法检查访问权限"
              description={state.error}
              action={
                <Button
                  onClick={() => {
                    setState({ kind: "loading" });
                    setAttempt((value) => value + 1);
                  }}
                >
                  重试
                </Button>
              }
            />
          )}
          {state.kind === "login" && (
            <AsyncContent
              load={loadLoginForm}
              componentProps={{ error: state.error, submitting, onLogin: login }}
              label="登录表单"
            />
          )}
        </main>
      )}
    </AuthContext.Provider>
  );
}
