# 模块 C 作业 - 工具系统

## 任务清单

### 第一部分：基础架构（必做）
- [ ] `core/tools/base.py` - Tool 基类
- [ ] `core/tools/registry.py` - ToolRegistry 注册表
- [ ] `tests/test_registry.py` - 注册表测试

### 第二部分：内置工具（必做，实现 3 个）
- [ ] `core/tools/builtin/adb_tool.py` - adb 命令执行
- [ ] `core/tools/builtin/log_parser.py` - 日志解析
- [ ] `core/tools/builtin/system_info.py` - 系统信息采集
- [ ] `tests/test_builtin_tools.py` - 工具执行测试

## 验收标准

运行以下命令全部通过：
```bash
cd D:/junior-to-senior/ai-agent-test-platform
pytest tests/test_registry.py -v
pytest tests/test_builtin_tools.py -v
```

输出示例：
```python
from core.tools.registry import ToolRegistry
from core.tools.builtin.adb_tool import AdbTool

registry = ToolRegistry()
registry.register(AdbTool())

print(registry.list_tools())
# ['adb_tool', 'log_parser', 'system_info']

result = registry.execute('adb_tool', command='shell getprop ro.build.version.release')
print(result)
# {'status': 'success', 'result': '11'}
```

## 开始前

先读完 Lesson C（我即将为你创建），理解：
1. 为什么需要工具注册表
2. Tool 基类的 4 个要素
3. Registry 的 3 个职责
4. 工具执行的错误处理

## 提交

完成后提交：
1. 所有代码文件
2. pytest 运行截图（全部通过）
3. 录音讲解（5-10分钟）："我的工具系统是怎么设计的、为什么这么做"
