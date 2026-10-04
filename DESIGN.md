---
name: Course Compass Demo
description: A tactile course-planning desk for confirming learning history and arranging the next step.
colors:
  ink: "#17262C"
  paper: "#F5F2EA"
  paper-raised: "#FFFDF8"
  rule: "#D8D2C4"
  schedule-yellow: "#F2C94C"
  course-mint: "#83C7A5"
  action-coral: "#E76F51"
  action-coral-ink: "#A8402A"
  quiet-blue: "#8EB6C7"
  muted-ink: "#657176"
  placeholder-ink: "#7C8588"
  success-ink: "#397357"
typography:
  display:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4.8rem)"
    fontWeight: 800
    lineHeight: 0.98
    letterSpacing: "-0.035em"
  body:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "0.78rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.04em"
  micro:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "0.625rem"
    fontWeight: 700
    lineHeight: 1.2
  caption:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "0.7rem"
    fontWeight: 600
    lineHeight: 1.4
  small:
    fontFamily: "ui-sans-serif, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "0.825rem"
    fontWeight: 500
    lineHeight: 1.5
rounded:
  micro: "2px"
  xs: "4px"
  message: "5px"
  sm: "6px"
  compact: "8px"
  mark: "9px"
  control: "10px"
  course-tight: "11px"
  card: "12px"
  course: "14px"
  panel: "16px"
  board: "20px"
  stage: "24px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "16px"
  lg: "24px"
  xl: "36px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper-raised}"
    rounded: "{rounded.control}"
    padding: "12px 18px"
  button-accent:
    backgroundColor: "{colors.schedule-yellow}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "12px 18px"
  input:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "13px 14px"
---

# Design System: Course Compass Demo

## Overview

**Creative North Star: "课程编排台"**

界面借用高校排课板、课程磁条和教务记录纸的结构感，但以现代、轻盈的操作界面呈现。用户不是在填写一张冗长表单，而是在与智能体共同把自己的学习经历逐项放到一张可见的课程板上，再从这张板出发安排下一阶段。

系统保持较高的信息密度，同时避免传统后台的蓝白模板感。色彩用于区分课程状态和操作意义，不用于装饰。关键确认动作有明确的物理感，正文区域始终保持平静、清晰。

**Key Characteristics:**

- 横向课程条目比通用卡片更重要；
- 暖纸色工作面配深墨色结构；
- 黄色表示正在安排，绿色表示已经确认，珊瑚色只用于关键提醒；
- 动效只服务于课程落位和推荐展开。

## Colors

主工作面采用暖纸色，文字和导航使用深墨色；三种课程色承担明确状态角色。

### Primary

