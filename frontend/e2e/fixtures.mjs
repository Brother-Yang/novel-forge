import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import process from "node:process";
import { clearTimeout, setTimeout } from "node:timers";
import { fileURLToPath, URL } from "node:url";
import { expect, test as base } from "@playwright/test";
import { loadConfigFromFile, preview } from "vite";

const frontend = fileURLToPath(new URL("../", import.meta.url));
const backend = fileURLToPath(new URL("../../backend/", import.meta.url));
const loopback = "127.0.0.1";

async function stop(child, closed) {
  if (!child || !closed) return;
  child.kill("SIGTERM");
  const deadline = setTimeout(() => child.kill("SIGKILL"), 5000);
  try {
    await closed;
  } finally {
    clearTimeout(deadline);
  }
}

export const test = base.extend({
  runtime: [
    async ({ browserName }, use, worker) => {
      const dist = join(frontend, "dist");
      if (!existsSync(join(dist, "index.html"))) throw new Error("请先运行 pnpm build");

      const directory = await mkdtemp(join(tmpdir(), "novel-forge-browser-"));
      const database = join(directory, "e2e.sqlite");
      const password = randomBytes(24).toString("base64url");
      let child;
      let closed;
      let server;
      let logs = "";
      try {
        const python = process.env.NOVEL_FORGE_E2E_PYTHON ?? join(backend, ".venv/bin/python");
        child = spawn(python, [join(backend, "tests/browser_e2e_server.py")], {
          cwd: directory,
          env: {
            PATH: process.env.PATH,
            HOME: directory,
            TMPDIR: directory,
            PYTHONPATH: backend,
            PYTHONDONTWRITEBYTECODE: "1",
            NOVEL_FORGE_BROWSER_E2E: "1",
            APP_ENV: "local",
            DATABASE_URL: `sqlite:///${database}`,
            CHROMA_DIR: join(directory, "chroma"),
            CORS_ALLOWED_ORIGINS: `http://${loopback}`,
            ACCESS_PASSWORD: password,
            SESSION_SECRET: randomBytes(48).toString("base64url"),
            LOGIN_ATTEMPTS_PER_WINDOW: "20",
            LLM_PROVIDER: "ollama",
            OLLAMA_BASE_URL: `http://${loopback}:1`,
            OPENAI_BASE_URL: `http://${loopback}:1`,
            DASHSCOPE_BASE_URL: `http://${loopback}:1`,
            OPENAI_API_KEY: "",
            DASHSCOPE_API_KEY: "",
            LLM_CONNECT_TIMEOUT_SECONDS: "1",
            LLM_RETRY_ATTEMPTS: "1",
            ANONYMIZED_TELEMETRY: "FALSE",
          },
        });
        closed = new Promise((resolve) => child.once("close", resolve));
        let launchError;
        child.on("error", (error) => {
          launchError = error;
          logs += `${error.message}\n`;
        });
        for (const output of [child.stdout, child.stderr]) {
          output.setEncoding("utf8");
          output.on("data", (chunk) => (logs += chunk));
        }
        let port = 0;
        await expect
          .poll(
            () => {
              if (launchError) throw launchError;
              if (child.exitCode !== null) throw new Error("测试后端提前退出，见后端日志");
              port = Number(/BROWSER_E2E_PORT=(\d+)\n/.exec(logs)?.[1] ?? 0);
              return port;
            },
            { timeout: 15_000, message: "测试后端取得回环端口" }
          )
          .toBeGreaterThan(0);
        const target = `http://${loopback}:${port}`;
        await expect
          .poll(
            async () => {
              try {
                const response = await globalThis.fetch(`${target}/api/health`);
                return response.ok;
              } catch {
                return false;
              }
            },
            { timeout: 20_000, message: "测试后端启动并完成迁移" }
          )
          .toBe(true);

        const loaded = await loadConfigFromFile(
          { command: "serve", mode: "production", isPreview: true },
          join(frontend, "vite.config.ts"),
          directory,
          "error"
        );
        const proxy = loaded?.config.server?.proxy?.["/api"];
        if (!proxy || typeof proxy === "string") throw new Error("未找到 Vite 的 /api 代理配置");
        server = await preview({
          configFile: false,
          envDir: false,
          root: directory,
          build: { outDir: dist },
          logLevel: "error",
          preview: {
            host: loopback,
            port: 0,
            strictPort: true,
            proxy: { "/api": { ...proxy, target } },
          },
        });
        const address = server.httpServer.address();
        if (!address || typeof address === "string") throw new Error("预览服务未取得回环端口");
        await use({ origin: `http://${loopback}:${address.port}`, database, password });
      } finally {
        try {
          await server?.close();
        } finally {
          await stop(child, closed);
          await rm(directory, { recursive: true, force: true });
          const output = worker.project.outputDir;
          await mkdir(output, { recursive: true });
          await writeFile(join(output, `backend-${browserName}-${worker.workerIndex}.log`), logs);
        }
      }
    },
    { scope: "worker", timeout: 60_000 },
  ],
  page: async ({ page, runtime }, use) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/*", (route) => {
      return new URL(route.request().url()).origin === runtime.origin
        ? route.continue()
        : route.abort("blockedbyclient");
    });
    await use(page);
    expect(errors).toEqual([]);
  },
});

export { expect };
