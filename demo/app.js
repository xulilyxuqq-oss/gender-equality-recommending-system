const courseCatalog = [
  {
    id: "C_PY_101",
    name: "Python 程序设计",
    field: "计算机科学",
    difficulty: "入门",
    advanced: false,
    keywords: ["python", "编程", "程序设计", "python入门"],
    prerequisites: [],
    description: "学习 Python 基础语法、程序结构与问题求解方法。",
  },
  {
    id: "C_PY_205",
    name: "Python 数据分析",
    field: "数据科学",
    difficulty: "中级",
    advanced: true,
    keywords: ["python", "数据分析", "pandas"],
    prerequisites: ["Python 程序设计"],
    description: "使用 Python 完成数据清洗、分析与基础可视化。",
  },
  {
    id: "C_MATH_110",
    name: "线性代数",
    field: "数学",
    difficulty: "基础",
    advanced: false,
    keywords: ["线性代数", "线代", "矩阵", "向量"],
    prerequisites: [],
    description: "学习向量、矩阵、线性变换和特征值等基础内容。",
  },
  {
    id: "C_MATH_120",
    name: "微积分",
    field: "数学",
    difficulty: "基础",
    advanced: false,
    keywords: ["微积分", "高等数学", "高数", "导数", "积分"],
    prerequisites: [],
    description: "覆盖极限、导数、积分及其基本应用。",
  },
  {
    id: "C_CS_100",
    name: "计算机基础",
    field: "计算机科学",
    difficulty: "入门",
    advanced: false,
    keywords: ["计算机基础", "计算机导论", "计基", "电脑基础"],
    prerequisites: [],
    description: "理解计算机系统、数据表示和基本软件工具。",
  },
  {
    id: "C_ML_210",
    name: "机器学习导论",
    field: "人工智能",
    difficulty: "中级",
    advanced: true,
    keywords: ["机器学习", "机器学习基础", "人工智能"],
    prerequisites: ["Python 程序设计", "线性代数"],
    description: "从监督学习和无监督学习入门，建立机器学习基本框架。",
  },
  {
    id: "C_ML_310",
    name: "统计学习方法",
    field: "人工智能",
    difficulty: "进阶",
    advanced: true,
    keywords: ["统计学习", "机器学习", "分类", "回归"],
    prerequisites: ["概率统计", "线性代数"],
    description: "系统学习常见统计学习模型、损失函数与优化方法。",
  },
  {
    id: "C_DATA_200",
    name: "数据分析基础",
    field: "数据科学",
    difficulty: "中级",
    advanced: false,
    keywords: ["数据分析", "数据科学", "数据处理"],
    prerequisites: ["Python 程序设计"],
    description: "建立从数据整理到分析结论表达的完整工作流。",
  },
  {
    id: "C_CS_220",
    name: "数据结构与算法",
    field: "计算机科学",
    difficulty: "中级",
    advanced: true,
    keywords: ["数据结构", "算法", "编程进阶"],
    prerequisites: ["Python 程序设计"],
    description: "学习线性结构、树、图、排序和基础算法分析。",
  },
  {
    id: "C_MATH_230",
    name: "概率统计",
    field: "数学",
    difficulty: "中级",
    advanced: false,
    keywords: ["概率统计", "概率", "统计"],
    prerequisites: ["微积分"],
    description: "学习随机变量、常见分布、统计估计和假设检验。",
  },
  {
    id: "C_DB_230",
    name: "数据库系统概论",
    field: "计算机科学",
    difficulty: "中级",
    advanced: false,
    keywords: ["数据库", "数据库原理", "sql", "数据管理"],
    prerequisites: ["计算机基础"],
    description: "理解关系模型、SQL、数据库设计与事务处理。",
  },
];

const recommendationTemplates = [
  {
    courseId: "C_DATA_200",
    reason: "与你学习经历相近的用户经常选择这门课，你已满足 Python 先修要求。",
  },
  {
    courseId: "C_CS_220",
    reason: "这门课能把编程基础推进到算法思维，并且先修条件已经满足。",
  },
  {
    courseId: "C_ML_210",
    reason: "你确认的 Python 与线性代数课程构成了这门课的核心先修基础。",
  },
  {
    courseId: "C_DB_230",
    reason: "这门课适合作为计算机基础之后的数据管理方向课程。",
  },
];

const state = {
  route: "home",
  account: null,
  accounts: new Map([
    ["course_demo", { username: "course_demo", password: "demo1234", workspace: null }],
  ]),
  authMode: "register",
  chatInitialized: false,
  chatStage: "idle",
  draft: {
    displayName: "",
    gender: null,
    courses: [],
  },
  resolutions: new Map(),
  recommendations: [],
  favorites: new Set(),
  history: [],
  viewingHistoryId: null,
  lastRecommendationAt: null,
  lastDrawerTrigger: null,
};

