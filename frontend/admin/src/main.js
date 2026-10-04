import { AdminApiError, adminApi, getAdminAuth, setAdminAuth } from "./api.js";

const root = document.querySelector("#admin-root");
const panelRoot = document.querySelector("#panel-root");
const toastRoot = document.querySelector("#toast-root");

const routes = [
  ["dashboard", "仪表盘", "总览"],
  ["courses", "课程库", "内容"],
  ["imports", "课程导入", "内容"],
  ["users", "用户与画像", "业务"],
  ["resolutions", "匹配审计", "业务"],
  ["recommendations", "推荐运行", "业务"],
  ["hyperparameters", "超参数分析", "算法"],
  ["fairness", "公平策略", "算法"],
  ["jobs", "后台任务", "运维"],
  ["audit", "审计日志", "运维"],
  ["system", "系统状态", "运维"],
  ["admins", "管理员管理", "账号"],
];

const state = {
  auth: getAdminAuth(),
  route: routeFromHash(),
  loading: false,
  error: "",
  data: {},
  selectedExperimentId: null,
  selectedResultIds: new Set(),
  menuOpen: false,
  panelReturnFocus: null,
  pendingAction: null,
  activeCourse: null,
};

function routeFromHash() {
  const value = window.location.hash.replace(/^#\/?/, "") || "dashboard";
  return routes.some(([route]) => route === value) ? value : "dashboard";
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function number(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function percent(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function statusTone(status) {
  if (["ACTIVE", "SUCCEEDED", "CONFIRMED", "VALIDATED", "READY", "HEALTHY", "CORRECT"].includes(status)) return "success";
  if (["FAILED", "BLOCKED", "DISABLED", "LOCKED", "DEGRADED", "UNAVAILABLE", "INCORRECT"].includes(status)) return "danger";
  if (["RUNNING", "QUEUED", "PENDING", "DRAFT", "VALIDATING"].includes(status)) return "pending";
  return "neutral";
}

function statusChip(status, label = status) {
  return `<span class="status status-${statusTone(status)}"><i></i>${escapeHtml(label || "—")}</span>`;
}

function toast(message, tone = "success") {
  const item = document.createElement("div");
  item.className = `toast toast-${tone}`;
  item.textContent = message;
  toastRoot.append(item);
  window.setTimeout(() => item.remove(), 3200);
}

function currentTitle() {
  return routes.find(([route]) => route === state.route)?.[1] || "管理台";
}

function navHtml() {
  let lastGroup = "";
  return routes.map(([route, label, group]) => {
    const heading = group !== lastGroup ? `<span class="nav-group">${group}</span>` : "";
    lastGroup = group;
    return `${heading}<button type="button" class="nav-item ${state.route === route ? "is-active" : ""}" data-route="${route}" ${state.route === route ? 'aria-current="page"' : ""}><span>${label}</span><b aria-hidden="true">${label.slice(0, 1)}</b></button>`;
  }).join("");
}

function shell(content) {
  const admin = state.auth?.admin;
  return `
    <div class="admin-shell ${state.menuOpen ? "menu-open" : ""}">
      <aside class="sidebar" aria-label="管理导航">
        <button class="admin-brand" type="button" data-route="dashboard">
          <span class="brand-bars" aria-hidden="true"><i></i><i></i><i></i></span>
          <span><strong>课序</strong><small>管理观测台</small></span>
        </button>
        <nav>${navHtml()}</nav>
        <div class="sidebar-foot">
          <span class="account-avatar" aria-hidden="true">${escapeHtml((admin?.display_name || "管").slice(0, 1))}</span>
          <span><strong>${escapeHtml(admin?.display_name || "管理员")}</strong><small>全部管理权限</small></span>
          <button type="button" data-action="logout">退出</button>
        </div>
      </aside>
      <div class="workspace">
        <header class="workspace-header">
          <button class="menu-toggle" type="button" data-action="toggle-menu" aria-expanded="${state.menuOpen}">导航</button>
          <div><span>课序管理台</span><strong>${currentTitle()}</strong></div>
          <div class="header-meta"><span>${escapeHtml(admin?.username || "admin")}</span><time>${new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date())}</time></div>
        </header>
        <main id="admin-main" tabindex="-1">${content}</main>
      </div>
    </div>
  `;
}

function pageHeader(title, summary, action = "") {
  return `<header class="page-header"><div><p>${escapeHtml(summary)}</p><h1>${escapeHtml(title)}</h1></div>${action}</header>`;
}

function emptyState(title, text) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></div>`;
}

function loadingView() {
  return shell(`<section class="page">${pageHeader(currentTitle(), "正在读取最新管理数据")}
    <div class="loading-ledger" aria-busy="true"><i></i><i></i><i></i><i></i></div></section>`);
}

function errorView() {
  return shell(`<section class="page">${pageHeader(currentTitle(), "本次读取没有完成")}
    <div class="error-sheet"><strong>无法加载管理数据</strong><p>${escapeHtml(state.error)}</p><button class="button button-primary" data-action="retry">重新读取</button></div></section>`);
}

function loginView() {
  return `
    <main class="login-page" id="admin-main">
      <section class="login-ledger" aria-labelledby="login-title">
        <div class="login-brand"><span class="brand-bars"><i></i><i></i><i></i></span><strong>课序管理台</strong></div>
        <div class="login-thesis">
          <span>COURSE OPERATIONS / 2026</span>
          <h1 id="login-title">看见目录、算法与公平策略的每一次变化。</h1>
          <p>一个管理员角色，完整权限，所有关键操作都有版本和审计记录。</p>
        </div>
        <dl class="login-facts">
          <div><dt>管理身份</dt><dd>独立令牌</dd></div>
          <div><dt>课程修改</dt><dd>版本保护</dd></div>
          <div><dt>策略发布</dt><dd>显式启用</dd></div>
        </dl>
      </section>
      <section class="login-form-wrap">
        <form id="login-form" class="login-form" novalidate>
          <header><span>管理员入口</span><h2>登录观测台</h2><p>普通用户账号不能进入这里。</p></header>
          <label class="field"><span>管理员用户名</span><input name="username" autocomplete="username" required value="admin" /></label>
          <label class="field"><span>密码</span><input name="password" type="password" autocomplete="current-password" minlength="8" required value="admin1234" /></label>
          <p class="form-error" role="alert">${escapeHtml(state.error)}</p>
          <button class="button button-accent button-block" type="submit" ${state.loading ? "disabled" : ""}>${state.loading ? "正在验证" : "进入管理台"}</button>
          <p class="demo-note">开发演示账号：admin / admin1234。部署前请在环境变量中修改。</p>
        </form>
      </section>
    </main>`;
}

function dashboardView() {
  const dashboard = state.data.dashboard;
  const health = state.data.health;
  const counts = dashboard.counts;
  const signals = [
    ["API", health.components.api.status],
    ["数据库", health.components.database.status],
    ["课程目录", health.components.course_catalog.status],
    ["公平策略", health.components.fairness_policy.status],
  ];
  return shell(`<section class="page dashboard-page">
    ${pageHeader("今日观测", "先判断系统状态，再进入需要处理的工作", `<button class="button button-primary" data-route="hyperparameters">新建参数实验</button>`)}
    <section class="signal-track" aria-label="系统状态">${signals.map(([label, value]) => `<div><span>${label}</span>${statusChip(value)}</div>`).join("")}</section>
    <section class="dashboard-ledger">
      <div class="ledger-title"><span>运营账册</span><strong>${escapeHtml(dashboard.catalog_version)}</strong></div>
      <dl class="metric-ledger">
        <div><dt>活动课程</dt><dd>${number(counts.active_courses)}</dd><small>共 ${number(counts.courses)} 门</small></div>
        <div><dt>确认画像</dt><dd>${number(counts.confirmed_profiles)}</dd><small>${number(counts.users)} 个账号</small></div>
        <div><dt>待确认匹配</dt><dd>${number(counts.pending_resolutions)}</dd><small>需要用户操作</small></div>
        <div><dt>推荐运行</dt><dd>${number(counts.recommendations)}</dd><small>历史快照</small></div>
      </dl>
    </section>
    <div class="dashboard-split">
      <section class="work-queue"><header><div><span>需要关注</span><h2>算法与策略</h2></div><button class="text-button" data-route="fairness">打开策略页</button></header>
        <div class="queue-row"><span class="queue-index">A</span><div><strong>${dashboard.active_policy ? "公平策略正在生效" : "尚未启用公平策略"}</strong><p>${dashboard.active_policy ? `活动快照 ${escapeHtml(dashboard.active_policy.policy_id)}` : "推荐仍可用，但响应会标记 fairness_applied=false。"}</p></div>${statusChip(dashboard.active_policy ? "ACTIVE" : "UNAVAILABLE")}</div>
        <div class="queue-row"><span class="queue-index">B</span><div><strong>${counts.running_jobs ? `${counts.running_jobs} 个任务运行中` : "后台任务队列空闲"}</strong><p>实验和策略计算状态可以在后台任务页追踪。</p></div>${statusChip(counts.running_jobs ? "RUNNING" : "HEALTHY")}</div>
      </section>
      <section class="activity-feed"><header><span>最近操作</span><h2>审计轨迹</h2></header>
        ${dashboard.recent_activity.length ? dashboard.recent_activity.map((item) => `<button class="activity-row" data-route="audit"><time>${formatDate(item.created_at)}</time><strong>${escapeHtml(item.action)}</strong><span>${escapeHtml(item.resource_type)} · ${escapeHtml(item.resource_id || "—")}</span></button>`).join("") : emptyState("还没有管理操作", "后续写操作会出现在这里。")}
      </section>
    </div>
  </section>`);
}

function coursesView() {
  const items = state.data.courses.items;
  return shell(`<section class="page">
    ${pageHeader("课程目录", `当前 ${number(state.data.courses.total)} 门课程，修改后立即生成新目录版本`, `<button class="button button-primary" data-action="new-course">创建课程</button>`)}
    <div class="table-tools"><label class="search-field"><span>搜索课程</span><input id="course-search" placeholder="课程 ID 或名称" /></label><button class="button button-quiet" data-action="search-courses">搜索</button><span class="tool-note">点击一行查看字段、先修和别名</span></div>
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>课程</th><th>领域</th><th>难度</th><th>高阶</th><th>目录状态</th><th>版本</th></tr></thead><tbody>
      ${items.map((course) => `<tr tabindex="0" data-open-course="${escapeHtml(course.course_id)}"><td><strong>${escapeHtml(course.course_name)}</strong><small>${escapeHtml(course.course_id)}</small></td><td>${escapeHtml(course.fields.join(" / "))}</td><td>${escapeHtml(course.difficulty_level)}</td><td>${course.is_advanced ? "是" : "否"}</td><td>${statusChip(course.status)}</td><td>v${course.row_version}</td></tr>`).join("")}
    </tbody></table></div>
  </section>`);
}

function importsView() {
  const imports = state.data.imports?.items || [];
  return shell(`<section class="page">
    ${pageHeader("课程导入", "浏览器读取 CSV 或 JSONL 文本，后端先校验，不会直接改动目录")}
    <div class="import-workbench">
      <form id="import-form" class="work-form">
        <header><span>校验新文件</span><h2>上传后先看行级问题</h2></header>
        <label class="file-drop"><input type="file" name="file" accept=".csv,.jsonl" required /><strong>选择 CSV 或 JSONL 文件</strong><span>单个文件不超过 5 MB</span></label>
        <button class="button button-primary" type="submit">开始校验</button>
      </form>
      <section class="import-history"><header><span>最近校验</span><h2>导入记录</h2></header>
        ${imports.length ? imports.map((item) => `<div class="record-row"><div><strong>${escapeHtml(item.filename)}</strong><span>${formatDate(item.created_at)} · ${item.summary.rows} 行</span></div>${statusChip(item.status)}<span>${item.summary.errors} 个错误</span><div class="record-actions">${item.status === "VALIDATED" ? `<button class="text-button" data-import-action="commit" data-import-id="${item.import_id}" data-row-version="${item.row_version}">提交</button>` : ""}${["VALIDATED", "BLOCKED"].includes(item.status) ? `<button class="text-button" data-import-action="cancel" data-import-id="${item.import_id}" data-row-version="${item.row_version}">取消</button>` : ""}</div></div>`).join("") : emptyState("尚无导入记录", "选择文件开始第一次校验。")}
      </section>
    </div>
  </section>`);
}

function usersView() {
  const items = state.data.users.items;
  return shell(`<section class="page">${pageHeader("用户与画像", `${number(state.data.users.total)} 个账号；详情读取会写入敏感访问审计`)}
    <div class="privacy-note"><strong>按需查看</strong><span>列表用于定位账号，姓名、性别和已学课程只在详情面板展开。</span></div>
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>账号</th><th>画像状态</th><th>已学课程</th><th>推荐次数</th><th>账号状态</th><th>创建时间</th></tr></thead><tbody>
      ${items.map((user) => `<tr tabindex="0" data-open-user="${escapeHtml(user.account_id)}"><td><strong>${escapeHtml(user.username)}</strong><small>${escapeHtml(user.account_id)}</small></td><td>${statusChip(user.profile_status)}</td><td>${number(user.completed_count)}</td><td>${number(user.recommendation_count)}</td><td>${statusChip(user.account_status)}</td><td>${formatDate(user.created_at)}</td></tr>`).join("")}
    </tbody></table></div></section>`);
}

function resolutionsView() {
  const items = state.data.resolutions.items;
  return shell(`<section class="page">${pageHeader("匹配审计", `${number(state.data.resolutions.total)} 条自然语言课程解析记录`)}
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>原始描述</th><th>候选</th><th>用户结论</th><th>审计结论</th><th>账号</th><th>发生时间</th></tr></thead><tbody>
      ${items.map((item) => `<tr tabindex="0" data-open-resolution="${escapeHtml(item.resolution_id)}"><td><strong>${escapeHtml(item.query)}</strong><small>${escapeHtml(item.resolution_id)}</small></td><td>${item.candidate_count}</td><td>${statusChip(item.status)}</td><td>${item.review_conclusion ? statusChip(item.review_conclusion) : "未审计"}</td><td>${escapeHtml(item.username)}</td><td>${formatDate(item.created_at)}</td></tr>`).join("")}
    </tbody></table></div></section>`);
}

function recommendationsView() {
  const items = state.data.recommendations.items;
  return shell(`<section class="page">${pageHeader("推荐运行", `${number(state.data.recommendations.total)} 条结果快照；历史课程名称不会被目录修改覆盖`)}
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>推荐运行</th><th>账号</th><th>来源</th><th>算法</th><th>公平策略</th><th>课程数</th><th>生成时间</th></tr></thead><tbody>
      ${items.map((item) => `<tr tabindex="0" data-open-recommendation="${escapeHtml(item.recommendation_id)}"><td><strong>${escapeHtml(item.recommendation_id)}</strong></td><td>${escapeHtml(item.username)}</td><td>${escapeHtml(item.source)}</td><td>${escapeHtml(item.algorithm_version)}</td><td>${item.fairness_applied ? statusChip("ACTIVE", item.fairness_policy_version) : statusChip("UNAVAILABLE", "未应用")}</td><td>${item.item_count}</td><td>${formatDate(item.generated_at)}</td></tr>`).join("")}
    </tbody></table></div></section>`);
}

function parameterInput(name, label, value, help) {
  return `<label class="parameter-field"><span>${label}</span><input name="${name}" value="${value}" required /><small>${help}</small></label>`;
}

function hyperparametersView() {
  const experiments = state.data.experiments.items || [];
  const selected = state.data.experiment;
  const results = state.data.experimentResults?.items || [];
  return shell(`<section class="page hyper-page">
    ${pageHeader("超参数分析", "扫描推荐质量、公平差距与换位成本的共同边界", `<button class="button button-quiet" data-action="load-latest-experiment">查看最近实验</button>`)}
    <div class="hyper-grid">
      <form id="experiment-form" class="parameter-sheet">
        <header><span>实验配置</span><h2>参数工作纸</h2><p>逗号分隔多个值会生成网格，最多 100 组。</p></header>
        <label class="field wide"><span>实验名称</span><input name="name" value="公平权重与邻居规模扫描" required maxlength="160" /></label>
        <div class="parameter-pairs">
          ${parameterInput("k_neighbors", "邻居数量", "10,20,40", "1—200")}
          ${parameterInput("candidate_size", "候选数量", "50", "不得小于 top_n")}
          ${parameterInput("top_n", "推荐数量", "10", "1—50")}
          ${parameterInput("implicit_score", "隐式反馈分", "1.0", "0—5")}
          ${parameterInput("fairness_lambda", "公平权重 λ", "0,0.15,0.3", "0—1")}
          ${parameterInput("target_gap", "目标曝光差", "0.05", "0—1")}
          ${parameterInput("max_total_cost", "最大换位成本", "0.08", "留空表示不限")}
          <label class="parameter-field"><span>先修约束</span><select name="enforce_prerequisites"><option value="true">启用</option><option value="false">停用</option></select><small>建议保持启用</small></label>
        </div>
        <div class="combination-preview"><span>预计组合</span><strong id="combination-count">9</strong><small>/ ${state.data.schema?.maximum_combinations || 100}</small></div>
        <p class="parameter-error" id="combination-error" role="alert" aria-live="polite"></p>
        <button class="button button-primary button-block" type="submit" data-experiment-submit>运行离线实验</button>
      </form>
      <section class="experiment-index"><header><span>实验索引</span><h2>最近运行</h2></header>
        ${experiments.length ? experiments.map((item) => `<button class="experiment-row ${selected?.experiment_id === item.experiment_id ? "is-active" : ""}" data-open-experiment="${escapeHtml(item.experiment_id)}"><span>${statusChip(item.status)}</span><strong>${escapeHtml(item.name)}</strong><small>${item.combination_count} 组 · ${formatDate(item.created_at)}</small></button>`).join("") : emptyState("还没有实验", "左侧配置一组参数开始分析。")}
      </section>
    </div>
    ${selected ? experimentResultsSection(selected, results) : ""}
  </section>`);
}

function experimentResultsSection(experiment, results) {
  const summary = experiment.summary || {};
  return `<section class="results-sheet">
    <header class="results-heading"><div><span>实验结果</span><h2>${escapeHtml(experiment.name)}</h2><p>${experiment.completed_count}/${experiment.combination_count} 组完成 · 数据集 ${escapeHtml(experiment.versions.dataset)}</p></div><div class="heading-actions">${statusChip(experiment.status)}<button class="text-button" data-action="export-experiment" data-experiment-id="${experiment.experiment_id}">导出 CSV</button><button class="text-button" data-action="clone-experiment" data-experiment-id="${experiment.experiment_id}">克隆实验</button>${["QUEUED", "RUNNING"].includes(experiment.status) ? `<button class="text-button danger-text" data-action="cancel-experiment" data-experiment-id="${experiment.experiment_id}">取消实验</button>` : ""}</div></header>
    <dl class="result-summary"><div><dt>最佳 Recall</dt><dd>${escapeHtml(summary.best_recall_result_id || "—")}</dd></div><div><dt>最小曝光差</dt><dd>${escapeHtml(summary.smallest_gap_result_id || "—")}</dd></div><div><dt>帕累托组合</dt><dd>${(summary.pareto_result_ids || []).length}</dd></div></dl>
    <div class="chart-and-table">
      <figure class="pareto-figure"><figcaption><strong>Recall / 曝光差前沿</strong><span>越靠右且越靠下越理想</span></figcaption><canvas id="pareto-chart" width="680" height="330" data-results='${escapeHtml(JSON.stringify(results.map((item) => ({ id: item.result_id, recall: item.metrics.recall_at_n, gap: item.metrics.gap_after, pareto: item.is_pareto_optimal }))))}'></canvas></figure>
      <div class="heat-list"><header><strong>参数热区</strong><span>公平权重 → 曝光差</span></header>${results.map((item) => `<button data-select-result="${item.result_id}" class="heat-cell ${state.selectedResultIds.has(item.result_id) ? "is-selected" : ""}" style="--heat:${Math.min(1, item.metrics.gap_after * 8)}"><span>λ ${item.parameters.fairness_lambda}</span><strong>${percent(item.metrics.gap_after)}</strong><small>R ${percent(item.metrics.recall_at_n)}</small></button>`).join("")}</div>
    </div>
    <div class="result-actions"><span data-selection-count>已选 ${state.selectedResultIds.size}/5 组</span><button class="button button-quiet" data-action="compare-results" ${state.selectedResultIds.size < 2 ? "disabled" : ""}>对比所选</button></div>
    <div class="data-table-wrap"><table class="data-table compact-table"><thead><tr><th>选择</th><th>k</th><th>λ</th><th>Recall@N</th><th>NDCG@N</th><th>覆盖率</th><th>曝光差</th><th>换位成本</th><th>前沿</th><th>操作</th></tr></thead><tbody>
      ${results.map((item) => `<tr><td><input type="checkbox" data-select-result="${item.result_id}" ${state.selectedResultIds.has(item.result_id) ? "checked" : ""} aria-label="选择结果 ${item.result_id}" /></td><td>${item.parameters.k_neighbors}</td><td>${item.parameters.fairness_lambda}</td><td>${percent(item.metrics.recall_at_n)}</td><td>${percent(item.metrics.ndcg_at_n)}</td><td>${percent(item.metrics.catalog_coverage)}</td><td>${percent(item.metrics.gap_after)}</td><td>${percent(item.metrics.total_swap_cost)}</td><td>${item.is_pareto_optimal ? statusChip("ACTIVE", "帕累托") : "—"}</td><td><button class="text-button" data-action="policy-from-result" data-result-id="${item.result_id}">生成策略草稿</button></td></tr>`).join("")}
    </tbody></table></div>
  </section>`;
}

function fairnessView() {
  const policies = state.data.policies.items || [];
  const active = policies.find((item) => item.status === "ACTIVE");
  return shell(`<section class="page">${pageHeader("公平策略", "离线计算、质量检查、显式启用；同一时刻仅一个活动策略", `<button class="button button-primary" data-action="compute-policy">计算新策略</button>`)}
    <div class="policy-banner ${active ? "has-policy" : "no-policy"}"><div><span>在线状态</span><h2>${active ? "公平重排已启用" : "当前没有活动公平策略"}</h2><p>${active ? `推荐将记录策略 ${escapeHtml(active.policy_id)}` : "推荐服务继续可用，并明确标记未应用公平策略。"}</p></div>${statusChip(active ? "ACTIVE" : "UNAVAILABLE")}</div>
    <div class="policy-ledger">${policies.length ? policies.map((item) => `<article class="policy-row"><div><span>${escapeHtml(item.policy_id)}</span><strong>λ ${item.parameters.fairness_lambda ?? "—"} · 目标差 ${percent(item.parameters.target_gap)}</strong><small>${formatDate(item.created_at)} · v${item.row_version}</small></div><dl><div><dt>Recall</dt><dd>${percent(item.metrics.recall_at_n)}</dd></div><div><dt>曝光差</dt><dd>${percent(item.metrics.gap_after)}</dd></div><div><dt>换位成本</dt><dd>${percent(item.metrics.total_swap_cost)}</dd></div></dl><div>${statusChip(item.status)}${item.status === "READY" ? `<button class="button button-quiet" data-activate-policy="${item.policy_id}" data-row-version="${item.row_version}" data-active-id="${active?.policy_id || ""}">启用</button>` : ""}${item.status === "RETIRED" ? `<button class="button button-quiet" data-rollback-policy="${item.policy_id}" data-row-version="${item.row_version}" data-active-id="${active?.policy_id || ""}">回滚至此</button>` : ""}</div></article>`).join("") : emptyState("尚无策略快照", "先在超参数结果中生成草稿，或直接计算新策略。")}</div>
    <p class="model-limit">适用范围说明：当前数据集与公平算法只比较男、女两个数据组，这不是对完整性别分类的定义。</p>
  </section>`);
}

function jobsView() {
  const items = state.data.jobs.items;
  return shell(`<section class="page">${pageHeader("后台任务", `${number(state.data.jobs.total)} 个任务；参数摘要已移除密钥和敏感原文`)}
    <div class="job-list">${items.length ? items.map((job) => `<article class="job-row"><div><strong>${escapeHtml(job.job_type)}</strong><span>${escapeHtml(job.job_id)}</span></div><div class="progress-track"><i style="width:${job.progress * 100}%"></i></div><span>${percent(job.progress, 0)}</span>${statusChip(job.status)}<time>${formatDate(job.created_at)}</time><div class="job-actions">${["QUEUED", "RUNNING"].includes(job.status) ? `<button class="text-button danger-text" data-job-action="cancel" data-job-id="${job.job_id}">取消</button>` : ""}${job.status === "FAILED" ? `<button class="text-button" data-job-action="retry" data-job-id="${job.job_id}">重试</button>` : ""}</div></article>`).join("") : emptyState("任务队列为空", "实验和策略计算任务会出现在这里。")}</div></section>`);
}

function auditView() {
  const items = state.data.audits.items;
  return shell(`<section class="page">${pageHeader("审计日志", `${number(state.data.audits.total)} 条管理轨迹；这里只展示脱敏摘要`)}
    <div class="audit-stream">${items.length ? items.map((item) => `<article><time>${formatDate(item.created_at)}</time><span class="audit-priority priority-${item.priority.toLowerCase()}">${escapeHtml(item.priority)}</span><div><strong>${escapeHtml(item.action)}</strong><p>${escapeHtml(item.resource_type)} · ${escapeHtml(item.resource_id || "—")}</p></div><span>${escapeHtml(item.username)}</span>${statusChip(item.result)}</article>`).join("") : emptyState("暂无审计日志", "登录后的关键读取和写操作会留在这里。")}</div></section>`);
}

function systemView() {
  const health = state.data.health;
  const config = state.data.config;
  return shell(`<section class="page">${pageHeader("系统状态", "健康检查与非敏感运行配置，不暴露数据库路径、令牌或 API Key")}
    <section class="system-board"><header><div><span>整体状态</span><h2>${health.status}</h2></div>${statusChip(health.status)}</header>
      <div class="component-list">${Object.entries(health.components).map(([name, component]) => `<div><strong>${escapeHtml(name)}</strong>${statusChip(component.status)}<span>${escapeHtml(component.version || component.provider || component.active_policy_id || "—")}</span></div>`).join("")}</div>
    </section>
    <section class="config-sheet"><header><span>公开配置</span><h2>运行边界</h2></header><dl>${Object.entries(config).filter(([, value]) => typeof value !== "object").map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl></section>
  </section>`);
}

function adminsView() {
  const items = state.data.admins.items;
  return shell(`<section class="page">${pageHeader("管理员管理", "唯一角色 admin；所有活动管理员拥有完整权限", `<button class="button button-primary" data-action="new-admin">创建管理员</button>`)}
    <div class="role-rule"><span>唯一角色</span><strong>admin</strong><p>不设置超级管理员、课程管理员或自定义权限点。</p></div>
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>管理员</th><th>角色</th><th>状态</th><th>最后登录</th><th>版本</th><th>操作</th></tr></thead><tbody>${items.map((item) => `<tr><td><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(item.username)} · ${escapeHtml(item.admin_id)}</small></td><td>${escapeHtml(item.role)}</td><td>${statusChip(item.status)}</td><td>${formatDate(item.last_login_at)}</td><td>v${item.row_version}</td><td><div class="inline-actions"><button class="text-button" data-open-admin="${item.admin_id}">编辑</button>${item.admin_id === state.auth.admin.admin_id ? "<span>当前账号</span>" : `<button class="text-button" data-admin-status="${item.status === "ACTIVE" ? "disable" : "restore"}" data-admin-id="${item.admin_id}" data-row-version="${item.row_version}">${item.status === "ACTIVE" ? "停用" : "恢复"}</button>`}</div></td></tr>`).join("")}</tbody></table></div>
  </section>`);
}

function renderRoute() {
  const views = {
    dashboard: dashboardView, courses: coursesView, imports: importsView, users: usersView,
    resolutions: resolutionsView, recommendations: recommendationsView,
    hyperparameters: hyperparametersView, fairness: fairnessView, jobs: jobsView,
    audit: auditView, system: systemView, admins: adminsView,
  };
  root.innerHTML = views[state.route]();
  if (state.route === "hyperparameters") {
    drawParetoChart();
    const form = document.querySelector("#experiment-form");
    if (form) updateExperimentPreview(form);
  }
}

async function loadRoute() {
  if (!state.auth) {
    root.innerHTML = loginView();
    return;
  }
  state.loading = true;
  state.error = "";
  root.innerHTML = loadingView();
  try {
    if (state.route === "dashboard") [state.data.dashboard, state.data.health] = await Promise.all([adminApi.dashboard(), adminApi.health()]);
    if (state.route === "courses") state.data.courses = await adminApi.courses();
    if (state.route === "imports") state.data.imports = await adminApi.imports();
    if (state.route === "users") state.data.users = await adminApi.users();
    if (state.route === "resolutions") state.data.resolutions = await adminApi.resolutions();
    if (state.route === "recommendations") state.data.recommendations = await adminApi.recommendations();
    if (state.route === "hyperparameters") {
      [state.data.schema, state.data.experiments] = await Promise.all([adminApi.hyperparameterSchema(), adminApi.experiments()]);
      const target = state.selectedExperimentId || state.data.experiments.items[0]?.experiment_id;
      if (target) await loadExperiment(target, false);
    }
    if (state.route === "fairness") state.data.policies = await adminApi.policies();
    if (state.route === "jobs") state.data.jobs = await adminApi.jobs();
    if (state.route === "audit") state.data.audits = await adminApi.audits();
    if (state.route === "system") [state.data.health, state.data.config] = await Promise.all([adminApi.health(), adminApi.config()]);
    if (state.route === "admins") state.data.admins = await adminApi.admins();
    renderRoute();
  } catch (error) {
    if (error instanceof AdminApiError && error.status === 401) {
      setAdminAuth(null);
      state.auth = null;
      state.error = error.message;
      root.innerHTML = loginView();
      return;
    }
    state.error = error.message || "请确认后端服务已经启动。";
    root.innerHTML = errorView();
  } finally {
    state.loading = false;
  }
}

async function loadExperiment(id, rerender = true) {
  state.selectedExperimentId = id;
  [state.data.experiment, state.data.experimentResults] = await Promise.all([
    adminApi.experiment(id), adminApi.experimentResults(id),
  ]);
  state.selectedResultIds.clear();
  if (rerender) renderRoute();
}

function openPanel(title, body, width = "normal") {
  if (!panelRoot.querySelector(".detail-panel")) state.panelReturnFocus = document.activeElement;
  root.inert = true;
  panelRoot.innerHTML = `<div class="panel-backdrop" data-action="close-panel"></div><aside class="detail-panel panel-${width}" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="panel-title"><header><div><span>检查面板</span><h2 id="panel-title">${escapeHtml(title)}</h2></div><button type="button" data-action="close-panel" aria-label="关闭">关闭</button></header><div class="panel-body">${body}</div></aside>`;
  panelRoot.querySelector(".detail-panel")?.focus();
}

function closePanel() {
  panelRoot.innerHTML = "";
  root.inert = false;
  state.pendingAction = null;
  const returnFocus = state.panelReturnFocus;
  state.panelReturnFocus = null;
  if (returnFocus?.isConnected) {
    window.requestAnimationFrame(() => returnFocus.focus({ preventScroll: true }));
  }
}

function openConfirmation({ title, summary, impact, confirmLabel = "确认执行", requireReason = true, action }) {
  state.pendingAction = action;
  openPanel(title, `<form id="confirm-action-form" class="panel-form confirmation-form">
    <div class="impact-note"><span>影响范围</span><strong>${escapeHtml(summary)}</strong><p>${escapeHtml(impact)}</p></div>
    <label class="field"><span>操作原因${requireReason ? "（必填）" : "（可选）"}</span><textarea name="reason" rows="4" maxlength="1000" ${requireReason ? "required" : ""} placeholder="说明为什么需要执行此操作，内容会写入审计日志。"></textarea></label>
    <div class="panel-actions"><button class="button button-danger" type="submit">${escapeHtml(confirmLabel)}</button><button class="button button-quiet" type="button" data-action="close-panel">返回检查</button></div>
  </form>`);
}

function downloadText(filename, content, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function openCourse(id) {
  const course = await adminApi.course(id);
  state.activeCourse = course;
  openPanel(course.course_name, `<form id="course-form" class="panel-form" data-course-id="${escapeHtml(id)}">
    <input type="hidden" name="row_version" value="${course.row_version}" />
    <label class="field"><span>课程名称</span><input name="course_name" value="${escapeHtml(course.course_name)}" required /></label>
    <div class="two-fields"><label class="field"><span>难度</span><select name="difficulty_level">${["入门", "中级", "高阶"].map((value) => `<option ${course.difficulty_level === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><label class="check-field"><input name="is_advanced" type="checkbox" ${course.is_advanced ? "checked" : ""} /><span>标记为高阶课程</span></label></div>
    <label class="field"><span>课程领域</span><input name="fields" value="${escapeHtml(course.fields.join(", "))}" required /><small>使用逗号分隔</small></label>
    <label class="field"><span>先修课程 ID</span><input name="prerequisites" value="${escapeHtml(course.prerequisites.map((item) => item.course_id).join(", "))}" /><small>使用逗号分隔；后端会检查自引用和环</small></label>
    <label class="field"><span>高阶标记规则</span><input name="advanced_label_rule" value="${escapeHtml(course.advanced_label_rule)}" /></label>
    <section class="alias-block"><header><strong>课程别名</strong><span>用于自然语言匹配</span></header>${course.aliases.length ? course.aliases.map((item) => `<span class="alias-tag">${escapeHtml(item.alias_text)} · ${item.status}</span>`).join("") : "<p>暂无人工别名。</p>"}<div><input id="new-alias" placeholder="新增别名" /><button type="button" class="button button-quiet" data-add-alias="${escapeHtml(id)}">添加</button></div></section>
    <div class="panel-actions"><button class="button button-primary" type="submit">保存课程</button><button class="button button-danger" type="button" data-course-status="${course.status === "ACTIVE" ? "archive" : "restore"}" data-course-id="${escapeHtml(id)}" data-row-version="${course.row_version}">${course.status === "ACTIVE" ? "归档课程" : "恢复课程"}</button></div>
  </form>`);
}

function openNewCourse() {
  openPanel("创建课程", `<form id="new-course-form" class="panel-form">
    <label class="field"><span>课程 ID</span><input name="course_id" required placeholder="例如 C_AI_101" /></label>
    <label class="field"><span>课程名称</span><input name="course_name" required /></label>
    <div class="two-fields"><label class="field"><span>难度</span><select name="difficulty_level"><option>入门</option><option>中级</option><option>高阶</option></select></label><label class="check-field"><input name="is_advanced" type="checkbox" /><span>高阶课程</span></label></div>
    <label class="field"><span>领域</span><input name="fields" required placeholder="计算机科学, 人工智能" /></label>
    <label class="field"><span>先修课程 ID</span><input name="prerequisites" placeholder="C_BASE, C_MATH" /></label>
    <button class="button button-primary button-block" type="submit">创建并生成目录版本</button>
  </form>`);
}

async function openUser(id) {
  const user = await adminApi.user(id);
  openPanel(user.display_name || user.username, `<section class="detail-stack">
    <dl class="detail-list"><div><dt>账号</dt><dd>${escapeHtml(user.username)}</dd></div><div><dt>姓名</dt><dd>${escapeHtml(user.display_name || "未填写")}</dd></div><div><dt>性别数据组</dt><dd>${user.gender_code === 1 ? "男" : user.gender_code === 2 ? "女" : "未填写"}</dd></div><div><dt>画像版本</dt><dd>v${user.profile_version}</dd></div><div><dt>推荐次数</dt><dd>${user.recommendation_count}</dd></div></dl>
    <section class="course-list"><header><strong>已学课程</strong><span>${user.completed_courses.length} 门</span></header>${user.completed_courses.map((course) => `<div><strong>${escapeHtml(course.course_name)}</strong><span>${escapeHtml(course.course_id)}</span></div>`).join("") || "<p>尚未确认课程。</p>"}</section>
    <div class="panel-actions"><button class="button button-quiet" data-simulate-user="${escapeHtml(id)}" data-profile-version="${user.profile_version}" ${user.profile_status !== "CONFIRMED" ? "disabled" : ""}>运行诊断模拟</button><button class="button ${user.account_status === "ACTIVE" ? "button-danger" : "button-primary"}" data-user-status="${user.account_status === "ACTIVE" ? "disable" : "restore"}" data-user-id="${escapeHtml(id)}" data-row-version="${user.row_version}">${user.account_status === "ACTIVE" ? "停用账号" : "恢复账号"}</button></div>
  </section>`);
}

async function openResolution(id) {
  const resolution = await adminApi.resolution(id);
  openPanel(`匹配：${resolution.query_text}`, `<section class="detail-stack"><div class="sensitive-banner">此处包含用户原始课程描述，读取已写入审计。</div>
    <div class="candidate-stack">${resolution.candidates.map((item) => `<article><span>#${item.candidate_rank} · ${percent(item.match_score)}</span><strong>${escapeHtml(item.course_name)}</strong><small>${escapeHtml(item.course_id)} · ${escapeHtml(item.match_reason)}</small></article>`).join("")}</div>
    <form id="resolution-review-form" data-resolution-id="${escapeHtml(id)}" class="panel-form"><label class="field"><span>审计结论</span><select name="conclusion"><option value="CORRECT">匹配正确</option><option value="INCORRECT">排序或候选错误</option><option value="UNCERTAIN">无法判断</option></select></label><label class="field"><span>期望课程 ID（可选）</span><input name="expected_course_id" /></label><label class="field"><span>审计备注</span><textarea name="note" rows="4"></textarea></label><button class="button button-primary" type="submit">保存审计结论</button></form>
  </section>`);
}

async function openRecommendation(id) {
  const record = await adminApi.recommendation(id);
  openPanel(record.recommendation_id, `<section class="detail-stack"><dl class="detail-list"><div><dt>账号</dt><dd>${escapeHtml(record.username)}</dd></div><div><dt>来源</dt><dd>${escapeHtml(record.source)}</dd></div><div><dt>算法</dt><dd>${escapeHtml(record.algorithm_version)}</dd></div><div><dt>公平策略</dt><dd>${escapeHtml(record.fairness_policy_version || "未应用")}</dd></div></dl><section class="rank-list">${record.items.map((item) => `<article><b>${item.rank}</b><div><strong>${escapeHtml(item.course.course_name)}</strong><p>${escapeHtml(item.reason_text)}</p></div></article>`).join("")}</section></section>`, "wide");
}

function openAdmin(admin) {
  openPanel(`管理员：${admin.display_name}`, `<section class="detail-stack">
    <dl class="detail-list"><div><dt>用户名</dt><dd>${escapeHtml(admin.username)}</dd></div><div><dt>角色</dt><dd>admin · 全部管理权限</dd></div><div><dt>状态</dt><dd>${escapeHtml(admin.status)}</dd></div></dl>
    <form id="admin-update-form" class="panel-form" data-admin-id="${admin.admin_id}"><input type="hidden" name="row_version" value="${admin.row_version}" /><label class="field"><span>显示名称</span><input name="display_name" required maxlength="80" value="${escapeHtml(admin.display_name)}" /></label><button class="button button-primary" type="submit">保存显示名称</button></form>
    <form id="admin-password-form" class="panel-form separated-form" data-admin-id="${admin.admin_id}"><label class="field"><span>重置密码</span><input name="password" type="password" minlength="8" maxlength="72" autocomplete="new-password" required /></label><p class="form-note">重置后该管理员已有刷新令牌会立即失效。</p><button class="button button-danger" type="submit">重置密码</button></form>
  </section>`);
}

function coursePayload(data) {
  return {
    row_version: Number(data.get("row_version")),
    course_name: data.get("course_name"),
    difficulty_level: data.get("difficulty_level"),
    fields: data.get("fields").split(",").map((value) => value.trim()).filter(Boolean),
    is_advanced: data.get("is_advanced") === "on",
    advanced_label_rule: data.get("advanced_label_rule"),
    prerequisite_course_ids: data.get("prerequisites").split(",").map((value) => value.trim()).filter(Boolean),
  };
}

function courseChangeSummary(current, next) {
  const comparable = [
    ["课程名称", current.course_name, next.course_name],
    ["难度", current.difficulty_level, next.difficulty_level],
    ["领域", current.fields.join("、"), next.fields.join("、")],
    ["高阶标记", current.is_advanced ? "是" : "否", next.is_advanced ? "是" : "否"],
    ["标记规则", current.advanced_label_rule, next.advanced_label_rule],
    ["先修课程", current.prerequisites.map((item) => item.course_id).join("、") || "无", next.prerequisite_course_ids.join("、") || "无"],
  ];
  return comparable.filter(([, before, after]) => String(before) !== String(after));
}

async function saveCourseUpdate(courseId, payload) {
  try {
    await adminApi.updateCourse(courseId, payload);
    closePanel();
    state.data.courses = await adminApi.courses();
    renderRoute();
    toast("课程和目录版本已更新");
  } catch (error) {
    if (error instanceof AdminApiError && error.status === 409) {
      const latest = await adminApi.course(courseId);
      openPanel("课程版本冲突", `<section class="detail-stack"><div class="impact-note"><span>保存已停止</span><strong>另一位管理员已更新该课程</strong><p>你的草稿基于 v${payload.row_version}，当前目录为 v${latest.row_version}。请以最新版本重新检查后再保存。</p></div><dl class="detail-list"><div><dt>你的名称</dt><dd>${escapeHtml(payload.course_name)}</dd></div><div><dt>当前名称</dt><dd>${escapeHtml(latest.course_name)}</dd></div><div><dt>你的领域</dt><dd>${escapeHtml(payload.fields.join("、"))}</dd></div><div><dt>当前领域</dt><dd>${escapeHtml(latest.fields.join("、"))}</dd></div></dl><button class="button button-primary" data-reopen-course="${escapeHtml(courseId)}">载入最新版本</button></section>`);
      return;
    }
    throw error;
  }
}

function parseParameterValues(rawValue, definition) {
  const raw = String(rawValue || "").trim();
  if (!raw && definition.type === "nullable_number") return [null];
  if (!raw) throw new Error("参数不能为空");
  const values = raw.split(",").map((part) => part.trim()).filter(Boolean).map((part) => Number(part));
  if (!values.length || values.some((value) => !Number.isFinite(value))) throw new Error("只能输入以逗号分隔的数字");
  if (definition.type === "integer" && values.some((value) => !Number.isInteger(value))) throw new Error("该参数只允许整数");
  if (values.some((value) => value < definition.minimum || value > definition.maximum)) {
    throw new Error(`取值范围为 ${definition.minimum}—${definition.maximum}`);
  }
  return [...new Set(values)];
}

function experimentConfiguration(form) {
  const data = new FormData(form);
  const schema = state.data.schema || { parameters: [], maximum_combinations: 100 };
  const definitions = Object.fromEntries(schema.parameters.map((item) => [item.name, item]));
  const parameters = {};
  const errors = [];
  for (const name of ["k_neighbors", "candidate_size", "top_n", "implicit_score", "fairness_lambda", "target_gap", "max_total_cost"]) {
    const input = form.elements.namedItem(name);
    try {
      parameters[name] = parseParameterValues(data.get(name), definitions[name]);
      input?.removeAttribute("aria-invalid");
    } catch (error) {
      input?.setAttribute("aria-invalid", "true");
      errors.push(`${input?.closest("label")?.querySelector("span")?.textContent || name}：${error.message}`);
    }
  }
  parameters.enforce_prerequisites = [data.get("enforce_prerequisites") === "true"];
  const count = Object.values(parameters).reduce((total, values) => total * (values?.length || 1), 1);
  if (parameters.candidate_size && parameters.top_n && Math.min(...parameters.candidate_size) < Math.max(...parameters.top_n)) {
    errors.push("候选数量的最小值不能小于推荐数量的最大值");
  }
  if (count > schema.maximum_combinations) errors.push(`组合数 ${count} 超过上限 ${schema.maximum_combinations}`);
  return { data, parameters, count, errors };
}

function updateExperimentPreview(form) {
  const preview = document.querySelector("#combination-count");
  const errorNode = document.querySelector("#combination-error");
  const submit = form.querySelector("[data-experiment-submit]");
  const config = experimentConfiguration(form);
  if (preview) preview.textContent = String(config.count);
  if (errorNode) errorNode.textContent = config.errors[0] || "参数组合与约束均有效。";
  errorNode?.classList.toggle("is-valid", !config.errors.length);
  if (submit) submit.disabled = Boolean(config.errors.length);
  return config;
}

function drawParetoChart() {
  const canvas = document.querySelector("#pareto-chart");
  if (!canvas) return;
  const results = JSON.parse(canvas.dataset.results || "[]");
  const context = canvas.getContext("2d");
  const scale = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 680;
  const height = 330;
  canvas.width = width * scale;
  canvas.height = height * scale;
  context.scale(scale, scale);
  context.clearRect(0, 0, width, height);
  context.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--rule-strong");
  context.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted");
  context.font = "12px system-ui";
  const pad = { left: 52, right: 24, top: 24, bottom: 40 };
  context.beginPath(); context.moveTo(pad.left, pad.top); context.lineTo(pad.left, height - pad.bottom); context.lineTo(width - pad.right, height - pad.bottom); context.stroke();
  if (!results.length) return;
  const recalls = results.map((item) => item.recall);
  const gaps = results.map((item) => item.gap);
  const minR = Math.min(...recalls) - 0.01; const maxR = Math.max(...recalls) + 0.01;
  const minG = Math.max(0, Math.min(...gaps) - 0.01); const maxG = Math.max(...gaps) + 0.01;
  context.fillText("Recall@N", width - 84, height - 12); context.fillText("曝光差", 8, 18);
  results.forEach((item) => {
    const x = pad.left + ((item.recall - minR) / (maxR - minR || 1)) * (width - pad.left - pad.right);
    const y = height - pad.bottom - ((item.gap - minG) / (maxG - minG || 1)) * (height - pad.top - pad.bottom);
    context.beginPath(); context.arc(x, y, item.pareto ? 7 : 4.5, 0, Math.PI * 2);
    context.fillStyle = item.pareto ? "#17231f" : "#8eb6c7"; context.fill();
    if (item.pareto) { context.strokeStyle = "#dfbd45"; context.lineWidth = 3; context.stroke(); }
  });
}

function syncResultSelection() {
  document.querySelectorAll("[data-select-result]").forEach((element) => {
    const selected = state.selectedResultIds.has(element.dataset.selectResult);
    if (element.matches("input")) element.checked = selected;
    else element.classList.toggle("is-selected", selected);
  });
  const count = document.querySelector("[data-selection-count]");
  if (count) count.textContent = `已选 ${state.selectedResultIds.size}/5 组`;
  const compare = document.querySelector("[data-action='compare-results']");
  if (compare) compare.disabled = state.selectedResultIds.size < 2;
}

async function submitExperiment(form) {
  const { data, parameters, count, errors } = updateExperimentPreview(form);
  if (errors.length) throw new Error(errors[0]);
  const created = await adminApi.createExperiment({ name: data.get("name"), mode: count === 1 ? "SINGLE" : "GRID", parameters });
  state.data.experiments = await adminApi.experiments();
  await loadExperiment(created.experiment_id, false);
  renderRoute();
  toast(`实验完成：${count} 组参数`);
}

root.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    if (form.id === "login-form") {
      state.loading = true; state.error = ""; root.innerHTML = loginView();
      const data = new FormData(form);
      const auth = await adminApi.login(data.get("username"), data.get("password"));
      setAdminAuth(auth); state.auth = auth; state.route = "dashboard"; window.location.hash = "/dashboard"; await loadRoute();
    }
    if (form.id === "experiment-form") await submitExperiment(form);
    if (form.id === "import-form") {
      const file = new FormData(form).get("file");
      const format = file.name.toLowerCase().endsWith(".csv") ? "CSV" : "JSONL";
      const result = await adminApi.validateImport({ filename: file.name, file_format: format, content: await file.text() });
      state.data.imports = await adminApi.imports(); renderRoute();
      toast(result.status === "VALIDATED" ? "文件校验通过" : `发现 ${result.summary.errors} 个问题`, result.status === "VALIDATED" ? "success" : "danger");
    }
  } catch (error) {
    state.error = error.message; toast(error.message, "danger");
    if (form.id === "login-form") { state.loading = false; root.innerHTML = loginView(); }
  }
});

panelRoot.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  try {
    if (form.id === "confirm-action-form") {
      const action = state.pendingAction;
      if (!action) return;
      const submit = form.querySelector("[type='submit']");
      if (submit) submit.disabled = true;
      await action(data.get("reason")?.trim() || null);
      state.pendingAction = null;
      if (panelRoot.contains(form)) closePanel();
      return;
    }
    if (form.id === "course-form") {
      const payload = coursePayload(data);
      const changes = courseChangeSummary(state.activeCourse, payload);
      if (!changes.length) { toast("没有需要保存的变更", "danger"); return; }
      openConfirmation({
        title: "确认课程变更",
        summary: `${state.activeCourse.course_name} · ${changes.length} 项变更`,
        impact: changes.map(([label, before, after]) => `${label}：${before || "—"} → ${after || "—"}`).join("；"),
        confirmLabel: "保存并生成目录版本",
        action: async (reason) => saveCourseUpdate(form.dataset.courseId, { ...payload, reason }),
      });
      return;
    }
    if (form.id === "new-course-form") {
      await adminApi.createCourse({
        course_id: data.get("course_id"), course_name: data.get("course_name"), difficulty_level: data.get("difficulty_level"),
        fields: data.get("fields").split(",").map((v) => v.trim()).filter(Boolean), is_advanced: data.get("is_advanced") === "on",
        prerequisite_course_ids: data.get("prerequisites").split(",").map((v) => v.trim()).filter(Boolean),
      });
      closePanel(); state.data.courses = await adminApi.courses(); renderRoute(); toast("课程已创建");
    }
    if (form.id === "resolution-review-form") {
      await adminApi.reviewResolution(form.dataset.resolutionId, { conclusion: data.get("conclusion"), expected_course_id: data.get("expected_course_id") || null, note: data.get("note") || null });
      closePanel(); state.data.resolutions = await adminApi.resolutions(); renderRoute(); toast("审计结论已保存");
    }
    if (form.id === "admin-update-form") {
      await adminApi.updateAdmin(form.dataset.adminId, { display_name: data.get("display_name"), row_version: Number(data.get("row_version")) });
      closePanel(); state.data.admins = await adminApi.admins(); renderRoute(); toast("管理员显示名称已更新");
    }
    if (form.id === "admin-password-form") {
      const adminId = form.dataset.adminId;
      const password = data.get("password");
      openConfirmation({ title: "确认重置密码", summary: "使该管理员的现有刷新令牌失效", impact: "重置后，该管理员需要使用新密码重新登录。", confirmLabel: "确认重置密码", action: async () => { await adminApi.resetAdminPassword(adminId, password); toast("管理员密码已重置"); } });
    }
  } catch (error) {
    form.querySelector("[type='submit']")?.removeAttribute("disabled");
    toast(error.message, "danger");
  }
});

root.addEventListener("click", async (event) => {
  const target = event.target.closest("button, tr, input[data-select-result]");
  if (!target) return;
  try {
    if (target.dataset.route) { state.route = target.dataset.route; state.menuOpen = false; window.location.hash = `/${state.route}`; return; }
    if (target.dataset.action === "toggle-menu") { state.menuOpen = !state.menuOpen; renderRoute(); }
    if (target.dataset.action === "retry") await loadRoute();
    if (target.dataset.action === "logout") { await adminApi.logout(state.auth.refresh_token).catch(() => null); setAdminAuth(null); state.auth = null; root.innerHTML = loginView(); }
    if (target.dataset.action === "new-course") openNewCourse();
    if (target.dataset.action === "new-admin") openPanel("创建管理员", `<form id="admin-create-form" class="panel-form"><label class="field"><span>用户名</span><input name="username" required /></label><label class="field"><span>显示名称</span><input name="display_name" required /></label><label class="field"><span>初始密码</span><input name="password" type="password" minlength="8" required /></label><p class="form-note">账号固定使用 admin 角色并拥有全部管理权限。</p><button class="button button-primary" type="submit">创建管理员</button></form>`);
    if (target.dataset.openCourse) await openCourse(target.dataset.openCourse);
    if (target.dataset.openUser) await openUser(target.dataset.openUser);
    if (target.dataset.openResolution) await openResolution(target.dataset.openResolution);
    if (target.dataset.openRecommendation) await openRecommendation(target.dataset.openRecommendation);
    if (target.dataset.openAdmin) {
      const admin = state.data.admins.items.find((item) => item.admin_id === target.dataset.openAdmin);
      if (admin) openAdmin(admin);
    }
    if (target.dataset.openExperiment) await loadExperiment(target.dataset.openExperiment);
    if (target.dataset.action === "load-latest-experiment" && state.data.experiments.items[0]) await loadExperiment(state.data.experiments.items[0].experiment_id);
    if (target.dataset.selectResult) {
      const id = target.dataset.selectResult;
      if (state.selectedResultIds.has(id)) state.selectedResultIds.delete(id);
      else if (state.selectedResultIds.size < 5) state.selectedResultIds.add(id);
      else toast("最多选择 5 组结果", "danger");
      syncResultSelection();
    }
    if (target.dataset.action === "compare-results") {
      const ids = [...state.selectedResultIds]; const comparison = await adminApi.compareResults(ids, ids[0]);
      openPanel("参数结果对比", `<div class="comparison-grid">${comparison.items.map((item) => `<article><span>${escapeHtml(item.result_id)}</span><strong>λ ${item.parameters.fairness_lambda} / k ${item.parameters.k_neighbors}</strong><dl><div><dt>Recall</dt><dd>${percent(item.metrics.recall_at_n)}</dd></div><div><dt>NDCG</dt><dd>${percent(item.metrics.ndcg_at_n)}</dd></div><div><dt>曝光差</dt><dd>${percent(item.metrics.gap_after)}</dd></div><div><dt>换位成本</dt><dd>${percent(item.metrics.total_swap_cost)}</dd></div></dl></article>`).join("")}</div>`, "wide");
    }
    if (target.dataset.action === "policy-from-result") { await adminApi.createPolicyDraft(target.dataset.resultId, "由超参数分析页生成，等待管理员检查后启用"); toast("公平策略草稿已生成"); }
    if (target.dataset.action === "export-experiment") {
      const exported = await adminApi.exportExperiment(target.dataset.experimentId, "CSV");
      downloadText(`${target.dataset.experimentId}.csv`, exported.content);
      toast("实验结果已导出");
    }
    if (target.dataset.action === "clone-experiment") {
      const cloned = await adminApi.cloneExperiment(target.dataset.experimentId);
      state.data.experiments = await adminApi.experiments();
      await loadExperiment(cloned.experiment_id, false); renderRoute(); toast("实验已克隆并运行");
    }
    if (target.dataset.action === "cancel-experiment") {
      const experimentId = target.dataset.experimentId;
      openConfirmation({ title: "取消参数实验", summary: experimentId, impact: "未完成组合将停止计算；已生成的结果仍保留在审计记录中。", confirmLabel: "确认取消", action: async () => { await adminApi.cancelExperiment(experimentId); state.data.experiments = await adminApi.experiments(); await loadExperiment(experimentId, false); renderRoute(); toast("实验已取消"); } });
    }
    if (target.dataset.action === "compute-policy") {
      const result = await adminApi.computePolicy({ fairness_lambda: 0.15, target_gap: 0.05, max_total_cost: 0.08, top_n: 10, minimum_group_size: 30 });
      state.data.policies = await adminApi.policies(); renderRoute(); toast(`策略 ${result.policy_id} 已完成计算`);
    }
    if (target.dataset.activatePolicy || target.dataset.rollbackPolicy) {
      const policyId = target.dataset.activatePolicy || target.dataset.rollbackPolicy;
      const candidate = state.data.policies.items.find((item) => item.policy_id === policyId);
      const current = state.data.policies.items.find((item) => item.status === "ACTIVE");
      const delta = current ? `Recall ${percent(current.metrics.recall_at_n)} → ${percent(candidate.metrics.recall_at_n)}；曝光差 ${percent(current.metrics.gap_after)} → ${percent(candidate.metrics.gap_after)}；换位成本 ${percent(current.metrics.total_swap_cost)} → ${percent(candidate.metrics.total_swap_cost)}` : `当前无活动策略；启用后曝光差预计为 ${percent(candidate.metrics.gap_after)}，换位成本 ${percent(candidate.metrics.total_swap_cost)}`;
      openConfirmation({ title: target.dataset.rollbackPolicy ? "确认回滚公平策略" : "确认启用公平策略", summary: `${current?.policy_id || "无活动策略"} → ${policyId}`, impact: delta, confirmLabel: target.dataset.rollbackPolicy ? "确认回滚" : "确认启用", action: async (reason) => { await adminApi.activatePolicy(policyId, { row_version: Number(target.dataset.rowVersion), expected_active_policy_id: target.dataset.activeId || null, reason }); state.data.policies = await adminApi.policies(); renderRoute(); toast(target.dataset.rollbackPolicy ? "公平策略已回滚" : "公平策略已启用"); } });
    }
    if (target.dataset.adminStatus) {
      const action = target.dataset.adminStatus;
      const adminId = target.dataset.adminId;
      const admin = state.data.admins.items.find((item) => item.admin_id === adminId);
      openConfirmation({ title: action === "disable" ? "停用管理员" : "恢复管理员", summary: `${admin?.display_name || adminId} · ${admin?.username || ""}`, impact: action === "disable" ? "该管理员的刷新令牌会立即失效，无法继续进入管理端。" : "恢复后该账号重新拥有全部管理员权限。", confirmLabel: action === "disable" ? "确认停用" : "确认恢复", action: async (reason) => { await adminApi.setAdminStatus(adminId, action, Number(target.dataset.rowVersion), reason); state.data.admins = await adminApi.admins(); renderRoute(); toast("管理员状态已更新"); } });
    }
    if (target.dataset.action === "search-courses") { state.data.courses = await adminApi.courses(document.querySelector("#course-search")?.value || ""); renderRoute(); }
    if (target.dataset.importAction) {
      const action = target.dataset.importAction;
      const importId = target.dataset.importId;
      openConfirmation({ title: action === "commit" ? "提交课程导入" : "取消课程导入", summary: importId, impact: action === "commit" ? "所有已校验课程会在同一事务中写入，并生成新的课程目录版本。" : "本次校验记录会保留，但不能再提交。", confirmLabel: action === "commit" ? "确认提交" : "确认取消", action: async (reason) => { if (action === "commit") await adminApi.commitImport(importId, Number(target.dataset.rowVersion), reason); else await adminApi.cancelImport(importId, Number(target.dataset.rowVersion), reason); state.data.imports = await adminApi.imports(); renderRoute(); toast(action === "commit" ? "导入已提交，目录版本已更新" : "导入已取消"); } });
    }
    if (target.dataset.jobAction) {
      const action = target.dataset.jobAction;
      const jobId = target.dataset.jobId;
      if (action === "cancel") openConfirmation({ title: "取消后台任务", summary: jobId, impact: "任务会停止并保留当前状态与审计轨迹。", confirmLabel: "确认取消", action: async () => { await adminApi.cancelJob(jobId); state.data.jobs = await adminApi.jobs(); renderRoute(); toast("任务已取消"); } });
      else { await adminApi.retryJob(jobId); state.data.jobs = await adminApi.jobs(); renderRoute(); toast("任务已重新排队"); }
    }
  } catch (error) { toast(error.message, "danger"); }
});

panelRoot.addEventListener("click", async (event) => {
  const target = event.target.closest("button, .panel-backdrop");
  if (!target) return;
  try {
    if (target.dataset.action === "close-panel") closePanel();
    if (target.dataset.reopenCourse) await openCourse(target.dataset.reopenCourse);
    if (target.dataset.addAlias) { const value = document.querySelector("#new-alias")?.value.trim(); if (value) { await adminApi.addAlias(target.dataset.addAlias, value); await openCourse(target.dataset.addAlias); toast("课程别名已添加"); } }
    if (target.dataset.courseStatus) {
      const action = target.dataset.courseStatus;
      const courseId = target.dataset.courseId;
      openConfirmation({ title: action === "archive" ? "归档课程" : "恢复课程", summary: `${state.activeCourse?.course_name || courseId} · ${courseId}`, impact: action === "archive" ? "课程会从检索候选和新推荐中移除，但历史推荐快照不变。" : "课程会重新进入检索候选与推荐目录。", confirmLabel: action === "archive" ? "确认归档" : "确认恢复", action: async (reason) => { await adminApi.setCourseStatus(courseId, action, Number(target.dataset.rowVersion), reason); closePanel(); state.data.courses = await adminApi.courses(); renderRoute(); toast("课程状态已更新"); } });
    }
    if (target.dataset.userStatus) {
      const action = target.dataset.userStatus;
      const userId = target.dataset.userId;
      openConfirmation({ title: action === "disable" ? "停用用户账号" : "恢复用户账号", summary: userId, impact: action === "disable" ? "用户现有刷新令牌会立即失效，但画像、课程确认和历史推荐仍保留。" : "用户可重新登录，原有业务数据保持不变。", confirmLabel: action === "disable" ? "确认停用" : "确认恢复", action: async (reason) => { await adminApi.setUserStatus(userId, action, Number(target.dataset.rowVersion), reason); closePanel(); state.data.users = await adminApi.users(); renderRoute(); toast("用户状态已更新"); } });
    }
    if (target.dataset.simulateUser) {
      const userId = target.dataset.simulateUser;
      const profileVersion = Number(target.dataset.profileVersion);
      openConfirmation({ title: "运行推荐诊断", summary: `${userId} · 画像 v${profileVersion}`, impact: "诊断只生成后台任务结果，不会写入用户推荐历史，也不会改变画像。", confirmLabel: "运行诊断", action: async (reason) => { const simulation = await adminApi.simulateRecommendation({ account_id: userId, profile_version: profileVersion, top_n: 10, reason }); openPanel("诊断模拟结果", `<section class="detail-stack"><div class="impact-note"><span>只读诊断</span><strong>${escapeHtml(simulation.job_id)}</strong><p>来源 ${escapeHtml(simulation.result.source)} · 公平策略 ${escapeHtml(simulation.result.fairness_policy_version || "未应用")}</p></div><section class="rank-list">${simulation.result.items.map((item) => `<article><b>${item.rank}</b><div><strong>${escapeHtml(item.course.course_name)}</strong><p>${escapeHtml(item.reason_text)}</p></div></article>`).join("")}</section></section>`, "wide"); } });
    }
  } catch (error) { toast(error.message, "danger"); }
});

panelRoot.addEventListener("submit", async (event) => {
  if (event.target.id !== "admin-create-form") return;
  event.preventDefault(); const data = new FormData(event.target);
  try { await adminApi.createAdmin(Object.fromEntries(data)); closePanel(); state.data.admins = await adminApi.admins(); renderRoute(); toast("管理员已创建"); }
  catch (error) { toast(error.message, "danger"); }
});

root.addEventListener("input", (event) => {
  const form = event.target.closest("#experiment-form");
  if (form) updateExperimentPreview(form);
});

root.addEventListener("keydown", (event) => {
  const row = event.target.closest("tr[data-open-course], tr[data-open-user], tr[data-open-resolution], tr[data-open-recommendation]");
  if (row && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    row.click();
  }
});

window.addEventListener("hashchange", () => { state.route = routeFromHash(); state.menuOpen = false; loadRoute(); });
window.addEventListener("keydown", (event) => {
  const panel = panelRoot.querySelector(".detail-panel");
  if (!panel) return;
  if (event.key === "Escape") { event.preventDefault(); closePanel(); return; }
  if (event.key !== "Tab") return;
  const focusable = [...panel.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
  if (!focusable.length) { event.preventDefault(); panel.focus(); return; }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});

loadRoute();
