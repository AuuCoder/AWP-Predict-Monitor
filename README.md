# AWP Predict Fleet

`AWP Predict Fleet` 是一个面向 **AWP Predict WorkNet** 的本地多钱包控制台。

它的目标是让用户用一个本地应用完成这些事情：

- 创建和管理多个钱包
- 配置不同钱包使用的模型、`base-url`、`api-key`
- 执行 AWP 注册、质押、分配到 Predict
- 启动和停止预测循环
- 查看余额、历史预测、健康状态
- 查看日志并导出

当前主界面是：

- `AWP Predict Fleet.app`
- 或本地浏览器控制台

不是旧的 Tk 原生窗口模式。

---

## 功能概览

### 钱包管理

- 创建新钱包
- 恢复已有钱包
- 钱包列表展示
- 删除钱包
- 支持钱包备注/实例名

### AWP 动作

- `注册 AWP`
- `质押 AWP`
- `分配到 Predict`
- `取消分配`
- `质押并分配`

### 预测控制

- 启动预测
- 停止预测
- 查看运行状态
- 查看余额
- 查看历史预测
- 健康检查

### 日志功能

- `[时间][钱包名][级别] 内容`
- 按钱包过滤
- 只看 `ERROR`
- 导出日志文件
- 分页查看

---

## 当前默认值

- 默认模型：`gpt-5.4`
- 默认锁仓天数：`3`
- 默认目标 WorkNet：`845300000003`

也就是：

- `Predict WorkNet`

---

## 目录结构

### 核心脚本

- [awp_predict_browser_app.py](/Users/chole/项目/bot/awp-predict/awp_predict_browser_app.py)
- [awp_predict_fleet.py](/Users/chole/项目/bot/awp-predict/awp_predict_fleet.py)
- [awp_predict_manager.py](/Users/chole/项目/bot/awp-predict/awp_predict_manager.py)
- [predict_loop.py](/Users/chole/项目/bot/awp-predict/predict_loop.py)
- [predict_monitor.py](/Users/chole/项目/bot/awp-predict/predict_monitor.py)

### 配置与模板

- [config.py](/Users/chole/项目/bot/awp-predict/config.py)
- [examples/default.env.example](/Users/chole/项目/bot/awp-predict/examples/default.env.example)
- [examples/wallet02.env.example](/Users/chole/项目/bot/awp-predict/examples/wallet02.env.example)

### 依赖目录

- [vendor/awp-skill](/Users/chole/项目/bot/awp-predict/vendor/awp-skill)
- [vendor/awp-wallet](/Users/chole/项目/bot/awp-predict/vendor/awp-wallet)

### 打包脚本

- [build_mac_app.sh](/Users/chole/项目/bot/awp-predict/scripts/build_mac_app.sh)

---

## 数据目录

macOS 应用当前默认把数据放在 **app 同目录**：

```text
AWP Predict Fleet.app
awp-predict-data/
```

其中包括：

- `fleet.json`
- `app.log`
- `awp-chain-cache.json`
- `wallets/`
- `env/`
- `runtime/`

关键文件：

- `awp-predict-data/fleet.json`
  多钱包主配置

- `awp-predict-data/app.log`
  主日志

- `awp-predict-data/awp-chain-cache.json`
  AWP 链上状态缓存

---

## 界面上会显示哪些状态

每个钱包卡片当前会展示：

- 备注
- 地址
- 配置组
- 模型
- 监控端口
- 运行状态
- 预测循环
- 监控服务
- 是否可预测
- 余额
- 是否已注册 AWP
- 已质押 AWP
- 已分配 AWP
- 是否已分配到 Predict
- 每日次数
- 最近成功提交
- 最近错误

说明：

- 钱包卡片状态适合作为快速参考
- **真实预测是否成功**，建议打开对应监控页确认，例如：
  - `http://127.0.0.1:8791/dashboard`

---

## macOS 使用方式

### 直接运行

当前已提供 macOS 应用包：

```text
dist/AWP Predict Fleet.app
```

双击后会：

1. 启动本地控制台服务
2. 自动打开浏览器页面
3. 显示钱包、AWP 状态、预测状态、日志

### 重新打包 macOS 版本

在项目目录执行：

```bash
cd /Users/chole/项目/bot/awp-predict
bash scripts/build_mac_app.sh
```

生成结果：

```text
dist/AWP Predict Fleet.app
```

---

## Linux 版本打包说明

当前仓库没有现成的 `build_linux.sh` 成品脚本，但结构已经适合整理为 Linux 便携版。

推荐步骤：

### 1. 准备 Linux 环境

确保有：

- `python3`
- `node`
- `git`

### 2. 获取源码

```bash
git clone <your-repo>
cd awp-predict
```

### 3. 安装运行依赖

Linux 版推荐先按源码模式运行：

```bash
python3 awp_predict_browser_app.py
```

如果要做便携包，建议打成：