const elements = {
  sidebar: document.querySelector("#sidebar"),
  mobileBar: document.querySelector(".mobile-bar"),
  menuButton: document.querySelector("#menu-button"),
  main: document.querySelector("#main-content"),
  chatThread: document.querySelector("#chat-thread"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  composerHint: document.querySelector("#composer-hint"),
  assistantStatus: document.querySelector("#assistant-status"),
  profileName: document.querySelector("#profile-name"),
  profileGender: document.querySelector("#profile-gender"),
  profileProgress: document.querySelector("#profile-progress"),
  confirmedCourses: document.querySelector("#confirmed-courses"),
  courseCount: document.querySelector("#course-count"),
  confirmProfile: document.querySelector("#confirm-profile"),
  recommendationBoard: document.querySelector("#recommendation-board"),
  recommendationMeta: document.querySelector("#recommendation-meta"),
  recommendationSubtitle: document.querySelector("#recommendation-subtitle"),
  historyList: document.querySelector("#history-list"),
  favoritesList: document.querySelector("#favorites-list"),
  drawer: document.querySelector("#detail-drawer"),
  drawerContent: document.querySelector("#drawer-content"),
  drawerScrim: document.querySelector("#drawer-scrim"),
  closeDrawer: document.querySelector("#close-drawer"),
  toastRegion: document.querySelector("#toast-region"),
  sidebarName: document.querySelector("#sidebar-name"),
  sidebarStatus: document.querySelector("#sidebar-status"),
  sidebarAvatar: document.querySelector("#sidebar-avatar"),
  sessionPanel: document.querySelector("#session-panel"),
  sessionAccountName: document.querySelector("#session-account-name"),
  authTabs: document.querySelector("#auth-tabs"),
  registerForm: document.querySelector("#register-form"),
  loginForm: document.querySelector("#login-form"),
  registerTab: document.querySelector("#register-tab"),
  loginTab: document.querySelector("#login-tab"),
};

function routeTo(route) {
  const protectedRoutes = new Set(["assistant", "recommendations", "history", "favorites"]);
  if (protectedRoutes.has(route) && !state.account) {
    route = "auth";
    showToast("先创建一个演示账号，再继续体验。");
  }

  state.route = route;
  document.querySelectorAll("[data-view]").forEach((view) => {
    view.classList.toggle("is-visible", view.dataset.view === route);
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.route === route;
    item.classList.toggle("is-active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });

  elements.sidebar.classList.remove("is-open");
  elements.menuButton.setAttribute("aria-expanded", "false");
  closeDetails();
  window.scrollTo({ top: 0, behavior: "smooth" });
  requestAnimationFrame(() => elements.main.focus({ preventScroll: true }));

  if (route === "assistant") initializeChat();
  if (route === "recommendations") renderRecommendations();
  if (route === "history") renderHistory();
  if (route === "favorites") renderFavorites();
  if (route === "auth") renderAuthState();
}

document.addEventListener("click", (event) => {
  const routeTarget = event.target.closest("[data-route]");
  if (routeTarget) {
    routeTo(routeTarget.dataset.route);
  }
});

elements.menuButton.addEventListener("click", () => {
  const open = elements.sidebar.classList.toggle("is-open");
  elements.menuButton.setAttribute("aria-expanded", String(open));
});

document.querySelector("#watch-demo").addEventListener("click", () => {
  const mechanism = document.querySelector("#mechanism-strip");
  mechanism.scrollIntoView({ behavior: "smooth", block: "center" });
  mechanism.classList.remove("is-highlighted");
  requestAnimationFrame(() => mechanism.classList.add("is-highlighted"));
  window.setTimeout(() => mechanism.classList.remove("is-highlighted"), 1600);
});

elements.registerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const username = document.querySelector("#register-username").value.trim();
  const password = document.querySelector("#register-password").value;
  const confirmPassword = document.querySelector("#register-confirm").value;
  const error = document.querySelector("#register-error");

  if (!username) {
    error.textContent = "请输入用户名。";
    return;
  }
  if (password.length < 8) {
    error.textContent = "密码至少需要 8 位。";
    return;
  }
  if (password.length > 72) {
    error.textContent = "密码不能超过 72 位。";
    return;
  }
  if (password !== confirmPassword) {
    error.textContent = "两次输入的密码不一致。";
    return;
  }
  if (state.accounts.has(username)) {
    error.textContent = "这个用户名已存在，请直接登录或换一个名称。";
    return;
  }

  error.textContent = "";
  createAccount(username, password);
});

document.querySelector("#demo-account").addEventListener("click", () => {
  loginAccount("course_demo");
});

elements.loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const username = document.querySelector("#login-username").value.trim();
  const password = document.querySelector("#login-password").value;
  const error = document.querySelector("#login-error");
  const account = state.accounts.get(username);

  if (!account || account.password !== password) {
    error.textContent = "用户名或密码错误。";
    return;
  }

  error.textContent = "";
  loginAccount(username);
});

elements.registerTab.addEventListener("click", () => setAuthMode("register"));
elements.loginTab.addEventListener("click", () => setAuthMode("login"));

document.querySelector("#logout-button").addEventListener("click", () => {
  saveActiveWorkspace();
  state.account = null;
  resetActiveWorkspace();
  updateSidebarAccount();
  renderAuthState();
  showToast("已退出当前演示账号。账号仍保存在本次页面会话中。");
  routeTo("auth");
});

function createAccount(username, password) {
  state.accounts.set(username, { username, password, workspace: null });
  loginAccount(username, true);
}

function loginAccount(username, created = false) {
  const account = state.accounts.get(username);
  if (!account) return;
  state.account = { username };
  restoreWorkspace(account.workspace);
  updateSidebarAccount();
  renderAuthState();
  showToast(created ? "演示账号已创建，画像将在对话中逐步完善。" : "已登录演示账号。");
  routeTo("assistant");
}

function updateSidebarAccount() {
  const username = state.account?.username;
  if (!username) {
    elements.sidebarName.textContent = "访客模式";
    elements.sidebarStatus.textContent = "尚未创建账号";
    elements.sidebarAvatar.textContent = "访";
    return;
  }
  elements.sidebarName.textContent = username;
  elements.sidebarStatus.textContent = state.draft.displayName && state.draft.gender ? "画像可继续调整" : "画像等待完善";
  elements.sidebarAvatar.textContent = username.slice(0, 1).toUpperCase();
}

function setAuthMode(mode) {
  state.authMode = mode;
  const registering = mode === "register";
  elements.registerForm.hidden = !registering;
  elements.loginForm.hidden = registering;
  elements.registerTab.classList.toggle("is-active", registering);
  elements.loginTab.classList.toggle("is-active", !registering);
  elements.registerTab.setAttribute("aria-selected", String(registering));
  elements.loginTab.setAttribute("aria-selected", String(!registering));
}

