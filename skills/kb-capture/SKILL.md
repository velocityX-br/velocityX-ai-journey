---
name: kb-capture
description: Capture research findings from a Claude Code conversation into the user's
  DevOps knowledge base (Docusaurus docs), or optimize an existing article there.
  Use when the user says "沉淀这个结果 / 写入知识库 / 记录到文档 / persist this / save to
  knowledge base", or "优化这篇文章 / 完善这篇文档 / refine this doc". Two modes:
  (1) persist a conversation answer as a new structured doc, (2) improve a specified
  existing article. Always previews and asks for confirmation before writing.
---

# KB Capture

把 Claude Code 调研出的结果沉淀进知识库，或优化知识库里已有的文章。

**知识库有两个根目录，按内容主题选择正确的一个：**

**(1) 公开知识库（默认）:**
`/Users/I577081/Workdir/Github/personal_workstation/devops-hackathons/docs`

这是一个 **Docusaurus** 文档库，按主题分目录（`Gardener/`、`Kubernetes/`、`Linux/`、`DNS/`、`AI/`、`Authorization/` 等）。文档多为**中文为主、保留英文技术术语**。

**(2) 企业私有知识库（SCI / OpenStack / Converged Cloud 专用）:**
`/Users/I577081/Workdir/Github/personal_workstation/devops-hackathons/local-private`

**强制规则：凡是与 OpenStack、SCI（SAP Cloud Infrastructure）、SAP Cloud Infrastructure、Converged Cloud / Converged Infrastructure 相关的文档，必须写入 `local-private/`，绝不写入公开的 `docs/`。**

- `local-private/` 已在 `.gitignore` 中排除，是企业内部专用、不进入公开 GitHub Pages 的知识沉淀区。
- 现有子目录：`Openstack/`（核心，`SCI_<Component>.md` 命名）、`Cloud/SCI/`、`SCI_API_Components_HO/`、`SCI_Infrastructure_Gardener/`、`GMPDNSBIND/`、`BTP_IAS/`、`BTP_AICore/`、`A2A/` 等。
- 判定关键词（命中任一即走 `local-private/`）：OpenStack、Ironic、Nova、Neutron、Cinder、Manila、Designate、Keystone、Glance、Swift、Octavia、Barbican、Keppel、Limes、Hermes、Kubernikus、Archer、Andromeda、Clavis、metal-operator/metal-api、Hammer、SCI、SAP Cloud Infrastructure、Converged Cloud、Converged Infrastructure、CCloud。

**核心原则：任何写盘（新建或修改）之前，永远先展示目标路径 + 内容预览，等用户确认后才动手。**

---

## 两种模式

启动时先判断用户意图属于哪种：

- **模式 A — 沉淀（Capture）**：把本次对话中的某个调研结论/回答整理成一篇新文档写入知识库。
- **模式 B — 优化（Optimize）**：针对用户指定的已有文章进行改进。

如果不确定是哪种，先问一句。

---

## 模式 A — 沉淀调研结果

### A1. 确定要沉淀的内容

- 默认沉淀**最近一次**的调研结论/回答。如果对话里有多个话题，让用户指明是哪一段。
- 内容处理方式 = **整理提炼**：
  - 提炼成结构化文档：清晰标题、要点列表、代码块、命令、结论。
  - **去掉闲聊、试错过程、无关往返**，只保留有价值的知识。
  - 保留关键的命令、配置片段、报错与解法、结论性判断。
  - 不要编造对话里没有的内容；不确定的地方标注 `> TODO: 待验证`。

### A2. 自动匹配目录（然后确认）

**第 0 步（最优先）— 判断走哪个根目录：**

- 内容命中 **SCI / OpenStack / SAP Cloud Infrastructure / Converged Cloud** 关键词（见上文判定关键词列表）→ 根目录 = **`local-private/`**，绝不写入 `docs/`。
- 其它主题 → 根目录 = 公开 `docs/`。

然后在选定的根目录内，根据内容主题**自动匹配**最合适的现有目录：

**若走 `local-private/`（SCI / OpenStack / Converged Cloud）:**
1. 先 `ls` / `Glob` `local-private/` 顶层与相关子目录。
2. 按内容类型匹配：
   - 单个 OpenStack/SCI 组件深度笔记（Ironic、Nova、Neutron、Cinder、Manila、Designate、Keystone、Glance、Swift、Octavia、Barbican、Keppel、Limes、Hermes、Kubernikus、Archer、Andromeda、Clavis、metal-operator 等）→ `Openstack/`，文件名 `SCI_<Component>.md`。
   - Hammer / 网络排障工具 → `Openstack/`（如 `Hammer_Tool.md`、`Hammer_NetCheck_ASR.md`）。
   - 完整调研报告（research report 形式）→ `SCI_API_Components_HO/`（如 `nova-research-report.md`）。
   - SCI 基础设施 / Gardener 交叉主题 → `SCI_Infrastructure_Gardener/`。
   - 本地到云网络 / 跨环境 → `Cloud/SCI/`。
   - Designate / DNS BIND 相关 → `GMPDNSBIND/`。
   - 找不到贴切子目录时，默认 `Openstack/` 并向用户确认。

