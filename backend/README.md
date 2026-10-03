# 后端服务

这是课程推荐系统的 FastAPI 内存实现，用于前后端联调。课程目录和历史交互读取仓库中的 JSONL 数据；账号、画像、会话、推荐历史和收藏只保存在进程内存中，服务重启后重置。

## 启动

在项目根目录执行：

```bash
python3 -m uvicorn backend.app.main:app --reload --port 8000
```

接口文档：`http://127.0.0.1:8000/docs`

预置账号：`course_demo`，密码：`demo1234`。

## 智谱智能体配置

服务启动时会自动读取项目根目录的 `.env`：

```env
AGENT_PROVIDER=zhipuai
AGENT_LLM_ENABLED=true
ZHIPUAI_API_KEY=你的真实APIKey
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPUAI_CHAT_COMPLETIONS_PATH=/chat/completions
ZHIPUAI_MODEL=glm-5.3-flash
```

模型只负责将状态机已经确定的回复改写得更自然，不能修改画像状态、确认课程或生成课程 ID。API Key 缺失、超时、限流或服务异常时，后端自动使用规则式回复继续完成流程。

## 当前实现

- 注册、登录、刷新令牌和退出；
- 用户画像查询、修改和最终确认；
- 课程搜索、详情和自然语言候选解析；
- 规则式智能体状态机、智谱自然语言生成和 SSE 消息流；
- 候选课程确认与拒绝；
- 基于现有交互数据的内存协同过滤和热门兜底；
- 推荐历史软删除和课程收藏；
- 统一错误结构和基础用户数据隔离。

公平策略快照尚未接入，因此推荐响应会明确返回 `fairness_applied=false`。
