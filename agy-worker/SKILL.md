---
name: agy-worker
description: 通过本机 Google Antigravity CLI（agy）将子任务委派给 Gemini 执行只读分析、代码审查、规划、实现或修复。适用于用户明确要求使用 Antigravity/Gemini，或在策略允许时委派独立第二意见审查与方案对比、前端 UI/UX 设计、生成供人类阅读的文档或报告、以及向用户回复大段详细解释等任务。
---

# AGY Worker

## 用户可配置偏好（可直接编辑）

```yaml
# 1. 触发模式：
#    - auto-eligible（默认）：不只在用户点名 AGY/Antigravity/Gemini 时分配，遇到下方契合任务也可主动考虑委派
#    - explicit-only：仅当用户明确要求委派给 AGY / Antigravity / Gemini 时触发
#      （改为 explicit-only 时，建议同步将 agents/openai.yaml 的 allow_implicit_invocation 设为 false）
trigger_mode: auto-eligible

# 2. 与普通 Codex subagent 相比优先考虑分给 AGY 的任务倾向（非绝对规则）：
#    以下场景默认优先考虑委派给 AGY（Gemini 4 Neon），强依赖 Codex 原生工具链时仍优先直接由 Codex 处理
#    建议同时修改 skill 描述
preferred_tasks:
  - 复杂情况必要时的独立“第二意见”审查与方案对比（PR/代码审查、边界风险排查、跨模型互补减少盲区）
  - 前端 UI/UX 设计（前端设计定调、复杂界面布局、CSS/交互原型）
  - 生成供人类阅读的文档、报告等内容时 [强烈建议]
  - 在用户询问复杂问题的详细解释时

# 3. AGY 会话保留与清理策略（控制会话复用，以及子代理结束时是否显式删除临时 AGY 对话）：
#    - 1 (always)：总是保留 AGY 会话，不自动删除；同任务后续轮次可按需通过 --conversation 复用
#    - 2 (never)：从不保留；每次新开会话，并在子代理结束时显式删除该临时 AGY 对话（单轮可直接加 --delete-after）
#    - 3 (auto，默认)：由 Codex 按下方规则自动决定是否复用，以及任务/子代理结束时保留还是显式删除
conversation_retention: auto
```

**`conversation_retention: auto` 判断标准：**
- **任务进行中是否复用（`--conversation <id>`）**：
  - **复用**：同一目标任务的延续轮次——包括对上次 AGY 回复的追问与细化、按刚给出的审查/方案直接续做修改、上次因超时/权限拦截中断后的增量补跑，或刚完成大范围上下文阅读并继续围绕同一批文件推进。
  - **新开**：切换到无关的新子任务、需要无锚定偏差的独立二次审查、工作区已有大幅改动导致旧上下文过时，或原会话已过长并出现跑偏/重复出错。
- **子代理/任务结束时保留还是显式删除**：
  - **显式删除**（调用 `run_agy.py delete <conversation_id>`，或单轮任务直接传 `--delete-after`）：一次性只读查询、独立审查、临时文档/大段解释生成，以及已完成且后续不太可能继续追问的临时子代理对话。
  - **保留对话**：多阶段长任务的中间成果、尚未收尾且预期还会继续追问/返工的会话、失败中断需要后续续接排查的会话，或用户明确希望在 `agy` 中回看/继续的对话。

---

## 子代理执行与面板可见性

