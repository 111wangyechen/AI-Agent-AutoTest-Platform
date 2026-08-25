"""
演示脚本共享工具：环境自检、插件加载、Agent 进度回调打印。
"""

import sys
from pathlib import Path

# 让 examples/ 能 import 到 core/
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core import config

# import 插件包 = 触发它们向 default_registry 注册自己
import core.plugins.acoustic  # noqa: F401
import core.plugins.lamp      # noqa: F401
from core.plugins import default_registry

# legacy_windows=False 关掉 Win32 控制台后端，避免 GBK 编码报错
# （Markdown 结论里的 • 等字符在 legacy 模式下会 UnicodeEncodeError）
console = Console(legacy_windows=False)


def preflight() -> None:
    """检查鉴权配置，缺失则给清晰指引并退出。"""
    if not config.has_credentials:
        console.print("[red]✗ 未配置 API 鉴权[/red]")
        console.print("  公司网关：ANTHROPIC_AUTH_TOKEN=sk-... + ANTHROPIC_BASE_URL=...")
        console.print("  官方直连：ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    auth = "公司网关" if config.auth_token else "官方直连"
    mode = "MOCK（样本数据）" if config.mock else f"真机 {config.device_addr or '(未指定)'}"
    console.print(f"[dim]模型：{config.model}   鉴权：{auth}   模式：{mode}[/dim]\n")


def make_event_printer():
    """返回一个 on_event 回调，把 Agent 每步工具调用打印出来（演示很直观）。"""
    def on_event(kind: str, payload) -> None:
        if kind == "stage":
            console.print(f"\n[bold magenta]▶ 阶段：{payload['stage']}[/bold magenta]")
        elif kind == "tool_call":
            console.print(
                f"  [cyan]→ {payload.get('agent','')} 调用[/cyan] "
                f"[bold]{payload['name']}[/bold]  {payload['input']}"
            )
        elif kind == "tool_result":
            status = payload["result"].get("status", "?")
            tag = "green" if status == "success" else "red"
            console.print(f"    [{tag}]← {status}[/{tag}]")
        elif kind == "exec":
            console.print(f"  [cyan]→ 执行[/cyan] {payload['case']} via {payload['collector']}")
        elif kind == "cases":
            console.print(f"  [cyan]→ 生成 {payload['count']} 条用例[/cyan]")
    return on_event
