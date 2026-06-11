# 群聊 AI 机器人（基于 WeChatFerry）

微信群聊 AI 对话机器人，基于 [WeChatFerry](https://github.com/lich0821/WeChatFerry) + Python + LLM（OpenAI 兼容接口）。

## 功能

- 🤖 **AI 对话**：@机器人 即可进行多轮对话，上下文连贯
- 🗂️ **多群隔离**：每个群的对话历史独立，互不干扰
- 📋 **命令系统**：`/help`、`/reset`、`/status`
- 🛡️ **群过滤**：白名单/黑名单控制响应的群
- 🔄 **Watchdog 守护**：崩溃自动重启

## 环境要求

- **Windows**（WeChatFerry 仅支持 Windows）
- Python >= 3.10
- 微信 Windows 客户端（版本需匹配 wcferry，当前适配 **3.9.12.x**）

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置

```bash
# 复制配置文件模板
copy config\config.yaml.example config\config.yaml

# 编辑 config\config.yaml，填入你的 LLM API Key
notepad config\config.yaml
```

主要配置项：

```yaml
llm:
  provider: "deepseek"           # 或其他兼容 OpenAI 接口的服务
  api_key: "sk-your-key-here"   # 必填
  base_url: "https://api.deepseek.com"
  model: "deepseek-chat"

bot:
  name: "小助手"                 # 机器人名字，用于 @ 检测
  system_prompt: "你是一个..."   # 角色设定
```

### 3. 登录微信

1. 打开 Windows 微信客户端并**登录你的小号**
2. 运行机器人：

```bash
python main.py
```

3. 如果输出「已获取登录二维码」，扫码即可（通常已登录状态会跳过）

### 4. 使用

在群里 **@机器人** 然后发送问题即可：

```
@小助手 今天天气怎么样？
@小助手 /help
@小助手 /reset
```

## 生产部署（Windows VPS）

### 使用 nssm 注册 Windows Service

1. 下载 [nssm](https://nssm.cc/download) 并解压

2. 以管理员身份运行：

```cmd
nssm install WeChatBot
```

3. 在弹出窗口中设置：
   - **Application**: `python` 的完整路径
   - **Startup directory**: 本项目根目录
   - **Arguments**: `watchdog.py`

4. 启动服务：

```cmd
nssm start WeChatBot
```

这样机器人开机自启，崩溃后 watchdog 自动重启。

### 关闭微信自动更新

在微信设置中关闭「自动更新」，防止版本升级导致 WCF 不兼容。

## 目录结构

```
├── main.py              # 入口
├── watchdog.py          # 守护进程（生产用）
├── requirements.txt     # Python 依赖
├── config/
│   ├── config.yaml.example  # 配置模板（提交 Git）
│   └── config.yaml          # 实际配置（不提交 Git，含密钥）
├── src/
│   ├── wcf_client.py    # WeChatFerry 封装
│   ├── bot_core.py      # 消息路由 & 会话管理
│   ├── llm_client.py    # LLM API 调用
│   └── config_loader.py # 配置加载
└── logs/
    └── bot.log          # 运行日志
```

## 风险提示

⚠️ WeChatFerry 通过注入 DLL 方式 Hook 微信客户端，**违反微信用户协议**，存在封号风险。请使用小号，不要发敏感内容。
