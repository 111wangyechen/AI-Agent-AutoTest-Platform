# AI Agent 测试平台 - 快速启动

## 当前状态
✅ 核心功能已实现  
✅ Mock 测试端到端跑通（走公司网关，真实调用）  
✅ 鉴权已配置（讯飞网关）  

## 鉴权说明（已配置好）

用的是**公司网关**（讯飞 one.iflytek.com），不是官方直连。`.env` 里是：
```bash
ANTHROPIC_AUTH_TOKEN=sk-...          # 公司给的 Bearer token
ANTHROPIC_BASE_URL=https://one.iflytek.com/api/llm/console/chat
```
token 来源：本机 `~/.claude/settings.json` 的 env 段。如果换官方直连，改用 `ANTHROPIC_API_KEY` 即可（代码两种都支持）。

## 立即行动

### 运行 Mock 测试

```bash
# 进入项目目录
cd D:/junior-to-senior/projects/ai-agent-test-platform

# Mock 模式运行（不连设备）
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo.py
```

**已验证**：Agent 调用 4 次工具，正确定位到"资源释放缺失导致 PCM 竞争"根因

### 3. 周六真机测试（8/23）

```bash
# 连接设备
adb connect <灵犀屏IP>:5555

# 推送串口工具
adb push uart_test /data/local/tmp/
adb shell chmod +x /data/local/tmp/uart_test

# 真机运行
DEVICE_ADDR=<灵犀屏IP>:5555 python examples/demo.py
```

## 文件说明

- `NEXT_STEPS.md` - 详细行动指南
- `examples/README.md` - 使用说明和故障排查
- `PROGRESS.md` - 开发进度记录

## 故障排查

**401 错误**：API Key 未设置或无效  
**Import 错误**：已修复（删除了 `core/tools/` 冲突目录）  
**adb 错误**：检查 `adb devices` 确认设备连接  

## 已实现功能

✅ 3个工具：adb_tool, logcat_capture, serial_diagnose  
✅ AnalysisAgent：手动工具循环 + Opus 4.8 adaptive thinking  
✅ Mock 模式：内置样本数据，不依赖真实设备  
✅ 环境配置：支持环境变量覆盖（PLATFORM_MOCK, DEVICE_ADDR）  

## 下一步开发

- [ ] 多场景测试（至少3个故障类型）
- [ ] 录制演示视频
- [ ] 用例生成 Agent（多Agent协同演示）
