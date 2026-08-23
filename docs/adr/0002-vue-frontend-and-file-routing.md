# ADR-0002：Vue 前端、组件库与自动文件路由

## 状态

已提议，待项目启动评审后接受。

## 背景

Web 端需要覆盖文件上传、多步任务创建、运行控制、大量行核对、证据查看、批量确认和受控导出。页面数量会随功能增长，且多数页面有角色、布局和面包屑要求。

团队希望用少量人力快速交付，因此前端应：

- 优先使用稳定、完整的管理端组件；
- 避免同时维护页面文件和手写路由表；
- 从 OpenAPI 获得 API 类型，避免重复定义 DTO；
- 清楚区分服务端业务状态和客户端 UI 状态；
- 不让核心核对流程依赖 beta 或实验 API。

## 决策

### 1. 使用 Vue 3、TypeScript 和 Vite

- 所有新组件使用 Vue Single-File Component、Composition API 和 `<script setup lang="ts">`。
- 业务逻辑从页面组件抽离为 composables 和纯 TypeScript 函数。
- 页面组件不直接拼接 API URL，只使用生成 Client 与应用层 composables。

### 2. 使用 Element Plus 作为主组件库

Element Plus 承担：

- `ElForm`、`ElInput`、`ElSelect` 等配置与审核表单；
- `ElUpload` 的交互外观，文件传输由自定义 `http-request` 连接分片/流式上传 API；
- `ElTable` + 服务端分页的任务、文件、模板和导出列表；
- `ElSteps`、`ElProgress`、`ElTimeline`、`ElTabs`、`ElDrawer` 和 `ElDialog` 等任务交互；
- `ElMessage`、`ElNotification` 和 `ElMessageBox` 的反馈与危险操作确认。

核对工作台的左侧长队列使用 TanStack Virtual 实现虚拟滚动，右侧证据区使用 Element Plus 基础组件组合。不使用仍为 beta 的 Element Plus Virtualized Table 承载核心核对流程。

### 3. 使用自动文件路由

使用 Vue Router 5 内置的文件路由插件。该能力由 `unplugin-vue-router` 合并而来，新项目不安装已归档的独立包：

- `src/pages` 中的 Vue 文件自动映射为路由；
- `[taskId].vue` 或 `[taskId]/index.vue` 表示动态路由参数；
- `[...path].vue` 承载未匹配路由；
- 插件生成路由名称、路由参数和 TypeScript 类型；
- 不再维护一份与页面目录重复的手写路由数组。

Vite 中的插件顺序固定为 `VueRouter()` 在 `Vue()` 之前，并将生成类型写入 `src/routes.d.ts`。运行时从 `vue-router/auto-routes` 导入 `routes` 传给 `createRouter`。

路由表是自动生成的，但导航规则必须显式。页面使用 `definePage` 声明扩展路由元数据：

```ts
definePage({
  meta: {
    title: '核对工作台',
    layout: 'app',
    requiresAuth: true,
    roles: ['REVIEWER', 'ADMIN'],
  },
})
```

`route-meta.d.ts` 对 `layout`、`requiresAuth`、`roles`、`title` 和面包屑元数据进行类型扩展。路由守卫处理登录恢复和页面级权限，但不代替后端授权。

### 4. 布局不再增加第二个自动插件

使用 `layout` 路由元数据在根容器中选择：

- `AppLayout`：业务导航、会话和系统状态；
- `PublicLayout`：登录、会话过期和公共错误页。

首版不引入独立的布局路由插件，避免布局与文件路由产生两套隐式规则。

### 5. 分离服务端状态和客户端状态

TanStack Query for Vue 管理：

- 任务、文件、模板、审核、导出和系统状态；
- 分页、请求取消、重试、缓存和 mutation 后的精确失效；
- SSE 事件到达后的局部缓存修补或失效。

Pinia 只管理：

- 当前会话和个人偏好；
- 核对三栏宽度、快捷键和个人视图；
- 需跨页保留但不属于服务端业务真相的 UI 状态。

