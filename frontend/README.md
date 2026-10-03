# 前端应用

这是课程推荐系统的正式前端目录。页面使用原生 HTML、CSS 和 JavaScript，通过 `/api/v1` 接口连接 `backend/`，不再在浏览器中模拟业务数据。

## 启动

先启动后端：

```bash
python3 -m uvicorn backend.app.main:app --reload --port 8000
```

再启动前端静态服务器：

```bash
cd frontend
python3 -m http.server 4173
```

访问 `http://127.0.0.1:4173`。

## 说明

- 默认后端地址是 `http://127.0.0.1:8000/api/v1`；
- 登录令牌保存在浏览器本地存储中；
- 页面支持系统深色模式和减少动态效果偏好；
- 预置账号是 `course_demo`，密码是 `demo1234`；
- 后端当前使用内存存储，重启后账号、画像、历史和收藏会重置。

