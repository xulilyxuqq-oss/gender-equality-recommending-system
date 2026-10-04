import { ApiError, api, getAuth, setAuth, streamMessage } from "./api.js";

const root = document.querySelector("#root");
const drawerRoot = document.querySelector("#drawer-root");
const toastRoot = document.querySelector("#toast-root");

const routes = new Set(["home", "auth", "assistant", "recommendations", "history", "favorites"]);
const state = {
  route: routeFromHash(),
  auth: getAuth(),
  profile: null,
  session: null,
  recommendation: null,
  history: [],
  favorites: [],
  favoriteIds: new Set(),
  authMode: "login",
  loading: false,
  streaming: false,
  assistantDelta: "",
  agentTrace: [],
  error: "",
};

function routeFromHash() {
  const route = window.location.hash.replace(/^#\/?/, "") || "home";
  return routes.has(route) ? route : "home";
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function genderLabel(code) {
  return code === 1 ? "男" : code === 2 ? "女" : "等待填写";
}

function sourceLabel(source) {
  return source === "collaborative" ? "相似学习路径" : "热门课程推荐";
}

function sessionStateLabel(value) {
  return {
    COLLECTING_NAME: "等待填写姓名",
    COLLECTING_GENDER: "等待选择性别",
    COLLECTING_COURSES: "正在补充课程",
    WAITING_COURSE_CONFIRMATION: "等待确认课程",
    PROFILE_REVIEW: "画像可以确认",
    COMPLETED: "本轮已经完成",
  }[value] || "正在整理画像";
}

function advancedRuleLabel(value) {
  return value === "prerequisite_count>=2" ? "先修课程数量达到 2 门时标记为高阶课程。" : value;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function showToast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  toastRoot.append(node);
  window.setTimeout(() => node.remove(), 2600);
}

function navButton(route, label) {
  const active = state.route === route;
  return `<button class="nav-link ${active ? "is-active" : ""}" type="button" data-route="${route}" ${active ? 'aria-current="page"' : ""}>${label}</button>`;
}

function shell(content) {
  const loggedIn = Boolean(state.auth);
  return `
    <header class="topbar">
      <button class="brand" type="button" data-route="home" aria-label="返回首页">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <span><strong>课序</strong><small>COURSE COMPASS</small></span>
      </button>
      <nav class="desktop-nav" aria-label="主导航">
        ${navButton("home", "首页")}
        ${loggedIn ? navButton("assistant", "智能推荐") : ""}
        ${loggedIn ? navButton("recommendations", "推荐结果") : ""}
        ${loggedIn ? navButton("history", "历史记录") : ""}
        ${loggedIn ? navButton("favorites", "我的收藏") : ""}
      </nav>
      <div class="account-actions">
        ${loggedIn ? `<span class="account-name">${escapeHtml(state.auth.account?.username || "已登录")}</span><button class="quiet-action" type="button" data-action="logout">退出</button>` : `<button class="button button-quiet compact" type="button" data-route="auth">登录</button>`}
        <button class="menu-button" type="button" data-action="toggle-menu" aria-expanded="false">菜单</button>
      </div>
      <nav class="mobile-nav" id="mobile-nav" aria-label="移动端导航" hidden>
        ${navButton("home", "首页")}
        ${loggedIn ? navButton("assistant", "智能推荐") : ""}
        ${loggedIn ? navButton("recommendations", "推荐结果") : ""}
        ${loggedIn ? navButton("history", "历史记录") : ""}
        ${loggedIn ? navButton("favorites", "我的收藏") : ""}
      </nav>
    </header>
    <main id="main-content" tabindex="-1">${content}</main>
    <footer class="site-footer">
      <strong>课序</strong>
      <span>当前版本使用内存服务，重启后业务数据重置。</span>
      <span>课程候选始终需要用户确认。</span>
    </footer>
  `;
}

function homeView() {
  return `
    <section class="hero" aria-labelledby="home-title">
      <div class="hero-copy">
        <span class="hero-eyebrow">从学习经历开始</span>
        <h1 id="home-title"><span>学过的课，</span><span>排成下一步。</span></h1>
        <p>描述已学课程，确认真实候选，再获得满足先修要求的推荐。</p>
        <div class="hero-actions">
          <button class="button button-accent button-large" type="button" data-action="start">开始推荐</button>
          ${state.auth ? `<button class="text-action" type="button" data-route="history">查看历史</button>` : `<button class="text-action" type="button" data-action="demo-login">使用演示账号</button>`}
        </div>
      </div>
      <figure class="hero-image">
        <img src="./public/assets/hero-course-planning.jpg" width="1122" height="1402" alt="学生在图书馆整理纸质课程卡和学习笔记" fetchpriority="high" />
        <figcaption>先确认学习经历，再生成下一阶段课程。</figcaption>
      </figure>
    </section>
    <section class="principle-section" aria-labelledby="principle-title">
      <div>
        <h2 id="principle-title">每一门课程，都由你确认。</h2>
        <p>自然语言降低填写成本，明确确认保证画像可靠。</p>
      </div>
      <ol class="process-list">
        <li><strong>描述学习经历</strong><span>一次说出一门或多门课程。</span></li>
        <li><strong>核对真实课程</strong><span>查看名称、领域和难度后选择。</span></li>
        <li><strong>获得后续建议</strong><span>先检查先修条件，再给出理由。</span></li>
      </ol>
    </section>
    <section class="trust-section" aria-labelledby="trust-title">
      <div class="trust-copy">
        <h2 id="trust-title">推荐依据看得懂，关键数据不代替你决定。</h2>
        <p>姓名、性别和课程只在对话中逐步补充。系统不会根据姓名猜测性别，也不会自动写入模糊课程。</p>
      </div>
      <dl class="trust-facts">
        <div><dt>课程身份</dt><dd>候选确认后写入</dd></div>
        <div><dt>先修条件</dt><dd>生成推荐前检查</dd></div>
        <div><dt>推荐解释</dt><dd>展示用户可读理由</dd></div>
      </dl>
    </section>
    <section class="product-proof" aria-labelledby="product-proof-title">
      <header class="product-proof-copy">
        <span class="section-label">实际产品界面</span>
        <h2 id="product-proof-title">从课程确认到结果解释，一条路径走完。</h2>
        <p>对话负责把模糊的学习经历变成可信画像，结果页负责说明课程为什么值得成为下一步。</p>
      </header>
      <div class="product-shots">
        <figure class="product-shot product-shot-assistant">
          <img src="./public/assets/product-assistant.jpg" width="1280" height="800" alt="智能推荐对话与画像草稿界面" loading="lazy" />
          <figcaption><strong>01 对话确认</strong><span>逐项核对课程，不自动猜测。</span></figcaption>
        </figure>
        <figure class="product-shot product-shot-results">
          <img src="./public/assets/product-recommendations.jpg" width="1280" height="1352" alt="课程推荐结果与推荐理由界面" loading="lazy" />
          <figcaption><strong>02 结果解释</strong><span>排序、先修状态和推荐理由同时可见。</span></figcaption>
        </figure>
      </div>
    </section>
  `;
}

function authView() {
  const login = state.authMode === "login";
  return `
    <section class="auth-page" aria-labelledby="auth-title">
      <div class="auth-intro">
        <span class="section-label">账号入口</span>
        <h1 id="auth-title">先创建账号，画像稍后再聊。</h1>
        <p>注册只保存登录凭据。姓名、性别和已学课程将在智能推荐过程中逐步确认。</p>
        <div class="auth-points" aria-label="账号说明">
          <span>注册不填写画像</span>
          <span>课程候选必须确认</span>
          <span>当前使用内存服务</span>
        </div>
      </div>
      <div class="auth-panel">
        <div class="segmented" role="tablist" aria-label="选择登录或注册">
          <button type="button" role="tab" data-auth-mode="login" aria-selected="${login}" class="${login ? "is-active" : ""}">登录</button>
          <button type="button" role="tab" data-auth-mode="register" aria-selected="${!login}" class="${!login ? "is-active" : ""}">注册</button>
        </div>
        <form id="auth-form" class="form-stack" novalidate>
          <div class="form-heading">
            <h2>${login ? "欢迎回来" : "创建账号"}</h2>
            <p>${login ? "继续完成课程画像或查看历史推荐。" : "创建后直接进入智能推荐。"}</p>
          </div>
          <label class="field">
            <span>用户名</span>
            <input name="username" autocomplete="username" minlength="1" maxlength="64" required placeholder="例如 lily_course" />
          </label>
          <label class="field">
            <span>密码</span>
            <input name="password" type="password" autocomplete="${login ? "current-password" : "new-password"}" minlength="8" maxlength="72" required placeholder="8-72 位" />
            <small>至少 8 位，最多 72 位。</small>
          </label>
          ${login ? "" : `<label class="field"><span>确认密码</span><input name="confirmPassword" type="password" autocomplete="new-password" minlength="8" maxlength="72" required placeholder="再次输入密码" /></label>`}
          <p class="form-error" role="alert">${escapeHtml(state.error)}</p>
          <button class="button button-primary button-block" type="submit" ${state.loading ? "disabled" : ""}>${state.loading ? "正在提交" : login ? "登录" : "创建账号"}</button>
          <button class="button button-quiet button-block" type="button" data-action="demo-login">使用演示账号</button>
          <p class="form-note">演示账号：course_demo / demo1234</p>
        </form>
      </div>
    </section>
  `;
}

function loadingView(title = "正在读取数据") {
  return `
    <section class="page-shell" aria-busy="true">
      <header class="page-heading"><h1>${title}</h1></header>
      <div class="skeleton-layout" aria-hidden="true">
        <div class="skeleton-block tall"></div><div><div class="skeleton-block"></div><div class="skeleton-block short"></div></div>
      </div>
    </section>
  `;
}

function errorView() {
  return `
    <section class="page-shell">
      <div class="empty-state error-state">
        <h1>暂时无法加载</h1>
        <p>${escapeHtml(state.error || "请确认后端服务已经启动。")}</p>
        <button class="button button-primary" type="button" data-action="retry-route">重新加载</button>
      </div>
    </section>
  `;
}

function messageHtml(message) {
  return `<div class="message message-${message.role}"><span class="message-role">${message.role === "assistant" ? "序" : "你"}</span><p>${escapeHtml(message.content)}</p></div>`;
}

function agentToolLabel(toolName) {
  return {
    search_courses: "搜索课程目录",
    resolve_course: "解析课程描述",
    get_course: "读取课程详情",
    get_profile_context: "读取当前画像",
    get_pending_resolutions: "检查待确认候选",
    update_draft_name: "更新姓名草稿",
    update_draft_gender: "更新性别草稿",
    create_course_resolution: "创建课程候选",
    accept_course_candidate: "确认课程候选",
    reject_course_candidate: "排除课程候选",
    request_profile_confirmation: "检查画像确认条件",
    create_recommendation: "生成课程推荐",
  }[toolName] || toolName;
}

function agentTraceStatusLabel(status) {
  return {
    running: "进行中",
    completed: "已完成",
    waiting: "等待确认",
    failed: "失败",
  }[status] || "记录";
}

function agentTraceHtml() {
  if (!state.agentTrace.length) return "";
  return `
    <section class="agent-trace" aria-label="智能体执行轨迹" aria-live="polite">
      <header class="agent-trace-header">
        <div class="agent-trace-title"><span class="section-label">Agent Trace</span><strong>执行轨迹</strong></div>
        <span class="agent-trace-state">${state.streaming ? "实时执行中" : "本轮已完成"}</span>
      </header>
      <ol class="agent-trace-list">
        ${state.agentTrace.map((item, index) => `
          <li class="agent-trace-item agent-trace-${escapeHtml(item.status || "completed")} agent-trace-phase-${escapeHtml(item.phase || "step")}">
            <span class="agent-trace-rail" aria-hidden="true"><span class="agent-trace-mark">${String(index + 1).padStart(2, "0")}</span></span>
            <div class="agent-trace-content">
              <div class="agent-trace-row"><strong>${escapeHtml(item.label || "正在处理")}</strong><span class="agent-trace-badge">${agentTraceStatusLabel(item.status)}</span></div>
              ${item.tool_name ? `<small>工具 · ${escapeHtml(agentToolLabel(item.tool_name))}</small>` : ""}
            </div>
          </li>
        `).join("")}
      </ol>
    </section>
  `;
}

function resolutionHtml(resolution) {
  const finalized = resolution.status !== "PENDING";
  const selected = resolution.selected_course_id;
  return `
    <article class="resolution ${finalized ? "is-final" : ""}">
      <header><strong>${resolution.status === "CONFIRMED" ? "已确认" : resolution.status === "REJECTED" ? "已排除" : "需要确认"}</strong><span>描述：${escapeHtml(resolution.query)}</span></header>
      ${resolution.candidates.length ? resolution.candidates.map((candidate, index) => `
        <div class="candidate ${selected === candidate.course_id ? "is-selected" : ""}">
          <span class="candidate-index">${String(index + 1).padStart(2, "0")}</span>
          <div><strong>${escapeHtml(candidate.course_name)}</strong><small>${escapeHtml(candidate.fields.join("，") || "未标注领域")}<br>${escapeHtml(candidate.difficulty_level)}，${Math.round(candidate.match_score * 100)}% 匹配</small></div>
          ${finalized ? "" : `<button class="button button-primary compact" type="button" data-confirm-resolution="${resolution.resolution_id}" data-course-id="${candidate.course_id}">确认此课程</button>`}
        </div>
      `).join("") : `<div class="no-candidate"><strong>没有找到可靠候选</strong><span>请换一种课程名称，或选择都不是。</span></div>`}
      ${finalized ? "" : `<footer><button class="text-danger" type="button" data-reject-resolution="${resolution.resolution_id}">都不是</button></footer>`}
    </article>
  `;
}

function profileBoardHtml(session) {
  const draft = session.profile_draft;
  const ready = session.state === "PROFILE_REVIEW";
  const progress = [draft.display_name, draft.gender_code, ready].filter(Boolean).length;
  return `
    <aside class="profile-board" aria-label="画像草稿">
      <header><div><span class="section-label">画像草稿</span><h2>已确认信息</h2></div><strong>${progress} / 3</strong></header>
      <dl class="profile-fields">
        <div><dt>姓名</dt><dd>${escapeHtml(draft.display_name || "等待填写")}</dd></div>
        <div><dt>性别</dt><dd>${genderLabel(draft.gender_code)}</dd></div>
      </dl>
      ${draft.display_name ? `<details class="inline-editor"><summary>修改姓名</summary><form id="draft-name-form"><label class="field dark"><span>姓名</span><input name="display_name" value="${escapeHtml(draft.display_name)}" required maxlength="80" /></label><button class="button button-accent compact" type="submit">保存姓名</button></form></details>` : ""}
      ${draft.gender_code ? `<details class="inline-editor"><summary>修改性别</summary><div class="choice-row"><button type="button" data-draft-gender="1">男</button><button type="button" data-draft-gender="2">女</button></div></details>` : ""}
      <div class="profile-courses">
        <div class="course-count"><span>已学课程</span><strong>${draft.completed_courses.length} 门</strong></div>
        ${draft.completed_courses.length ? draft.completed_courses.map((course) => `<div class="confirmed-course"><span><strong>${escapeHtml(course.course_name)}</strong><small>已确认</small></span><button type="button" data-remove-draft-course="${course.course_id}">移除</button></div>`).join("") : `<p class="empty-slot">课程确认后会出现在这里。</p>`}
      </div>
      <button class="button button-accent button-block" type="button" data-action="confirm-profile" ${ready && !state.streaming ? "" : "disabled"}>确认画像并生成推荐</button>
      <p class="board-note">只有确认过的课程会进入正式画像。</p>
    </aside>
  `;
}

function assistantView() {
  if (!state.session) return loadingView("正在建立课程对话");
  const session = state.session;
  const pending = session.course_resolutions.filter((item) => item.status === "PENDING");
  const inputDisabled = state.streaming || pending.length > 0 || session.state === "COMPLETED";
  return `
    <section class="page-shell assistant-page" aria-labelledby="assistant-title">
      <header class="page-heading assistant-heading"><div><span class="section-label">智能推荐</span><h1 id="assistant-title">一起整理你的学习路径</h1></div><span class="state-label">${sessionStateLabel(session.state)}</span></header>
      <div class="assistant-grid">
        <section class="chat-panel" aria-label="课程推荐对话">
          <div class="chat-thread" id="chat-thread" aria-live="polite">
            ${session.messages.map(messageHtml).join("")}
            ${state.assistantDelta ? messageHtml({ role: "assistant", content: state.assistantDelta }) : ""}
            ${session.course_resolutions.map(resolutionHtml).join("")}
          </div>
          ${agentTraceHtml()}
          ${session.state === "COLLECTING_GENDER" ? `<div class="quick-actions"><button type="button" data-quick-message="男">男</button><button type="button" data-quick-message="女">女</button></div>` : ""}
          ${session.state === "COLLECTING_COURSES" ? `<div class="quick-actions"><button type="button" data-quick-message="我没有学过课程">没有学过课程</button><button type="button" data-quick-message="我没有其他课程">完成课程描述</button></div>` : ""}
          <form class="composer" id="chat-form">
            <label class="sr-only" for="chat-input">输入消息</label>
            <textarea id="chat-input" name="message" rows="1" required ${inputDisabled ? "disabled" : ""} placeholder="${pending.length ? "请先处理课程候选" : "描述一门或多门课程"}"></textarea>
            <button class="button button-accent" type="submit" ${inputDisabled ? "disabled" : ""}>${state.streaming ? "接收中" : "发送"}</button>
          </form>
          <p class="composer-note">Enter 发送，Shift + Enter 换行。</p>
        </section>
        ${profileBoardHtml(session)}
      </div>
    </section>
  `;
}

function recommendationRows(items) {
  return items.map((item) => {
    const course = item.course;
    const favorite = state.favoriteIds.has(course.course_id);
    return `
      <article class="recommendation-row">
        <span class="rank">${String(item.rank).padStart(2, "0")}</span>
        <div class="course-summary"><strong>${escapeHtml(course.course_name)}</strong><small>${escapeHtml(course.fields.join("，"))}<br>${escapeHtml(course.difficulty_level)}，先修条件已满足</small></div>
        <p>${escapeHtml(item.reason_text)}</p>
        <div class="row-actions"><button type="button" data-toggle-favorite="${course.course_id}" class="${favorite ? "is-favorite" : ""}">${favorite ? "取消收藏" : "收藏"}</button><button type="button" data-course-detail="${course.course_id}">详情</button></div>
      </article>
    `;
  }).join("");
}

function recommendationsView() {
  if (!state.recommendation) {
    return `
      <section class="page-shell"><header class="page-heading"><div><span class="section-label">推荐结果</span><h1>为你安排的下一步</h1></div></header>
      <div class="empty-state"><h2>还没有推荐结果</h2><p>完成画像确认后，这里会显示满足先修条件的课程。</p><button class="button button-primary" type="button" data-action="new-session">开始智能推荐</button></div></section>
    `;
  }
  const rec = state.recommendation;
  return `
    <section class="page-shell" aria-labelledby="recommendations-title">
      <header class="page-heading recommendation-heading"><div><span class="section-label">下一阶段课程</span><h1 id="recommendations-title">为你安排的下一步</h1><p>推荐理由和先修状态来自本次结果快照。</p></div><button class="button button-quiet" type="button" data-action="new-session">调整画像</button></header>
      <div class="recommendation-meta"><span><strong>推荐来源</strong>${sourceLabel(rec.source)}</span><span><strong>生成时间</strong>${formatDate(rec.generated_at)}</span><span><strong>公平策略</strong>${rec.fairness_applied ? "已应用" : "基础推荐"}</span></div>
      <div class="recommendation-list">${rec.items.length ? recommendationRows(rec.items) : `<div class="empty-state compact"><h2>暂时没有合适课程</h2><p>可以返回对话调整已学课程。</p></div>`}</div>
    </section>
  `;
}

function historyView() {
  return `
    <section class="page-shell" aria-labelledby="history-title">
      <header class="page-heading"><div><span class="section-label">推荐档案</span><h1 id="history-title">历史记录</h1><p>这里保留每次生成时的课程结果。</p></div></header>
      <div class="history-list">
        ${state.history.length ? state.history.map((item) => `<article class="history-row"><time>${formatDate(item.generated_at)}</time><div><strong>${sourceLabel(item.source)}</strong><small>${item.course_count} 门课程</small></div><div class="row-actions"><button type="button" data-open-history="${item.recommendation_id}">查看结果</button><button class="danger-action" type="button" data-delete-history="${item.recommendation_id}">删除</button></div></article>`).join("") : `<div class="empty-state"><h2>还没有推荐记录</h2><p>完成一次推荐后，结果快照会出现在这里。</p><button class="button button-primary" type="button" data-action="new-session">开始智能推荐</button></div>`}
      </div>
    </section>
  `;
}

function favoritesView() {
  return `
    <section class="page-shell" aria-labelledby="favorites-title">
      <header class="page-heading"><div><span class="section-label">课程收藏夹</span><h1 id="favorites-title">我的收藏</h1><p>保存感兴趣的课程，稍后继续查看。</p></div></header>
      <div class="favorite-list">
        ${state.favorites.length ? state.favorites.map((item) => `<article class="favorite-row"><div><strong>${escapeHtml(item.course.course_name)}</strong><small>${escapeHtml(item.course.fields.join("，"))}<br>收藏于 ${formatDate(item.created_at)}</small></div><div class="row-actions"><button type="button" data-course-detail="${item.course.course_id}">详情</button><button class="danger-action" type="button" data-toggle-favorite="${item.course.course_id}">取消收藏</button></div></article>`).join("") : `<div class="empty-state"><h2>暂时没有收藏</h2><p>在推荐结果或课程详情中收藏感兴趣的课程。</p><button class="button button-primary" type="button" data-route="recommendations">查看推荐结果</button></div>`}
      </div>
    </section>
  `;
}

function currentView() {
  if (state.loading && state.route !== "auth") return loadingView();
  if (state.error && state.route !== "auth") return errorView();
  if (state.route === "home") return homeView();
  if (state.route === "auth") return authView();
  if (state.route === "assistant") return assistantView();
  if (state.route === "recommendations") return recommendationsView();
  if (state.route === "history") return historyView();
  if (state.route === "favorites") return favoritesView();
  return homeView();
}

function render() {
  root.innerHTML = shell(currentView());
  requestAnimationFrame(() => {
    const chat = document.querySelector("#chat-thread");
    if (chat) chat.scrollTop = chat.scrollHeight;
  });
}

function requireAuth() {
  if (state.auth) return true;
  go("auth");
  return false;
}

async function hydrateRoute() {
  if (["assistant", "recommendations", "history", "favorites"].includes(state.route) && !requireAuth()) return;
  state.error = "";
  try {
    if (state.route === "assistant" && !state.session) {
      state.loading = true;
      render();
      state.session = await api.createSession();
    }
    if (state.route === "recommendations") {
      state.loading = true;
      render();
      await loadFavorites();
      if (!state.recommendation) {
        const history = await api.recommendationHistory();
        state.history = history.items;
        if (history.items[0]) state.recommendation = await api.recommendation(history.items[0].recommendation_id);
      }
    }
    if (state.route === "history") {
      state.loading = true;
      render();
      state.history = (await api.recommendationHistory()).items;
    }
    if (state.route === "favorites") {
      state.loading = true;
      render();
      await loadFavorites();
    }
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
  }
}

async function loadFavorites() {
  const payload = await api.favorites();
  state.favorites = payload.items;
  state.favoriteIds = new Set(payload.items.map((item) => item.course.course_id));
}

function go(route) {
  state.route = routes.has(route) ? route : "home";
  if (window.location.hash !== `#/${state.route}`) window.history.pushState(null, "", `#/${state.route}`);
  state.error = "";
  render();
  hydrateRoute();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function authenticate(username, password, mode) {
  state.loading = true;
  state.error = "";
  render();
  try {
    const auth = mode === "login" ? await api.login(username, password) : await api.register(username, password);
    setAuth(auth);
    state.auth = auth;
    state.profile = await api.profile();
    state.session = null;
    showToast(mode === "login" ? "登录成功" : "账号已创建");
    go("assistant");
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
  }
}

async function submitChat(message) {
  if (!state.session || state.streaming) return;
  state.streaming = true;
  state.assistantDelta = "";
  state.agentTrace = [];
  state.session.messages.push({ role: "user", content: message });
  render();
  try {
    await streamMessage(
      state.session.chat_session_id,
      { message, client_message_id: `client_${Date.now()}_${Math.random().toString(16).slice(2)}` },
      ({ event, data }) => {
        if (event === "message.delta") {
          state.assistantDelta += data.text || "";
          render();
        }
        if (event === "agent.step") {
          const index = state.agentTrace.findIndex((item) => item.trace_id === data.trace_id);
          if (index >= 0) state.agentTrace[index] = data;
          else state.agentTrace.push(data);
          render();
        }
      },
    );
    state.session = await api.session(state.session.chat_session_id);
  } catch (error) {
    showToast(error.message);
  } finally {
    state.streaming = false;
    state.assistantDelta = "";
    render();
  }
}

async function decideResolution(resolutionId, payload) {
  try {
    state.session = await api.decideResolution(state.session.chat_session_id, resolutionId, payload);
    render();
  } catch (error) {
    showToast(error.message);
  }
}

async function confirmProfile() {
  state.loading = true;
  render();
  try {
    state.profile = await api.confirmProfile(state.session.profile_version, state.session.chat_session_id);
    state.recommendation = await api.createRecommendation(state.profile.profile_version, 8);
    showToast("推荐结果已经生成");
    go("recommendations");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.loading = false;
    render();
  }
}

async function toggleFavorite(courseId) {
  try {
    if (state.favoriteIds.has(courseId)) {
      await api.removeFavorite(courseId);
      state.favoriteIds.delete(courseId);
      showToast("已取消收藏");
    } else {
      await api.addFavorite(courseId);
      state.favoriteIds.add(courseId);
      showToast("已加入收藏");
    }
    if (state.route === "favorites") await loadFavorites();
    render();
  } catch (error) {
    showToast(error.message);
  }
}

async function openCourse(courseId) {
  try {
    const course = await api.course(courseId);
    const favorite = state.favoriteIds.has(courseId);
    drawerRoot.innerHTML = `
      <div class="drawer-scrim" data-action="close-drawer"></div>
      <aside class="course-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header><span class="section-label">课程详情</span><button type="button" data-action="close-drawer">关闭</button></header>
        <div class="drawer-body">
          <span class="course-code">${escapeHtml(course.course_id)}</span>
          <h2 id="drawer-title">${escapeHtml(course.course_name)}</h2>
          <p>${escapeHtml(course.fields.join("，"))}<br>${escapeHtml(course.difficulty_level)}，${course.is_advanced ? "高阶课程" : "普通课程"}</p>
          <section><h3>高阶标记规则</h3><p>${escapeHtml(advancedRuleLabel(course.advanced_label_rule))}</p></section>
          <section><h3>先修课程</h3>${course.prerequisites.length ? course.prerequisites.map((item) => `<div class="prerequisite"><span>${escapeHtml(item.course_name)}</span><strong>${course.prerequisites_satisfied ? "已满足" : "未满足"}</strong></div>`).join("") : `<p>这门课没有先修要求。</p>`}</section>
          <button class="button ${favorite ? "button-quiet" : "button-accent"} button-block" type="button" data-toggle-favorite="${courseId}">${favorite ? "取消收藏" : "收藏课程"}</button>
        </div>
      </aside>
    `;
    document.querySelector(".course-drawer button").focus();
  } catch (error) {
    showToast(error.message);
  }
}

function closeDrawer() {
  drawerRoot.innerHTML = "";
}

document.addEventListener("click", async (event) => {
  const routeButton = event.target.closest("[data-route]");
  if (routeButton) return go(routeButton.dataset.route);

  const authMode = event.target.closest("[data-auth-mode]");
  if (authMode) {
    state.authMode = authMode.dataset.authMode;
    state.error = "";
    return render();
  }

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "start") return go(state.auth ? "assistant" : "auth");
  if (action === "retry-route") return hydrateRoute();
  if (action === "close-drawer") return closeDrawer();
  if (action === "toggle-menu") {
    const menu = document.querySelector("#mobile-nav");
    const button = document.querySelector("[data-action='toggle-menu']");
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
    return;
  }
  if (action === "demo-login") return authenticate("course_demo", "demo1234", "login");
  if (action === "logout") {
    try {
      await api.logout(state.auth?.refresh_token);
    } catch {
      // Local sign-out still succeeds if the in-memory server has restarted.
    }
    setAuth(null);
    Object.assign(state, { auth: null, profile: null, session: null, recommendation: null, history: [], favorites: [], favoriteIds: new Set(), agentTrace: [] });
    showToast("已退出账号");
    return go("home");
  }
  if (action === "new-session") {
    state.session = null;
    return go("assistant");
  }
  if (action === "confirm-profile") return confirmProfile();

  const quick = event.target.closest("[data-quick-message]");
  if (quick) return submitChat(quick.dataset.quickMessage);

  const confirm = event.target.closest("[data-confirm-resolution]");
  if (confirm) return decideResolution(confirm.dataset.confirmResolution, { course_id: confirm.dataset.courseId });
  const reject = event.target.closest("[data-reject-resolution]");
  if (reject) return decideResolution(reject.dataset.rejectResolution, { rejected: true });

  const gender = event.target.closest("[data-draft-gender]");
  if (gender) {
    try {
      state.session = await api.patchDraft(state.session.chat_session_id, { gender_code: Number(gender.dataset.draftGender) });
      showToast("性别已更新");
      return render();
    } catch (error) {
      return showToast(error.message);
    }
  }
  const removeCourse = event.target.closest("[data-remove-draft-course]");
  if (removeCourse) {
    try {
      state.session = await api.removeDraftCourse(state.session.chat_session_id, removeCourse.dataset.removeDraftCourse);
      showToast("课程已从画像草稿移除");
      return render();
    } catch (error) {
      return showToast(error.message);
    }
  }

  const favorite = event.target.closest("[data-toggle-favorite]");
  if (favorite) return toggleFavorite(favorite.dataset.toggleFavorite);
  const details = event.target.closest("[data-course-detail]");
  if (details) return openCourse(details.dataset.courseDetail);
  const openHistory = event.target.closest("[data-open-history]");
  if (openHistory) {
    try {
      state.recommendation = await api.recommendation(openHistory.dataset.openHistory);
      return go("recommendations");
    } catch (error) {
      return showToast(error.message);
    }
  }
  const deleteHistory = event.target.closest("[data-delete-history]");
  if (deleteHistory) {
    try {
      await api.deleteRecommendation(deleteHistory.dataset.deleteHistory);
      state.history = (await api.recommendationHistory()).items;
      showToast("历史记录已删除");
      return render();
    } catch (error) {
      return showToast(error.message);
    }
  }
});

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.target.id === "auth-form") {
    const form = new FormData(event.target);
    const username = String(form.get("username") || "").trim();
    const password = String(form.get("password") || "");
    const confirmation = String(form.get("confirmPassword") || "");
    if (!username || password.length < 8) {
      state.error = "请输入用户名，并使用至少 8 位密码。";
      return render();
    }
    if (state.authMode === "register" && password !== confirmation) {
      state.error = "两次输入的密码不一致。";
      return render();
    }
    return authenticate(username, password, state.authMode);
  }
  if (event.target.id === "chat-form") {
    const input = event.target.querySelector("textarea");
    const message = input.value.trim();
    if (message) {
      input.value = "";
      return submitChat(message);
    }
  }
  if (event.target.id === "draft-name-form") {
    const name = String(new FormData(event.target).get("display_name") || "").trim();
    if (!name) return;
    try {
      state.session = await api.patchDraft(state.session.chat_session_id, { display_name: name });
      showToast("姓名已更新");
      return render();
    } catch (error) {
      return showToast(error.message);
    }
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
  if (event.target.id === "chat-input" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.target.form.requestSubmit();
  }
});

window.addEventListener("hashchange", () => {
  const route = routeFromHash();
  if (route !== state.route) {
    state.route = route;
    render();
    hydrateRoute();
  }
});

async function bootstrap() {
  if (state.auth) {
    try {
      state.profile = await api.profile();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setAuth(null);
        state.auth = null;
      }
    }
  }
  render();
  hydrateRoute();
}

bootstrap();
