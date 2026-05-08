# AWP Predict Multi-Wallet Pack

这套目录现在支持两种运行方式：

1. 单钱包默认实例
2. 多钱包实例化 `systemd` 服务

另外新增了一份给用户直接使用的单文件入口：

- [awp_predict_manager.py](/Users/chole/项目/bot/awp-predict/awp_predict_manager.py)
- [awp_predict_fleet.py](/Users/chole/项目/bot/awp-predict/awp_predict_fleet.py)

它现在能做这些事：

- 首次自动生成钱包
- 首次返回钱包地址和私钥，后续复用同一个钱包
- 默认模型使用 `gpt-5.4`
- 支持改成 `gpt-5`、`gpt-5.4` 等其他模型
- 支持外置 `config.json` 保存 `model / base-url / api-key`
- 手动 stake AWP
- 一键启动预测后台
- 自动分配不重复的 monitor 端口
- 查询钱包地址 / 私钥 / 服务状态

如果你要的是“一个程序直接控制多个钱包”，优先用：

- `awp_predict_fleet.py`

当前 mac 软件版会：

- 双击后启动本地服务
- 自动打开浏览器向导页
- 在页面里实时显示当前步骤和日志

首开时还会自动做这些事：

- 如果本机没有 `~/.codex/skills/awp-skill`
- 就从软件自带的 `vendor/awp-skill` 自动安装

Mac 软件的数据目录现在不再默认写到 `~/Library/Application Support/...`，
而是跟着软件文件所在目录走：

```text
AWP Predict Fleet.app
awp-predict-data/
```

其中会保存：

- `fleet.json`
- `wallets/`
- `env/`

它额外支持：

- 多钱包集中管理
- 多钱包共享同一套 profile
- 单个钱包独立覆盖模型 / base-url / api-key
- 单个和批量查询余额
- 单个和批量查询历史预测
- 一键检查所有钱包预测状态是否正常

核心思路：

- `gateway` 继续共用一套 `awp-predict-gateway.service`
- 每个钱包单独起一套：
  - `awp-predict-loop@<instance>`
  - `awp-predict-monitor@<instance>`
- 每个实例只需要一份独立 env 文件：
  - `/etc/awp-predict/<instance>.env`

## 文件结构

- `predict_loop.py`
  预测 loop，已经改成从环境变量读取钱包路径、实例名、服务名
- `predict_monitor.py`
  监控面板，已经改成从环境变量读取端口、服务名、journal unit
- `predict-loop.sh`
  统一启动入口，启动前自动导出当前钱包私钥和地址
- `systemd/awp-predict-loop@.service`
  多实例 loop 模板
- `systemd/awp-predict-monitor@.service`
  多实例 monitor 模板
- `examples/default.env.example`
  单钱包默认示例
- `examples/wallet02.env.example`
  第二个钱包示例

## 推荐目录

共享代码目录：

```bash
/srv/awp-predict
```

实例 env：

```bash
/etc/awp-predict/default.env
/etc/awp-predict/wallet02.env
/etc/awp-predict/wallet03.env
```

钱包目录示例：

```bash
/srv/awp-miner/.wallet
/srv/awp-wallets/wallet02/.wallet
/srv/awp-wallets/wallet03/.wallet
```

## 单钱包默认实例

复制默认 env：

```bash
mkdir -p /etc/awp-predict
cp /srv/awp-predict/examples/default.env.example /etc/awp-predict/default.env
```

安装 service：

```bash
cp /srv/awp-predict/systemd/awp-predict-gateway.service /etc/systemd/system/
cp /srv/awp-predict/systemd/awp-predict-loop.service /etc/systemd/system/
cp /srv/awp-predict/systemd/awp-predict-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now awp-predict-gateway awp-predict-loop awp-predict-monitor
```

## 多钱包实例

以 `wallet02` 为例：

1. 准备 env

```bash
mkdir -p /etc/awp-predict
cp /srv/awp-predict/examples/wallet02.env.example /etc/awp-predict/wallet02.env
```

2. 修改这些字段

- `AWP_WALLET_HOME`
- `AWP_AGENT_ID`
- `AWP_PREDICT_MONITOR_PORT`
- `AWP_PREDICT_LOOP_SERVICE`
- `AWP_PREDICT_MONITOR_SERVICE`
- `AWP_PREDICT_LOOP_JOURNAL_UNIT`