**若走公开 `docs/`:**
1. 先 `Glob` / `ls` 知识库顶层目录，按主题关键词匹配：
   - Gardener / shoot / seed / garden → `Gardener/`
   - Kubernetes / k8s / pod / controller / operator → `Kubernetes/`（含子目录如 `Security/`、`Monitoring/`、`K8S_Network/`）
   - DNS / bind / coredns → `DNS/`
   - Linux / systemd / OpenSUSE / kvm → `Linux/`
   - 认证授权 / OIDC / IAS / LDAP / TLS → `Authorization/`、`Kubernetes/CloudAuthorization_IAS/`、`LDAP/`、`TLS/`
   - AI / LLM / MCP / LangChain / Skill → `AI/`、`MCP/`
   - Helm → `HELM/`；GitOps / ArgoCD → `GitOps/`；CICD → `CICD/`
   - Hyperscaler / AWS / Azure / GCP → `Hyperscalar/`
   - 其它按最接近的顶层目录归类。
   - ⚠️ 注意：公开 `docs/` 下即便存在 `Openstack/`、`Cloud/SCI/` 等目录，SCI/OpenStack 内容也**不**写这里（它们已被 `.gitignore` 排除）；一律写 `local-private/`。

2. 若已有更贴切的**子目录**，优先放子目录。
3. **向用户确认**：展示推荐的「根目录 + 目录 + 文件名」，并说明理由。用户可改目录、改文件名，或让你新建目录。
4. 找不到合适分类时，明确询问，不要硬塞。

**文件命名**：跟随该目录现有命名风格。`local-private/Openstack/` 用 `SCI_<Component>.md`（PascalCase）；公开 `docs/` 多为 `PascalCase.md` 或带主题词（如 `Gardener_DNS.md`、`Garden_Concept.md`）。用描述性名字，不要用日期戳除非用户要求。

### A3. 确定写入语言 = 跟随周边

- 读该目标目录里 1–2 篇邻近文档，判断主要语言。
- 大多数目录是**中文为主 + 英文技术术语**，就用这种风格。
- 若邻近文档是全英文，则用英文。保持与周边一致，不强行翻译术语。

### A4. Frontmatter

- 该库 frontmatter **可选且极简**。多数文档没有 frontmatter，部分只有 `sidebar_position`。
- 默认**不加** frontmatter，除非邻近文档普遍带有；若加，只加最小必要项（如 `sidebar_position`），不要臆造 tags/date。

### A5. 预览并确认（强制）

写盘前，展示：
1. 最终目标路径（绝对路径）。
2. 是**新建**还是**追加到已有文件**（若同名文件已存在，必须提示，并问：新建改名 / 追加 / 覆盖）。
3. 整理后的完整 Markdown 内容预览。

用户确认后，才用 `Write`（新建）或 `Edit`（追加/合并）落盘。

---

## 模式 B — 优化指定文章

### B1. 读取目标文章

- 用户会指定一篇已有文章（路径或文件名）。先 `Read` 全文。
- 若只给了模糊名字，用 `Glob` 在知识库里定位，命中多个时让用户选。

### B2. 优化能力（按需组合）

根据用户诉求，应用下列一项或多项：

- **补全与纠错**：补充缺失的上下文/前提、修正技术错误、补全不完整的命令或代码示例、修正过时信息。
- **结构重排**：整理标题层级、把散乱内容归纳成小节、把并列信息转成表格、补最小必要的 frontmatter。
- **合并新内容**：把本次对话新调研出的结果**合并进**已有文章——去重、更新旧结论、在正确的小节插入，而不是简单堆在末尾。
- **语言润色**：让表达更通顺、术语统一（可复用 `english-polish` 的思路：技术术语使用社区标准写法）。保持作者原有语气，不过度改写。

优化时**保留原文含义与作者风格**，只做必要改动。若某处改动会改变技术含义，先标出来问，不要擅自定夺。

### B3. 以 diff 方式预览并确认（强制）

- 展示**逐项改动**（原文 → 修改后 + 一句理由），或给出关键差异说明。
- 用户可选择：全部应用 / 只应用部分（指明第几条）/ 不应用。
- **确认后**才用 `Edit` 修改文件，且只改确认过的条目，不动其它部分。

---

## 规则

- **永远先确认再写盘**（新建和修改都一样）。先给路径 + 内容/差异预览。
- **SCI / OpenStack / SAP Cloud Infrastructure / Converged Cloud 内容一律写 `local-private/`**，绝不写公开 `docs/`。这是硬性规则。
- **自动匹配目录后仍需用户确认**，用户对分类和文件名有最终决定权。
- **语言跟随周边文档**，不强行翻译或统一语言。
- **整理提炼、不照搬**闲聊；但也**不编造**对话里没有的技术内容。
- **最小改动**优先，保留作者原有结构与语气。
- `local-private/Openstack/` 下的 SCI 文档，习惯在开头附一行 `> Source: ...` 标注来源（如 `sci-ai-mcp RAG`、SCI ADR、公开源码路径）；沉淀 SCI 内容时若能确定来源，跟随此约定。
- 目标文件已存在时，必须提示并让用户选择：改名新建 / 追加 / 覆盖，绝不静默覆盖。
- 只操作两个知识库根目录之下的文件：公开 `.../devops-hackathons/docs`，或私有 `.../devops-hackathons/local-private`。

## 本 Skill 不做的事

- 不在未确认前写入或覆盖任何文件。
- **不把 SCI / OpenStack / Converged Cloud 内容误写进公开 `docs/`。**
- 不擅自新建顶层目录（需用户同意）。
- 不把整段原始对话（含闲聊/试错）直接倒进文档。
- 不改变已有文章的技术含义或作者语气。