function renderAuthState() {
  const loggedIn = Boolean(state.account);
  elements.sessionPanel.hidden = !loggedIn;
  elements.authTabs.hidden = loggedIn;
  if (loggedIn) {
    elements.registerForm.hidden = true;
    elements.loginForm.hidden = true;
    elements.sessionAccountName.textContent = state.account.username;
  } else {
    setAuthMode(state.authMode);
  }
}

function saveActiveWorkspace() {
  if (!state.account) return;
  const account = state.accounts.get(state.account.username);
  if (!account) return;
  account.workspace = {
    draft: {
      displayName: state.draft.displayName,
      gender: state.draft.gender,
      courseIds: state.draft.courses.map((course) => course.id),
    },
    recommendations: state.recommendations.map((item) => ({
      rank: item.rank,
      courseId: item.course.id,
      reason: item.reason,
      source: item.source,
    })),
    favorites: [...state.favorites],
    history: state.history.map((entry) => ({
      ...entry,
      items: entry.items.map((item) => ({
        rank: item.rank,
        courseId: item.course.id,
        reason: item.reason,
        source: item.source,
      })),
    })),
    lastRecommendationAt: state.lastRecommendationAt,
  };
}

function restoreWorkspace(workspace) {
  resetActiveWorkspace();
  if (!workspace) return;
  state.draft = {
    displayName: workspace.draft.displayName,
    gender: workspace.draft.gender,
    courses: workspace.draft.courseIds.map(findCourse).filter(Boolean),
  };
  state.recommendations = workspace.recommendations.map(hydrateRecommendation).filter((item) => item.course);
  state.favorites = new Set(workspace.favorites);
  state.history = workspace.history.map((entry) => ({
    ...entry,
    createdAt: new Date(entry.createdAt),
    deletedAt: entry.deletedAt ? new Date(entry.deletedAt) : null,
    items: entry.items.map(hydrateRecommendation).filter((item) => item.course),
  }));
  state.lastRecommendationAt = workspace.lastRecommendationAt ? new Date(workspace.lastRecommendationAt) : null;
  updateProfileBoard();
  renderRecommendations();
  renderHistory();
  renderFavorites();
}

function resetActiveWorkspace() {
  state.chatInitialized = false;
  state.chatStage = "idle";
  state.draft = { displayName: "", gender: null, courses: [] };
  state.resolutions = new Map();
  state.recommendations = [];
  state.favorites = new Set();
  state.history = [];
  state.viewingHistoryId = null;
  state.lastRecommendationAt = null;
  elements.chatThread.replaceChildren();
  updateProfileBoard();
  renderRecommendations();
  renderHistory();
  renderFavorites();
}

function findCourse(courseId) {
  return courseCatalog.find((course) => course.id === courseId);
}

function hydrateRecommendation(item) {
  return { ...item, course: findCourse(item.courseId) };
}

function initializeChat() {
  if (state.chatInitialized) return;
  state.chatInitialized = true;

  if (state.draft.displayName && state.draft.gender) {
    state.chatStage = "ready";
    elements.assistantStatus.textContent = "画像可以调整";
    addAssistantMessage(`欢迎回来，${state.draft.displayName}。你的画像和本次页面会话中的结果已经恢复，可以继续添加课程或重新生成推荐。`);
    elements.chatInput.placeholder = "继续描述已学课程…";
    updateProfileBoard();
    return;
  }

  if (state.draft.displayName) {
    state.chatStage = "gender";
    elements.assistantStatus.textContent = "等待性别选择";
    addAssistantMessage(`欢迎回来，${state.draft.displayName}。我们从上次中断的位置继续，请选择你的性别。`);
    renderQuickActions([
      { label: "男", value: "male" },
      { label: "女", value: "female" },
    ]);
    elements.chatInput.placeholder = "也可以输入“男”或“女”…";
    return;
  }

  state.chatStage = "name";
  elements.assistantStatus.textContent = "正在采集姓名";
  addAssistantMessage("你好，我是课程路径助手。注册已经完成，但我们还没有填写任何画像信息。先告诉我应该怎么称呼你？");
  elements.chatInput.placeholder = "输入你的姓名或称呼…";
  setTimeout(() => elements.chatInput.focus(), 350);
}

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.chatInput.value.trim();
  if (!message) return;
  elements.chatInput.value = "";
  autoResizeInput();
  handleUserMessage(message);
});

elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

elements.chatInput.addEventListener("input", autoResizeInput);

function autoResizeInput() {
  elements.chatInput.style.height = "auto";
  elements.chatInput.style.height = `${Math.min(elements.chatInput.scrollHeight, 130)}px`;
}

