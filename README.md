# 🔮 TBHStargaze

**TaskBarHero 宝箱掉落队列预测器** — 提前看到下一批掉落，不再盲开箱。

通过 [Frida](https://frida.re/) 读取游戏内存中已生成但未消耗的掉落队列，在本地 Web UI 展示物品名、稀有度、Steam 市场价格，并支持关注物品命中提醒。

![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue) ![License: MIT](https://img.shields.io/badge/License-MIT-green) ![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey)

## ✨ 功能
<img width="1383" height="917" alt="image" src="https://github.com/user-attachments/assets/3d44064e-733a-4aaf-a9f5-adafa0b7dbf0" />

- **宝箱队列预览** — 实时显示普通/首领宝箱的掉落队列
- **稀有度着色** — 10 级稀有度颜色，与 [TBH 攻略站](https://lynkzhang.github.io/tbh-guide/) 一致
- **Steam 市场价格** — 自动查询物品在 Steam Market 的最新挂单价格（6 小时缓存）
- **关注物品提醒** — 设置关注物品，命中时浏览器响铃 + 弹窗通知
- **物品搜索** — 支持按 ID 或名字搜索，快速添加关注
- **单实例保护** — 防止多次启动导致游戏崩溃
- **零安装便携包** — 解压即用，自带 Python + 依赖，无需预装任何环境

## 🚀 快速开始

### 方式一：便携包（推荐，无需安装 Python）

1. 下载最新 Release 的 `TBHStargaze-portable.zip`
2. 解压到任意目录
3. 首次运行：双击 `tools\install-deps.bat`（安装 frida + psutil，约 30 秒）
4. 启动游戏 → 双击 `启动.bat`（需管理员权限）
5. 浏览器自动打开 `http://127.0.0.1:18765/`

### 方式二：本地 Python 环境

```bash
# 克隆仓库
git clone https://github.com/Lynkzhang/tbh-stargaze.git
cd tbh-stargaze

# 安装依赖
pip install frida psutil

# 启动游戏后运行
python src/tbh_reader.py http
# 浏览器打开 http://127.0.0.1:18765/
```

## 🏗️ 项目结构

```
box-queue-reader/
├── src/
│   ├── tbh_reader.py          # 主程序（HTTP 服务 + Frida 注入）
│   ├── steam_price.py          # Steam Market 价格查询客户端
│   ├── probe.py                # 内存读取 sanity check（开发调试用）
│   ├── resources/
│   │   ├── drop_items_agent.js # Frida agent（复用自 xmodhub）
│   │   ├── item.json           # 物品 ID → 中文名映射（5935 条）
│   │   ├── item_grades.json    # 物品 ID → 稀有度映射
│   │   ├── item_color.json     # 物品颜色配置
│   │   ├── watched_ids.json    # 关注物品 ID 列表
│   │   ├── gear_market_names.json      # 装备 → Steam 英文名
│   │   ├── material_market_names.json  # 材料 → Steam 英文名
│   │   └── generic_market_names.json   # 通用物品 → Steam 英文名
│   └── web/
│       ├── index.html          # Web UI（单文件，内嵌 CSS）
│       └── app.js              # 前端逻辑
├── tools/
│   ├── build-portable.ps1      # 打包便携 zip
│   ├── build_grades.py         # 从 CSV 提取 item_grades.json
│   ├── build_market_names.py   # 从 tbh-copilot 提取市场名映射
│   ├── setup.ps1               # 首次运行依赖安装脚本
│   ├── install-deps.bat.template # 便携包依赖安装模板
│   └── extract_resources.py    # 从 xmodhub exe 提取资源
├── 启动.bat                     # Windows 启动脚本
├── requirements.txt            # Python 依赖
├── LICENSE                     # MIT License
└── README.md
```

## 🔧 API 接口

| 端点 | 方法 | 说明 |
|---|---|---|
| `/queue` | GET | 最新队列快照（含物品名、稀有度、价格） |
| `/watched` | GET | 当前关注物品 ID 列表 |
| `/watched/add` | POST | 添加关注 `{ids: [123, 456]}` |
| `/watched/remove` | POST | 移除关注 `{ids: [123]}` |
| `/watched/reload` | POST | 从 disk 重新加载关注列表 |
| `/items` | GET | 完整物品 ID → 名称字典 |
| `/grades` | GET | 完整物品 ID → 稀有度字典 |
| `/price/<id>` | GET | 查询单个物品的 Steam 价格 |
| `/price/stats` | GET | 价格缓存统计 |
| `/health` | GET | 服务状态 |

## ⚙️ 命令行参数

```bash
# HTTP 模式（默认）
python src/tbh_reader.py http --host 127.0.0.1 --port 18765

# CLI 模式（终端输出 JSON Lines）
python src/tbh_reader.py cli

# 跳过单实例检查（危险，可能导致游戏崩溃）
python src/tbh_reader.py http --force
```

## 🔍 工作原理

1. **Frida 注入** — 通过 `frida.attach()` 挂载到 `TaskBarHero.exe` 进程
2. **Agent 扫描** — `drop_items_agent.js` 扫描 IL2CPP 堆内存，定位 `vw` 类的 `Dictionary<EBoxType, List<BoxData>>` 字段
3. **数据解码** — 解码 `ObscuredInt`（XOR + 位移）还原物品 ID
4. **队列推送** — Agent 通过 `send()` 将队列 JSON 发送到 Python 端
5. **HTTP 服务** — Python 端提供 REST API + 静态文件服务
6. **Steam 价格** — 异步队列查询 Steam Market API，600ms 节流，6 小时缓存

## 📊 Steam 价格

- 数据源：`steamcommunity.com/market/priceoverview/`（appid=3678970）
- 币种：CNY（¥）
- 仅可交易稀有度（Legendary / Immortal / Arcana / Beyond）会查询价格
- 缓存 6 小时，失败项 10 分钟后重试
- 受 Steam API 限速，中国大陆可能需要加速器才能访问

## ⚠️ 注意事项

- **需管理员权限** — Frida attach 需要管理员权限才能读取游戏内存
- **单实例运行** — 同时运行两个实例会注入两次 Frida，导致游戏崩溃
- **只读不写** — 本工具仅读取内存，不修改游戏数据
- **Steam API 限制** — 中国境内访问 Steam 社区可能需要加速器

## 🙏 致谢

- [Frida](https://frida.re/) — 动态二进制插桩框架
- [tbh-copilot](https://github.com/shigake/tbh-copilot) — Steam 市场名映射数据（MIT）
- [xmodhub](https://github.com/xmodhub/xmodhub) — 原始 Frida agent（本项目复用其 `drop_items_agent.js`）

## 📄 License

[MIT](LICENSE) © Lynkzhang