```text
awp-predict-linux/
  awp_predict_browser_app.py
  awp_predict_fleet.py
  predict_loop.py
  predict_monitor.py
  vendor/
  examples/
  scripts/
```

然后配一个启动脚本，例如：

```bash
#!/usr/bin/env bash
set -e
python3 awp_predict_browser_app.py
```

### 4. 打包建议

可以选择：

- `.tar.gz` 便携包
- `systemd` 部署包
- 直接源码运行

---

## Windows 版本打包说明

当前仓库没有现成的 `build_windows.bat` / `build_windows.ps1` 成品脚本，但可以按下面方式整理。

### 1. 准备 Windows 环境

建议准备：

- Python 3
- Node.js
- Git

### 2. 获取源码

```powershell
git clone <your-repo>
cd awp-predict
```

### 3. 先以源码方式运行

```powershell
python awp_predict_browser_app.py
```

### 4. 打包思路

Windows 版推荐先做成：

- 源码目录 + 启动脚本
- 或后续再封装成 `.exe`

一个最简单的启动脚本示例：

```powershell
python awp_predict_browser_app.py
```

### 5. 实际建议

因为这个项目依赖：

- Python
- Node runtime
- `awp-wallet`
- `predict-agent`

Windows 版最稳的方案通常是：

1. 先在 Windows 环境里把源码跑通
2. 再决定是否做 `.exe` 封装

---

## AWP / Predict 操作建议

### 标准流程

1. 创建钱包
2. 注册 AWP
3. 向钱包转入 AWP
4. 质押
5. 分配到 Predict
6. 启动预测

## App 使用流程

如果用户是第一次使用 app，推荐按下面顺序操作：

### 1. 创建钱包

- 打开 `AWP Predict Fleet.app`
- 在“创建新钱包”区域填写：
  - 钱包名
  - 可选的备注/配置组
- 点击：
  - `创建/打开钱包`

建议：

- 钱包名使用英文、数字、`-`、`_`
- 例如：
  - `default`
  - `default1`
  - `wallet02`

### 2. 注册 AWP

- 在钱包列表里勾选刚创建的钱包
- 点击：
  - `注册 AWP`

完成后，钱包应当至少具备：

- 已注册 AWP

### 3. 外部转入 AWP

- 把 AWP 转到该钱包地址
- 等链上到账

### 4. 质押并分配

- 在页面里设置：
  - `AWP 数量`
  - `锁定天数`
  - `Worknet = 845300000003`
- 勾选目标钱包
- 点击：
  - `质押并分配`

当前默认锁定天数：

- `3 天`

### 5. 检查是否分配到 Predict

钱包卡片里重点看这些字段：

- `是否已注册 AWP`
- `已质押 AWP`
- `已分配 AWP`
- `是否已分配到 Predict`
- `是否可预测`

如果平台还没放行，日志里可能会出现：

- `Stake gate`

### 6. 启动预测

- 勾选目标钱包
- 点击：
  - `启动预测`

启动后重点观察：

- `运行状态`
- `预测循环`
- `监控服务`
- `最近成功提交`
- `每日次数`
- `最近错误`

### 7. 查看日志

如果要确认程序当前在做什么，可以看日志区：

- 支持按钱包过滤
- 支持只看 `ERROR`
- 支持分页
- 支持导出

推荐重点看：

- 是否有新的 `txHash`
- 是否有 `Stake gate`
- 是否有模型上游错误
- 是否有新的成功提交记录

### 当前项目里推荐使用的动作

- `注册 AWP`
- `质押 AWP`
- `分配到 Predict`
- `质押并分配`

### 说明

`质押 AWP` 和 `分配到 Predict` 是两个不同动作。  
如果需要一步完成，使用：

- `质押并分配`

如果需要把已经分配出去的额度收回，使用：

- `取消分配`

---

## 常见注意事项

### 1. 钱包名建议使用英文/数字

推荐：

- `default`
- `default1`
- `wallet02`

不推荐直接用中文作为底层钱包标识。

### 2. 平台放行比页面显示更重要

即使页面显示已分配，最终还是要看：

- 平台是否放行预测提交

### 3. 链上结果优先级最高

和 AWP 相关的操作，最可信的是：

- 链上交易回执
- 官方 AWP API
- Predict 平台实际返回

---

## 常见问题

### 1. 钱包卡片显示和预期不一致

钱包卡片的状态可能会因为：

- 平台延迟
- AWP 官方状态口径不一致
- 缓存更新时差

出现短时间不一致。

如果你要确认 **真实预测是否已经成功**，优先查看：

- `http://127.0.0.1:8791/dashboard`
- 或对应钱包 monitor 页面

### 2. 分配数量异常或大于预期

如果你在官网看到：

- 分配数量不对
- 或者像是“叠加分配”

建议先：

1. 去官网执行取消分配
2. 回到软件中重新执行分配

当前 app 里也已经提供：

- `取消分配`
