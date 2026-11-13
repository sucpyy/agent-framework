"""Agent Framework — Desktop Application

以 Chrome/Edge App Mode 启动——无地址栏、无标签栏，外观等同原生桌面应用。
无需安装任何 GUI 框架，Python + 浏览器即可。
"""

import os
import sys
import subprocess
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import app as fastapi_app


def get_api_key():
    if len(sys.argv) > 2:
        return sys.argv[2]
    return os.environ.get("DEEPSEEK_API_KEY", "")


def run_server(host: str, port: int):
    import uvicorn
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")


def find_browser():
    """Find Chrome or Edge executable."""
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


def open_app_window(url: str):
    """以 App Mode 打开浏览器——无地址栏、无标签，像桌面应用。"""
    browser = find_browser()
    if browser:
        # Chrome/Edge --app 模式: 无边框的独立窗口
        subprocess.Popen(
            [browser, f"--app={url}", "--window-size=1200,800"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Fallback: 普通浏览器
        import webbrowser
        webbrowser.open(url)


def main():
    app_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_root)
    os.environ.setdefault("AGENT_WORKSPACE_ROOT", app_root)

    api_key = get_api_key()
    if api_key:
        os.environ["DEEPSEEK_API_KEY"] = api_key

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    print(f"Starting Agent Framework...")
    print(f"  Backend: DeepSeek API")
    print(f"  http://{host}:{port}")
    print()

    # 启动后端
    t = threading.Thread(target=run_server, args=(host, port), daemon=True)
    t.start()
    time.sleep(2)

    # App Mode 打开
    browser_path = find_browser()
    if browser_path:
        print(f"  Opening in App Mode ({os.path.basename(browser_path)})...")
    else:
        print(f"  Opening in browser...")

    open_app_window(url)

    print(f"  Ready. Close this window to exit.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")


if __name__ == "__main__":
    main()