- **Deep Registrar Ink** (#17262C)：导航、正文、主要按钮和高对比结构。
- **Schedule Yellow** (#F2C94C)：当前步骤、主操作和等待安排的课程。

### Secondary

- **Confirmed Course Mint** (#83C7A5)：已经确认的课程及成功状态。
- **Action Coral** (#E76F51)：拒绝、错误和需要用户注意的阻塞状态。
- **Deep Action Coral** (#A8402A)：珊瑚色背景上的错误文字和收藏图标。
- **Quiet Catalog Blue** (#8EB6C7)：推荐课程和信息型状态。

### Neutral

- **Desk Paper** (#F5F2EA)：页面背景。
- **Raised Paper** (#FFFDF8)：输入区和需要聚焦的工作面。
- **Ledger Rule** (#D8D2C4)：分隔线和低对比边界。
- **Muted Ink** (#657176)：次要文字。
- **Placeholder Ink** (#7C8588)：输入占位文字。
- **Success Ink** (#397357)：浅绿色表面上的成功文字。

**The Status Color Rule.** 黄色、绿色、蓝色和珊瑚色必须保持固定语义，不得作为随机装饰色混用。

## Typography

**Display Font:** 系统无衬线字体，优先 PingFang SC。

**Body Font:** 系统无衬线字体，优先 PingFang SC。

**Character:** 字体像清晰的课程表和现代教务工具，紧凑但不机械。大标题使用高字重和紧字距，正文保持舒展。

### Hierarchy

- **Display**：800，clamp(2.4rem, 5vw, 6rem)，用于首页主张。
- **Headline**：750，1.6–2rem，用于页面标题。
- **Title**：700，1.05–1.2rem，用于课程名和关键状态。
- **Body**：400，1rem，行高 1.65，正文不超过 72ch。
- **Label**：700，0.78rem，用于状态、字段名和课程元数据。
- **Small**：500，0.825rem，用于辅助说明和按钮文字。
- **Caption**：600，0.7rem，用于课程元数据和提示。
- **Micro**：700，0.625rem，只用于品牌副标和极短标识。

## Layout

桌面端使用“窄导航 + 主工作面 + 画像侧栏”的三段式布局。主工作面允许聊天和课程确认占据最大宽度，侧栏持续显示画像进度。首页用不对称两栏，右侧直接演示课程落位机制。

空间基于 6、10、16、24、36 像素节奏。移动端在 860px 以下折叠导航，把画像侧栏变为可展开区域；所有主要操作保持单列和自然阅读顺序。

## Elevation & Depth

默认界面以色块和分隔线组织层级，不为每个容器添加阴影。只有正在拖入或新确认的课程条目、浮出的移动导航和详情抽屉获得结构性阴影。

- **Course Lift**：0 10px 24px rgba(23, 38, 44, 0.14)，用于课程落位瞬间和悬停。
- **Panel Lift**：0 18px 48px rgba(23, 38, 44, 0.18)，用于详情抽屉。

**The Flat Desk Rule.** 工作面在静止状态保持平整；阴影表示真实的前后层级或状态变化。

## Shapes

课程条目使用 14px 圆角和非对称内部布局，像可移动的排课磁条。普通控件使用 10px 圆角，主面板最多 16px。小型状态标签可以使用胶囊形，但主要按钮和容器不得使用胶囊形。

## Components

### Buttons

- 主按钮使用深墨色底和暖白文字。
- 当前步骤按钮可使用排课黄底和深墨文字。
- 悬停时产生 2px 位移和结构性阴影；键盘焦点使用 3px 黄色外环。
- 危险或拒绝操作使用珊瑚色文字，不使用大面积红色底。

### Course Strips

- 左侧状态色块、中央课程信息、右侧操作构成固定结构。
- 已确认课程使用绿色；待确认使用黄色；推荐课程使用蓝色。
- 新确认课程通过一次短促的落位动画进入画像区。

### Inputs

- 使用抬升纸色背景和 1px 账册分隔线。
- 聚焦时边界切换为深墨色，并出现黄色焦点环。
- 错误信息直接说明问题和恢复操作。

### Navigation

- 桌面导航为深墨色竖栏，活动项使用黄色实心标记。
- 移动端改为顶部栏和可展开菜单。
- 图标均为内联 SVG，不依赖图标字体。

## Do's and Don'ts

### Do:

- **Do** 用课程条目和状态变化直接表现产品机制。
- **Do** 让画像进度在智能体页面始终可见。
- **Do** 使用真实的中文课程名称和明确的模拟数据标记。
- **Do** 为键盘焦点、加载、空数据和错误提供完整状态。

### Don't:

- **Don't** 用同尺寸图标卡片铺满页面。
- **Don't** 使用渐变文字、装饰性玻璃效果或霓虹光晕。
- **Don't** 把性别公平统计暴露给普通用户。
- **Don't** 用虚构客户、评价、准确率或商业指标填充 Demo。

## Admin Console Extension

管理端延续“课程编排台”，但进入更高密度的“教务观测台”模式。页面以连续工作纸、账册表格和检查面板组织信息，不使用等尺寸指标卡铺满页面。

- **Admin surfaces**：`#EDF0EB` 为管理工作面，`#FAFCF8` 为工作纸，`#E4E9E3` 为表头和安静分区。
- **Admin navigation**：`#101B17` 为主导航，`#1B2B25` 为次级深色表面；活动项仍由 Schedule Yellow 标识。
- **Admin status ramp**：管理端可以使用主色的深浅派生值呈现状态文字，但黄色、薄荷绿、蓝色和珊瑚色的语义保持不变。
- **Admin density**：管理表格使用 0.625–0.825rem 的 caption、label 和 small 字号；页面标题使用 1.8–3rem，登录主张可使用既有 Display 上限。
- **Admin shapes**：密集状态标记可使用 6–9px 圆角；工作纸保持直角或最多 12px，检查面板使用真实阴影表达前后层级。
- **Admin motion**：导航活动标记使用 transform，检查面板从右侧进入；数据和表格默认静止。