function handleUserMessage(message) {
  addUserMessage(message);

  if (state.chatStage === "edit-name") {
    state.draft.displayName = message.slice(0, 24);
    state.chatStage = state.draft.gender ? "ready" : "gender";
    updateProfileBoard();
    updateSidebarAccount();
    showTyping(() => {
      addAssistantMessage(`已把姓名改为“${state.draft.displayName}”。${state.draft.gender ? "画像可以再次确认。" : "接下来请选择性别。"}`);
      if (!state.draft.gender) {
        renderQuickActions([
          { label: "男", value: "male" },
          { label: "女", value: "female" },
        ]);
      }
    });
    return;
  }

  if (state.chatStage === "edit-gender") {
    const gender = parseGender(message);
    if (!gender) {
      showTyping(() => addAssistantMessage("请直接选择“男”或“女”。"));
      return;
    }
    finishGenderEdit(gender);
    return;
  }

  if (state.chatStage === "name") {
    state.draft.displayName = message.slice(0, 24);
    state.chatStage = "gender";
    updateProfileBoard();
    updateSidebarAccount();
    showTyping(() => {
      addAssistantMessage(`好的，${state.draft.displayName}。当前演示模型只支持男、女两个数据组，请选择你的性别。系统不会根据姓名推断。`);
      renderQuickActions([
        { label: "男", value: "male" },
        { label: "女", value: "female" },
      ]);
      elements.assistantStatus.textContent = "等待性别选择";
      elements.chatInput.placeholder = "也可以输入“男”或“女”…";
    });
    return;
  }

  if (state.chatStage === "gender") {
    const gender = parseGender(message);
    if (!gender) {
      showTyping(() => {
        addAssistantMessage("我没有识别到明确选项。请直接选择“男”或“女”。");
        renderQuickActions([
          { label: "男", value: "male" },
          { label: "女", value: "female" },
        ]);
      });
      return;
    }
    acceptGender(gender);
    return;
  }

  if (state.chatStage === "courses") {
    if (/没有|没学过|暂无|跳过|无课程/.test(message)) {
      state.chatStage = "ready";
      elements.assistantStatus.textContent = "画像可以确认";
      showTyping(() => {
        addAssistantMessage("明白了。这次将使用不要求先修课程的热门课程作为兜底推荐。请检查右侧画像，然后确认生成推荐。");
        updateProfileBoard();
      });
      return;
    }
    beginCourseResolution(message);
    return;
  }

  if (state.chatStage === "resolving") {
    showTyping(() => {
      addAssistantMessage("请先处理上方的课程确认卡。每一项都确认或拒绝后，我们再继续。");
    });
    return;
  }

  if (state.chatStage === "ready") {
    beginCourseResolution(message);
  }
}

function parseGender(value) {
  const normalized = value.trim().toLowerCase();
  if (normalized === "男" || normalized === "男性" || normalized === "male") return 1;
  if (normalized === "女" || normalized === "女性" || normalized === "female") return 2;
  return null;
}

function acceptGender(gender) {
  state.draft.gender = gender;
  state.chatStage = "courses";
  updateProfileBoard();
  showTyping(() => {
    addAssistantMessage("最后，请用自然语言告诉我你学过哪些课程。可以一次说多门，例如：“我学过 Python 入门和线性代数”。");
    renderQuickActions([{ label: "我还没有学过课程", value: "no-courses" }]);
    elements.assistantStatus.textContent = "等待已学课程";
    elements.chatInput.placeholder = "描述一门或多门已学课程…";
  });
}

function finishGenderEdit(gender) {
  state.draft.gender = gender;
  state.chatStage = state.draft.displayName ? "ready" : "name";
  updateProfileBoard();
  showTyping(() => {
    addAssistantMessage(`性别已更新为“${gender === 1 ? "男" : "女"}”。画像可以再次确认。`);
    elements.assistantStatus.textContent = "画像可以确认";
    elements.chatInput.placeholder = "继续描述已学课程…";
  });
}

function renderQuickActions(actions) {
  const container = document.createElement("div");
  container.className = "quick-actions";
  actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      const validStage =
        ((action.value === "male" || action.value === "female") && state.chatStage === "gender") ||
        ((action.value === "edit-male" || action.value === "edit-female") && state.chatStage === "edit-gender") ||
        (action.value === "no-courses" && state.chatStage === "courses");
      if (!validStage) return;
      container.querySelectorAll("button").forEach((item) => (item.disabled = true));
      if (action.value === "male") {
        addUserMessage("男");
        acceptGender(1);
      } else if (action.value === "female") {
        addUserMessage("女");
        acceptGender(2);
      } else if (action.value === "edit-male") {
        addUserMessage("男");
        finishGenderEdit(1);
      } else if (action.value === "edit-female") {
        addUserMessage("女");
        finishGenderEdit(2);
      } else if (action.value === "no-courses") {
        addUserMessage("我还没有学过课程");
        state.chatStage = "ready";
        elements.assistantStatus.textContent = "画像可以确认";
        showTyping(() => {
          addAssistantMessage("好的，我会使用不要求先修课程的热门推荐。请检查右侧画像，然后确认生成推荐。");
          updateProfileBoard();
        });
      }
    });
    container.append(button);
  });
  elements.chatThread.append(container);
  scrollChat();
}

function beginCourseResolution(message) {
  state.chatStage = "resolving";
  elements.assistantStatus.textContent = "正在匹配课程";
  const phrases = splitCoursePhrases(message);

  showTyping(() => {
    addAssistantMessage(`我从你的描述中识别出 ${phrases.length} 项课程。下面是课程库中最相近的结果，请逐项确认。`);
    phrases.forEach((phrase, index) => {
      const resolution = {
        id: `res_${Date.now()}_${index}`,
        query: phrase,
        candidates: matchCourses(phrase),
        status: "pending",
      };
      state.resolutions.set(resolution.id, resolution);
      renderResolutionCard(resolution);
    });
    elements.assistantStatus.textContent = "等待课程确认";
    scrollChat();
  });
}

function splitCoursePhrases(message) {
  const cleaned = message
    .replace(/我(曾经)?学过/g, "")
    .replace(/还有|以及|并且|和/g, "、")
    .replace(/[，,；;]/g, "、");
  const phrases = cleaned
    .split("、")
    .map((part) => part.replace(/[。.!！?？]/g, "").trim())
    .filter(Boolean);
  return phrases.length ? phrases.slice(0, 5) : [message.trim()];
}

function normalize(value) {
  return value.normalize("NFKC").toLowerCase().replace(/[\s·\-_/]/g, "");
}

function fuzzySimilarity(a, b) {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;
  const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      current[j] = Math.min(
        current[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)
      );
    }
    previous.splice(0, previous.length, ...current);
  }
  return 1 - previous[b.length] / Math.max(a.length, b.length);
}

