import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, App, Button, ConfigProvider, Modal, Spin } from "antd";
import { authApi } from "../api/auth";
import { onUnauthorized } from "../api/client";
import { useAppStore } from "../store";
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
  const [popupHost, setPopupHost] = useState<HTMLDivElement | null>(null);
  const hasDrafts = useAppStore((s) => Object.keys(s.chapterDrafts).length > 0);

  useEffect(() => {
    if (!hasDrafts) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [hasDrafts]);

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
      // Pending confirmations must not become actionable again after signing in.
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
    if (
      Object.keys(useAppStore.getState().chapterDrafts).length &&
      !window.confirm("退出登录会清除本窗口尚未保存的章节草稿。确定退出？")
    ) {
      return;
    }
    await authApi.logout();
    Modal.destroyAll();
    setState({ kind: "login" });
  }, []);

  const ready = state.kind === "ready";
  const keepWorkspace = ready || (state.kind === "login" && state.keepWorkspace);
  const discardDrafts = state.kind === "login" && !keepWorkspace;
  useEffect(() => {
    if (discardDrafts) useAppStore.getState().clearChapterDrafts();
  }, [discardDrafts]);

  return (
    <AuthContext.Provider value={ready && state.protected ? logout : null}>
      {keepWorkspace && (
        <AuthSessionRevisionContext.Provider value={sessionRevision}>
          <div hidden={!ready} style={{ display: ready ? "contents" : "none" }}>
            <div ref={setPopupHost} />
            {popupHost && (
              <ConfigProvider getPopupContainer={() => popupHost}>
                <App component={false}>{children}</App>
              </ConfigProvider>
            )}
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
