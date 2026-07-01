TBHStargaze - 宝箱观星
========================================

【首次使用】
  1. 双击 tools\install-deps.bat 安装依赖（只需要一次，几十秒）
  2. 启动游戏 TaskBarHero
  3. 右键 启动.bat -> 以管理员身份运行
  4. 浏览器会自动打开 http://127.0.0.1:18765/

【日常使用】
  1. 开游戏
  2. 右键 启动.bat -> 以管理员身份运行
  3. 浏览器看队列

【网页功能】
  - 普通宝箱 / 首领宝箱 / ACT 宝箱 三栏并排
  - 每个物品按品质上色
  - "即将掉落"第一个高亮显示
  - 右上角搜索框可添加关注物品
  - 命中关注物品时浏览器响铃 + 弹窗
  - 关注列表自动持久化保存

【刷新规则】
  - 切到"掉落不同等级箱子"的关卡 -> 队列刷新
  - 同等级关卡重进通常不刷新
  - 不满意当前掉落？跨等级切换关卡即可换一批

【常见问题】
  Q: 浏览器打不开？
  A: 检查 PowerShell 是不是有 "ERROR: 已有另一个 tbh_reader 在运行"
     有的话杀掉旧进程：任务管理器找 python.exe，结束之

  Q: "Frida attach failed: PermissionDeniedError"
  A: 必须以管理员身份运行 启动.bat

  Q: 游戏闪退？
  A: 千万不要同时跑两个本工具实例！工具已经加锁但保险起见手动确认

  Q: 中文乱码？
  A: 启动.bat 已经设置了 UTF-8，如果还乱码就用浏览器看，控制台只是辅助

【单实例锁】
  本工具同一时间只允许一个实例运行（端口 18764 是锁）。
  如果误开两个，第二个会直接报错退出，不会伤游戏。

【目录结构】
  启动.bat                启动入口
  src\
    tbh_reader.py         主程序
    web\                  网页界面
    resources\            Frida agent + 物品名 + 配置
  python-portable\        内置 Python 3.12
  wheels\                 frida + psutil 离线安装包
  tools\
    install-deps.bat      首次安装依赖

【更新】
  覆盖整个文件夹即可。watched_ids.json 配置文件在 src\resources\，
  备份它就能保留你的关注列表。

【风险声明】
  本工具复用自 TBHStargaze 原始 Frida agent，只读游戏内存。
  TaskBarHero 是单机游戏，不影响其他玩家。
  自行承担杀软误报风险（加白名单即可）。