function matchCourses(query) {
  const q = normalize(query);
  const scored = courseCatalog
    .map((course) => {
      const name = normalize(course.name);
      let score = 0;
      let matchReason = "课程名称相似";
      const takeScore = (nextScore, nextReason) => {
        if (nextScore > score) {
          score = nextScore;
          matchReason = nextReason;
        }
      };
      if (name === q) takeScore(1, "标准化名称完全匹配");
      else if (name.includes(q) || q.includes(name)) takeScore(0.92, "课程名称相似");
      const nameSimilarity = fuzzySimilarity(name, q);
      if (nameSimilarity >= 0.62) takeScore(nameSimilarity * 0.9, "课程名称相似");
      course.keywords.forEach((keyword) => {
        const key = normalize(keyword);
        if (q.includes(key) || key.includes(q)) takeScore(key === q ? 0.96 : 0.83, key === q ? "常用名称相符" : "课程名称相似");
        const keywordSimilarity = fuzzySimilarity(key, q);
        if (keywordSimilarity >= 0.67) takeScore(keywordSimilarity * 0.86, "课程名称相似");
      });
      if (q.includes(normalize(course.field))) takeScore(0.58, "课程领域关键词相符");
      return { course, score, matchReason };
    })
    .filter((item) => item.score >= 0.58)
    .sort((a, b) => b.score - a.score || a.course.name.localeCompare(b.course.name, "zh-CN"))
    .slice(0, 3);
  return scored;
}

function renderResolutionCard(resolution) {
  const card = document.createElement("article");
  card.className = "resolution-card";
  card.dataset.resolutionId = resolution.id;

  const head = document.createElement("div");
  head.className = "resolution-head";
  head.innerHTML = `<span>需要你确认</span><span class="resolution-query">描述：“${escapeHtml(resolution.query)}”</span>`;
  card.append(head);

  if (!resolution.candidates.length) {
    const empty = document.createElement("div");
    empty.className = "candidate-course";
    empty.innerHTML = `
      <span class="candidate-index">?</span>
      <div class="candidate-copy">
        <strong>没有找到可靠匹配</strong>
        <small>可以换一种课程名称重新描述，或跳过这一项。</small>
      </div>
      <div class="candidate-actions">
        <button type="button" class="reject" data-reject-resolution="${resolution.id}">跳过</button>
      </div>
    `;
    card.append(empty);
  } else {
    resolution.candidates.forEach(({ course, score, matchReason }, index) => {
      const candidate = document.createElement("div");
      candidate.className = "candidate-course";
      candidate.innerHTML = `
        <span class="candidate-index">0${index + 1}</span>
        <div class="candidate-copy">
          <strong>${escapeHtml(course.name)}</strong>
          <small>${escapeHtml(course.field)}，${escapeHtml(course.difficulty)}<br />${escapeHtml(matchReason)}，匹配 ${Math.round(score * 100)}%</small>
        </div>
        <div class="candidate-actions">
          <button type="button" class="accept" data-confirm-course="${course.id}" data-resolution="${resolution.id}">就是这门</button>
          ${index === 0 ? `<button type="button" class="reject" data-reject-resolution="${resolution.id}">都不是</button>` : ""}
        </div>
      `;
      card.append(candidate);
    });
  }

  const result = document.createElement("div");
  result.className = "resolution-result";
  card.append(result);
  elements.chatThread.append(card);
}

elements.chatThread.addEventListener("click", (event) => {
  const confirmButton = event.target.closest("[data-confirm-course]");
  if (confirmButton) {
    confirmResolution(confirmButton.dataset.resolution, confirmButton.dataset.confirmCourse);
    return;
  }
  const rejectButton = event.target.closest("[data-reject-resolution]");
  if (rejectButton) rejectResolution(rejectButton.dataset.rejectResolution);
});

function confirmResolution(resolutionId, courseId) {
  const resolution = state.resolutions.get(resolutionId);
  if (!resolution || resolution.status !== "pending") return;
  const matched = resolution.candidates.find(({ course }) => course.id === courseId);
  if (!matched) return;

  resolution.status = "confirmed";
  resolution.selectedCourseId = courseId;
  if (!state.draft.courses.some((course) => course.id === courseId)) {
    state.draft.courses.push(matched.course);
  }

  const card = elements.chatThread.querySelector(`[data-resolution-id="${resolutionId}"]`);
  card.classList.add("is-resolved", "is-confirmed");
  card.querySelector(".resolution-head span").textContent = "已经确认";
  card.querySelector(".resolution-result").textContent = `已加入画像：${matched.course.name}`;
  updateProfileBoard();
  checkResolutionCompletion();
}

function rejectResolution(resolutionId) {
  const resolution = state.resolutions.get(resolutionId);
  if (!resolution || resolution.status !== "pending") return;
  resolution.status = "rejected";
  const card = elements.chatThread.querySelector(`[data-resolution-id="${resolutionId}"]`);
  card.classList.add("is-resolved");
  card.querySelector(".resolution-head").style.background = "var(--coral-soft)";
  card.querySelector(".resolution-head span").textContent = "已经跳过";
  card.querySelector(".resolution-result").textContent = `没有把“${resolution.query}”写入画像。`;
  checkResolutionCompletion();
}

function checkResolutionCompletion() {
  const pending = hasPendingResolutions();
  if (pending) return;
  state.chatStage = "ready";
  elements.assistantStatus.textContent = "画像可以确认";
  showTyping(() => {
    const count = state.draft.courses.length;
    addAssistantMessage(
      count
        ? `课程已经核对完成，共确认 ${count} 门。请检查右侧画像，确认后我会生成下一阶段建议。`
        : "这次没有写入已学课程，将使用热门课程兜底。请检查画像后继续。"
    );
    updateProfileBoard();
  });
}

