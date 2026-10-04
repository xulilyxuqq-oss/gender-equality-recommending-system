# 后端服务

这是课程推荐系统的 FastAPI 后端。课程目录、历史交互，以及账号、画像、会话、课程匹配、推荐历史和收藏都持久化在同一个可写 SQLite 数据库：`dataset/recommender.sqlite3`。服务重启不会清空这些业务数据。

## Windows 启动与数据库准备

在项目根目录使用 PowerShell。后端只使用一份可写数据集：`dataset/recommender.sqlite3`；不要为 API 服务另外维护一份业务库。

若已有数据库，在迁移或重建前必须先备份：

```powershell
Copy-Item dataset/recommender.sqlite3 dataset/recommender.backup.sqlite3
```

若本地没有数据库，或需要从仓库的 SQL 转储重新建立**新的数据库文件**，先确认目标文件不存在，然后运行：

```powershell
sqlite3.exe dataset/recommender.sqlite3 ".read dataset/recommender_dump.sql"
```

如果要由 JSONL 源数据重新构建，则同样先备份或移走现有数据库，再运行：

```powershell
python -m dataset.build_sqlite
```

启动服务：

```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```

启动阶段会先检查课程与推荐核心表，并要求 `PRAGMA integrity_check` 返回 `ok`，之后才自动应用 `backend/migrations/` 中尚未执行的应用迁移，并把迁移版本记录在 `schema_migrations`；检查失败时不会创建应用表或演示账号。应用不应依赖手工执行迁移。接口文档位于 `http://127.0.0.1:8000/docs`，健康检查会报告 `storage=sqlite` 和当前 `database_schema_version`。

可使用以下只读检查确认文件完整性和外键关系：

```powershell
sqlite3.exe dataset/recommender.sqlite3 "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

## 配置

默认数据库路径为项目根目录下的 `dataset/recommender.sqlite3`。可在启动前通过环境变量覆盖路径、JWT 签名密钥和初始管理员：

```powershell
$env:DATABASE_PATH = "D:/data/recommender.sqlite3"
$env:JWT_SECRET = "replace-with-a-long-random-production-secret"
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "replace-with-a-strong-admin-password"
$env:ADMIN_DISPLAY_NAME = "系统管理员"
python -m uvicorn backend.app.main:app --reload --port 8000
```

生产环境必须设置强随机的 `JWT_SECRET`，并对 `DATABASE_PATH` 指向的单一数据库执行常规备份；迁移前尤其应先备份。

## 数据写入保证

- 注册、令牌、画像、聊天会话与消息、课程候选、推荐快照和收藏都保存到该 SQLite 文件。
- 接受聊天消息时，用户消息、规则回复、会话状态、课程候选与流事件序号在同一事务中保存。模型润色在开始输出前写回；写回失败时仍输出已保存的规则回复，流式输出阶段不再访问数据库。
- 用户确认画像时，画像状态与版本、`users` 身份、已完成课程和 `interactions` 中 `comment=0` 的隐式反馈在同一即时事务中提交；任一步失败会整体回滚，不会留下部分推荐器数据。
- 已有显式评分不会被确认画像产生的隐式反馈覆盖；从画像删除课程时，只删除对应的隐式反馈。
- SQLite 使用外键约束、WAL 模式和 5 秒忙等待。备份时应在没有写入服务的维护窗口进行，或使用能安全处理 SQLite WAL 文件的备份工具。

## 演示账号

服务每次启动都会检查 `course_demo`。只有该账号不存在时，才创建演示账号 `course_demo` / `demo1234`；已有账号、画像或历史不会被重置，也不会覆盖已改动的演示数据。

管理端使用完全独立的管理员令牌。首次启动且没有管理员账号时，会读取 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 和 `ADMIN_DISPLAY_NAME` 创建初始管理员；开发默认值为 `admin` / `admin1234`，部署前必须覆盖。管理端接口位于 `/api/v1/admin`。

## 智谱智能体配置

服务启动时会自动读取项目根目录的 `.env`：

```env
AGENT_PROVIDER=zhipuai
AGENT_LLM_ENABLED=true
AGENT_MODE=autonomous
AGENT_MAX_STEPS=6
AGENT_MAX_TOOL_CALLS=8
AGENT_TURN_TIMEOUT_SECONDS=30
ZHIPUAI_API_KEY=你的真实APIKey
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPUAI_CHAT_COMPLETIONS_PATH=/chat/completions
ZHIPUAI_MODEL=glm-5.3-flash
```

`AGENT_MODE=autonomous` 时，模型可以在受控工具集合中自主选择课程搜索、课程解析和画像草稿操作；所有工具调用都经过 Policy 校验并记录到 `app_agent_runs` / `app_agent_steps`。候选课程确认和最终画像确认仍需用户明确操作，模型不能直接执行 SQL 或猜测课程 ID。API Key 缺失、超时、限流或服务异常时，后端自动回退到规则式流程。

## 当前实现

- 注册、登录、刷新令牌和退出；
- 用户画像查询、修改和最终确认；
- 课程搜索、详情和自然语言候选解析；
- 有限步自主课程画像 Agent、受控课程工具、持久化 Agent 运行记录、规则式回退和 SSE 消息流；
- 候选课程确认与拒绝；
- 基于现有交互数据的协同过滤和热门兜底；
- 推荐历史软删除和课程收藏；
- 单一 `admin` 角色的独立认证、管理员账号管理和敏感读取审计；
- 课程创建、编辑、归档、恢复、别名、目录版本和 CSV/JSONL 校验导入；
- 用户状态、匹配审计、推荐快照和系统健康观察；
- 超参数单组/网格实验、指标对比、帕累托标记、导出和策略草稿；
- 公平策略计算、单活动策略启用和在线推荐策略版本记录；
- SQLite 持久化、启动迁移、统一错误结构和用户数据隔离。

没有活动公平策略时推荐响应返回 `fairness_applied=false`；管理员显式启用策略后，在线推荐会执行受换位成本约束的重排，并记录对应策略版本。
