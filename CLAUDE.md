# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Discord 自动营销机器人系统 - 基于 Tauri + Python 的跨平台桌面应用。支持多 Discord 账号管理，自动发送自定义内容到指定频道，具备账号轮换、定时发送等功能。

### 技术栈
- **前端**: Tauri v1 + React 18 + TypeScript + Tailwind CSS + Vite
- **后端**: Python 3.11 (Flask) + discord.py-self
- **数据库**: SQLite (WAL 模式)
- **构建**: GitHub Actions (Windows/macOS) + PyInstaller

## 核心架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Tauri Desktop App                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  React Frontend                        │  │
│  │  - AutoSenderPage (自动发送控制台)                     │  │
│  │  - AccountsPage (账号管理)                             │  │
│  │  - ContentsPage (内容管理)                             │  │
│  └────────────────────┬──────────────────────────────────┘  │
│                       │ HTTP (localhost:5001)                │
│  ┌────────────────────▼──────────────────────────────────┐  │
│  │              Python Sidecar (Flask)                    │  │
│  │  app.py → REST API 入口                                │  │
│  └────────────────────┬──────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────┘
                        │
    ┌───────────────────┼───────────────────┐
    ▼                   ▼                   ▼
┌─────────┐     ┌─────────────┐     ┌─────────────┐
│ bot.py  │     │ database.py │     │auto_sender  │
│ Discord │◄───►│   SQLite    │◄───►│  任务调度    │
│ 多账号   │     │ metadata.db │     │  轮换发送    │
└─────────┘     └─────────────┘     └─────────────┘
```

**关键设计**: Flask 与 Discord.py 运行在不同线程，使用 `asyncio.run_coroutine_threadsafe()` 跨线程调用。

## 核心模块

### backend/bot.py
- `DiscordBotClient`: Discord 客户端类，继承自 `discord.Client`
- `bot_clients`: 全局列表，存储所有活跃的机器人实例
- 冷却机制: `account_last_sent` 字典管理 (account_id, channel_id) → timestamp

### backend/auto_sender.py
- `auto_send_loop()`: 异步发送循环，支持轮换模式和单账号模式
- `stop_sender_event`: asyncio.Event 控制任务停止
- `task_status`: 全局字典跟踪发送进度，支持暂停/恢复

### backend/database.py
- `Database` 类: SQLite 数据库管理，使用 WAL 模式
- 数据路径: `~/Library/Application Support/DiscordAutoSender/metadata.db` (macOS)
- 主要表: `discord_accounts`, `contents`, `account_channels`

## 常用命令

### 开发

```bash
# 安装依赖
pnpm install                              # 前端依赖
pip install -r backend/requirements.txt   # 后端依赖

# 启动开发环境 (需要同时运行)
cd backend && python app.py               # 后端 API (端口 5001)
pnpm tauri dev                            # Tauri + Vite 前端
```

### 构建

```bash
# 构建 Python 后端为可执行文件
pyinstaller --onefile --name backend backend/app.py

# 复制到 Tauri binaries 目录
# macOS:
cp dist/backend src-tauri/binaries/backend-aarch64-apple-darwin
# Windows:
# copy dist\backend.exe src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe

# 构建 Tauri 应用
pnpm tauri build
```

## API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/accounts` | GET/POST | 账号列表/添加账号 |
| `/api/accounts/<id>/start` | POST | 启动账号连接 |
| `/api/accounts/<id>/stop` | POST | 停止账号连接 |
| `/api/contents` | GET/POST | 内容列表/添加内容 |
| `/api/contents/<id>` | GET/PUT/DELETE | 内容 CRUD |
| `/api/sender/start` | POST | 启动自动发送 |
| `/api/sender/stop` | POST | 停止自动发送 |
| `/api/sender/pause` | POST | 暂停发送 |
| `/api/sender/resume` | POST | 恢复发送 |
| `/api/sender/status` | GET | 获取发送状态 |

## 注意事项

- 使用 `discord.py-self` (用户账号) 而非官方 `discord.py` (Bot 账号)
- Sidecar 命名规则: `backend-{target-triple}` (如 `backend-x86_64-pc-windows-msvc.exe`)
- 前端通过 `http://127.0.0.1:5001/api` 与后端通信
