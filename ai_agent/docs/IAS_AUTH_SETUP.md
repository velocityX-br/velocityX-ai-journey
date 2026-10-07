# IAS 登录集成指南（OIDC / BFF 模式）

将 Web UI（FastAPI + React SPA）接入 **SAP Cloud Identity Services (IAS)** 做登录认证。
采用 **BFF（Backend-For-Frontend）** 模式：所有 OIDC 流程在服务端完成，浏览器只持有一个
签名的 HttpOnly session cookie，SPA 永远看不到 `client_id` / `client_secret`。

> 本文档同时是一份「实战复盘」——记录了本次在 **Trial** 环境完成集成时踩过的坑，
> 并在最后给出 **迁移到非 Trial 环境 / 其他 URL** 的清单，方便后续复用。

---

## 目录

1. [架构总览](#1-架构总览)
2. [前置条件](#2-前置条件)
3. [IAS 侧配置](#3-ias-侧配置)
4. [后端代码改动](#4-后端代码改动)
5. [前端代码改动](#5-前端代码改动)
6. [环境变量 `.env`](#6-环境变量-env)
7. [构建与启动](#7-构建与启动)
8. [验证登录流程](#8-验证登录流程)
9. [踩坑记录（本次会话复盘）](#9-踩坑记录本次会话复盘)
10. [迁移到非 Trial 环境 / 其他 URL](#10-迁移到非-trial-环境--其他-url)
11. [附录：完整成功日志](#11-附录完整成功日志)

---

## 1. 架构总览

```
浏览器 (SPA)                后端 (FastAPI)                    IAS 租户
   │                            │                               │
   │  GET /  (加载 SPA)         │                               │
   │◀───────────────────────────                               │
   │  GET /auth/me  ──────────▶ │ 无 session → 401              │
   │◀── 401 ────────────────────                               │
   │                            │                               │
   │  redirect /auth/login ───▶ │ 生成 state+nonce 存 session   │
   │                            │ ──302──▶ IAS /oauth2/authorize │
   │◀═══════════════ 302 跳转到 IAS 登录页 ══════════════════════▶│
   │                                                            │
   │  用户在 IAS 输入账号密码 ────────────────────────────────▶ │
   │                            │                               │
   │◀══ IAS 302 回 /auth/callback?code=…&state=… ═══════════════│
   │  GET /auth/callback ─────▶ │ 校验 state → 换 token         │
   │                            │ POST IAS /oauth2/token ─────▶ │
   │                            │ 存 user 进 session            │
   │◀── 307 redirect / ─────────                               │
   │  GET /auth/me ───────────▶ │ 有 session → 200 {user}       │
   │  GET /api/configs ───────▶ │ 200                           │
   │  POST /api/chat (带 cookie)│ 200 (SSE)                     │
```

**关键点**：
- SPA 只做两件事：进页面查 `/auth/me`，401 就跳 `/auth/login`；所有 `fetch` 带
  `credentials: "include"` 让 cookie 随行。
- 后端 `auth.py` 是 **opt-in**：5 个环境变量不全时 `install_auth()` 直接 no-op，
  测试和本地无认证运行完全不受影响。

---

## 2. 前置条件

| 前置项 | 说明 |
|--------|------|
| IAS 租户 | 一个可用的 IAS 租户，URL 形如 `https://<tenant>.accounts.ondemand.com`（Trial 为 `*.trial-accounts.ondemand.com`） |
| IAS 管理员权限 | 能进入 `https://<tenant>.accounts.ondemand.com/admin/` 创建 OIDC 应用 |
| Python 依赖 | `authlib`、`itsdangerous`（本项目已通过 `uv add authlib itsdangerous` 加入） |
| 服务端口固定 | 后端监听端口必须与 `IAS_REDIRECT_URI` 的端口一致（本次为 **3010**） |

> **注意**：BTP 里「创建 Cloud Identity Services 实例」≠「创建 IAS 租户」。
> 若走 BTP service broker，会要求先在 subaccount 与 IAS 之间用 OIDC 建立 Trust
> （报错 `OIDC Trust missing`）。本文档走的是 **直接在 IAS 管理台注册 OIDC 应用** 的
> 路径（Path A），不依赖 BTP service broker，更适合本地测试。

---

## 3. IAS 侧配置

在 IAS 管理台创建一个 OIDC 应用并拿到凭据：

1. 打开 `https://<tenant>.accounts.ondemand.com/admin/`
2. **Applications & Resources → Applications → Create**（选 OpenID Connect 类型）
3. 进入应用的 **OpenID Connect Configuration**：
   - **Redirect URIs**：填 **精确** 的回调地址（见下方匹配规则）
     ```
     http://localhost:3010/auth/callback
     ```
   - 记录 **Client ID**
   - 生成并记录 **Client Secret**
4. **Save**

### Redirect URI 精确匹配规则（IAS 逐字符比对）

| 部分 | 要求 |
|------|------|
| scheme | `http`（localhost 可用 http）/ 生产用 `https` |
| host | `localhost` 与 `127.0.0.1` **是两个不同字符串**，必须与代码发送的一致 |
| port | 与后端监听端口一致（`3010`） |
| path | `/auth/callback`，**无尾部斜杠** |

> 只要有一处不匹配，IAS 会报：
> *"The redirect_uri sent by the client application must match the configuration…"*

---

## 4. 后端代码改动

### 4.1 新增 `src/agent_chat/auth.py`

独立的认证模块，避免污染 `api.py` 核心逻辑。核心接口 `install_auth(app) -> bool`：

- `_configured()`：5 个环境变量全部非空才返回 `True`，否则整个模块 no-op。
- `/auth/login`：发起 OIDC authorization-code 流程，302 跳 IAS。
- `/auth/callback`：用 `?code=` 换 token，存 `{sub, email, name}` 进 session。
  **失败时重定向到 `/?auth_error=…`**（不是返回 JSON 死页，否则会触发 SPA 登录循环）。
- `/auth/me`：SPA 轮询此接口判断登录态（未登录 401）。
- `/auth/logout`：清本地 session（不做 IAS 单点登出）。

```python
def install_auth(app: FastAPI) -> bool:
    if not _configured():
        return False
    app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"])
    _register()                    # 从 IAS discovery 文档注册 provider
    app.include_router(router)     # 挂上 /auth/* 路由
    return True
```

### 4.2 `src/agent_chat/api.py` 挂载认证

在 `create_app()` 里、`app = FastAPI(...)` 之后：

```python
app = FastAPI(title="agent-chat web UI", lifespan=lifespan)

# ⚠️ 关键：先加载 .env，再 install_auth。
# create_app 在模块 import 时就运行，早于任何 load_config() 触发的 dotenv 加载，
# 否则 install_auth 读不到 IAS_* 变量 → 静默 no-op。
from agent_chat.config import _load_dotenv_once
_load_dotenv_once()

from agent_chat.auth import install_auth
auth_enabled = install_auth(app)
```

并在 `/api/chat` 前加会话门禁：

```python
if auth_enabled and not request.session.get("user"):
    return JSONResponse({"detail": "Not authenticated"}, status_code=401)
```

---

## 5. 前端代码改动

### 5.1 `web/src/App.tsx` —— 登录门禁（含防循环保护）

进页面先查 `/auth/me`，401 就跳 `/auth/login`。**关键防坑**：用
`sessionStorage["auth-redirecting"]` 做一次性守卫，保证每个标签页最多只跳一次
`/auth/login`——否则重复调用会覆盖 session 里的 OIDC `state`，导致回调
`mismatching_state`（CSRF）失败。

```tsx
void fetch("/auth/me", { credentials: "include" })
  .then((response) => {
    if (response.status === 401) {
      if (sessionStorage.getItem("auth-redirecting") === "1") return; // 防循环
      sessionStorage.setItem("auth-redirecting", "1");
      window.location.href = "/auth/login";
      return;
    }
    sessionStorage.removeItem("auth-redirecting"); // 已登录，清守卫
    void loadConfigs();
  })
  .catch(() => setConfigs([]));
```

`/api/configs` 的 fetch 也加 `credentials: "include"`。

### 5.2 `web/src/runtime.ts` —— 聊天请求带 cookie

```tsx
const response = await fetch("/api/chat", {
  method: "POST",
  credentials: "include",   // ← 让 HttpOnly session cookie 随行
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ ... }),
  signal: abortSignal,
});
```

---

## 6. 环境变量 `.env`

放在项目根 `ai_agent/.env`（由 `config.py::_load_dotenv_once()` 自动加载，
`override=False` 即已存在的环境变量优先）：

```dotenv
IAS_ISSUER=https://<tenant>.accounts.ondemand.com
IAS_CLIENT_ID=<从 IAS 应用复制>
IAS_CLIENT_SECRET=<从 IAS 应用复制>
IAS_REDIRECT_URI=http://localhost:3010/auth/callback
SESSION_SECRET=<随机长字符串，用于签名 session cookie>
```

生成 `SESSION_SECRET`：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> **5 个变量缺一不可**——只要一个为空，`install_auth()` 返回 `False`，认证整体不生效
> （`/auth/*` 全部 404，`/api/chat` 不设门禁）。

---

## 7. 构建与启动

```bash
# 1. 构建前端（产物写入 src/agent_chat/static/）
cd ai_agent/web
npm install          # 首次
npm run build        # tsc --noEmit && vite build

# 2. 启动后端（端口必须与 IAS_REDIRECT_URI 端口一致）
cd ai_agent
.venv/bin/agent serve --host 127.0.0.1 --port 3010
```

启动日志里若看到 `authlib` 相关加载且无报错，说明认证已挂上。

---

## 8. 验证登录流程

### 8.1 命令行冒烟测试（不需要浏览器）

```bash
# /auth/me 未登录 → 401
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3010/auth/me          # 期望 401

# /api/chat 无 session → 401（门禁生效）
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:3010/api/chat \
  -H "Content-Type: application/json" -d '{"query":"hi"}'                        # 期望 401

# /auth/login → 302 且 Location 指向 IAS authorize
curl -s -o /dev/null -w "%{http_code}\n%{redirect_url}\n" \
  http://127.0.0.1:3010/auth/login                                              # 期望 302 → IAS

# IAS discovery 可达性
curl -s -o /dev/null -w "%{http_code}\n" \
  https://<tenant>.accounts.ondemand.com/.well-known/openid-configuration       # 期望 200
```

> `/auth/login` 若返回 **404** → 说明 `install_auth` no-op 了，检查 `.env` 5 个变量
> 是否齐全、`_load_dotenv_once()` 是否在 `install_auth` 之前调用。

### 8.2 浏览器完整登录

用 **无痕窗口** 打开 `http://localhost:3010`：
1. SPA 加载 → `/auth/me` 401 → 自动跳 `/auth/login`
2. 302 到 IAS 登录页 → 输入 IAS 账号密码
3. IAS 回调 `/auth/callback?code=…` → 后端换 token、存 session → 307 回 `/`
4. `/auth/me` 200 → 聊天可用

---

## 9. 踩坑记录（本次会话复盘）

按发生顺序，每个坑都给出「现象 → 根因 → 修复」。

### 坑 1：`redirect_uri` 不匹配
- **现象**：IAS 报 *"The redirect_uri sent by the client application must match the configuration…"*
- **根因**：`.env` 里 `IAS_REDIRECT_URI=http://localhost:3010/auth/callback`，但 IAS 应用里没登记这个精确串（且服务一度跑在 8010，端口对不上）。
- **修复**：① 后端改到 **3010** 与 redirect_uri 端口一致；② 在 IAS 应用 Redirect URIs 里 **逐字符** 加上 `http://localhost:3010/auth/callback`。

### 坑 2：`install_auth` 静默 no-op（`/auth/*` 全 404）
- **现象**：`/auth/me`、`/auth/login` 返回 404，`/api/chat` 不设门禁（返回 200）。
- **根因**：`_load_dotenv_once()` 只在 `load_config()` 时触发，而 `install_auth()` 在
  `api:app` 模块 import 时就运行——**早于** dotenv 加载，读到的 `IAS_*` 全为空，
  `_configured()` 返回 `False`。
- **修复**：在 `create_app()` 里 `install_auth(app)` **之前** 显式调用 `_load_dotenv_once()`。

### 坑 3：`mismatching_state`（CSRF Warning）
- **现象**：IAS 登录成功回调后报
  *"Authentication failed: mismatching_state: CSRF Warning! State not equal in request and response."*
- **根因**：日志显示浏览器 **多次** 命中 `/auth/login`，每次生成新 `state` 覆盖 session；
  等真正的回调带着 *旧* `state` 回来时，已与 session 里 *最后存的* `state` 不符。
  循环来源：回调若失败会停在 JSON 死页 / SPA 反复 `/auth/me` 401 → 再跳 `/auth/login`。
- **修复**（两处）：
  - 前端 `App.tsx` 用 `sessionStorage["auth-redirecting"]` 做 **一次性守卫**，每标签页只跳一次。
  - 后端 `auth.py` 回调失败改为 **重定向 `/?auth_error=…`**，不再返回死页 JSON。

> 三个坑修复后，日志确认完整成功：`/auth/login 302 → /oauth2/token 200 →
> /oauth2/certs 200 → /auth/callback 307 → /auth/me 200 → /api/configs 200`。

---

## 10. 迁移到非 Trial 环境 / 其他 URL

后续换到生产 IAS 租户、部署到真实域名时，按此清单操作。**核心口诀：redirect_uri
必须三处一致——代码发送的、IAS 登记的、浏览器实际访问的 origin。**

### 10.1 需要改的地方一览

| # | 位置 | Trial（本次） | 非 Trial / 生产（示例） |
|---|------|--------------|------------------------|
| 1 | `.env` `IAS_ISSUER` | `https://abxpwxcgd.trial-accounts.ondemand.com` | `https://<prod-tenant>.accounts.ondemand.com` |
| 2 | `.env` `IAS_CLIENT_ID` | Trial 应用的 client id | 生产应用的 client id |
| 3 | `.env` `IAS_CLIENT_SECRET` | Trial 应用密钥 | 生产应用密钥 |
| 4 | `.env` `IAS_REDIRECT_URI` | `http://localhost:3010/auth/callback` | `https://agent.example.com/auth/callback` |
| 5 | `.env` `SESSION_SECRET` | 本地随机串 | **重新生成** 一个独立的强随机串 |
| 6 | IAS 应用 Redirect URIs | `http://localhost:3010/auth/callback` | `https://agent.example.com/auth/callback`（与 #4 逐字符一致） |
| 7 | 后端监听 | `--host 127.0.0.1 --port 3010` | 由反向代理/容器决定，见 10.2 |

### 10.2 生产环境要额外注意

1. **HTTPS**：生产 redirect_uri 必须 `https://`。IAS 生产应用通常拒绝 `http://`。
2. **反向代理下的 host / 端口**：应用真正对外的 origin 是代理暴露的域名，
   redirect_uri 要写 **对外域名**（如 `https://agent.example.com/auth/callback`），
   而不是容器内部的 `127.0.0.1:8000`。确保代理透传 `X-Forwarded-Proto/Host`，
   否则 Authlib 生成的 redirect_uri 可能是内网地址。
3. **Session cookie 安全属性**：生产建议给 `SessionMiddleware` 配
   `https_only=True`（cookie 加 `Secure`）。若登录会跨站回调，可能需要
   `same_site="none"` + `Secure`。当前代码用 Starlette 默认（`SameSite=lax`），
   顶层 GET 回调可用；如遇 cookie 丢失再调整。
4. **多副本部署**：`SessionMiddleware` 用的是 **无状态签名 cookie**（不是服务端存储），
   多副本天然共享，只要各副本 `SESSION_SECRET` 一致即可。
5. **`localhost` vs `127.0.0.1`**：始终二选一并全程统一（IAS 登记、`.env`、浏览器地址栏）。

### 10.3 迁移后的验证顺序（照抄第 8 节）

```bash
# 换成生产租户/域名后：
curl -s -o /dev/null -w "%{http_code}\n" https://<prod-tenant>.accounts.ondemand.com/.well-known/openid-configuration  # 200
curl -s -o /dev/null -w "%{http_code}\n%{redirect_url}\n" https://agent.example.com/auth/login                          # 302 → IAS
```

再用无痕窗口走一遍浏览器登录。若报 `redirect_uri` 不匹配 → 回到 #4/#6 对齐；
若报 `mismatching_state` → 检查是否 cookie 未随回调返回（HTTPS/SameSite/域名不一致）。

### 10.4 关于 BTP 集成路径（备选）

若最终要走 BTP service broker（`identity-broker`）而非直接注册 OIDC 应用，需先：
1. 确认 subaccount 已与某个 IAS 租户建立 **OIDC Trust**（否则报 `OIDC Trust missing`）。
2. 在 BTP subaccount 的 **Trust Configuration** 里能看到该 IAS 租户（而非只有
   `Default identity provider`）。
3. 再通过 SMCTL / service instance 注册 OIDC 应用。

本项目当前走的是 **Path A（直接 IAS 注册）**，不涉及上述 BTP Trust 步骤。

---

## 11. 附录：完整成功日志

本次 Trial 环境成功登录的服务端日志（节选，已脱敏 code）：

```
GET  /auth/login                         → 302 Found
POST .../oauth2/token                    → 200            (换取 access token)
GET  .../oauth2/certs                    → 200            (取公钥验签 id_token)
GET  /auth/callback?code=…&state=…&iss=… → 307 Temporary Redirect  (存 session，回 /)
GET  /                                    → 200
GET  /auth/me                            → 200            ✓ 已登录
GET  /api/configs                        → 200            ✓ 配置加载
POST /api/chat  (带 session cookie)      → 200 (SSE)      ✓ 聊天可用
```

---

## 涉及的文件清单

| 文件 | 改动 |
|------|------|
| `src/agent_chat/auth.py` | 新增：IAS OIDC 认证模块（BFF） |
| `src/agent_chat/api.py` | 修改：`create_app` 里先 `_load_dotenv_once()` 再 `install_auth`；`/api/chat` 加 session 门禁 |
| `web/src/App.tsx` | 修改：登录门禁 + 一次性防循环守卫；fetch 带 `credentials:"include"` |
| `web/src/runtime.ts` | 修改：`/api/chat` fetch 带 `credentials:"include"` |
| `.env` | 新增：`IAS_*` + `SESSION_SECRET` 五个变量 |