未提交审核编辑默认留在页面内，必要时写入会话级草稿 store；提交成功后立即清除。

### 6. 页面取数不使用实验性 Data Loaders

Vue Router 5 首版只用稳定的文件路由、类型路由和页面元数据。页面数据使用 TanStack Query for Vue 获取，不使用实验性 Data Loaders。

## 主要页面与文件映射

| URL | 页面文件 |
| --- | --- |
| `/` | `pages/index.vue` |
| `/login` | `pages/login.vue` |
| `/tasks` | `pages/tasks/index.vue` |
| `/tasks/new` | `pages/tasks/new.vue` |
| `/tasks/:taskId` | `pages/tasks/[taskId]/index.vue` |
| `/tasks/:taskId/review` | `pages/tasks/[taskId]/review.vue` |
| `/tasks/:taskId/exports` | `pages/tasks/[taskId]/exports.vue` |
| `/files` | `pages/files/index.vue` |
| `/files/:fileId` | `pages/files/[fileId].vue` |
| `/templates` | `pages/templates/index.vue` |
| `/templates/:templateId` | `pages/templates/[templateId].vue` |
| `/admin/users` | `pages/admin/users.vue` |
| `/admin/system` | `pages/admin/system.vue` |
| `/admin/audit` | `pages/admin/audit.vue` |
| 未匹配 | `pages/[...path].vue` |

## 被否决或推迟的方案

### React + Ant Design

否决。本项目已明确使用 Vue 3，不同时保留第二套前端生态。

### Nuxt

首版否决。系统是 NAS 内网登录后的业务 SPA，不需要 SSR、SEO 或服务端页面渲染。Vite + Vue Router 的运行时和部署边界更小。

### 手工维护路由表

否决。页面增删需要同步修改两处，且动态参数和路由名称容易失去类型约束。

### Element Plus Table V2 作为核心审核表格

推迟。官方仍将 Virtualized Table 标记为 beta，且审核工作台的交互更接近“队列 + 行详情 + 证据”，而不是一个巨型电子表格。

### 所有状态放入 Pinia

否决。它会复制后端业务真相，导致任务控制、SSE 更新和审核版本冲突更难处理。

## 后果

### 正面

- 新增页面即新增路由，不产生重复配置；
- 路由参数、名称和元数据在编译期获得约束；
- Element Plus 减少通用管理端组件的自研成本；
- 服务端与客户端状态边界明确；
- 核对工作台可按业务交互单独优化。

### 代价

- 需要维护路由元数据的 TypeScript 扩展；
- 自动路由约定必须进入代码评审和脚手架模板；
- 核对长队列使用 TanStack Virtual，需自行实现更精确的焦点、选中和键盘导航；
- Element Plus 组件组合仍需要一层项目设计令牌和业务组件封装。

## 验证方式

1. 新增、删除和重命名页面时，生成路由与 TypeScript 类型自动更新。
2. 向不存在的命名路由或错误参数导航时，TypeScript 检查失败。
3. 未登录、无角色和会话过期的导航行为通过单元和端到端测试。
4. 核对队列在 50,000 条读模型索引下保持流畅滚动、键盘导航和当前项定位。
5. 前端不存在手工维护的完整业务路由数组。
6. 常规页面的数据只存在 TanStack Query 缓存中，Pinia 不保存任务实体副本。

## 参考资料

- Vue 3：https://vuejs.org/guide/introduction.html
- Vue Router：https://router.vuejs.org/
- Vue Router 5 文件路由：https://router.vuejs.org/file-based-routing/
- 文件路由约定：https://router.vuejs.org/file-based-routing/file-based-routing
- Element Plus 组件：https://element-plus.org/en-US/component/overview.html
- Element Plus Virtualized Table：https://element-plus.org/en-US/component/table-v2.html
- TanStack Query for Vue：https://tanstack.com/query/latest/docs/framework/vue/overview
- Pinia：https://pinia.vuejs.org/