3. 安装模板 service

```bash
cp /srv/awp-predict/systemd/awp-predict-loop@.service /etc/systemd/system/
cp /srv/awp-predict/systemd/awp-predict-monitor@.service /etc/systemd/system/
systemctl daemon-reload
```

4. 启动实例

```bash
systemctl enable --now awp-predict-loop@wallet02
systemctl enable --now awp-predict-monitor@wallet02
```

如果 `gateway` 还没启动，再补：

```bash
systemctl enable --now awp-predict-gateway
```

## 常用检查

看服务：

```bash
systemctl status awp-predict-gateway awp-predict-loop@wallet02 awp-predict-monitor@wallet02 --no-pager
```

看 loop 日志：

```bash
journalctl -u awp-predict-loop@wallet02 -n 80 --no-pager
```

看监控接口：

```bash
curl -s http://127.0.0.1:8792/api/dashboard
```

## 关键约束

- 每个实例的 `AWP_WALLET_HOME` 必须不同
- 每个实例的 `AWP_AGENT_ID` 最好不同
- 每个实例的 `AWP_PREDICT_MONITOR_PORT` 必须不同
- 目前设计为多个钱包共用一套 `gateway`
- `query-status.py` 可能滞后，最终以链上与实际提交结果为准

## 单文件入口用法

### 单钱包入口

首开程序，如果没有钱包就自动创建：

```bash
python3 /srv/awp-predict/awp_predict_manager.py open
```

默认会使用 `default` 实例。

- 第一次执行：自动建钱包，返回地址和私钥
- 后续执行：复用老钱包，不再新建

如果要开第二个钱包：

```bash
python3 /srv/awp-predict/awp_predict_manager.py open wallet02
```

默认模型是 `gpt-5.4`。如果要自定义：

```bash
python3 /srv/awp-predict/awp_predict_manager.py open wallet03 \
  --model gpt-5 \
  --base-url https://your-api.example/v1
```

每个实例会生成：

- `config.json`
- `*.env`
- 钱包目录

其中 `config.json` 是给用户改的，路径类似：

```bash
/srv/awp-wallets/default/config.json
/srv/awp-wallets/wallet02/config.json
```

你需要在 `config.json` 里填：

- `openai.api_key`
- `openai.base_url`
- `openai.model`

查看钱包地址：

```bash
python3 /srv/awp-predict/awp_predict_manager.py wallet default
```

查看钱包私钥：

```bash
python3 /srv/awp-predict/awp_predict_manager.py wallet default --show-private-key
```

手动质押并分配到 Predict：

```bash
python3 /srv/awp-predict/awp_predict_manager.py stake default \
  --amount 1000 \
  --lock-days 3
```

外部转入 AWP 并完成 stake 后，再手动一键启动预测：

```bash
python3 /srv/awp-predict/awp_predict_manager.py start default
```

`start` 会同时拉起：

- 预测 loop
- monitor
- 共用 gateway

也就是你说的“启动检测功能后，维活程序也一起启动”。

修改模型：

```bash
python3 /srv/awp-predict/awp_predict_manager.py config default \
  --model gpt-5 \
  --api-key sk-xxxx \
  --restart
```

查看后台状态：

```bash
python3 /srv/awp-predict/awp_predict_manager.py status default
```

### 多钱包舰队入口

首开默认钱包：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py open
```

创建共享 profile：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py profile-set common \
  --model gpt-5.4 \
  --base-url https://your-api.example/v1 \
  --api-key sk-xxxx
```

让多个钱包共用同一套配置：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py open wallet02 --profile common
python3 /srv/awp-predict/awp_predict_fleet.py open wallet03 --profile common
```

让单个钱包独立覆盖配置：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py wallet-config wallet03 \
  --model gpt-5 \
  --api-key sk-wallet03
```

批量启动全部钱包：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py start --all
```

一键检查全部钱包是否正常：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py health --all
```

批量看余额：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py balance --all
```

批量看历史：

```bash
python3 /srv/awp-predict/awp_predict_fleet.py history --all --limit 5
```