function hasPendingResolutions() {
  return [...state.resolutions.values()].some((resolution) => resolution.status === "pending");
}

function updateProfileBoard() {
  elements.profileName.textContent = state.draft.displayName || "等待填写";
  elements.profileGender.textContent = state.draft.gender === 1 ? "男" : state.draft.gender === 2 ? "女" : "等待填写";
  elements.courseCount.textContent = `${state.draft.courses.length} 门`;

  const completedParts = Number(Boolean(state.draft.displayName)) + Number(Boolean(state.draft.gender)) + Number(state.chatStage === "ready");
  elements.profileProgress.textContent = `${completedParts} / 3`;
  elements.confirmProfile.disabled = state.chatStage !== "ready" || !state.draft.displayName || !state.draft.gender;
  if (!elements.confirmProfile.disabled) {
    elements.confirmProfile.textContent = state.history.some((entry) => !entry.deletedAt)
      ? "确认变更并重新生成"
      : "确认画像并生成推荐";
  } else {
    elements.confirmProfile.textContent = "完成画像后可生成";
  }

  if (!state.draft.courses.length) {
    elements.confirmedCourses.innerHTML = `
      <div class="empty-course-slot">
        <span>${state.chatStage === "ready" ? "本次使用热门课程兜底" : "课程确认后会落在这里"}</span>
      </div>
    `;
    return;
  }

  elements.confirmedCourses.innerHTML = state.draft.courses
    .map(
      (course) => `
        <div class="confirmed-course">
          <div>
            <strong>${escapeHtml(course.name)}</strong>
            <small>${escapeHtml(course.field)}，${escapeHtml(course.difficulty)}</small>
          </div>
          <button type="button" data-remove-profile-course="${course.id}" aria-label="从画像中移除${escapeHtml(course.name)}">移除</button>
        </div>
      `
    )
    .join("");
}

document.querySelector(".profile-edit-actions").addEventListener("click", (event) => {
  const button = event.target.closest("[data-edit-profile]");
  if (!button) return;
  if (hasPendingResolutions()) {
    showToast("请先确认或跳过当前课程匹配。");
    elements.chatThread.querySelector("[data-resolution-id]:not(.is-resolved)")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const field = button.dataset.editProfile;
  routeTo("assistant");

  if (field === "name") {
    state.chatStage = "edit-name";
    elements.assistantStatus.textContent = "正在修改姓名";
    addAssistantMessage("请告诉我新的姓名或称呼。");
    elements.chatInput.placeholder = "输入新的姓名或称呼…";
  } else if (field === "gender") {
    state.chatStage = "edit-gender";
    elements.assistantStatus.textContent = "正在修改性别";
    addAssistantMessage("请重新选择性别。当前演示模型只支持男、女两个数据组。");
    renderQuickActions([
      { label: "男", value: "edit-male" },
      { label: "女", value: "edit-female" },
    ]);
  } else if (field === "courses") {
    state.chatStage = "courses";
    elements.assistantStatus.textContent = "等待新增课程";
    addAssistantMessage("继续用自然语言描述你学过的课程，我仍会逐项匹配并请你确认。");
    elements.chatInput.placeholder = "描述一门或多门已学课程…";
  }
  setTimeout(() => elements.chatInput.focus(), 120);
  updateProfileBoard();
});

elements.confirmedCourses.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-profile-course]");
  if (!button) return;
  const course = findCourse(button.dataset.removeProfileCourse);
  state.draft.courses = state.draft.courses.filter((item) => item.id !== button.dataset.removeProfileCourse);
  state.chatStage = hasPendingResolutions() ? "resolving" : state.draft.displayName && state.draft.gender ? "ready" : state.chatStage;
  state.viewingHistoryId = null;
  updateProfileBoard();
  addAssistantMessage(`已从画像中移除“${course?.name || "这门课程"}”。历史推荐快照不会受到影响。`);
  showToast("课程已从画像草稿移除。");
});

elements.confirmProfile.addEventListener("click", () => {
  if (elements.confirmProfile.disabled) return;
  elements.confirmProfile.disabled = true;
  elements.confirmProfile.textContent = "正在生成课程板…";
  elements.assistantStatus.textContent = "正在生成推荐";
  addAssistantMessage("画像已确认。正在检查先修条件，并安排下一阶段课程……");

  setTimeout(() => {
    generateRecommendations();
    elements.confirmProfile.textContent = "画像已确认";
    elements.sidebarStatus.textContent = "画像已确认";
    showToast("推荐课程板已经生成。");
    routeTo("recommendations");
  }, 1100);
});

function generateRecommendations() {
  const completedIds = new Set(state.draft.courses.map((course) => course.id));
  const completedNames = new Set(state.draft.courses.map((course) => course.name));
  let source = state.draft.courses.length ? "collaborative" : "popular_fallback";
  let templates = recommendationTemplates.filter((item) => {
    const course = findCourse(item.courseId);
    return course && !completedIds.has(item.courseId) && course.prerequisites.every((name) => completedNames.has(name));
  });

  if (!state.draft.courses.length || !templates.length) {
    source = "popular_fallback";
    templates = courseCatalog
      .filter((course) => !completedIds.has(course.id) && course.prerequisites.length === 0)
      .slice(0, 4)
      .map((course) => ({
        courseId: course.id,
        reason: "这门热门基础课程不要求先修课，可以直接开始学习。",
      }));
  }

  state.recommendations = templates.map((template, index) => ({
    rank: index + 1,
    course: courseCatalog.find((course) => course.id === template.courseId),
    reason: template.reason,
    source,
  }));

  state.viewingHistoryId = null;
  state.lastRecommendationAt = new Date();

  state.history.unshift({
    id: `history_${Date.now()}`,
    createdAt: state.lastRecommendationAt,
    deletedAt: null,
    source,
    items: state.recommendations.map((item) => ({ ...item })),
  });
  saveActiveWorkspace();
  renderRecommendations();
  renderHistory();
}

