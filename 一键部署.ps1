<#
    WorkBuddy-Daily 一键部署器（PowerShell 启动器）

    用法：
        右键本文件 → "使用 PowerShell 运行"
        或在 PowerShell 里：
            powershell -ExecutionPolicy Bypass -File .\一键部署.ps1
            powershell -ExecutionPolicy Bypass -File .\一键部署.ps1 --schedule 09:10,23:30

    所有参数会原样透传给 deploy.py，见 README.md。
#>

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$interactive = ($args.Count -eq 0)

if ($interactive) {
    Write-Host ''
    Write-Host '  WorkBuddy-Daily 一键部署器' -ForegroundColor Cyan
    Write-Host '  ==========================' -ForegroundColor Cyan
    Write-Host ''
}

# ---- 找一个可用的 Python ----
$exe = $null
$pre = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = 'py'; $pre = @('-3')
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $exe = 'python'; $pre = @()
}

if (-not $exe) {
    Write-Host '  [x] 没找到 Python。' -ForegroundColor Red
    Write-Host ''
    Write-Host '      Python 是运行本脚本的前提，可以用 winget 自动安装。'
    Write-Host ''

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $choice = '1'
        if ($interactive) {
            Write-Host '        1) 自动安装 Python 3.12（推荐）'
            Write-Host '        2) 只打印安装命令，我自己装'
            Write-Host '        3) 退出'
            Write-Host ''
            $choice = Read-Host '      请输入序号 [1]'
            if ([string]::IsNullOrWhiteSpace($choice)) { $choice = '1' }
        } else {
            $choice = '2'      # 非交互：不擅自装软件
        }

        if ($choice -eq '3') {
            Write-Host ''
            Write-Host '      已取消，未做任何改动。'
            if ($interactive) { Read-Host '按回车退出' | Out-Null }
            exit 1
        }

        if ($choice -eq '1') {
            Write-Host ''
            Write-Host '      正在用 winget 安装 Python 3.12 ...（可能弹出 UAC 授权窗口，请点“是”）'
            Write-Host ''
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            Write-Host ''
            Write-Host '  ---------------------------------------------------------------' -ForegroundColor Yellow
            Write-Host '   安装已结束。'
            Write-Host ''
            Write-Host '   当前这个窗口还认不到新装的 Python（进程的 PATH 不会自动刷新），'
            Write-Host '   所以请关掉窗口，重新运行一次本启动器。'
            Write-Host '  ---------------------------------------------------------------' -ForegroundColor Yellow
            Write-Host ''
            if ($interactive) { Read-Host '按回车退出' | Out-Null }
            exit 0
        }
    }

    Write-Host '      请任选一种方式安装后重试：'
    Write-Host '        winget install -e --id Python.Python.3.12'
    Write-Host '        或到 https://www.python.org/downloads/ 下载'
    Write-Host ''
    Write-Host '      注意：安装时请勾选 "Add Python to PATH"'
    Write-Host ''
    if ($interactive) { Read-Host '按回车退出' | Out-Null }
    exit 1
}

$deployPy = Join-Path $PSScriptRoot 'deploy.py'
if (-not (Test-Path $deployPy)) {
    Write-Host "  [x] 找不到 deploy.py，请确认它和本文件在同一目录：" -ForegroundColor Red
    Write-Host "      $PSScriptRoot"
    if ($interactive) { Read-Host '按回车退出' | Out-Null }
    exit 1
}

if ($args.Count -eq 0) {
    # 无参数 = 双击：先显示启动菜单（1 部署 / 2 添加账号 / 3 查看状态 / 4 退出）
    # 菜单由 deploy.py 渲染，中文编码才正确
    & $exe @pre $deployPy --menu
} else {
    & $exe @pre $deployPy @args
}
$rc = $LASTEXITCODE

if ($interactive) {
    Write-Host ''
    if ($rc -eq 0) {
        Write-Host '  完成。' -ForegroundColor Green
    } else {
        Write-Host "  退出码 $rc，请看上面的错误信息。" -ForegroundColor Yellow
    }
    Read-Host '按回车关闭' | Out-Null
}

exit $rc
