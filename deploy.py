#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy-Daily 一键部署器
==========================

把 L0NE-6/WorkBuddy-Daily 部署到你自己 GitHub 账号的**私有**仓库，
并用 GitHub Actions 每天定时自动签到 / 跑成长中心任务。

凭据（手机号 / AT / RT）从本机 WorkBuddy 登录态文件自动读取，
不需要手抄，也不会打印到终端。

用法：
    python deploy.py                     # 全自动部署（含一次验证运行）
    python deploy.py --check             # 只查看当前部署状态
    python deploy.py --dry-run           # 预演，不做任何改动
    python deploy.py --schedule 09:10,23:30
    python deploy.py --pushplus <token>  # 顺带配好微信推送
    python deploy.py --bark https://api.day.app/xxxx
    python deploy.py --token "手机号:AT:RT"   # 手动指定凭据
    python deploy.py --no-run            # 只部署，不触发验证运行

依赖：git、GitHub CLI(gh，且已 gh auth login)、Python 3.9+
"""

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

UPSTREAM = "https://github.com/L0NE-6/WorkBuddy-Daily.git"
DEFAULT_REPO_NAME = "WorkBuddy-Daily"
WORKFLOW_FILE = ".github/workflows/workbuddy.yml"
SECRET_KEY = "WORKBUDDY_REFRESH_TOKEN"
DEFAULT_SCHEDULE = "07:00,12:00,23:30"
OPS_DOC = "运维说明.md"

# ---------------------------------------------------------------- 终端输出

# Windows 中文控制台默认 cp936，直接打印 emoji 会 UnicodeEncodeError。
# 这里统一把输出改成 UTF-8 + 容错，并且把控制台代码页切到 65001。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if os.name == "nt":
    os.system("")                      # 打开 Windows 10+ 的 ANSI 转义支持
    os.system("chcp 65001 >nul 2>nul")  # 让中文/emoji 在 cmd 里正常显示

_NO_COLOR = os.environ.get("NO_COLOR") or not sys.stdout.isatty()


def _c(code):
    return "" if _NO_COLOR else code


C_RESET, C_DIM, C_OK, C_WARN, C_ERR, C_INFO, C_BOLD = (
    _c("\033[0m"), _c("\033[90m"), _c("\033[32m"), _c("\033[33m"),
    _c("\033[31m"), _c("\033[36m"), _c("\033[1m"),
)

STEP = 0

# 由 --gh-proxy 注入：让 gh 子进程走指定代理（gh 不读 git 的 http.proxy）
GH_PROXY = None


def banner():
    print(f"""{C_BOLD}
