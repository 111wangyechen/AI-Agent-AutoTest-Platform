"""pytest 配置：把项目根加入 sys.path，并强制 mock 模式（不连真机、不调 LLM）。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("PLATFORM_MOCK", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))
