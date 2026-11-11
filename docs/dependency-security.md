# Python 依赖安全

截至 2026-11-11，依赖审计仍报告 `chromadb==1.5.9` 的四条公告。公告均列出该版本受影响，且没有发布修复版本。

| 公告 | 公告涉及的入口 |
| --- | --- |
| [GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) | Chroma Python HTTP 服务创建集合时接受请求提供的模型仓库及 `trust_remote_code`，可在认证前触发代码执行。 |
| [GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf) | 有集合更新权限的 Chroma HTTP 客户端可通过更新集合的嵌入配置触发代码执行。 |
| [GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) | `SimpleRBACAuthorizationProvider` 检查动作权限时缺少租户、数据库和集合范围。 |
| [GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) | 服务端授权校验缺失使认证用户能跨租户操作集合。 |

本项目通过 `PersistentClient` 固定使用进程内 Rust 实现。FastAPI 对外提供小说业务接口，集合创建和更新参数由后端控制。部署只启动小说 API。禁止单独运行或代理 Chroma HTTP 服务，也不要将 `CHROMA_DIR` 交给可通过网络访问的 Chroma 实例。

小说后端计算嵌入后向 Chroma 提供数值向量。创建和读取集合时显式传入 `embedding_function=None`。在 1.5.9 中，纯文本查询仍可能从集合 schema 回退到默认模型，数值向量写入也可能为稀疏索引检查解析已有 schema。`CHROMA_DIR` 只使用本项目生成的目录或可信备份。禁止导入不可信的 Chroma 数据目录。 [Chroma 集合文档](https://docs.trychroma.com/docs/collections/manage-collections) · [Chroma 客户端配置问题](https://github.com/chroma-core/chroma/issues/6717) · [回归测试](../backend/tests/test_vector_store.py)

保留这四条依赖审计记录。上游发布修复后，重新评估并升级锁定版本，运行后端回归和离线端到端测试。