function renderRecommendations() {
  if (!state.recommendations.length) {
    elements.recommendationMeta.hidden = true;
    elements.recommendationSubtitle.textContent = "完成画像后，这里会显示推荐结果。";
    elements.recommendationBoard.innerHTML = `
      <div class="empty-state">
        <h2>课程板还是空的</h2>
        <p>先与智能体完成画像确认，我们会在这里安排下一阶段课程。</p>
        <button class="button button-primary" type="button" data-route="assistant">开始智能推荐</button>
      </div>
    `;
    return;
  }

  const source = state.recommendations[0].source;
  const historyEntry = state.viewingHistoryId
    ? state.history.find((entry) => entry.id === state.viewingHistoryId)
    : null;
  elements.recommendationMeta.hidden = false;
  elements.recommendationSubtitle.textContent = historyEntry
    ? "这是当时生成的结果快照，不会随当前画像变化。"
    : source === "collaborative"
      ? `根据 ${state.draft.courses.length} 门已学课程和相似学习路径生成。`
      : state.draft.courses.length
        ? "当前没有更多已满足先修条件的候选，先补充不要求先修课的热门基础课程。"
        : "当前没有已学课程，先从不要求先修课的热门课程开始。";

  elements.recommendationMeta.querySelector("span:first-child").innerHTML =
    `<strong>推荐来源</strong> ${source === "collaborative" ? "相似学习路径" : "热门课程兜底"}`;
  const generatedAt = historyEntry?.createdAt || state.lastRecommendationAt;
  elements.recommendationMeta.querySelectorAll("span")[1].innerHTML =
    `<strong>生成时间</strong> ${generatedAt ? formatDateTime(generatedAt) : "刚刚"}`;

  elements.recommendationBoard.innerHTML = state.recommendations
    .map(({ rank, course, reason }) => {
      const favorite = state.favorites.has(course.id);
      return `
        <article class="recommendation-row">
          <div class="recommendation-rank">${String(rank).padStart(2, "0")}</div>
          <div class="recommendation-course">
            <strong>${escapeHtml(course.name)}</strong>
            <small>${escapeHtml(course.field)}，${escapeHtml(course.difficulty)}<br />${course.prerequisites.length ? "先修已满足" : "无先修要求"}</small>
          </div>
          <div class="recommendation-reason">${escapeHtml(reason)}</div>
          <div class="recommendation-actions">
            <button class="action-button ${favorite ? "is-favorite" : ""}" type="button" data-favorite-course="${course.id}" aria-label="${favorite ? "取消收藏" : "收藏"}${escapeHtml(course.name)}">
              ${favorite ? "已收藏" : "收藏"}
            </button>
            <button class="action-button" type="button" data-course-details="${course.id}" aria-label="查看${escapeHtml(course.name)}详情">
              详情
            </button>
          </div>
        </article>
      `;
    })
    .join("");
}

document.addEventListener("click", (event) => {
  const favoriteButton = event.target.closest("[data-favorite-course]");
  if (favoriteButton) {
    toggleFavorite(favoriteButton.dataset.favoriteCourse);
    return;
  }
  const detailButton = event.target.closest("[data-course-details]");
  if (detailButton) {
    openDetails(detailButton.dataset.courseDetails, detailButton);
    return;
  }
  const removeFavorite = event.target.closest("[data-remove-favorite]");
  if (removeFavorite) {
    toggleFavorite(removeFavorite.dataset.removeFavorite);
    return;
  }
  const openHistory = event.target.closest("[data-open-history]");
  if (openHistory) {
    openHistorySnapshot(openHistory.dataset.openHistory);
    return;
  }
  const deleteHistory = event.target.closest("[data-delete-history]");
  if (deleteHistory) deleteHistorySnapshot(deleteHistory.dataset.deleteHistory);
});

function toggleFavorite(courseId) {
  const course = courseCatalog.find((item) => item.id === courseId);
  if (!course) return;
  if (state.favorites.has(courseId)) {
    state.favorites.delete(courseId);
    showToast(`已取消收藏“${course.name}”。`);
  } else {
    state.favorites.add(courseId);
    showToast(`已收藏“${course.name}”。`);
  }
  renderRecommendations();
  renderFavorites();
  if (elements.drawer.classList.contains("is-open")) openDetails(courseId, state.lastDrawerTrigger, false);
}

