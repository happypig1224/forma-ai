# FormaAI

## 项目文档

- [产品需求（首版）](docs/PRD.md)
- [全局业务流程](docs/global-workflow.md)
- [Python 后端开发规范（草案）](docs/python-backend-development-handbook.md)
- [Java 后端开发规范手册（通用工程原则参考）](java-development-handbook.md)

## 后端首批实现

已创建最小 FastAPI 工程，以及 `user_account`、`category` 的模型和 Alembic 初始迁移。当前只提供健康检查；注册登录、双 Token 与分类业务接口尚未实现。

开发基线：Python 3.14、MySQL 8.4、Redis。精确依赖版本见 `backend/uv.lock`。数据库与 Redis 需自行提供；应用不会在启动时自动建表或迁移。

在项目根目录执行以下 PowerShell 命令：

```powershell
Set-Location backend
uv sync --locked --group dev
# 仅首次执行；如已有 .env，不要覆盖。
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 填写 .env 中的 MySQL 和 Redis 连接配置后执行。
uv run --locked python -m formaai
```

启动参数：`--host`、`--port`、`--reload` / `--no-reload`、`--log-level`。默认仅监听 `127.0.0.1:8000`，热重载默认关闭。通过 `uv run --locked python -m formaai --help` 查看帮助。

## 项目配置

配置定义见 [config.py](backend/src/formaai/config.py)，完整变量示例见 [backend/.env.example](backend/.env.example)。在 `backend/` 目录启动，读取当前目录的 `.env`；不会自动寻找其他目录或覆盖已有 `.env`。

优先级：**显式启动参数 > 系统环境变量 > .env > 代码默认值**。启动参数只覆盖监听地址、端口、热重载和日志级别，校验后会传给应用工厂及重载子进程。

| 配置范围 | 变量与行为 |
|---|---|
| 运行环境 | `FORMAAI_APP_ENV`：development / test / production；生产环境禁止热重载 |
| 服务与日志 | `FORMAAI_HOST`、`FORMAAI_PORT`、`FORMAAI_RELOAD`、`FORMAAI_LOG_LEVEL`；日志级别用于统一启动入口的 Uvicorn 日志 |
| 接口文档 | `FORMAAI_DOCS_ENABLED` 可选；未设置时开发/测试开启，生产关闭，作用于 `/docs` 与 `/openapi.json` |
| 跨域 | `FORMAAI_CORS_ORIGINS` 使用 JSON 数组，例如 `["http://localhost:5173"]`；默认 `[]`，按来源白名单开放。`FORMAAI_CORS_ALLOW_CREDENTIALS` 默认 false |
| 数据库 | `FORMAAI_MYSQL_*` 控制连接、连接池大小与连接/读写超时；应用与迁移使用同一配置 |
| Redis | `FORMAAI_REDIS_URL` 与连接数、连接/读写超时；URL 只允许 redis/rediss，不允许查询参数覆盖配置 |

MySQL 用户、库名、密码及 Redis 地址必须提供。密码和 URL 中的凭证不得提交到仓库。非法配置启动时失败并报告字段，不打印原始敏感输入；`.env` 中拼错或未知的键会报错。超时单位为秒。JWT、邮件服务商和模型配置在对应功能接入时再增加，本阶段不创建空配置。

## 数据库与检查

数据库迁移与启动分开，需要初始化数据库时再独立执行 `uv run --locked alembic upgrade head`。

迁移仅用于新建且为空的 FormaAI 数据库。`/health/live` 表示进程存活，`/health/ready` 检查 MySQL 和 Redis；依赖不可用时返回 503，不暴露连接错误详情。正式业务 readiness 的迁移版本检查在业务接口接入时补齐。

检查命令（在 `backend/` 执行）：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src/formaai
uv run --locked pytest -q
```

真实数据库测试需要显式设置 `FORMAAI_TEST_MYSQL_URL`，指向专用 MySQL 8.4 测试服务器。测试账户需有建库/删库权限：测试仅创建并删除随机命名的 `formaai_test_<uuid>` 数据库，不操作已有业务库。不设置该变量时集成测试会跳过，不能视为数据库验收通过。应用迁移不提供破坏性降级，失败时先检查 MySQL 已执行的 DDL，禁止盲目重跑或删除表。

2026-11-21 验证结果：在隔离 MySQL 8.4.13 上运行全部测试，14 项通过（9 项真实数据库测试、5 项基础测试），无跳过。覆盖空库迁移、模型/迁移一致性（含默认值）、唯一/外键/CHECK 约束、UTC 微秒精度，以及重复执行 head 后保留已有数据。当前只有一份 revision，因此这不是历史业务库升级或备份恢复演练。测试库、临时容器和测试凭证均已清理；未操作业务数据库。细节见开发规范 §3.8。
