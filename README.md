# WorkBuddy-Daily 一键部署器

## 下载后怎么用

1. 在本页点 **Code → Download ZIP**，解压完整文件夹。
2. 先在 WorkBuddy 桌面客户端登录你的账号。
3. Windows 双击 `一键部署.bat`；macOS / Linux 在解压目录运行 `bash deploy.sh`。
4. 按界面提示安装缺少的工具、完成 GitHub 官方授权，然后选“开始部署”。部署器会在**你自己的账号**下创建私有仓库并运行一次验证。
5. 以后可再次运行启动器，选“查看当前部署状态”或“添加账号”。定时运行由 GitHub Actions 执行，电脑关机也可以运行；具体积分取决于 WorkBuddy 的实际任务和规则，不能保证每天获得固定积分。

**请保持部署后的仓库为 Private。** 自动续期所需的 `wb_refresh_tokens.json` 会以明文保存在该私有仓库的 Git 历史中。只有信任拥有仓库读取权限的人时才分享访问权限。不要上传该文件、登录态文件、`token.txt` 或运行后保留的工作目录到本公开分享仓库。

本仓库只分发部署器；部署时才从[上游项目](https://github.com/L0NE-6/WorkBuddy-Daily)下载实际运行代码。首次使用需要 GitHub 账号、可访问 GitHub 的网络，以及本机已登录的 WorkBuddy 客户端。

---

把 [L0NE-6/WorkBuddy-Daily](https://github.com/L0NE-6/WorkBuddy-Daily) 部署到**你自己 GitHub 账号的私有仓库**，
用 GitHub Actions 每天定时自动完成 WorkBuddy 签到 + 成长中心任务（青龙面板都不用，电脑关机也能跑）。

**你不需要手抄任何 Token** —— 脚本自己从本机 WorkBuddy 登录态文件里读手机号 / AT / RT。

---

> ### 你拿到的是分发包
>
> 核心运行文件是 `deploy.py`、`deploy.sh`、`一键部署.bat`、`一键部署.ps1`；
> `README.md` 是使用说明。**不需要任何第三方 Python 包**，标准库即可运行。
>
> **最快验证能不能用**（不改动任何东西）：
> ```bash
> python deploy.py --dry-run
> ```
> 它会检查 git / GitHub CLI / 登录态，并打印出「待会儿到底要干什么」。
> 全绿了再去掉 `--dry-run` 正式跑。

### 它到底动了你什么？（先看这个再决定跑不跑）

| 动作 | 说明 |
| :--- | :--- |
| 建一个**私有**仓库 | 在你自己的 GitHub 账号下，名字默认 `WorkBuddy-Daily`；可 `--repo-name` 改 |
| 写入代码 | 从上游 `L0NE-6/WorkBuddy-Daily` 克隆（MIT 协议，版权归原作者） |
| 写入 1 个 Secret | `WORKBUDDY_REFRESH_TOKEN`，值是你本机的 WorkBuddy 登录态；GitHub Secret 加密存储，但运行后令牌还会以明文写入私有仓库的 `wb_refresh_tokens.json` 以便续期 |
| 开一个定时任务 | GitHub Actions，每天 07:00 / 12:00 / 23:30（北京时间）跑签到 |
| 本机改什么 | 默认**什么都不留**（用临时目录，跑完自动删）；只有加 `--persist` 才会在工作目录留一份克隆 |

**它不会做的**：不碰你的 WorkBuddy 客户端、不装任何常驻程序、
不会把你的凭据发到除了你自己 GitHub 仓库和 WorkBuddy 服务以外的其他服务（脚本只调 `gh` 和 `git`，没有自己的上报）。如果你同意自动安装缺少的工具，系统会安装 Python、Git 或 GitHub CLI。

> ⚠️ **唯一要记住的事**：那个私有仓库里会有你的 WorkBuddy 登录态文件
> （`wb_refresh_tokens.json`，Actions 自动续期需要它）。
> **永远不要把它改成 Public。** 部署器每次运行都会校验可见性，发现是公开的会直接中止。

---

## 30 秒上手

### 前置条件（只做一次）

| 需要 | 检查命令 | 没有的话 |
| :--- | :--- | :--- |
| Python 3.9+ | `python --version` | `winget install --id Python.Python.3.12` |
| Git | `git --version` | `winget install --id Git.Git` |
| GitHub CLI | `gh --version` | `winget install --id GitHub.cli` |
| gh 已登录 | `gh auth status` | `gh auth login` |
| 本机登录过 WorkBuddy 桌面端 | — | 打开客户端登录一次 |

> 国内网络不好装 winget 包的话，用的浏览器打开 [cli.github.com](https://cli.github.com) 和 [git-scm.com](https://git-scm.com/downloads) 手动安装。

**缺少的运行环境，脚本会主动帮你装。**

不用提前自己检查 Python / Git / gh —— 缺什么它会告诉你，并可以直接用 `winget` 装上：

```
[1] 检查运行环境
    ✗ 缺少 git
    ✗ 缺少 gh

    要现在自动安装吗？
      会用 winget 安装，过程中可能弹出 UAC 授权窗口，请点「是」
      1) 自动安装（推荐）
      2) 只打印命令，我自己装
      3) 退出
```

| 缺什么 | 在哪里处理 | 怎么装 |
| :--- | :--- | :--- |
| **Python** | 启动器（`.bat` / `.ps1`）先检查 | 选 1 → `winget install Python.Python.3.12` |
| **Git / GitHub CLI** | `deploy.py` 检查 | 选 1 → `winget install Git.Git / GitHub.cli` |

> **装完要重开一次窗口。** 这是 Windows 的限制：进程启动时的 PATH 是快照，
> 新装的工具要**重开终端**才会被找到。脚本装完会明确提示你重新双击启动器，
> 不会让你对着"Python not found"发呆。
>
> 也**不会擅自装软件** —— 安装属于修改系统，必须你选 1 才动手；
> 用了 `-y`（非交互模式）时只打印命令，不会偷偷装。

**首次运行会自动帮你完成 GitHub 授权。** 如果 `gh` 还没登录，脚本会直接拉起
`gh auth login --web`，你按提示在浏览器里填一次性验证码即可，不用另开终端敲命令。

#### 网络前提（重要）

`gh` 和 `git` 走的不是同一个网络通道：

| 工具 | 目标 | 说明 |
| :--- | :--- | :--- |
| `git`（clone/push） | `github.com:443` | 你机器上若配了 `http.proxy`，会走代理 |
| `gh api` | `api.github.com` | 通常直连也能通 |
| **`gh auth login`** | **`github.com/login/device/code`** | **直连 `github.com`，且不读 git 的代理配置** |

所以会出现这种组合：`gh api` 正常、仓库也推得动，**但授权流程卡住超时**。
原因是 `gh` 只看 `HTTPS_PROXY` 环境变量。两条绕法：

**方法一：让 gh 也走你 git 用的那个代理**

```bash
python deploy.py --gh-proxy http://127.0.0.1:10808
```

（把地址换成你自己 `git config --get http.proxy` 的输出。脚本在未登录时会自动检测并提示你。）

**方法二：用 PAT 登录（最省事，不需要 github.com 可达）**

1. 浏览器打开 GitHub → Settings → Developer settings → **Personal access tokens**，
   新建一个 Classic token，勾选 **`repo`** 和 **`workflow`** 两个 scope
2. 存成 `token.txt`，然后执行：

```bash
gh auth login --with-token < token.txt
```

这条只依赖 `api.github.com`，在 github.com 被墙、也没有代理的网络下同样可用。
授权完成后重跑 `python deploy.py` 即可。

### 运行

**方式一：直接用 Python（推荐，逻辑全在这里，行为最确定）**

**不带任何参数运行会先弹出启动菜单**，选了才干：

```bash
python deploy.py
```

```
  请选择要执行的操作
    1) 开始部署 / 修改配置
    2) 添加账号（先在 WorkBuddy 客户端切换到新账号并登录）
    3) 查看当前部署状态
    4) 退出

    请输入序号 [1]：
```

- **选 1** → 进入逐项配置向导，最后确认才动手
- **选 2** → 把本机当前账号**追加**到令牌库（多账号用，见下文「多账号怎么用」）
- **选 3** → 只查看状态，不修改任何东西

> 双击 `一键部署.bat` / `一键部署.ps1` 走的就是这条路（它们会带 `--menu` 参数）。

想要「一条命令跑完、什么都不问」（脚本化 / CI 用）：

```bash
python deploy.py -y          # 跳过菜单和向导，全部用默认值
python deploy.py --menu      # 强制显示菜单
python deploy.py -i          # 跳过菜单，直接进配置向导
```


**不带任何参数运行会进入交互向导**，逐项问你（直接回车 = 用默认值）：

```bash
python deploy.py
```

```
[2] 交互式配置
    直接回车 = 用默认值；Ctrl+C 可随时退出

    GitHub 仓库名（建在你自己的账号下，私有） [WorkBuddy-Daily]：
    每天什么时候签到（北京时间）？
      1) 推荐：07:00 / 12:00 / 23:30（含夜猫子活动窗口） ← 默认
      2) 只早上跑一次：09:10
      3) 早晚各一次：09:10 / 23:30
      4) 自定义
    请输入序号 [1]：

    检测到仓库里已有 1 个账号：
      · 133****84（更新于 2026-09-22 18:16）

    本次要怎么处理账号？
      1) 追加/更新本机当前账号（不动其它账号） ← 默认
      2) 只更新配置，不改账号
    请输入序号 [1]：

    即将执行
      · 目标仓库   your-name/WorkBuddy-Daily（私有）
      · 签到时间   07:00,12:00,23:30（北京时间）
      · 令牌处理   追加/更新本机账号
      · 验证运行   部署后立即跑一次并打印结果

    确认开始部署？ (Y/n)：
```

想要「一条命令跑完、什么都不问」（脚本化 / CI 用）：

```bash
python deploy.py -y          # 跳过向导，全部用默认值
python deploy.py -i          # 反过来：强制进向导（即使带了参数）
```

```bash
python deploy.py
```

**方式二：双击运行（Windows）**

| 文件 | 说明 |
| :--- | :--- |
| `一键部署.ps1` | 右键 →「使用 PowerShell 运行」。若提示执行策略，用 `powershell -ExecutionPolicy Bypass -File .\一键部署.ps1` |
| `一键部署.bat` | 直接双击。找不到 Python 时会给安装提示 |

**方式三：macOS / Linux**
```bash
chmod +x deploy.sh && ./deploy.sh
```

> 两个 Windows 启动器都只是「找 Python → 转发参数给 `deploy.py`」的薄壳，
> 没有任何业务逻辑。所以哪怕启动器出问题，`python deploy.py` 一定可用。
> 如果 PowerShell 拦截脚本，用：
> ```powershell
> powershell -ExecutionPolicy Bypass -File .\一键部署.ps1
> ```

跑完会打印：

```
[1] 检查运行环境
    ✓ git   git version 2.55.0
    ✓ gh    gh version 2.89.0
    ✓ gh 已登录：your-name
    ✓ 令牌权限：repo / workflow 齐备

[2] 读取本机 WorkBuddy 登录态
    · 来源：C:\Users\you\AppData\Local\CodeBuddyExtension\...\workbuddy-desktop.info
    ✓ 账号 133****84    AT 1331 字符 / RT 700 字符

[3] 换算定时任务（北京 → UTC）
    ✓ 北京 07:00  →  cron 0 23 * * *
    ...
```

---

## 全部参数

| 参数 | 说明 |
| :--- | :--- |
| （无参数） | 全自动部署：建私库 → 推代码 → 写 Secret → 跑一次验证并打印结果 |
| `--check` | 只看当前部署状态（仓库可见性 / Secrets / 工作流 / 最近运行） |
| `--dry-run` | 预演：只检查凭据和换算定时，**不碰任何东西** |
| `--schedule 09:10,23:30` | 改定时（北京时间，逗号分隔）。默认 `07:00,12:00,23:30` |
| `--pushplus <token>` | 顺带配好 PushPlus 微信推送 |
| `--bark <url>` | 顺带配好 Bark iOS 推送 |
| `--token "手机号:AT:RT"` | 手动指定凭据（换电脑 / 自动读取失败时用） |
| `--no-run` | 部署完不触发验证运行 |
| `--repo-name <name>` | 仓库名，默认 `WorkBuddy-Daily` |
| `--gh-proxy <url>` | 让 `gh` 走指定代理。**`gh` 不读 git 的 `http.proxy`**，所以只有 git 配了代理的机器需要它，如 `--gh-proxy http://127.0.0.1:10808` |
| `--persist` | 改用固定工作目录 `<脚本目录>/WorkBuddy-Daily`（保留克隆，便于本机调试） |
| `--workdir <path>` | 手动指定工作目录（覆盖默认） |
| `--ephemeral` | 【已经是默认行为】保留此开关只为兼容旧命令 |
| `--force-secret` | 仓库已有令牌库时，仍强制用本机凭据覆盖 Secret（危险，见下） |
| `--yes` | 配合 `--force-secret`，确认接受令牌回退风险 |

**幂等**：重复运行是安全的。第二次跑会复用已有仓库、对齐远端 main、只提交真正的差异，
并且**不会**用本机旧凭据覆盖已被 Actions 接管的令牌。

> ⚠️ `--force-secret` 是危险操作。仓库里的 `wb_refresh_tokens.json` 是**活的**——
> Actions 每次续期都会轮换令牌并回写，所以它通常比本机登录态文件更新。
> 直接覆盖会让令牌**回退**到旧值（服务端旧 RT 往往已作废）→ 下次运行 401。
> 部署器会自动比较两边的 JWT `exp`，发现仓库更新就**中止**；确实要重置时需
> 先在桌面端重新登录拿到全新凭据，再加 `--yes`。

---

## 常见用法

```bash
# 先看看会发生什么
python deploy.py --dry-run

# 改成早上 9:10 + 晚上 23:30（夜猫子窗口）两趟
python deploy.py --schedule 09:10,23:30

# 加上微信推送
python deploy.py --pushplus 你的PushPlusToken

# 出问题了，先看状态
python deploy.py --check
```

---

## 脚本做了什么

1. **校验环境** —— git / gh / gh 登录态 / 令牌是否含 `repo,workflow` 权限。缺权限会直接告诉你跑 `gh auth refresh -s repo,workflow`。
2. **接通 git 授权** —— 自动执行 `gh auth setup-git`，把 gh 变成 git 的凭据助手。少了这步，没配过凭据助手的机器上 `git push` 会要求输入用户名/密码。
3. **读凭据** —— 按 Windows → macOS → Linux 顺序探测 `workbuddy-desktop.info`，取 `account.phoneNumber` + `auth.accessToken` + `auth.refreshToken`。多账号只取当前登录账号。
4. **过期预检** —— 本地解析 JWT 的 `exp`，提前告诉你 AT / RT 各剩多少天；RT 已过期会直接中止，不用等 CI 跑完才看到 401。
5. **换算定时** —— 北京时间 → UTC cron（cron 都是 UTC，手算容易错一天）。
6. **建私有仓库** —— `gh repo create --private`，创建后**再次校验**可见性；如果你指定的仓库名已存在且是**公开**的，脚本会直接中止，避免把 Token 写进公开仓库。
7. **同步代码** —— `origin` 指向你的仓库、`upstream` 指向作者原仓库（方便以后 `git merge upstream/main`）。全量克隆，规避浅克隆推送失败。
8. **改工作流** —— 三处最小改动，全部幂等、带 `hardened-by-deployer` 注释（明细见下）。
9. **生成运维文档** —— 往仓库写 `运维说明.md`。**只覆盖本工具生成过的版本，不会动你手写的文档。**
10. **写 Secret** —— 通过 stdin 传给 `gh secret set`，不出现在命令行、不落盘、不回显。
11. **跑一次验证** —— 触发 workflow，轮询到结束，抓出关键日志（签到结果 / 领奖 / 未完成任务）打印给你。

### 对上游工作流的三处改动

| 位置 | 改动 | 原因 |
| :--- | :--- | :--- |
| `Checkout` | 加 `fetch-depth: 0` | 令牌回写需要完整历史才能合并远端 |
| `Run WorkBuddy` 的 env | 补 `BARK_URL` | 上游 README 声明了该 Secret，但 env 没接线，Bark 推送实际永不生效 |
| `Persist token state` | 裸 `git push` → **重试 5 次 + 合并远端后重推**，失败时 `::error::` 明确报错 | **最关键**：原版 push 一旦被拒（并发提交 / 分支保护），本次已轮换的 refreshToken 就丢了，而服务端旧 RT 通常已作废 → 账号需要人工重新登录 |

---

## 多账号怎么用

**先知道一个关键事实**：`workbuddy-desktop.info` 里只有**当前登录账号**这一份令牌 ——
`accounts` / `allAccounts` 数组里只有 uid、昵称、手机号，**没有 token 字段**，
而 `auth` 块只有一份。所以本机一次只能取到一个号的令牌。

多账号就是「逐个把它加进去」。

### 方式一：切换客户端登录 + `--add-account`（推荐）

```bash
# ① 先在 WorkBuddy 桌面端登录账号 A，然后
python deploy.py                  # 首次部署，写入账号 A

# ② 在客户端登出，登录账号 B，然后
python deploy.py --add-account    # 追加 B，A 不受影响

# ③ 还有号？重复第 ② 步即可
python deploy.py --add-account
```

进向导时它也会问你（会先把仓库里已有账号列出来）：

```
    检测到仓库里已有 1 个账号：
      · 133****84（更新于 2026-09-22 18:16）

    本次要怎么处理账号？
      1) 追加/更新本机当前账号（不动其它账号） ← 默认
      2) 只更新配置，不改账号
```

`--add-account` 实际做了三件事：

1. 读仓库里的令牌库（`wb_refresh_tokens.json`，含**所有**已配置账号）
2. 把本机当前账号合并进去，写回仓库并推送
3. 把 Secret 更新成**多行**格式（每行 `手机号:AT:RT`）作为兜底

> **防回退护栏**：同一个手机号已存在时，会比较两边令牌的 `exp`。
> 仓库里的令牌由 Actions 持续轮换、通常更新，**只有本机确实更新时才覆盖**；
> 否则会提示「仓库里的令牌比本机新，保持不动」，避免用一个旧令牌把有效令牌踢掉。
> 解析不出 `exp` 的令牌也一律不覆盖。

### 方式二：不想切换客户端？用上游的短信登录工具

上游项目里有个 `workbuddy_login.py`，用**手机号 + 短信验证码**登录，
对任意账号都能输出一行 `手机号:AT:RT`，不依赖桌面端当前登录的是谁：

```bash
# 从上游仓库取这个文件（MIT 协议）
# https://github.com/L0NE-6/WorkBuddy-Daily/blob/main/workbuddy_login.py
python workbuddy_login.py <你的手机号>    # 按提示输入收到的短信验证码
```

每个号跑一次，把得到的多行拼在一起，一次性交给部署器：

```bash
python deploy.py --token "134xxxx1111:AT1:RT1
188xxxx2222:AT2:RT2"
```

（`--token` 支持**换行分隔的多行**，也支持用 `@` 分隔。）

### 确认现在有几个账号

```bash
python deploy.py --check          # 看仓库状态
python deploy.py -i               # 向导里会列出令牌库中的全部账号
```

或直接读仓库里的令牌库：

```bash
gh api repos/<你>/WorkBuddy-Daily/contents/wb_refresh_tokens.json \
  --jq '.content' | base64 -d | python -c "import json,sys; print(list(json.load(sys.stdin)))"
```

### ⚠️ 两个会让人以为「卡住了」的点

**1）新账号必须先过新手引导**

首次加入的账号如果**没在客户端完成新手引导**，云端任务会全部被前置条件挡住：

```
[账号2] 🌱 成长: 等级0 连签0天 能量0 累签0天        ← 全新账号
[账号2] ✗ create_canvas:   error prerequisite not met: first_buddy
[账号2] ✗ Library_read:    error prerequisite not met: first_buddy
[账号2] ✗ Expert_...:      error prerequisite not met: first_buddy   （共 18 项）
[账号2] 🔁 18 项未落账，逐个重试...
```

脚本会**逐个重试这 18 项**（每项之间有间隔），所以整次运行会被拉长到 **6~7 分钟**，
看起来像卡住了。**这不是卡住** —— 去客户端拿这个号登录一次、把新手引导走完
（创建第一个 Buddy），下次运行就正常了。

**2）多账号会明显变慢**

| 账号数 | 典型耗时 |
| :--- | :--- |
| 1 个（正常账号） | 约 1 分钟 |
| 2 个（含 1 个新号） | 6~7 分钟 |

等待期间脚本**每 20 秒打印一次心跳**，并给出运行链接，不会像以前那样屏幕几分钟不动。
想中途退出等待按 **Ctrl+C** —— 任务在 GitHub 上照常跑，不影响结果。

### 常见误区

| 想法 | 实际 |
| :--- | :--- |
| 「我登录了两个号，脚本应该能一次读出来」 | ❌ 本地文件只有当前账号的令牌 |
| 「小号直接再跑一次 `deploy.py` 就加上了」 | ❌ 默认不会动已有令牌库，脚本会**提示你加 `--add-account`** |
| 「`--token` 传多个账号会覆盖前面的」 | ❌ 它写进 Secret 是多行格式；但**仓库令牌库**才是权威，要改它得用 `--add-account` |

---

## 令牌机制（为什么不用管它）

脚本要的是 **`手机号:AT:RT`** 三元组，不是单个 accessToken。

- 触发条件：**距上次刷新 > 10 天** 或 **AT 7 天内即将过期** → 用 RT 换发新 AT+RT 并落盘。
- Actions 在**私有仓库**里把 `wb_refresh_tokens.json` 提交回仓库持久化，所以**配一次就永续**，不用像纯 AT 方案那样每 60 天手动换。
- 副作用（好事）：令牌库约每 10 天产生一次提交，让仓库保持活跃，**避免 GitHub 因 60 天无活动停用定时工作流**。

> ⚠️ **仓库必须保持 Private。** `wb_refresh_tokens.json` 里是明文 RT，等同账号控制权。
> 脚本每次部署都会校验可见性，但你自己别手动改成 Public。

---

## 推送通知

| 渠道 | 配置 | 说明 |
| :--- | :--- | :--- |
| PushPlus | Secret `PUSHPLUS_TOKEN` | 微信，[sct.ftqq.com](https://sct.ftqq.com) 扫码取 token |
| Bark | Secret `BARK_URL` | iOS，如 `https://api.day.app/xxxxxxxx` |

```bash
gh secret set PUSHPLUS_TOKEN --repo <你>/WorkBuddy-Daily
gh secret set BARK_URL      --repo <你>/WorkBuddy-Daily
```

都不配也能跑 —— 报告会直接打到 Actions 日志里（见下）。

### 关于 QQ 通知

**已按要求撤回（2026-09-22）。** 部署器现在会主动清理历史遗留：

- 删除曾注入到 `workbuddy_daily.py` 的 `_qq_notify()` 函数
- 删除 `workbuddy.yml` 里的 `QQ_BOT_URL` / `QQ_BOT_TARGET` / `QQ_BOT_TOKEN`

当时撤回的原因：QQ 官方机器人是「事件驱动 + 被动回复」模型，**主动消息有严格配额**，
做不了每日定时主动推送；能走通的只有自建 OneBot 11（NapCat / LLOneBot），
而它要求 GitHub Actions 能访问到一个公网地址，运维成本明显高于 PushPlus / Bark。

需要再加回来时，照 BUG 审查报告里「六、QQ 通知接入」那一节的接口说明实现即可。

### 报告输出兜底

上游 `send_notify_all` 原本是「只推不打印」：**没配任何推送渠道时，运行报告完全不会输出**，
CI 日志里只剩零散任务行。部署器会把 `send_notify_all` 规范成：

```python
r1 = send_notify(title, content)
r2 = _bark_notify(title, content)
if not (r1 or r2):
    print("\n" + content)      # 没有渠道可用时，把报告打到 stdout
return r1 or r2
```

只动这一个函数（带 `hardened-by-deployer:notify-stdout` 标记），便于日后 merge upstream。

---

## 想再分发？

**当前这个目录里没有打包工具** —— `打包给朋友.py` 只存在于作者的完整仓库，不在分发包里。

如果要把它传给下一个人，最稳妥的做法是**只传这一个目录**，并且先把工作目录清掉
（默认的临时目录模式已经保证了这一点：跑完会自动删，本机不留凭据副本）。

需要自己做一个「带泄露扫描的分享包」时，按下面的思路写个脚本即可：

1. 白名单只挑 `deploy.py` / `deploy.sh` / `一键部署.bat` / `一键部署.ps1` / `README.md`
2. 扫描产物里是否含：真实 JWT（`eyJ` 开头且长度 > 60）、GitHub PAT、手机号、邮箱、本机绝对路径
3. 命中就拒绝出包

> **千万不要**把「跑过之后的目录」整个拷给别人：默认临时目录模式本来就不留东西，
> 但如果你用了 `--persist`，工作目录里会有 `wb_refresh_tokens.json`（明文 refreshToken）
> 和 `.git`（历史里也提交过它）。那个文件夹一旦被拷走，等于把账号交出去。

### 部署器自带的防泄露清场

即使真的拷了整个目录过去，`deploy.py` 也会拦住：

| 检查 | 行为 |
| :--- | :--- |
| 本地 git 历史里含 `wb_refresh_tokens.json` | 新建仓库前重置为上游干净历史，并提示 |
| 改 `origin` 地址后残留旧 remote-tracking 引用 | 显式清理，避免误继承上一个远端的历史 |
| 工作区残留他人的凭据文件 | 一并删除（首次运行会用他自己的 Secret 重新自举） |

---

## 排障

| 现象 | 处理 |
| :--- | :--- |
| `gh` 令牌缺少权限 | `gh auth refresh -s repo,workflow` |
| 推送报 `refusing to allow a Personal Access Token to create or update workflow` | 同上 —— 推 `.github/workflows/` 下的文件必须有 `workflow` 权限 |
| 推送报 `could not read Username` / `terminal prompts disabled` | `gh auth setup-git`（部署器已自动执行，仍失败说明 gh 凭据本身有问题） |
| 提示「仓库里的令牌比本机凭据更新」 | 这是**保护**不是错误。正常情况不需要 `--force-secret`；真要重置先重新登录桌面端再加 `--yes` |
| 找不到 WorkBuddy 登录态 | 先登录桌面端；或用 `--token "手机号:AT:RT"` |
| 日志报 `TOKEN_EXPIRED` / 401 | RT 失效。重新登录桌面端，然后 `python deploy.py --force-secret`（先确认新的凭据确实比仓库里的新） |
| 推送报 `index-pack failed` | 脚本会自动 `git fetch --unshallow` 后重试；仍失败就删掉 `--workdir` 目录重新跑 |
| Actions 里令牌回写步骤失败 | 仓库有分支保护/规则集不允许 Actions 推 main。给 `github-actions[bot]` 开白名单，或接受令牌不持久化 |
| 定时没生效 | 新建/修改 workflow 后 GitHub 需要几分钟注册；`gh run list` 确认 |

更详细的排障见部署完成后仓库里的 `运维说明.md`。

---

## 设计取舍

- **不 fork、新建独立私库**：Fork 出来的仓库默认会继承上游可见性，且 Fork 无法设为 Private 后再改回。独立私库最干净。
- **不做 CF / 青龙版本**：GitHub Actions 零成本、零常驻设备、维护最省；CF 方案需要自己写 Worker 且 Token 无法自动续期（详见项目内调研报告）。
- **不覆盖手写文档**：`运维说明.md` 带生成标记，手写版本会被保护。
- **默认从本机读取凭据**：无需把令牌写在命令行。`--token`、`--pushplus`、`--bark` 是高级参数；直接把值写在命令行可能被系统进程列表或终端历史记录看到。部署后的私有仓库会持久化明文令牌库。

---

上游项目 MIT License，代码版权归原作者。本部署器仅为部署封装。