function renderHistory() {
  const visibleHistory = state.history.filter((entry) => !entry.deletedAt);
  if (!visibleHistory.length) {
    elements.historyList.innerHTML = `
      <div class="empty-state compact">
        <h2>还没有推荐记录</h2>
        <p>完成一次推荐后，结果快照会出现在这里。</p>
      </div>
    `;
    return;
  }

  elements.historyList.innerHTML = visibleHistory
    .map((entry) => {
      const time = new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(entry.createdAt);
      return `
        <article class="history-item">
          <span class="history-date">${time}</span>
          <div>
            <strong>${entry.source === "collaborative" ? "个性化课程推荐" : "热门入门课程推荐"}</strong>
            <small>${entry.items.length} 门课程，模拟结果快照</small>
          </div>
          <div class="history-course-preview" aria-label="课程预览">
            ${entry.items.slice(0, 4).map((item, index) => `<span class="preview-tab">0${index + 1}</span>`).join("")}
          </div>
          <div class="history-actions">
            <button class="button button-quiet" type="button" data-open-history="${entry.id}">查看快照</button>
            <button class="history-delete" type="button" data-delete-history="${entry.id}">删除</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function openHistorySnapshot(historyId) {
  const entry = state.history.find((item) => item.id === historyId && !item.deletedAt);
  if (!entry) return;
  state.viewingHistoryId = historyId;
  state.recommendations = entry.items.map((item) => ({ ...item }));
  routeTo("recommendations");
}

function deleteHistorySnapshot(historyId) {
  const entry = state.history.find((item) => item.id === historyId && !item.deletedAt);
  if (!entry) return;
  entry.deletedAt = new Date();
  if (state.viewingHistoryId === historyId) state.viewingHistoryId = null;
  saveActiveWorkspace();
  renderHistory();
  showToast("推荐记录已删除（演示中按软删除处理）。");
}

function renderFavorites() {
  const courses = [...state.favorites]
    .map((id) => courseCatalog.find((course) => course.id === id))
    .filter(Boolean);

  if (!courses.length) {
    elements.favoritesList.innerHTML = `
      <div class="empty-state compact">
        <h2>暂时没有收藏</h2>
        <p>在推荐结果或课程详情中收藏感兴趣的课程。</p>
      </div>
    `;
    return;
  }

  elements.favoritesList.innerHTML = courses
    .map(
      (course) => `
        <article class="favorite-item">
          <span class="favorite-mark">收藏</span>
          <div class="favorite-copy">
            <strong>${escapeHtml(course.name)}</strong>
            <small>${escapeHtml(course.field)}，${escapeHtml(course.difficulty)}<br />刚刚收藏</small>
          </div>
          <div class="recommendation-actions">
            <button class="action-button" type="button" data-course-details="${course.id}" aria-label="查看${escapeHtml(course.name)}详情">
              详情
            </button>
            <button class="action-button is-favorite" type="button" data-remove-favorite="${course.id}" aria-label="取消收藏${escapeHtml(course.name)}">
              取消
            </button>
          </div>
        </article>
      `
    )
    .join("");
}

function openDetails(courseId, trigger, rememberTrigger = true) {
  const course = courseCatalog.find((item) => item.id === courseId);
  if (!course) return;
  if (rememberTrigger) state.lastDrawerTrigger = trigger;
  const favorite = state.favorites.has(courseId);
  const prerequisiteRows = course.prerequisites.length
    ? course.prerequisites
        .map(
          (name) => `
            <div class="prerequisite-row">
              <span>${escapeHtml(name)}</span>
              <span class="prerequisite-state ${state.draft.courses.some((item) => item.name === name) ? "" : "is-missing"}">${state.draft.courses.some((item) => item.name === name) ? "已满足" : "尚未完成"}</span>
            </div>
          `
        )
        .join("")
    : `<div class="prerequisite-row"><span>无先修要求</span><span class="prerequisite-state">可直接学习</span></div>`;

  elements.drawerContent.innerHTML = `
    <span class="drawer-course-code">${escapeHtml(course.id)}</span>
    <h2 class="drawer-title" id="detail-title">${escapeHtml(course.name)}</h2>
    <p class="drawer-subtitle">${escapeHtml(course.field)}，${escapeHtml(course.difficulty)}${course.advanced ? "，高阶课程" : ""}</p>
    <div class="drawer-section">
      <h3>高阶标记</h3>
      <p>${course.advanced ? "是。演示课程库已将这门课程标记为高阶课程。" : "否。演示课程库未将这门课程标记为高阶课程。"}</p>
    </div>
    <div class="drawer-section">
      <h3>先修课程</h3>
      ${prerequisiteRows}
    </div>
    <button class="button ${favorite ? "button-quiet" : "button-accent"} button-block" type="button" data-favorite-course="${course.id}">
      ${favorite ? "取消收藏" : "收藏这门课程"}
    </button>
  `;
  elements.drawer.classList.add("is-open");
  elements.drawer.setAttribute("aria-hidden", "false");
  elements.drawerScrim.hidden = false;
  elements.main.inert = true;
  elements.sidebar.inert = true;
  elements.mobileBar.inert = true;
  elements.closeDrawer.focus();
}

function closeDetails() {
  if (!elements.drawer.classList.contains("is-open")) return;
  elements.drawer.classList.remove("is-open");
  elements.drawer.setAttribute("aria-hidden", "true");
  elements.drawerScrim.hidden = true;
  elements.main.inert = false;
  elements.sidebar.inert = false;
  elements.mobileBar.inert = false;
  state.lastDrawerTrigger?.focus?.();
}

elements.closeDrawer.addEventListener("click", closeDetails);
elements.drawerScrim.addEventListener("click", closeDetails);
document.addEventListener("keydown", (event) => {
  if (event.key === "Tab" && elements.drawer.classList.contains("is-open")) {
    const focusable = [...elements.drawer.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  if (event.key === "Escape") {
    closeDetails();
    elements.sidebar.classList.remove("is-open");
    elements.menuButton.setAttribute("aria-expanded", "false");
  }
});

function addAssistantMessage(text) {
  addMessage(text, "assistant");
}

function addUserMessage(text) {
  addMessage(text, "user");
}

function addMessage(text, role) {
  const message = document.createElement("div");
  message.className = `message message--${role}`;
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = role === "assistant" ? "序" : (state.draft.displayName || state.account?.username || "我").slice(0, 1);
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  message.append(avatar, bubble);
  elements.chatThread.append(message);
  scrollChat();
}

function showTyping(callback) {
  const typing = document.createElement("div");
  typing.className = "message message--assistant";
  typing.innerHTML = `
    <span class="message-avatar">序</span>
    <div class="message-bubble typing-indicator"><span></span><span></span><span></span></div>
  `;
  elements.chatThread.append(typing);
  scrollChat();
  setTimeout(() => {
    typing.remove();
    callback();
  }, 520);
}

function scrollChat() {
  requestAnimationFrame(() => {
    elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
  });
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  elements.toastRegion.append(toast);
  setTimeout(() => toast.remove(), 3200);
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

updateProfileBoard();
renderRecommendations();
renderHistory();
renderFavorites();