╔══════════════════════════════════════════════════════════╗
║   🌱 WorkBuddy-Daily 一键部署器                          ║
║   私有仓库 + GitHub Actions 每日自动签到                 ║
╚══════════════════════════════════════════════════════════╝{C_RESET}""")


def step(title):
    global STEP
    STEP += 1
    print(f"\n{C_INFO}{C_BOLD}[{STEP}]{C_RESET}{C_BOLD} {title}{C_RESET}")


def ok(msg):
    print(f"    {C_OK}✓{C_RESET} {msg}")


def info(msg):
    print(f"    {C_DIM}·{C_RESET} {msg}")


def warn(msg):
    print(f"    {C_WARN}!{C_RESET} {msg}")


def die(msg, code=1):
    print(f"\n    {C_ERR}✗ {msg}{C_RESET}\n")
    sys.exit(code)


# ---------------------------------------------------------------- 命令执行

def run(cmd, cwd=None, stdin_data=None, check=True):
    """
    执行命令。stdin_data 走 stdin 传参，避免密钥出现在命令行/进程列表里。

    env 里关掉所有交互式提示：缺凭据时立刻报错，而不是把脚本挂在那里等输入。
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")   # git 不弹用户名/密码
    env.setdefault("GIT_ASKPASS", "")            # 禁用图形/终端问密码
    env.setdefault("SSH_ASKPASS", "")
    env.setdefault("GH_PROMPT_DISABLED", "1")    # gh 不弹交互
    env.setdefault("GH_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("GH_PAGER", "cat")

    # gh 只看 HTTPS_PROXY/HTTP_PROXY，不读 git 的 http.proxy ——
    # 所以「只有 git 配了代理」的机器上，gh auth login 会直连 github.com 并超时。
    if GH_PROXY:
        env.setdefault("HTTPS_PROXY", GH_PROXY)
        env.setdefault("HTTP_PROXY", GH_PROXY)

    proc = subprocess.run(
        cmd, cwd=cwd, input=stdin_data, capture_output=True,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        hint = ""
        low = detail.lower()
        if "workflow" in low and ("scope" in low or "refusing to allow" in low):
            hint = ("\n      → 令牌缺少 workflow 权限，执行：\n"
                    "        gh auth refresh -s repo,workflow")
        elif "could not read username" in low or "authentication failed" in low \
                or "terminal prompts disabled" in low:
            hint = ("\n      → git 没有可用凭据，执行：\n"
                    "        gh auth setup-git")
        elif "non-fast-forward" in low or "failed to push some refs" in low:
            hint = "\n      → 远端有新提交，先 git pull 再重试"
        die(f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}\n"
            f"      {detail[:600]}{hint}")
    return proc


def gh(args, stdin_data=None, check=True):
    return run(["gh"] + args, stdin_data=stdin_data, check=check)


def git(args, cwd, check=True):
    return run(["git"] + args, cwd=cwd, check=check)


# ---------------------------------------------------------------- 前置检查

def gh_auth_state():
    """返回 (登录名 或 None, 原始输出)。

    注意：多账号环境下某个账号失效时 gh 也会返回非 0，
    只要输出里有 "Logged in" 就说明当前账号可用，不能一律判为未登录。
    """
    p = run(["gh", "auth", "status"], check=False)
    blob = (p.stdout or "") + (p.stderr or "")
    if "Logged in" not in blob:
        return None, blob
    m = re.search(r"account\s+(\S+)", blob)
    return (m.group(1) if m else "unknown"), blob


def detect_git_proxy():
    """读 git 配置里的 http.proxy（没配返回 None）。"""
    p = run(["git", "config", "--get", "http.proxy"], check=False)
    return (p.stdout or "").strip() or None


def gh_env_proxy():
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def login_help_lines():
    """未登录时给出可落地的指引（针对 github.com 直连不通的网络）。"""
    lines = []
    gp = detect_git_proxy()
    ep = gh_env_proxy()
    if gp and not ep:
        lines.append(f"{C_WARN}检测到 git 配了代理 {gp}，但 gh 不会自动使用它{C_RESET}")
        lines.append("      可以让 gh 也走同一个代理，重跑时加上：")
        lines.append(f"        python deploy.py --gh-proxy {gp}")
    elif gp and ep:
        lines.append(f"{C_DIM}git 代理 {gp}；gh 当前代理 {ep}{C_RESET}")
    lines.append(f"{C_DIM}若 github.com 直连不通（api.github.com 通、但设备码页超时），还有一条更省事的路：{C_RESET}")
    lines.append("      在 GitHub 网页生成 PAT（勾选 repo + workflow），存成 token.txt 后执行：")
    lines.append("        gh auth login --with-token < token.txt")
    lines.append(f"{C_DIM}这条只依赖 api.github.com，无代理、github.com 被墙的环境也能用。{C_RESET}")
    return lines


def interactive_gh_login():
    """
    在脚本内直接拉起官方授权流程（需要交互终端）。

    为什么值得做：陌生人是「下载 → 运行」的动作，如果这里只丢一句
    「请先执行 gh auth login」，他就得再开一次终端、再记一条命令。
    直接在这里接手，才是真正的「一条命令跑通」。
    """
    if not sys.stdin.isatty():
        warn("当前不是交互终端（stdin 被重定向），无法在此拉起授权流程")
        return False

    print()
    print(f"    {C_WARN}检测到尚未登录 GitHub{C_RESET}，现在帮你拉起官方授权：")
    print(f"    {C_DIM}{'─' * 60}{C_RESET}")
    print(f"    {C_DIM}接下来 gh 会让你按回车，然后在浏览器里填入一次性验证码{C_RESET}")
    print(f"    {C_DIM}{'─' * 60}{C_RESET}")

    # 这里必须允许交互：把之前给子进程加的「禁止提示」开关摘掉
    env = dict(os.environ)
    for k in ("GH_PROMPT_DISABLED", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
              "SSH_ASKPASS", "GH_PAGER"):
        env.pop(k, None)
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    # 关键：这条路径走的是 subprocess.call，绕过了 run()，
    # 所以 --gh-proxy 必须在这里单独注入，否则 gh 仍会直连 github.com 超时。
    if GH_PROXY:
        env.setdefault("HTTPS_PROXY", GH_PROXY)
        env.setdefault("HTTP_PROXY", GH_PROXY)

    try:
        rc = subprocess.call(
            ["gh", "auth", "login", "--hostname", "github.com",
             "--git-protocol", "https", "--web", "--skip-ssh-key"],
            env=env,
        )
    except Exception as e:
        warn(f"拉起 gh auth login 失败：{e}")
        return False

    print()
    if rc != 0:
        warn(f"授权流程未完成（退出码 {rc}）")
        return False
    return True


WINGET_PKGS = {
    "git": "Git.Git",
    "gh": "GitHub.cli",
}

TOOL_URLS = {
    "git": "https://git-scm.com/downloads",
    "gh": "https://cli.github.com",
}


def find_winget():
    """返回 winget 可执行路径；不在 PATH 时去几个已知位置找。"""
    if os.name != "nt":
        return None
    p = shutil.which("winget")
    if p:
        return p
    for cand in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"),
        os.path.expandvars(r"%ProgramFiles%\WindowsApps\winget.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def winget_available():
    return find_winget() is not None


def print_manual_install(missing):
    """
    按当前系统打印最合适的安装命令。

    注意：必须按 OS 显式分支 —— 早期版本写成「有 winget 用它，否则是 macOS 用 brew，
    其余一律 apt/dnf」，结果 Windows 上 winget 不在 PATH 时会建议用户
    `sudo apt install git`。
    """
    is_win = os.name == "nt"
    is_mac = sys.platform == "darwin"

    print(f"    {C_BOLD}手动安装方式：{C_RESET}")
    for tool in missing:
        print(f"      {C_BOLD}{tool}{C_RESET}")
        if is_win:
            print(f"        winget install -e --id {WINGET_PKGS[tool]}")
            if not winget_available():
                print(f"        {C_DIM}（找不到 winget？到 Microsoft Store 搜「应用安装程序 / App Installer」装上）{C_RESET}")
        elif is_mac:
            print(f"        brew install {tool}")
        else:
            print(f"        sudo apt install {tool}   （Debian/Ubuntu）")
            print(f"        sudo dnf install {tool}   （Fedora/RHEL）")
        if tool == "gh":
            print(f"        官方安装说明：{TOOL_URLS['gh']}")
        else:
            print(f"        官方下载：{TOOL_URLS['git']}")


def offer_install_missing(missing, non_interactive=False):
    """
    缺少工具时询问是否自动安装。

    设计取舍：**安装软件属于改系统，必须用户明确同意**，
    所以非交互模式（-y / 管道）只打印命令、不擅自装。
    返回 True 表示装完且当前进程已能调用。
    """
    print()
    for tool in missing:
        print(f"    {C_ERR}✗{C_RESET} 缺少 {C_BOLD}{tool}{C_RESET}")

    if not winget_available():
        print()
        print_manual_install(missing)
        die("请安装后重试")

    if non_interactive:
        print()
        print_manual_install(missing)
        die("请安装后重试（非交互模式下不会自动安装软件）")

    print()
    print(f"    {C_BOLD}要现在自动安装吗？{C_RESET}")
    print(f"      {C_DIM}会用 winget 安装，过程中可能弹出 UAC 授权窗口，请点「是」{C_RESET}")
    print(f"      {C_INFO}1){C_RESET} 自动安装（推荐）")
    print(f"      {C_INFO}2){C_RESET} 只打印命令，我自己装")
    print(f"      {C_INFO}3){C_RESET} 退出")
    print()
    choice = _ask("请输入序号", "1")

    if choice == "3":
        die("已取消，未做任何改动")
    if choice == "2":
        print()
        print_manual_install(missing)
        die("请安装后重试")

    print()
    winget_exe = find_winget()
    for tool in missing:
        pkg = WINGET_PKGS[tool]
        info(f"正在安装 {tool}（{pkg}）…请留意是否弹出授权窗口")
        rc = subprocess.call([
            winget_exe, "install", "-e", "--id", pkg,
            "--accept-source-agreements", "--accept-package-agreements",
        ])
        if rc == 0:
            ok(f"{tool} 的安装命令已成功执行")
        else:
            warn(f"{tool} 安装未成功（winget 退出码 {rc}）")

    still = [t for t in missing if shutil.which(t) is None]
    if not still:
        ok("工具已就绪，继续")
        return True

    print()
    warn(f"这几个刚装好，但当前终端还认不到：{', '.join(still)}")
    info("这是正常现象 —— 新装的工具要**重开终端**才会进入 PATH，当前进程的环境变量改不了。")
    info(f"请关掉这个窗口，重新双击「一键部署」，就能继续了。")
    die("需要重开一次终端")


def ensure_tools(non_interactive=False):
    """检查 git / gh / 登录态；未登录时尝试在脚本内完成授权。返回 GitHub 登录名。"""
    global STEP
    step("检查运行环境")
    if sys.version_info < (3, 9):
        die(f"Python 版本过低（{sys.version.split()[0]}），需要 3.9+")

    missing = [t for t in ("git", "gh") if shutil.which(t) is None]
    if missing:
        offer_install_missing(missing, non_interactive=non_interactive)

    ok(f"git   {run(['git', '--version']).stdout.strip()}")
    ok(f"gh    {run(['gh', '--version']).stdout.strip().splitlines()[0]}")

    login, blob = gh_auth_state()
    if login is None and interactive_gh_login():
        login, blob = gh_auth_state()          # 授权完再验一次
    if login is None:
        print()
        for line in login_help_lines():
            print("      " + line)
        die("GitHub CLI 未登录，自动授权也没能完成。\n"
            "      修好上面的任一条后重跑本脚本即可。")

    ok(f"gh 已登录：{C_BOLD}{login}{C_RESET}")

    # 作用域检查：不依赖引号格式，按词匹配
    scopes = re.search(r"Token scopes:\s*(.+)", blob)
    if scopes:
        have = set(re.findall(r"[A-Za-z_][A-Za-z0-9_:\-]*", scopes.group(1)))
        lack = [s for s in ("repo", "workflow") if s not in have]
        if lack:
            die(f"gh 令牌缺少权限：{', '.join(lack)}\n"
                f"      请执行：gh auth refresh -s repo,workflow\n"
                f"      （没有 workflow 权限时，git 无法推送 .github/workflows/ 下的文件）")
        ok("令牌权限：repo / workflow 齐备")

    # 关键：确保 git 能用 gh 的凭据推 HTTPS 仓库。
    # 少了这一步，没配过凭据助手的机器上 git push 会要求输入用户名/密码。
    p = run(["gh", "auth", "setup-git"], check=False)
    if p.returncode == 0:
        ok("git 凭据助手：已接入 gh")
    else:
        warn("gh auth setup-git 执行失败；若推送时报认证错误，请手动运行它")

    return login


def gh_login_name():
    return run(["gh", "api", "user", "-q", ".login"]).stdout.strip()


# ---------------------------------------------------------------- 凭据

def credential_candidates():
    return [
        os.path.expandvars(r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"),
        os.path.expandvars(r"%APPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"),
        os.path.expanduser("~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
        os.path.expanduser("~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
    ]


def load_credential(explicit):
    """返回 (phone, at, rt, source) 或 None。只读本地文件，不联网。"""
    if explicit:
        parts = explicit.split(":")
        if len(parts) == 3:
            return parts[0].strip(), parts[1].strip(), parts[2].strip(), "--token 参数"
        if len(parts) == 2:
            return parts[0].strip(), "", parts[1].strip(), "--token 参数"
        return "", explicit.strip(), "", "--token 参数"

    for path in credential_candidates():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            warn(f"读取失败（跳过）：{path} — {exc}")
            continue

        acc = data.get("account") or {}
        auth = data.get("auth") or {}
        phone = str(acc.get("phoneNumber") or acc.get("uin") or "").strip()
        at = str(auth.get("accessToken") or "").strip()
        rt = str(auth.get("refreshToken") or "").strip()
        if rt:
            return phone, at, rt, path
        warn(f"文件里没有 refreshToken（跳过）：{path}")
    return None


def jwt_exp(token):
    """从 JWT 里取 exp 时间戳（不验签，仅用于提前预警）。解析失败返回 None。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def describe_expiry(label, token):
    """返回 (剩余天数 或 None, 提示文本)。"""
    exp = jwt_exp(token)
    if exp is None:
        return None, f"{label} 有效期未知"
    days = (exp - time.time()) / 86400
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp))
    if days < 0:
        return days, f"{label} {C_ERR}已过期{C_RESET}（{when}）"
    return days, f"{label} 剩余 {days:.0f} 天（至 {when}）"


def read_credential_or_die(explicit):
    cred = load_credential(explicit)
    if cred is None:
        die("没找到 WorkBuddy 登录态文件。\n"
            "      请先在本机登录 WorkBuddy 桌面端；\n"
            "      或用 --token \"手机号:AT:RT\" 手动指定。")
    phone, at, rt, source = cred
    if not rt:
        die("没有拿到 refreshToken，无法部署。")
    return phone, at, rt, source


def mask_phone(phone):
    return (phone[:3] + "****" + phone[-2:]) if len(phone) > 5 else (phone or "(未标注)")


# ---------------------------------------------------------------- 调度换算

def parse_schedule(text):
    """'07:00,12:00,23:30' → [('0 23 * * *', '北京 07:00'), ...]（北京时间 → UTC）"""
    out = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m:
            die(f"时间格式不对：{raw}（应为 HH:MM，例如 07:00）")
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            die(f"时间越界：{raw}")
        out.append((f"{minute} {(hour - 8) % 24} * * *", f"北京 {hour:02d}:{minute:02d}"))
    if not out:
        die("--schedule 没有解析出任何时间点")
    return out


HARDEN_MARK = "hardened-by-deployer"
# 各改动点用独立标记，避免互相误判「已经改过」
CHECKOUT_MARK = HARDEN_MARK + ":checkout"
PERSIST_MARK = HARDEN_MARK + ":persist"

# 加固后的令牌回写步骤。
# 原版是裸 `git push`：一旦被拒（并发提交 / 分支保护），
# 本次已轮换的 refreshToken 就丢失了，而服务端旧 RT 通常已作废 → 账号需要人工重新登录。
HARDENED_PERSIST = '''      - name: Persist token state (private repo only)
        # {MARK}: 推送被拒时自动同步远端并重试，避免"已轮换的令牌丢失"
        if: always() && github.event.repository.private == true
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -f wb_refresh_tokens.json 2>/dev/null || true
          if git diff --cached --quiet; then
            echo "令牌状态无变化"
            exit 0
          fi
          git commit -m "chore: 更新令牌状态 [skip ci]"
          for i in 1 2 3 4 5; do
            if git push; then
              echo "✅ 令牌状态已推送"
              exit 0
            fi
            echo "第 $i 次推送被拒，同步远端后重试..."
            git fetch --unshallow origin main 2>/dev/null || git fetch origin main
            git merge -X ours --no-edit -m "chore: 合并远端令牌状态 [skip ci]" origin/main || true
            sleep 5
          done
          echo "::error::令牌状态推送失败 —— 本次轮换的 refreshToken 未持久化，请检查分支保护规则，或本地执行 deploy.py --force-secret 重置"
          exit 1
'''.replace("{MARK}", PERSIST_MARK)


def _harden_checkout(text):
    """给 actions/checkout 加 fetch-depth: 0，让回写步骤能正常合并远端。"""
    if CHECKOUT_MARK in text or "fetch-depth" in text:
        return text, False
    needle = """      - name: Checkout
        uses: actions/checkout@v4
"""
    if needle not in text:
        return text, False
    repl = needle + f"""        with:
          fetch-depth: 0   # {CHECKOUT_MARK}: 回写令牌状态时需要完整历史来合并
"""
    return text.replace(needle, repl, 1), True


def _harden_persist(text):
    """用带重试的版本替换裸 git push 的令牌回写步骤。"""
    if PERSIST_MARK in text:
        return text, False

    marker = "      - name: Persist token state"
    idx = text.find(marker)
    if idx < 0:
        return text, False

    for line in text[idx:].split("\n")[1:]:
        # 后面还有别的 step → 不敢整段替换，交给人工
        if line.startswith("      - name:") or line.startswith("      - uses:"):
            return text, None

    return text[:idx] + HARDENED_PERSIST, True


def patch_workflow(path, crons):
    """
    原样保留上游工作流，只做三处必要改动（全部幂等）：
      1. 替换 schedule 段为指定定时
      2. 透传 BARK_URL（上游 README 声明了该 Secret 但 env 未接线）
      3. 加固令牌回写步骤 + checkout 拉全量历史
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, i, replaced = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.startswith("  schedule:"):
            out.append(line)
            i += 1
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].strip() == ""):
                i += 1
            for expr, label in crons:
                out.append(f'    - cron: "{expr}"     # {label}')
            replaced = True
            continue
        out.append(line)
        i += 1

    text = "\n".join(out)

    # 逐个补齐 env 透传。
    # 关键：每个变量必须**独立判断**，不能写成「某个变量不在就插入一整块」——
    # 那样一旦该变量已存在，条件就永远为假，后续新增的变量再也进不去。
    env_add = []
    if "BARK_URL" not in text:
        env_add.append("          BARK_URL: ${{ secrets.BARK_URL }}")

    anchor = "          PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}"
    if env_add and anchor in text:
        text = text.replace(anchor, anchor + "\n" + "\n".join(env_add), 1)

    # 清理历史版本加过、现已撤回的环境变量（QQ_BOT_* 渠道已下线）
    dropped = []
    for name in ("QQ_BOT_URL", "QQ_BOT_TARGET", "QQ_BOT_TOKEN"):
        pat = re.compile(r"(?m)^\s*" + name + r":\s*\$\{\{\s*secrets\." + name + r"\s*\}\}\s*\n")
        text, n = pat.subn("", text)
        if n:
            dropped.append(name)
    if dropped:
        warn(f"已移除历史遗留的环境变量：{', '.join(dropped)}")

    text, _ = _harden_checkout(text)
    text, persist_ok = _harden_persist(text)
    if persist_ok is None:
        warn("令牌回写步骤后面还有其它步骤，已跳过加固（请人工检查）")

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return replaced


# ---------------------------------------------------------------- GitHub 仓库

def repo_exists(full_name):
    return run(["gh", "repo", "view", full_name, "--json", "name"], check=False).returncode == 0


def repo_is_private(full_name):
    p = run(["gh", "repo", "view", full_name, "--json", "isPrivate"], check=False)
    if p.returncode != 0:
        return None
    try:
        return bool(json.loads(p.stdout)["isPrivate"])
    except Exception:
        return None


def repo_has_store(full_name):
    """仓库里是否已有令牌库。有则说明 Actions 已接管续期，Secret 无需再动。"""
    return run(["gh", "api", f"repos/{full_name}/contents/wb_refresh_tokens.json"],
               check=False).returncode == 0


def fetch_repo_store(full_name):
    """从仓库里取令牌库（用于比较新旧，不打印内容）。取不到返回 None。"""
    p = run(["gh", "api", f"repos/{full_name}/contents/wb_refresh_tokens.json",
             "--jq", ".content"], check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        raw = base64.b64decode(p.stdout.strip()).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def store_newest_exp(store):
    """取令牌库里最新的 exp 时间戳。"""
    newest = 0
    for item in (store or {}).values():
        if not isinstance(item, dict):
            continue
        for key in ("access_token", "refresh_token"):
            exp = jwt_exp(item.get(key) or "")
            if exp:
                newest = max(newest, exp)
    return newest


def remove_repo_store(workdir):
    """
    把令牌库从仓库里删掉。
    必须这么做 --force-secret 才有意义：脚本的 _bootstrap_store() 在令牌库存在且非空时
    会直接 return，完全忽略环境变量，只覆盖 Secret 是没有任何效果的。
    """
    name = "wb_refresh_tokens.json"
    path = os.path.join(workdir, name)
    tracked = git(["ls-files", "--error-unmatch", name], workdir, check=False)
    if tracked.returncode != 0 and not os.path.isfile(path):
        return False
    if tracked.returncode == 0:
        git(["rm", "-f", "--quiet", name], workdir, check=False)
    if os.path.isfile(path):
        os.remove(path)
    return True


def create_or_reuse_repo(full_name, description):
    target = f"https://github.com/{full_name}.git"
    if repo_exists(full_name):
        if repo_is_private(full_name) is False:
            die(f"仓库 {full_name} 已存在且是 {C_BOLD}公开{C_RESET} 的。\n"
                f"      令牌文件会写进该仓库，绝不能公开。\n"
                f"      请先改为 Private，或用 --repo-name 换个名字。")
        info(f"仓库已存在（Private），复用：{full_name}")
        return target, False

    run(["gh", "repo", "create", full_name, "--private",
         "--description", description], check=True)
    if repo_is_private(full_name) is not True:
        die("仓库创建后可见性校验未通过，请手动确认它是 Private 再继续")
    ok(f"已创建私有仓库：{full_name}")
    return target, True


# ---------------------------------------------------------------- 本地 git 流程

def clear_origin_refs(workdir):
    """
    清掉 refs/remotes/origin/* 的本地位图缓存。

    为什么必须做：把 origin 从「A 仓库」改指到「B 仓库」时，git 不会删除旧的
    remote-tracking 引用。于是 `git rev-parse origin/main` 依然能命中 A 的历史，
    脚本会误判「B 已有 main」，进而把 A 的提交（含令牌库）继承下来并推到 B。
    这是真实发生过的泄露路径，必须显式清理。
    """
    refs = git(["for-each-ref", "--format=%(refname)", "refs/remotes/origin"],
               workdir, check=False).stdout.split()
    for ref in refs:
        if ref.strip():
            git(["update-ref", "-d", ref.strip()], workdir, check=False)
    return len(refs)


def remote_has_main(workdir):
    """直接问远端有没有 main，不依赖本地可能过期的 remote-tracking 引用。"""
    p = git(["ls-remote", "--heads", "origin", "main"], workdir, check=False)
    return p.returncode == 0 and "refs/heads/main" in (p.stdout or "")


def prepare_repo(workdir, target_url):
    """保证 workdir 是干净的、origin 指向目标仓库的 git 仓库。"""
    if not os.path.isdir(os.path.join(workdir, ".git")):
        # 空目录是合法的（--ephemeral 会先建目录，git clone 也能克隆进空目录）
        if os.path.exists(workdir) and os.listdir(workdir):
            die(f"目标目录已存在且非空、也不是 git 仓库：{workdir}\n"
                f"      请先移走它，或用 --workdir 指定别的位置")
        info("克隆上游仓库（全量，避免浅克隆推送失败）...")
        run(["git", "clone", UPSTREAM, workdir])

    remotes = set(git(["remote"], workdir).stdout.split())
    if "origin" in remotes:
        url = git(["remote", "get-url", "origin"], workdir).stdout.strip()
        if "L0NE-6/WorkBuddy-Daily" in url:
            if "upstream" in remotes:
                git(["remote", "remove", "origin"], workdir)
            else:
                git(["remote", "rename", "origin", "upstream"], workdir)
            remotes = set(git(["remote"], workdir).stdout.split())

    if "upstream" not in remotes:
        git(["remote", "add", "upstream", UPSTREAM], workdir)

    if "origin" not in remotes:
        git(["remote", "add", "origin", target_url], workdir)
    else:
        cur = git(["remote", "get-url", "origin"], workdir).stdout.strip()
        if cur != target_url:
            git(["remote", "set-url", "origin", target_url], workdir)
            n = clear_origin_refs(workdir)
            if n:
                warn(f"origin 已改指到 {target_url}，同时清掉 {n} 个旧远端引用"
                     f"（否则会误继承上一个远端的历史）")

    return "origin" in set(git(["remote"], workdir).stdout.split())


def history_has_store(workdir):
    """本地 HEAD 历史里是否已跟踪了令牌库（说明这份克隆被别人用过）。"""
    p = git(["ls-tree", "-r", "--name-only", "HEAD"], workdir, check=False)
    return p.returncode == 0 and "wb_refresh_tokens.json" in p.stdout.split()


def scrub_foreign_store(workdir):
    """
    新建仓库前的清场：把「别人用过的那份克隆」里夹带的令牌清掉。

    背景：令牌库被 Actions 以 git add -f 提交，所以它会进入提交历史。
    如果把整个目录（含 .git）拷给别人，别人的首次 push 会把这份历史一起推上去，
    他的仓库里就会出现**你的** refreshToken —— 实测已复现。
    """
    had_history = history_has_store(workdir)
    removed_local = []

    if had_history:
        git(["fetch", "upstream", "main", "--quiet"], workdir, check=False)
        probe = git(["rev-parse", "--verify", "--quiet", "upstream/main"], workdir, check=False)
        if probe.returncode != 0 or not probe.stdout.strip():
            die("本地历史里夹带了令牌库，但取不到上游干净历史，无法安全继续。\n"
                "      请删掉这个目录重新克隆上游后再试。")
        git(["reset", "--hard", "HEAD"], workdir, check=False)
        git(["checkout", "-B", "main", "upstream/main"], workdir)
        git(["reset", "--hard", "upstream/main"], workdir)

    # 工作区里未被跟踪（.gitignore 挡住）的凭据副本也要清掉，
    # 否则脚本本地跑起来会误用别人的令牌。
    for name in ("wb_refresh_tokens.json", "WORKBUDDY_ACCESS_TOKEN.txt", "wb_login_result.json"):
        path = os.path.join(workdir, name)
        if os.path.isfile(path):
            os.remove(path)
            removed_local.append(name)

    if had_history:
        warn("检测到这份克隆里夹带了他人的令牌库，已重置为上游干净历史")
    if removed_local:
        warn(f"已清理工作区残留凭据：{', '.join(removed_local)}")
    return had_history or bool(removed_local)


def sync_with_origin(workdir):
    """
    远端已有 main 时，把本地强制对齐到 origin/main（继承历史 + 令牌库 + 运维文档），
    否则以本地/上游 HEAD 为起点建立 main。
    """
    # 用 ls-remote 直接问远端，而不是 rev-parse 本地可能过期的 remote-tracking 引用
    if remote_has_main(workdir):
        git(["fetch", "origin", "main", "--quiet"], workdir, check=False)
        dirty = git(["status", "--porcelain"], workdir).stdout.strip()
        if dirty:
            warn("本地有未提交改动，将重置为远端版本（本目录由部署器管理）")
        git(["reset", "--hard", "HEAD"], workdir, check=False)
        git(["checkout", "-B", "main", "origin/main"], workdir)
        git(["reset", "--hard", "origin/main"], workdir)
        info("已对齐到远端 main（继承令牌库与历史）")
        return True

    # 远端为空 = 本次是「新建仓库」，绝不能把别人克隆里夹带的令牌带过去
    scrub_foreign_store(workdir)

    cur = git(["rev-parse", "--abbrev-ref", "HEAD"], workdir).stdout.strip()
    if cur != "main":
        git(["checkout", "-B", "main"], workdir)
    info("远端为空，以干净的上游代码为起点建立 main")
    return False


def commit_if_dirty(workdir, message):
    """
    先 add 再判断是否有暂存内容，最后才 commit。
    不能反过来用 `git status` 判断 —— 在 Windows + autocrlf 下，
    重写一个内容相同的文件（仅换行归一化差异）会让 status 与 add 的结论不一致，
    结果就是「以为有改动 → 真的 commit → nothing to commit」硬报错。
    """
    git(["add", "-A"], workdir)
    staged = git(["diff", "--cached", "--quiet"], workdir, check=False)
    if staged.returncode == 0:
        return False
    git(["-c", "user.name=WorkBuddy Deployer",
         "-c", "user.email=noreply@local", "commit", "-m", message], workdir)
    ok(f"已提交本地改动：{message}")
    return True


def robust_rmtree(path):
    """
    删除目录树。

    Windows 上 git 的 .git/objects/** 是**只读**文件（mode 0o444），
    直接 shutil.rmtree 会抛 `PermissionError: [WinError 5] 拒绝访问`，
    而且 shutil.rmtree 的 onerror 回调在新版 Python 里已被弃用 ——
    所以这里不依赖回调，先整棵树递归去掉只读位，再删。
    """
    if not os.path.exists(path):
        return True

    for dirpath, dirnames, filenames in os.walk(path):
        for name in list(filenames) + list(dirnames):
            full = os.path.join(dirpath, name)
            try:
                os.chmod(full, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except Exception:
                pass
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except Exception:
        pass

    for _ in range(3):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(0.4)
    return not os.path.exists(path)


def push(workdir):
    p = git(["push", "-u", "origin", "HEAD:main"], workdir, check=False)
    if p.returncode == 0:
        return
    blob = (p.stderr or "") + (p.stdout or "")
    if "index-pack failed" in blob or "shallow" in blob:
        warn("浅克隆导致推送失败，补全历史后重试...")
        git(["fetch", "--unshallow", "upstream"], workdir, check=False)
        p = git(["push", "-u", "origin", "HEAD:main"], workdir, check=False)
    if p.returncode != 0 and ("non-fast-forward" in blob or "rejected" in blob):
        warn("远端有新提交，自动合并后重试...")
        git(["fetch", "origin", "main"], workdir, check=False)
        m = git(["merge", "--no-edit", "origin/main"], workdir, check=False)
        if m.returncode != 0:
            git(["merge", "--abort"], workdir, check=False)
            die("与远端 main 冲突且无法自动合并。\n"
                "      请手动处理后再运行，或改用 --workdir 指向已有部署目录。")
        p = git(["push", "-u", "origin", "HEAD:main"], workdir, check=False)
    if p.returncode != 0:
        die(f"推送失败：{(p.stderr or p.stdout).strip()[:400]}")


# ---------------------------------------------------------------- 脚本补丁

NOTIFY_MARK = HARDEN_MARK + ":notify-stdout"

# 目标形态：上游的 send_notify_all + 一个兜底打印。
# 上游原本「只推不打印」—— 没配任何推送渠道时运行报告完全不会输出，
# CI 日志里只剩零散任务行，看不到「各账号运行报告 / 总计」。
CANONICAL_SEND_ALL = f'''# {NOTIFY_MARK}
def send_notify_all(title, content):
    """推送通知到所有已配置的渠道（PushPlus + Bark）。"""
    r1 = send_notify(title, content)
    r2 = _bark_notify(title, content)
    if not (r1 or r2):
        # 没有任何推送渠道可用时，把报告打到 stdout，方便在 CI 日志里直接看
        print("\\n" + content)
    return r1 or r2
'''

# 早期版本注入过的 QQ 通知块标记。该渠道已按用户要求撤回，这里负责清理残留。
_OBSOLETE_QQ_MARKERS = (
    "# " + HARDEN_MARK + ":qq-notify",
    "def _qq_notify(title, content):",
)


def _strip_obsolete_qq(text):
    """删除历史版本注入的 _qq_notify 块。返回 (新文本, 是否改动)。"""
    for marker in _OBSOLETE_QQ_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        end = text.find("def send_notify_all(title, content):", idx)
        if end < 0:
            return text, False
        return text[:idx] + text[end:], True
    return text, False


def patch_script(workdir):
    """
    给上游 workbuddy_daily.py 打最小补丁（幂等）：
      1. 清掉早期版本注入的 _qq_notify / QQ 渠道（已撤回）
      2. 把 send_notify_all 规范成「没有推送渠道时把报告打到 stdout」

    只动这一个函数，便于日后 merge upstream。
    """
    path = os.path.join(workdir, "workbuddy_daily.py")
    if not os.path.isfile(path):
        return False, "找不到 workbuddy_daily.py"

    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    original = text

    text, _ = _strip_obsolete_qq(text)

    start = text.find("def send_notify_all(title, content):")
    if start < 0:
        return False, "找不到 send_notify_all，上游可能已改结构"

    # 函数体范围：到下一个顶层 def/class，或文件末尾
    m = re.search(r"\n(?=(?:def |class |if __name__))", text[start:])
    end = start + (m.start() + 1 if m else len(text))

    # 去掉紧贴函数上方的旧标记注释，避免残留孤儿行。
    # 这里必须做两次 rstrip：先剥掉尾部空行才能匹配到标记行，
    # 匹配后再剥一次，否则每次运行都会多留一行空行 —— 文件会越跑越长（不幂等）。
    head = text[:start].rstrip("\n")
    hm = re.search(r"\n?# " + re.escape(HARDEN_MARK) + r":[\w\-]+$", head)
    if hm:
        head = head[:hm.start()]
    head = head.rstrip("\n")

    new_text = head + "\n\n\n" + CANONICAL_SEND_ALL + "\n" + text[end:]

    if new_text == original:
        return False, "已是目标形态"

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_text)
    return True, "已撤回 QQ 渠道，并规范报告输出"
# ---------------------------------------------------------------- 运维文档

OPS_TEMPLATE = """# 运维说明（私有部署实例）

> 上游项目：https://github.com/L0NE-6/WorkBuddy-Daily （MIT）
> 本仓库只是私有部署副本，代码版权归上游作者。**本仓库必须保持 Private。**
> 本文件由一键部署器生成。

---

## 一、当前部署状态

| 项 | 值 |
| :--- | :--- |
| 仓库 | https://github.com/{full_name} |
| 可见性 | Private |
| 运行方式 | GitHub Actions / ubuntu-latest / Python 3.11 |
| 定时（北京时间） | {schedule} |
| Secret | `WORKBUDDY_REFRESH_TOKEN`（格式 `手机号:AT:RT`） |
| 上游 remote | `upstream` → L0NE-6/WorkBuddy-Daily |

**定时规则**（`.github/workflows/workbuddy.yml`，cron 用 UTC，北京 = UTC+8）

| 北京时间 | cron | 备注 |
| :---: | :--- | :--- |
{cron_rows}

> 夜猫子活动只在 **23:00–08:00** 期间计数，所以必须保留一个落在该窗口内的定时点。

---

## 二、日常操作

```bash
# 看运行记录
gh run list  --repo {full_name} --limit 5

# 看某次日志
gh run watch <run-id> --repo {full_name}
gh run view  <run-id> --repo {full_name} --log

# 立刻手动跑一次
gh workflow run workbuddy.yml --repo {full_name} --ref main
```

本地调试（在已登录 WorkBuddy 的电脑上）：

```bash
export WORKBUDDY_REFRESH_TOKEN="手机号:AT:RT"
python workbuddy_daily.py --query      # 只读：查积分/用量/成长
python workbuddy_daily.py --refresh    # 只刷新 Token
python workbuddy_daily.py              # 全流程
```

拉上游更新：

```bash
git fetch upstream && git merge upstream/main && git push
```

---

## 三、令牌机制（为什么不用管它）

- 脚本用的不是单个 `accessToken`，而是 **`AT` + `RT`（refreshToken）**。
- 触发条件：**距上次刷新 > 10 天** 或 **AT 7 天内即将过期** → 用 RT 换发新 AT+RT 并落盘。
- 工作流在**私有仓库**中会把 `wb_refresh_tokens.json` 提交回仓库持久化（`git add -f`），
  所以令牌状态跟着仓库走，**无需人工续期**。
- 附带好处：令牌库约每 10 天产生一次提交，让仓库保持活跃，
  **避免 GitHub 因 60 天无活动自动停用定时工作流**。

> ⚠️ `wb_refresh_tokens.json` 里是明文 RT，等同账号控制权。
> **一旦仓库转为 Public，令牌立刻泄露。**

---

## 四、故障排查

| 现象 | 原因 / 处理 |
| :--- | :--- |
| 日志报 `TOKEN_EXPIRED` / 401 / 403 | RT 失效。重新登录桌面端取新凭据，更新 Secret（见下） |
| Secret 更新后没生效 | Actions 每次运行都读最新 Secret，稍等 1 分钟再触发即可 |
| 定时到点没跑 | GitHub schedule 高峰期会延迟几分钟到几十分钟，`gh run list` 确认；长时间无记录可手动触发 |
| 「发现应用 / 企鹅教师助手」长期未完成 | 判据上报后可能跨日结算，多观察几天 |
| 夜猫子活动始终未完成 | 确认 23:30 那条 cron 还在，且当天确实跑过 |
| 公益专家 / 学生认证 / 关注公众号 | **设计上无法自动完成**：需真实捐款 / 微信实名 / 微信扫码 |

### 更新令牌（RT 失效时）

1. 在已登录的电脑上运行 `python workbuddy_login.py`（短信验证码登录）拿到新的 `手机号:AT:RT`；
   或直接打开凭据文件取 `accessToken` / `refreshToken`。
2. 写入 Secret：
   ```bash
   printf '%s' '手机号:AT:RT' | gh secret set WORKBUDDY_REFRESH_TOKEN --repo {full_name}
   ```
3. 若仓库里还留着 `wb_refresh_tokens.json`，脚本会优先用它而忽略 Secret ——
   这种情况先删掉该文件再跑一次，让它从 Secret 重新自举。

---

## 五、可选：推送通知

当前若未配置推送，报告只写在工作流日志里。要每天收到通知：

| Secret | 用途 |
| :--- | :--- |
| `PUSHPLUS_TOKEN` | PushPlus → 微信，https://sct.ftqq.com 扫码取 token |
| `BARK_URL` | Bark → iOS，如 `https://api.day.app/xxxxxxxx` |

```bash
gh secret set PUSHPLUS_TOKEN --repo {full_name}
gh secret set BARK_URL      --repo {full_name}
```


---

## 六、重新部署 / 更新定时

用同一个一键部署器再跑一次即可（幂等）：

```bash
python deploy.py                       # 复用已有仓库，只同步改动
python deploy.py --schedule 09:10,23:30  # 改定时
python deploy.py --check               # 只看状态
python deploy.py --force-secret        # 用本机新凭据重置令牌状态
```

---

## 七、部署器对上游工作流做的改动

除定时以外，部署器只改了两处，都是幂等的，且带 `hardened-by-deployer` 注释便于识别：

| 位置 | 改动 | 原因 |
| :--- | :--- | :--- |
| `Run WorkBuddy` 的 env | 补上 `BARK_URL` | 上游 README 声明了该 Secret，但工作流 env 没接线，Bark 推送实际永远不会生效 |
| `workbuddy_daily.py` | 无任何推送渠道时把报告打到 stdout | 上游原本「只推不打印」，CI 日志里看不到运行汇总 |
| `Checkout` 步骤 | 加 `fetch-depth: 0` | 令牌回写需要完整历史才能合并远端 |
| `Persist token state` 步骤 | 裸 `git push` → **重试 5 次 + 合并远端后重推**，失败时 `::error::` 明确报错 | **关键**：原版 push 一旦被拒（并发提交 / 分支保护），本次已轮换的 refreshToken 就丢了；而服务端旧 RT 通常已作废 → 账号会需要人工重新登录。这是最需要防的故障 |

### 如果令牌状态推送持续失败

说明仓库有分支保护/规则集不允许 Actions 直接推 main。二选一：

1. 在仓库 Settings → Rules 里给 `github-actions[bot]` 开白名单；
2. 或接受令牌不持久化，改用「每次运行都从 Secret 取」，此时需自己保证 Secret 里的 RT 不过期。
"""


GENERATED_MARK = "本文件由一键部署器生成"

def write_ops_doc(workdir, full_name, crons, schedule):
    """生成运维文档。只覆盖本工具生成过的版本，不碰手写的文档。"""
    rows = [f"| {label.replace('北京 ', '')} | `{expr}` | "
            f"{'夜猫子窗口' if '23:' in label else '日常'} |"
            for expr, label in crons]
    content = OPS_TEMPLATE.format(
        full_name=full_name, schedule=schedule, cron_rows="\n".join(rows)
    )
    path = os.path.join(workdir, OPS_DOC)

    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if old == content:
            return False, False
        if GENERATED_MARK not in old:
            return False, True  # 手写文档，保留不动
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return True, False


# ---------------------------------------------------------------- 运行与验证

def _recent_run_ids(full_name, limit=20):
    q = run(["gh", "run", "list", "--repo", full_name, "--workflow", "workbuddy.yml",
             "--limit", str(limit), "--json", "databaseId"], check=False)
    if q.returncode != 0 or not q.stdout.strip():
        return set()
    try:
        return {r["databaseId"] for r in json.loads(q.stdout)}
    except Exception:
        return set()


def trigger_and_watch(full_name, timeout_min=15):
    before = _recent_run_ids(full_name)

    # 新建仓库 / 刚推上去的工作流，GitHub 需要几秒到一分钟注册，
    # 这期间 `gh workflow run` 会报 could not find any workflows —— 所以要重试。
    last_err = ""
    triggered = False
    for attempt in range(1, 7):
        p = run(["gh", "workflow", "run", "workbuddy.yml", "--repo", full_name, "--ref", "main"],
                check=False)
        if p.returncode == 0:
            triggered = True
            break
        last_err = ((p.stderr or "") + (p.stdout or "")).strip()
        if attempt < 6:
            wait = 4 * attempt
            info(f"触发失败（第 {attempt} 次），{wait}s 后重试…")
            time.sleep(wait)

    if not triggered:
        warn("未能自动触发工作流（新仓库注册工作流通常需要几十秒）")
        warn(f"仓库本身已就绪，稍后手动跑一次即可：\n"
             f"        gh workflow run workbuddy.yml --repo {full_name} --ref main")
        warn(f"最后错误：{last_err[:200]}")
        return None

    # 只认「触发前不存在的 run」，否则新运行还没注册时可能抓到上一次的旧记录
    run_id = None
    for _ in range(30):
        time.sleep(4)
        new_ids = _recent_run_ids(full_name) - before
        if new_ids:
            run_id = max(new_ids)
            break
    if not run_id:
        warn(f"没取到运行 ID，请手动查看：https://github.com/{full_name}/actions")
        return None

    ok(f"已触发运行 #{run_id}，等待完成（最长 {timeout_min} 分钟）...")
    info(f"实时进度（可随时打开看）：https://github.com/{full_name}/actions/runs/{run_id}")
    info("账号越多跑得越久，通常 1 个账号约 1 分钟")

    deadline = time.time() + timeout_min * 60
    started_at = time.time()
    last_tick = -99
    try:
        while time.time() < deadline:
            q = run(["gh", "run", "view", str(run_id), "--repo", full_name,
                     "--json", "status,conclusion"], check=False)
            if q.returncode == 0 and q.stdout.strip():
                try:
                    d = json.loads(q.stdout)
                    if d.get("status") == "completed":
                        return run_id, d.get("conclusion")
                except Exception:
                    pass
            # 每 20 秒给一次心跳，否则屏幕上几分钟不动，看着像卡死了
            elapsed = int(time.time() - started_at)
            if elapsed - last_tick >= 20:
                last_tick = elapsed
                print(f"      {C_DIM}· 已等待 {elapsed}s，运行仍在进行中…"
                      f"（Ctrl+C 可退出等待，后台不受影响）{C_RESET}")
            time.sleep(10)
        warn("等待超时，任务仍在后台运行")
        return run_id, None
    except KeyboardInterrupt:
        print()
        warn("已退出等待 —— 任务仍在 GitHub 上正常跑，不影响结果")
        info(f"稍后看结果：https://github.com/{full_name}/actions/runs/{run_id}")
        info(f"或在本目录执行：python deploy.py --check")
        return run_id, None


def show_run_summary(full_name, run_id):
    p = run(["gh", "run", "view", str(run_id), "--repo", full_name, "--log"], check=False)
    if p.returncode != 0 or not p.stdout:
        return
    keys = ("签到", "checkin", "💰", "📊", "🌱", "🎁", "🏁", "🐾", "📦", "🏅",
            "🏫", "🎰", "🩹", "⚠️", "❌", "ERROR")
    lines = []
    for raw in p.stdout.split("\n"):
        body = (raw.split("\t") or [raw])[-1]
        body = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ", "", body).rstrip()
        # 跳过 ASCII 艺术横幅（“║ 🌱 WorkBuddy 全能脚本 ║”这类会被关键词命中，但没信息量）
        if not body or any(c in body for c in "║╔╗╚╝"):
            continue
        if any(k in body for k in keys):
            lines.append(body)
    if lines:
        print()
        print(f"    {C_BOLD}本次运行关键日志：{C_RESET}")
        for line in lines[-35:]:
            print(f"      {line}")


# ---------------------------------------------------------------- 多账号

def store_to_secret_lines(store):
    """把令牌库拼成 Secret 需要的多行格式：每行 手机号:AT:RT"""
    lines = []
    for phone, v in (store or {}).items():
        at = (v or {}).get("access_token", "")
        rt = (v or {}).get("refresh_token", "")
        if rt:
            lines.append(f"{phone}:{at}:{rt}")
    return "\n".join(lines)


def merge_account_into_store(store, phone, at, rt):
    """
    把本机账号并入令牌库。返回 (新库, 状态)。
    状态取值：'new' 新增 / 'updated' 已更新 / 'kept' 仓库里的更新、保持不动。

    护栏：同一个手机号已存在时，比较两边令牌的 exp ——
    仓库里的令牌由 Actions 持续轮换，通常比本机登录态文件更新。
    直接用本机覆盖会造成令牌回退（旧 RT 已作废 → 下次 401），
    所以这里只在「本机更新」时才覆盖。
    """
    store = dict(store or {})
    key = phone or "account"

    if key not in store:
        status = "new"
    else:
        status = "updated"
        old_item = store.get(key) or {}
        # 令牌内容和仓库里完全一致时，连 updated 时间戳都不动 ——
        # 否则每次运行都会产生一个只为改时间戳的空提交。
        if (old_item.get("refresh_token") == rt
                and old_item.get("access_token") == at):
            return store, "same"
        old_exp = max([e for e in (jwt_exp(old_item.get("access_token") or ""),
                                  jwt_exp(old_item.get("refresh_token") or "")) if e] or [0])
        new_exp = max([e for e in (jwt_exp(at), jwt_exp(rt)) if e] or [0])
        # 仓库里的 exp 已知、且「本机更新」这件事证明不了时，一律保持仓库的。
        # 否则一个解析不出 exp 的令牌会静默把仓库里有效的令牌覆盖掉。
        if old_exp and (not new_exp or old_exp > new_exp):
            return store, "kept"

    store[key] = {
        "refresh_token": rt,
        "access_token": at,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }
    return store, status


def store_account_list(store):
    """返回可打印的账号摘要列表。"""
    out = []
    for phone, v in (store or {}).items():
        out.append(f"{mask_phone(phone)}（更新于 {(v or {}).get('updated', '?')}）")
    return out


def commit_store(workdir, store, message):
    """
    把令牌库写进文件、提交并推送。
    注意：必须由本函数负责落盘 —— 早期版本只做 git add，
    结果合并后的内容根本没写进文件，静默变成「内容无变化」。
    wb_refresh_tokens.json 被 .gitignore 挡住，必须 git add -f。
    """
    path = os.path.join(workdir, "wb_refresh_tokens.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=1)
    git(["add", "-f", "wb_refresh_tokens.json"], workdir, check=False)
    q = git(["diff", "--cached", "--quiet"], workdir, check=False)
    if q.returncode == 0:
        return False
    git(["-c", "user.name=WorkBuddy Deployer",
         "-c", "user.email=noreply@local", "commit", "-m", message], workdir)
    push(workdir)
    return True


def show_menu(args):
    """
    启动菜单 —— 打开（或双击）后先选做什么，选了才动手。

    返回选中的动作：'deploy' / 'add' / 'check' / 'quit'
    """
    while True:
        print()
        print(f"  {C_BOLD}请选择要执行的操作{C_RESET}")
        print(f"    {C_INFO}1){C_RESET} 开始部署 / 修改配置")
        print(f"    {C_INFO}2){C_RESET} 添加账号（先在 WorkBuddy 客户端切换到新账号并登录）")
        print(f"    {C_INFO}3){C_RESET} 查看当前部署状态")
        print(f"    {C_INFO}4){C_RESET} 退出")
        print()
        try:
            raw = input(f"    请输入序号 [1]：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if not raw:
            return "deploy"
        if raw in ("1", "2", "3", "4"):
            return {"1": "deploy", "2": "add", "3": "check", "4": "quit"}[raw]
        print(f"    {C_WARN}请输入 1 ~ 4{C_RESET}")


# ---------------------------------------------------------------- 交互向导

def _ask(prompt, default=""):
    """单行输入；回车用默认值。EOF/中断返回默认值。"""
    tail = f" {C_DIM}[{default}]{C_RESET}" if default else ""
    try:
        raw = input(f"    {prompt}{tail}：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return raw or default


def _ask_choice(prompt, options, default_idx=1):
    """options: [(label, value)]；返回被选中的 value。"""
    print(f"    {C_BOLD}{prompt}{C_RESET}")
    for i, (label, _) in enumerate(options, 1):
        mark = f" {C_DIM}← 默认{C_RESET}" if i == default_idx else ""
        print(f"      {i}) {label}{mark}")
    while True:
        raw = _ask("请输入序号", str(default_idx))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        print(f"      {C_WARN}请输入 1 ~ {len(options)}{C_RESET}")


def _ask_yes(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    raw = _ask(f"{prompt} {C_DIM}({hint}){C_RESET}", "")
    if not raw:
        return default
    return raw.strip().lower().startswith("y")


def schedule_ok(text):
    """向导里用：不抛异常地校验定时格式。"""
    import re as _re
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = _re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            return False
    return bool(text and text.strip())


def run_wizard(args, login, workdir):
    """交互式配置向导。返回 True 表示继续部署，False 表示用户取消。"""
    step("交互式配置")
    print(f"    {C_DIM}直接回车 = 用默认值；Ctrl+C 可随时退出{C_RESET}")
    print()

    # --- 1) 仓库名 ---
    args.repo_name = _ask("GitHub 仓库名（建在你自己的账号下，私有）", args.repo_name)
    full_name = f"{args.owner or login}/{args.repo_name}"

    # --- 2) 签到时间 ---
    print()
    CUSTOM = "__custom__"
    preset = _ask_choice("每天什么时候签到（北京时间）？", [
        ("推荐：07:00 / 12:00 / 23:30（含夜猫子活动窗口）", DEFAULT_SCHEDULE),
        ("只早上跑一次：09:10", "09:10"),
        ("早晚各一次：09:10 / 23:30", "09:10,23:30"),
        ("自定义", CUSTOM),
    ], 1)
    if preset == CUSTOM:
        while True:
            raw = _ask("请输入时间，逗号分隔", DEFAULT_SCHEDULE)
            if schedule_ok(raw):
                args.schedule = raw
                break
            print(f"      {C_WARN}格式不对，示例：07:00,12:00,23:30{C_RESET}")
    else:
        args.schedule = preset
    print(f"      {C_OK}✓{C_RESET} 已选：{args.schedule}")

    # --- 3) 账号处理（仓库已有令牌库时才问）---
    print()
    existing = None
    if repo_exists(full_name):
        existing = fetch_repo_store(full_name)
    if existing:
        names = store_account_list(existing)
        print(f"    {C_BOLD}检测到仓库里已有 {len(existing)} 个账号：{C_RESET}")
        for n in names:
            print(f"      · {n}")
        print()
        print(f"    {C_DIM}说明：本机登录态里只有「当前登录账号」一份令牌。{C_RESET}")
        print(f"    {C_DIM}要加新账号，需要先在 WorkBuddy 桌面端切换到那个号并登录，再运行本脚本。{C_RESET}")
        print()
        action = _ask_choice("本次要怎么处理账号？", [
            ("追加/更新本机当前账号（不动其它账号）", "add"),
            ("只更新配置，不改账号", "keep"),
        ], 1)
        if action == "add":
            args.add_account = True
    else:
        info("仓库还不存在（或里面还没有令牌库），本次会创建并写入本机账号")

    # --- 4) 汇总确认 ---
    print()
    print(f"    {C_BOLD}即将执行{C_RESET}")
    print(f"      · 目标仓库   {full_name}（私有）")
    print(f"      · 签到时间   {args.schedule}（北京时间）")
    print(f"      · 工作目录   {'临时目录，跑完自动清理' if not args.persist else '固定目录'}")
    print(f"      · 令牌处理   {'追加/更新本机账号' if args.add_account else '沿用仓库现有令牌库'}")
    print(f"      · 验证运行   {'跳过' if args.no_run else '部署后立即跑一次并打印结果'}")
    print()
    if not _ask_yes("确认开始部署？", True):
        warn("已取消，未做任何改动")
        return False
    return True


# ---------------------------------------------------------------- 模式

def cmd_check(full_name):
    step("部署状态")
    if not repo_exists(full_name):
        warn(f"仓库 {full_name} 不存在 —— 还没部署过")
        return
    data = json.loads(run(["gh", "repo", "view", full_name,
                           "--json", "visibility,url,isPrivate"]).stdout)
    ok(f"仓库          {data['url']}")
    if data["isPrivate"]:
        ok("可见性        PRIVATE")
    else:
        warn(f"可见性        {data['visibility']}   {C_ERR}← 危险！必须改为 Private{C_RESET}")

    s = run(["gh", "secret", "list", "--repo", full_name], check=False)
    names = [l.split("\t")[0] for l in s.stdout.strip().split("\n") if l.strip()]
    ok(f"Secrets       {', '.join(names) if names else '（无）'}")

    w = run(["gh", "api", f"repos/{full_name}/actions/workflows",
             "-q", '.workflows[] | "\\(.name)  \\(.state)"'], check=False)
    if w.stdout.strip():
        for line in w.stdout.strip().splitlines():
            ok(f"工作流        {line}")

    r = run(["gh", "run", "list", "--repo", full_name, "--limit", "5"], check=False)
    if r.stdout.strip():
        print()
        print(f"    {C_BOLD}最近运行：{C_RESET}")
        for line in r.stdout.strip().split("\n"):
            if line.strip():
                print(f"      {line}")


def cmd_deploy(args, full_name, workdir, mode="默认"):
    # 注意：banner() 与 ensure_tools() 已在 main() 里先执行完。
    # 不能放回这里 —— full_name 依赖登录名，而登录名必须在检查之后才能拿到，
    # 否则未登录时会先抛 gh 的原始报错，而不是友好引导。

    # 1) 凭据
    step("读取本机 WorkBuddy 登录态")
    phone, at, rt, source = read_credential_or_die(args.token)
    secret_value = f"{phone or 'account'}:{at}:{rt}"
    info(f"来源：{source}")
    ok(f"账号 {mask_phone(phone)}    AT {len(at)} 字符 / RT {len(rt)} 字符")
    info("凭据只经 stdin 写入 GitHub Secret，不打印、不落盘")

    # 提前把过期问题暴露出来，别等 CI 跑完才 401
    at_days, at_msg = describe_expiry("AT", at) if at else (None, "AT 为空")
    rt_days, rt_msg = describe_expiry("RT", rt)
    if at_days is not None and at_days < 0:
        warn(f"{at_msg} —— 将依赖 RT 自动换取新 AT")
    else:
        info(at_msg)
    if rt_days is not None and rt_days < 0:
        die(f"{rt_msg}\n      登录态已失效，请重新登录 WorkBuddy 桌面端再试。")
    else:
        info(rt_msg)

    # 2) 定时
    step("换算定时任务（北京 → UTC）")
    crons = parse_schedule(args.schedule)
    for expr, label in crons:
        ok(f"{label}  →  cron {C_DIM}{expr}{C_RESET}")

    if args.dry_run:
        step("预演结束（--dry-run，未做任何改动）")
        exists = repo_exists(full_name)
        print(f"""
    {C_BOLD}将要执行{C_RESET}
      · 目标仓库   {full_name}（{'已存在，复用' if exists else '将新建为 Private'}）
      · 工作目录   {mode}
      · 推送分支   main
      · Secret     {SECRET_KEY} ← 由 stdin 写入，不回显
      · 验证运行   {'跳过' if args.no_run else '触发一次并等待结果'}

    {C_DIM}确认无误后去掉 --dry-run 重新执行。{C_RESET}
""")
        return

    # 3) 仓库
    step("创建 / 校验私有仓库")
    target_url, created = create_or_reuse_repo(full_name, args.description)

    # 4) 代码
    step("准备代码")
    info(f"工作目录模式：{mode}")
    prepare_repo(workdir, target_url)
    ok(f"代码目录 {workdir}")
    sync_with_origin(workdir)

    # 5) 改写工作流 + 生成运维文档
    step("改写工作流定时")
    wf = os.path.join(workdir, WORKFLOW_FILE)
    if not os.path.isfile(wf):
        die(f"找不到工作流文件：{wf}")
    patch_workflow(wf, crons)
    ok(f"{WORKFLOW_FILE} 已写入 {len(crons)} 个定时点")
    wrote, kept = write_ops_doc(workdir, full_name, crons, args.schedule)
    if wrote:
        ok(f"{OPS_DOC} 已生成")
    elif kept:
        warn(f"{OPS_DOC} 是手写文档，保留不动（不覆盖）")

    if args.force_secret:
        # 危险操作防护：仓库里的令牌库是「活的」——Actions 每次续期都会轮换并回写。
        # 本机登录态文件里的凭据很可能更旧，直接覆盖会造成令牌回退 → 下次运行 401。
        store = fetch_repo_store(full_name)
        store_exp = store_newest_exp(store)
        local_exp = max([e for e in (jwt_exp(at), jwt_exp(rt)) if e] or [0])
        if store and store_exp > local_exp:
            when_store = time.strftime("%Y-%m-%d %H:%M", time.localtime(store_exp))
            when_local = (time.strftime("%Y-%m-%d %H:%M", time.localtime(local_exp))
                          if local_exp else "未知")
            if not args.yes:
                die("仓库里的令牌比本机凭据更新，覆盖会造成令牌回退！\n"
                    f"        仓库令牌有效期至：{when_store}\n"
                    f"        本机凭据有效期至：{when_local}\n\n"
                    "      绝大多数情况不需要 --force-secret（Actions 会自动续期）。\n"
                    "      确实要重置时：\n"
                    "        1) 先在 WorkBuddy 桌面端重新登录，拿到全新凭据\n"
                    "        2) 再执行：python deploy.py --force-secret --yes")
            warn(f"--yes 已确认：仓库令牌至 {when_store}，本机至 {when_local}，将强行覆盖")
        if remove_repo_store(workdir):
            ok("已从仓库移除 wb_refresh_tokens.json → 下次运行会从 Secret 重新自举")
        else:
            info("仓库里本来就没有令牌库，无需移除")

    step("修补通知与日志输出")
    patched, msg = patch_script(workdir)
    if patched:
        ok(f"workbuddy_daily.py：{msg}")
    elif msg == "已打过补丁":
        info(f"workbuddy_daily.py：{msg}")
    else:
        warn(f"workbuddy_daily.py：{msg}（报告可能无法在日志里显示）")

    changed = commit_if_dirty(workdir, "chore: 一键部署器同步配置（定时 + 报告输出 + 运维说明）")
    if not changed:
        info("配置无变化，无需提交")

    step("推送代码")
    push(workdir)
    ok("代码已推送到 origin/main")

    # 6) Secret
    step("写入 Secret")
    store = fetch_repo_store(full_name)
    if store is None and not created:
        store = None

    if store:
        names = store_account_list(store)
        print(f"      仓库现有账号（{len(store)}）：{', '.join(names)}")

    local_key = phone or "account"
    if args.add_account and store is not None:
        # ---- 追加模式：合并进令牌库、写回仓库、并把 Secret 更新为多行 ----
        merged, status = merge_account_into_store(store, phone, at, rt)
        if status == "same":
            info(f"{mask_phone(phone)} 的令牌与仓库里完全一致，无需改动")
            ok(f"当前共 {len(merged)} 个账号：{', '.join(store_account_list(merged))}")
        elif status == "kept":
            warn(f"仓库里 {mask_phone(phone)} 的令牌比本机新，保持不动（避免令牌回退）")
            ok(f"当前共 {len(merged)} 个账号：{', '.join(store_account_list(merged))}")
        else:
            msg = ("feat: 追加账号 %s" % mask_phone(phone)) if status == "new"                 else ("chore: 更新账号 %s 的令牌" % mask_phone(phone))
            changed = commit_store(workdir, merged, msg)
            if changed:
                ok(f"令牌库已更新并推送（{'新增' if status == 'new' else '更新'} {mask_phone(phone)}）")
            else:
                info("令牌库内容无变化")
            gh(["secret", "set", SECRET_KEY, "--repo", full_name],
               stdin_data=store_to_secret_lines(merged))
            ok(f"{SECRET_KEY} 已更新为 {len(merged)} 个账号（多行格式，作为兜底）")
            print(f"      当前共 {len(merged)} 个账号："
                  f"{', '.join(store_account_list(merged))}")
    elif store and local_key not in store:
        # ---- 本机是「新账号」但没开追加：说清楚怎么办 ----
        warn(f"本机账号 {mask_phone(phone)} 不在仓库的令牌库里")
        info("如果这是你的第二个账号，请加 --add-account 追加；")
        info("否则仓库会继续只用已有的那些账号签到。")
    elif repo_has_store(full_name) and not args.force_secret and not created:
        warn("仓库里已有令牌库 wb_refresh_tokens.json，跳过 Secret 更新")
        info("令牌已由 Actions 自动续期；确实要覆盖请加 --force-secret")
    else:
        gh(["secret", "set", SECRET_KEY, "--repo", full_name], stdin_data=secret_value)
        ok(f"{SECRET_KEY}  已写入（加密，不可回读）")
    if args.pushplus:
        gh(["secret", "set", "PUSHPLUS_TOKEN", "--repo", full_name], stdin_data=args.pushplus)
        ok("PUSHPLUS_TOKEN 已写入（微信推送）")
    if args.bark:
        gh(["secret", "set", "BARK_URL", "--repo", full_name], stdin_data=args.bark)
        ok("BARK_URL 已写入（iOS 推送）")

    # 7) 验证
    if args.no_run:
        step("跳过验证运行（--no-run）")
    else:
        step("触发一次验证运行")
        result = trigger_and_watch(full_name)
        if result:
            run_id, conclusion = result
            if conclusion == "success":
                ok(f"运行 #{run_id} {C_OK}成功{C_RESET}")
            elif conclusion:
                warn(f"运行 #{run_id} 结束：{conclusion}")
            show_run_summary(full_name, run_id)

    print(f"""
{C_OK}{C_BOLD}══════════════════════════════════════════════════════════
  ✅ 部署完成
══════════════════════════════════════════════════════════{C_RESET}

  仓库      https://github.com/{full_name}   （Private，请勿改公开）
  工作流    https://github.com/{full_name}/actions
  定时      {args.schedule}（北京时间）

{C_BOLD}常用命令{C_RESET}
  查看状态     python {os.path.basename(__file__)} --check
  看运行记录   gh run list  --repo {full_name} --limit 5
  看某次日志   gh run view  <run-id> --repo {full_name} --log
  立刻跑一次   gh workflow run workbuddy.yml --repo {full_name} --ref main

{C_WARN}注意{C_RESET}
  · 令牌由 Actions 自动续期并提交回仓库，无需人工维护；
    正因如此，仓库里的 wb_refresh_tokens.json 含明文 RT —— 保持 Private。
  · 「公益专家 / 学生认证 / 关注公众号」需人工完成，脚本设计上不做。
  · 详细说明见仓库里的 {OPS_DOC}。
""")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(
        prog="deploy.py",
        description="WorkBuddy-Daily 一键部署到 GitHub Actions（私有仓库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--check", action="store_true", help="只查看当前部署状态")
    ap.add_argument("--dry-run", action="store_true", help="预演：只检查与展示，不做改动")
    ap.add_argument("--repo-name", default=DEFAULT_REPO_NAME,
                    help=f"仓库名，默认 {DEFAULT_REPO_NAME}")
    ap.add_argument("--owner", default=None, help="GitHub 用户名，默认当前 gh 登录账号")
    ap.add_argument("--workdir", default=None,
                    help="手动指定工作目录（覆盖默认的临时目录 / --persist）")
    ap.add_argument("--schedule", default=DEFAULT_SCHEDULE,
                    help=f"北京时间定时，逗号分隔，默认 {DEFAULT_SCHEDULE}")
    ap.add_argument("--token", default=None,
                    help='手动指定凭据 "手机号:AT:RT"（默认自动读本机登录态）')
    ap.add_argument("--pushplus", default=None, help="PushPlus Token（微信推送，可选）")
    ap.add_argument("--bark", default=None, help="Bark URL（iOS 推送，可选）")
    ap.add_argument("--no-run", action="store_true", help="部署后不触发验证运行")
    ap.add_argument("--menu", action="store_true",
                    help="先显示启动菜单（1 开始部署 / 2 添加账号 / 3 查看状态 / 4 退出）；双击启动器就是这个模式")
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="强制进入交互向导（不带任何参数时本来就会进）")
    ap.add_argument("--add-account", action="store_true",
                    help="把本机当前账号追加到令牌库（多账号：切换客户端登录后逐个追加）")
    ap.add_argument("--force-secret", action="store_true",
                    help="即使仓库已有令牌库，也强制用本机凭据覆盖 Secret")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="非交互模式：跳过交互向导全部用默认值；同时确认 --force-secret 的令牌回退风险")
    ap.add_argument("--ephemeral", action="store_true",
                    help="【默认行为】用临时工作目录，跑完自动删除；此开关保留是为了兼容旧命令")
    ap.add_argument("--gh-proxy", default=None,
                    help="让 gh 走指定代理（gh 不读 git 的 http.proxy），如 http://127.0.0.1:10808")
    ap.add_argument("--persist", action="store_true",
                    help="改用固定工作目录 <脚本目录>/WorkBuddy-Daily（保留克隆，便于本机调试）")
    ap.add_argument("--description", default="WorkBuddy 成长中心全能签到脚本（私有部署实例）",
                    help="仓库描述")
    args = ap.parse_args()

    global GH_PROXY
    GH_PROXY = (args.gh_proxy or "").strip() or None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.workdir:
        workdir = args.workdir
        cleanup = None
        mode = f"指定目录 {workdir}"
    elif args.persist:
        workdir = os.path.join(script_dir, DEFAULT_REPO_NAME)
        cleanup = None
        mode = "固定目录（--persist）"
    else:
        # 默认临时工作目录：令牌库只落在 %TEMP%，跑完即抹掉
        workdir = tempfile.mkdtemp(prefix="wbd_work_")
        cleanup = workdir
        mode = "临时目录（默认，跑完自动清理）"

    banner()
    # 环境检查（含缺工具时的自动安装引导、未登录时的授权引导）统一在 ensure_tools 里做。
    # 这里不能再放重复的前置检查 —— 之前 main() 里那句 `if shutil.which("gh") is None: die(...)`
    # 会在 ensure_tools 之前就退出，把自动安装流程整个挡住。
    login = ensure_tools(non_interactive=bool(args.yes))

    try:
        if args.check:
            cmd_check(f"{args.owner or login}/{args.repo_name}")
            return

        # 交互总开关：显式 -i/--menu，或「一条参数都没给 + 有交互终端」；
        # 加 -y 则全部跳过，直接按默认值部署（脚本化 / CI 用）。
        bare = len(sys.argv) == 1
        interactive = (not args.yes) and (
            args.menu or args.interactive or (bare and sys.stdin.isatty())
        )

        if interactive:
            if not sys.stdin.isatty() and not args.menu:
                # 管道等非交互环境：别硬等输入，退回默认全自动
                interactive = False

        if interactive:
            # 非 -y 且带参数时（例如只给了 --dry-run），沿用原来的「直接向导」行为；
            # 真正的「先选菜单」用于双击启动器 / 裸跑
            if args.menu or bare:
                action = show_menu(args)
                if action == "quit":
                    info("已退出，未做任何改动")
                    return
                if action == "check":
                    cmd_check(f"{args.owner or login}/{args.repo_name}")
                    return
                if action == "add":
                    args.add_account = True
                    print()
                    info(f"将把本机当前账号追加到 "
                         f"{args.owner or login}/{args.repo_name} 的令牌库")
                    info("请确认已在 WorkBuddy 客户端切换到要添加的那个账号")
                    if not _ask_yes("继续？", True):
                        info("已取消")
                        return
                    full_name = f"{args.owner or login}/{args.repo_name}"
                    cmd_deploy(args, full_name, workdir, mode)
                    return

            if not run_wizard(args, login, workdir):
                return

        full_name = f"{args.owner or login}/{args.repo_name}"
        cmd_deploy(args, full_name, workdir, mode)
    finally:
        # --ephemeral：令牌库只落在临时目录，跑完就抹掉，本机不留明文凭据
        if cleanup and os.path.exists(cleanup):
            if robust_rmtree(cleanup):
                print(f"\n  {C_DIM}·{C_RESET} 临时工作目录已清理（本机未残留令牌）")
            else:
                print(f"\n  {C_WARN}!{C_RESET} 临时目录未能完全删除，请手动清理：{cleanup}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n    已取消\n")
        sys.exit(130)