- **推荐在策略允许时优先以 Codex 原生子代理（subagent）运行本技能任务**，并在开启子代理时**明确要求让这项任务显示在 Codex 的子代理面板里**（通过 Codex 原生子代理工具派生子代理线程来执行 `scripts/run_agy.py`，隔离噪声上下文并便于在子代理面板查看实时进度与结果）。[Codex 子代理文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- 若当前策略或环境不允许启动子代理，再由主代理直接运行脚本。
- **身份边界**：无论由主代理还是 Codex 子代理执行，均须将结果标明为来自外部 `AGY CLI`，并在需要追踪、续接或清理时引用 AGY 实际返回的 `conversation_id`。禁止伪造 Codex 原生 `agent_role` 或 `agent_path`，也不要把 AGY 的 `conversation_id` 传给 Codex 原生子代理工具。

---

## 调用脚本

从**目标工作区**运行当前加载技能目录下的 `scripts/run_agy.py`（Codex 标准技能路径为项目级 `.agents/skills/agy-worker`、用户级 `$HOME/.agents/skills/agy-worker`，兼容旧路径 `$CODEX_HOME/skills/agy-worker`；始终以实际加载到的 `SKILL.md` 所在目录为准）。给 worker 明确的任务说明、可访问或修改的路径、期望输出和验收条件。注意交代必要背景与已排除项，以及是否需要特定产物。

| 委派任务 | 调用模式 | 给 worker 的要求 |
| --- | --- | --- |
| 阅读、分析、审查、问答、生成文档/解释 | `read-only` | 明确限定只读操作、直接给出发现与依据（不走 `/plan`，建议加 `--sandbox`）。 |
| 架构设计、制定实施计划 | `plan` | 使用只读探索并输出结构化方案与依据（透传 `--mode plan`，建议加 `--sandbox`）。 |
| 实现功能、修复问题（仅改文件） | `accept-edits` | 在授权范围内直接编辑，报告实际改动、验证结果和受阻步骤。 |
| 实现并需在沙箱内跑安全测试/构建 | `auto` | 在授权范围内编辑并在 AGY 沙箱中运行相关验证（要求本机 AGY 支持并启用终端沙箱）。 |

**模型与档位**：默认 `--model gemini-4-neon --effort medium`，透传 `agy` 原生 `--model` 与 `--effort`（支持 `low|medium|high|xhigh`，用户说的 `extra high` 自动映射为 `xhigh`）。可按需用 `--model` 指定本机 `agy models` 中的其他模型；不要同时传带档位后缀的模型 ID 和 `--effort`。如模型不可用，核对本机 `agy models` 并报告。

```bash
# 只读分析/审查（不走 /plan）
python3 "<skill-dir>/scripts/run_agy.py" read-only --sandbox --delete-after <<'TASK'
只读检查 src/cache.py。指出缓存键在哪里生成，并给出相关函数名。
TASK

# 子代理结束时显式删除临时 AGY 对话（按 UUID 清理本机 agy-cli 存储）
python3 "<skill-dir>/scripts/run_agy.py" delete "<conversation_id>"
```

**常用可选参数**：
- `--prompt <text>`：命令行直接传任务（也可走 stdin；超长任务时脚本会自动把指令放 `-p`、任务正文走 stdin 管道，避免触发 `ARG_MAX`）。
- `--conversation <id>`：按会话保留策略续接已有 AGY 会话（或配合 `delete` 指定要删除的会话 ID）。
- `--delete-after`：单轮临时任务执行成功后自动清理本次新建的 AGY 对话。
- `--print-timeout <duration>`：默认 `10m`（传 `0s` 关闭限时），脚本同时带进程兜底超时以防 headless 挂起。
- `--sandbox`：启用 AGY 终端沙箱限制。
- `--add-dir <path>`：附加额外工作区目录（可重复传入）。
- `--json-schema <schema-or-path>`：要求 AGY 输出符合 Schema 的 `structured_output`。

---

## 沙箱与权限

- **外发授权与遇拒处理**：本会话**首次**将当前项目的代码、架构或内部上下文发给外部 AGY 服务前，若用户尚未明确授权发送这些内容，先向用户简短确认一次是否允许将相关代码/上下文发送给本机 `agy`（Google Antigravity）服务及大致范围，获准后再调用。若仍被 Codex `auto-reviewer` 拒绝，不要反复重试同一命令，直接向用户说明被拒原因与拟发送范围，等待明确授权或在 Codex `.rules` 中配置放行。
- **Codex 侧沙箱**：执行 `run_agy.py` 需要外网访问模型 API 并读取本机 AGY 认证凭据（如 `~/.gemini/antigravity-cli/`）；执行 `delete` / `--delete-after` 还需写权限以清理 `~/.gemini/antigravity-cli/` 中该会话的本地文件与索引。若因 Codex 沙箱阻断导致 `Please sign in`、网络或清理报错，应向用户请求放行/提权，不要误判为本机未登录 AGY。AGY 首次认证需在交互式 `agy` 会话中完成。
- **AGY 侧权限**：`read-only`（不传 `--mode`，走默认模式 + 只读提示词）与 `plan`（透传 `--mode plan`）均不是权限硬锁，建议叠加 `--sandbox`（如需会话级临时权限覆盖可参考 `agy --settings`，但避免覆盖用户原有 `deny` 规则）。`accept-edits` 自动接受工作区文件编辑，终端命令仍受 `deny > ask > allow` 规则约束；`auto` 自动编辑并在沙箱内运行安全命令。默认调用绝不带 `--dangerously-skip-permissions`。需要额外权限时，指出被拒的具体操作和最小 `action(target)` 规则，保留用户现有权限设置。

---

## 验收与追踪

- 脚本只在 `agy` 退出码为 0、stdout 为单个 JSON 对象、`status` 为 `SUCCESS`、`error` 无错误，且 `response` 为非空白字符串（启用 `--json-schema` 时 `structured_output` 非空）时原样输出 JSON。
- 同时检查脚本退出码和独立 stderr。失败时如 AGY 返回了 JSON，脚本会在 stderr 中保留 `conversation_id`、`status` 与 `error` 以便追踪或续接。
- Headless 可能跳过需要交互授权的工具然后正常返回；若必需操作被拒或未执行，报告实际完成的范围。对写任务检查实际文件差异（如 `git diff`）与相关测试，失败重试前先检查是否已产生部分修改。
- 因 `agy` CLI 当前尚未提供官方非交互删会话子命令，`delete` / `--delete-after` 采用严格 UUID 校验，尽力清理 `~/.gemini/antigravity-cli/` 下对应会话的文件与 `conversation_summaries.db` 记录。

[CLI Reference](https://antigravity.google/docs/cli/reference) · [Headless Mode](https://antigravity.google/docs/cli/headless) · [Permissions](https://antigravity.google/docs/cli/permissions) · [Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
