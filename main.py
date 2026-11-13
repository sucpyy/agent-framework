"""Agent Framework v1.0 — 统一入口点

用法:
  python main.py                    # 启动 Web UI (默认)
  python main.py --server           # 启动 Web 服务器
  python main.py --app              # 启动桌面应用 (Chrome App Mode)
  python main.py --chat             # 启动命令行交互终端
  python main.py --demo             # 运行完整流水线演示
  python main.py --port 8080        # 指定端口
  python main.py --help             # 查看帮助

环境变量:
  DEEPSEEK_API_KEY    — DeepSeek API 密钥
  OPENAI_API_KEY      — OpenAI API 密钥
  ANTHROPIC_API_KEY   — Anthropic API 密钥
  OPENAI_BASE_URL     — 自定义 API 地址 (默认: https://api.deepseek.com/v1)
"""

import argparse
import os
import sys
import webbrowser
from pathlib import Path


# ─── 颜色 ──────────────────────────────────────

class C:
    """终端颜色常量。"""
    R = "\033[91m"
    G = "\033[92m"
    Y = "\033[93m"
    B = "\033[94m"
    M = "\033[95m"
    C = "\033[96m"
    W = "\033[97m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RST = "\033[0m"


# ─── Banner ────────────────────────────────────

BANNER = f"""
{C.BOLD}{C.M}  ╔══════════════════════════════════════╗
  ║     Agent Framework v1.0              ║
  ║     Workspace OS 级编排层              ║
  ╚══════════════════════════════════════╝{C.RST}

  {C.DIM}统一入口 — 支持 Web UI / 桌面应用 / CLI / 演示{C.RST}
"""


# ─── 模式定义 ──────────────────────────────────

MODES = {
    "server": {
        "help": "启动 Web 服务器 (FastAPI)",
        "entry": "server:app",
    },
    "app": {
        "help": "启动桌面应用 (Chrome/Edge App Mode)",
        "entry": "app:main",
    },
    "chat": {
        "help": "启动命令行交互终端",
        "entry": "chat:main",
    },
    "demo": {
        "help": "运行完整流水线演示",
        "entry": "demo:run_demo",
    },
}


# ─── 辅助函数 ──────────────────────────────────

def print_banner():
    """打印启动 Banner。"""
    print(BANNER)


def check_api_key() -> bool:
    """检查是否配置了 API 密钥，返回 True 表示有可用密钥。"""
    keys = [
        ("DEEPSEEK_API_KEY", "DeepSeek"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("ANTHROPIC_API_KEY", "Anthropic"),
    ]
    available = [name for env, name in keys if os.environ.get(env)]
    if available:
        print(f"  {C.G}✓{C.RST} API 密钥已配置: {', '.join(available)}")
        return True
    else:
        print(f"  {C.Y}⚠{C.RST} 未检测到 API 密钥")
        print(f"    设置环境变量: DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY")
        print(f"    或使用 --api-key 参数传入")
        return False


def find_browser() -> str | None:
    """查找 Chrome 或 Edge 可执行文件路径。"""
    paths = [
        # Chrome
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        # Edge
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _import_uvicorn():
    """延迟导入 uvicorn，避免未安装时启动报错。"""
    try:
        import uvicorn
        return uvicorn
    except ImportError:
        print(f"  {C.R}✗{C.RST} 未安装 uvicorn，请执行: pip install uvicorn")
        sys.exit(1)


# ─── 模式启动器 ────────────────────────────────

def run_server(port: int, host: str = "127.0.0.1"):
    """启动 FastAPI Web 服务器。"""
    print(f"  {C.B}模式:{C.RST} Web 服务器 (FastAPI)")
    print(f"  {C.B}地址:{C.RST} http://{host}:{port}")
    print()

    # 动态导入避免循环依赖
    from server import app as fastapi_app
    uvicorn = _import_uvicorn()

    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")


def run_app(port: int, host: str = "127.0.0.1"):
    """启动桌面应用 (Chrome/Edge App Mode)。"""
    import threading
    import time

    print(f"  {C.B}模式:{C.RST} 桌面应用 (App Mode)")
    print(f"  {C.B}地址:{C.RST} http://{host}:{port}")
    print()

    # 启动后端服务器
    from server import app as fastapi_app
    uvicorn = _import_uvicorn()

    t = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": fastapi_app, "host": host, "port": port, "log_level": "warning"},
        daemon=True,
    )
    t.start()
    time.sleep(2)

    # 以 App Mode 打开浏览器
    url = f"http://{host}:{port}"
    browser_path = find_browser()
    if browser_path:
        import subprocess
        print(f"  {C.G}✓{C.RST} 使用 {os.path.basename(browser_path)} App Mode 打开")
        subprocess.Popen(
            [browser_path, f"--app={url}", "--window-size=1200,800"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        print(f"  {C.Y}⚠{C.RST} 未找到 Chrome/Edge，使用默认浏览器打开")
        webbrowser.open(url)

    print(f"\n  {C.DIM}按 Ctrl+C 退出...{C.RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  {C.Y}正在关闭...{C.RST}")


def run_chat():
    """启动命令行交互终端。"""
    print(f"  {C.B}模式:{C.RST} 命令行交互终端")
    print()

    import asyncio
    from chat import main as chat_main

    asyncio.run(chat_main())


def run_demo():
    """运行完整流水线演示。"""
    print(f"  {C.B}模式:{C.RST} 完整流水线演示")
    print()

    import asyncio
    from demo import build_workspace, run_demo as demo_run

    user_input = "搜索 Python asyncio 最佳实践，并重构 auth 模块的 JWT 实现"
    ws, tracker, info = asyncio.run(build_workspace("auto"))
    asyncio.run(demo_run(ws, tracker, info, user_input))


# ─── CLI 解析 ──────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Agent Framework v1.0 — 统一入口点",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  python main.py                    # 启动 Web UI
  python main.py --chat             # 启动 CLI 终端
  python main.py --app --port 8080  # 桌面应用 + 自定义端口
  python main.py --demo             # 运行演示
  python main.py --api-key sk-xxx   # 指定 API 密钥
        """,
    )

    # 模式选择 (互斥组确保只选一个模式)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--server", action="store_true",
        help="启动 Web 服务器 (FastAPI)",
    )
    mode_group.add_argument(
        "--app", action="store_true",
        help="启动桌面应用 (Chrome/Edge App Mode)",
    )
    mode_group.add_argument(
        "--chat", action="store_true",
        help="启动命令行交互终端",
    )
    mode_group.add_argument(
        "--demo", action="store_true",
        help="运行完整流水线演示",
    )

    # 通用选项
    parser.add_argument(
        "--port", type=int, default=8000,
        help="服务器端口 (默认: 8000)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="服务器主机地址 (默认: 127.0.0.1)",
    )
    parser.add_argument(
        "--api-key", type=str, default="",
        help="设置 API 密钥 (会写入环境变量)",
    )
    parser.add_argument(
        "--no-banner", action="store_true",
        help="不打印启动 Banner",
    )

    return parser.parse_args(argv)


# ─── 主入口 ────────────────────────────────────

def main():
    """Agent Framework 统一入口。"""
    args = parse_args()

    # 设置 API 密钥
    if args.api_key:
        os.environ["DEEPSEEK_API_KEY"] = args.api_key

    # 打印 Banner
    if not args.no_banner:
        print_banner()

    # 检查 API 密钥
    has_key = check_api_key()
    print()

    # 确定模式并启动
    if args.server:
        run_server(port=args.port, host=args.host)
    elif args.app:
        run_app(port=args.port, host=args.host)
    elif args.chat:
        run_chat()
    elif args.demo:
        run_demo()
    else:
        # 默认模式: 启动 Web 服务器并打开浏览器
        run_server(port=args.port, host=args.host)


if __name__ == "__main__":
    main()
