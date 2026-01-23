# Discord 自动营销机器人

基于 Tauri + Python 的跨平台桌面应用，支持多 Discord 账号管理，自动发送微店商品链接到指定频道。

## 功能特性

- **多账号管理**: 通过 Token 登录多个 Discord 账号
- **店铺管理**: 添加多个微店店铺，抓取商品数据
- **自动发送**: 选择账号、店铺和频道，自动发送商品链接
- **账号轮换**: 多账号轮换发送，降低风控风险
- **定时发送**: 自定义发送间隔（10秒 - 1小时）

## 技术栈

- **前端**: Tauri + React + TypeScript + Tailwind CSS
- **后端**: Python (Flask) + discord.py-self
- **数据库**: SQLite
- **构建**: GitHub Actions (Windows/macOS)

## 开发环境

### 前置要求

- Node.js 18+
- Python 3.10+
- Rust 1.70+

### 安装依赖

```bash
# 安装前端依赖
npm install

# 安装后端依赖
cd backend
pip install -r requirements.txt
```

### 开发模式

```bash
# 启动后端 (终端1)
cd backend
python app.py

# 启动前端 (终端2)
npm run tauri dev
```

## 构建

### 本地构建

```bash
# 构建 Python 后端
cd backend
pyinstaller --onefile --name backend app.py

# 构建 Tauri 应用
npm run tauri build
```

### GitHub Actions 构建

推送 tag 触发自动构建：

```bash
git tag v1.0.0
git push origin v1.0.0
```

## 项目结构

```
DiscordSend/
├── backend/                    # Python 后端
│   ├── app.py                  # Flask API 入口
│   ├── bot.py                  # Discord 机器人客户端
│   ├── database.py             # SQLite 数据库管理
│   ├── weidian_scraper.py      # 微店商品抓取
│   ├── auto_sender.py          # 自动发送任务调度
│   └── config.py               # 配置文件
├── src/                        # React 前端
│   ├── pages/                  # 页面组件
│   └── App.tsx                 # 主应用
├── src-tauri/                  # Tauri 配置
└── .github/workflows/          # GitHub Actions
```

## 注意事项

- 使用 `discord.py-self` 而非官方 `discord.py`，用于用户账号
- 请妥善保管 Discord Token，避免泄露
- 建议发送间隔设置在 60 秒以上，避免触发 Discord 限制

## License

MIT
# discordSend
# DiscordAd
