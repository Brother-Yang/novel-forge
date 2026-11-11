"""Launch the real application on a kernel-assigned loopback port for Playwright."""

import os
import socket

import uvicorn


def main() -> None:
    if os.environ.get("NOVEL_FORGE_BROWSER_E2E") != "1":
        raise RuntimeError("从 frontend 运行 pnpm test:e2e 以创建隔离环境")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        print(f"BROWSER_E2E_PORT={listener.getsockname()[1]}", flush=True)
        config = uvicorn.Config("app.main:app", lifespan="on")
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
