# GitHub 上传与 CI 配置指南

> 项目已配好 `.gitignore` / `LICENSE` / GitHub Actions workflow，  
> 按下面步骤操作即可完整上线。

---

## 一、上传前检查（必做）

### 1. 确认敏感文件被挡

```bash
# .env 里有公司网关 token，绝对不能提交
grep -F ".env" .gitignore  # 应能看到 .env 被列出

# 手动确认 .env 不会被 git 追踪
git status .env 2>/dev/null  # 应显示 ignored 或不出现
```

### 2. 检查 `.env.example` 内容

`.env.example` 是模板文件（会提交），里面**不能有真实 token**——只有占位符注释。

---

## 二、初始化仓库并推送

```bash
cd projects/ai-agent-test-platform

# 初始化 git
git init
git add .
git status  # 再次确认 .env 不在待提交列表

# 首次提交
git commit -m "Initial commit: AI Agent test platform with checkpoint/resume

- 通用内核 + 主控/5 子 Agent 编排
- 声学插件（AI 台灯）+ WiFi/通话插件
- Checkpoint/Resume + Agent 驱动漂移研判（10 场景/三级严重程度）
- 53 项单测 + PreflightAgent 真调 LLM 验证
- CI/CD: GitHub Actions（Python 3.11/3.12 双版本）"

# 关联 GitHub 远程仓库
git remote add origin https://github.com/YOUR_USERNAME/ai-agent-test-platform.git
git branch -M main
git push -u origin main
```

---

## 三、GitHub 仓库配置

### 1. 更新 README 里的 badge URL

打开 `README.md` 第 3-5 行，把 `YOUR_USERNAME` 替换成真实用户名：

```markdown
[![Tests](https://github.com/YOUR_USERNAME/ai-agent-test-platform/actions/workflows/test.yml/badge.svg)](...)
```

### 2. 配置 Secrets（用于真 LLM 验证）

进入 GitHub 仓库页面：**Settings → Secrets and variables → Actions → New repository secret**

添加以下 Secret（二选一）：

**方式 A：官方直连**
- Name: `ANTHROPIC_API_KEY`
- Value: `sk-ant-api03-xxxxx`

**方式 B：公司网关**（如果用讯飞网关，需要修改 `.github/workflows/llm-verify.yml`）
- `ANTHROPIC_AUTH_TOKEN`: `sk-xxxxx`
- `ANTHROPIC_BASE_URL`: `https://one.iflytek.com/api/llm/console/chat`

⚠️ **注意**：公司网关的 token 不建议放公开 GitHub 仓库，即使加密。建议：
- 用官方 Anthropic API Key（新建一个只用于 CI 的账号，限制配额）
- 或者把 LLM 验证 workflow 禁用，只本地跑

### 3. Actions 权限（如果 CI 没自动跑）

Settings → Actions → General → Workflow permissions → 选 "Read and write permissions"

---

## 四、验证 CI 是否成功

推送后进入 GitHub 仓库：

1. 顶部 tab 点击 **Actions**
2. 应该能看到 `Tests` workflow 正在跑（黄色圆圈）
3. 等待完成，绿色勾 = 通过
4. README 里的 badge 会自动变绿

如果失败，点进具体 job 看日志：
- 常见原因 1：Python 版本不匹配 → 检查 `pyproject.toml` 的 `requires-python`
- 常见原因 2：依赖装不上 → 检查 `pip install -e .` 输出
- 常见原因 3：测试真的挂了 → 本地 `PLATFORM_MOCK=1 pytest tests/` 复现

---

## 五、后续维护

### 每次改代码前

```bash
# 本地先跑一遍
PLATFORM_MOCK=1 pytest tests/ -v

# 通过后再提交
git add .
git commit -m "描述改了什么"
git push
```

### PR 流程

1. 从 `main` 建新分支：`git checkout -b feature/xxx`
2. 提交、推送、在 GitHub 上开 PR
3. CI 自动跑，通过后才能 merge
4. LLM 验证是可选的（不阻塞 PR），需要时手动触发

### 手动触发 LLM 验证

进入 Actions → LLM Verification → Run workflow → Run

---

## 六、Repository Description 建议

在 GitHub 仓库首页设置 About（右上角齿轮）：

**Description**:
> AI Agent-based automated testing platform for AIoT devices. Multi-agent orchestration with checkpoint/resume and drift detection.

**Website**: (留空或个人博客)

**Topics** (关键词标签，方便搜索):
- `ai-agent`
- `llm`
- `testing`
- `claude`
- `anthropic`
- `test-automation`
- `python`
- `checkpoint-resume`
- `mcp`
- `aiot`

---

## 七、进一步优化建议

### 加 CODEOWNERS

`.github/CODEOWNERS`:
```
* @your-github-username
```

### 加 PR 模板

`.github/PULL_REQUEST_TEMPLATE.md`:
```markdown
## 改动描述

## 测试情况
- [ ] 本地 `PLATFORM_MOCK=1 pytest tests/` 通过
- [ ] 新功能有配套单测
- [ ] 如果改了 LLM 相关逻辑，跑过 test_preflight_llm.py

## Breaking Change
- [ ] 是 / 否
```

### 加 Issue 模板

`.github/ISSUE_TEMPLATE/bug_report.md`:
```markdown
## 问题描述

## 复现步骤

## 环境
- Python 版本:
- OS:
- 是否真机: 是/否
```

---

**上传后的样子**：
- ✅ 绿色的 Tests badge
- ✅ 完整的 README（架构图 + 测试策略 + 快速开始）
- ✅ Actions 页面有 CI 运行历史
- ✅ 53 项单测全绿，公开可查
- ✅ 面试时能直接甩仓库链接

**面试话术**：
> "这个项目在 GitHub 上开源了，CI 每次 push 自动跑 53 项单测，覆盖了确定性核心逻辑。真调 LLM 的验证脚本单独跑，验证 Agent 的关键结论是否正确。测试策略我写了专门的文档——因为测试 AI 系统不能像传统软件那样断言字符串全等，我把系统切成两半分开测。"
