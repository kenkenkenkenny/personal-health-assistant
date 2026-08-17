# Personal Health Assistant

这是一个个人使用的 Google Health API 健康助手。当前 MVP 已完成 Phase 1–5：

- 从 `.env` 读取并验证配置；
- 通过 Google 官方 OAuth 2.0 库完成授权码流程；
- 将 refresh token 保存在本机 `token.json`（权限 `0600`）；
- access token 过期时自动刷新；
- 通过统一的 Google Health REST 客户端读取单日 Steps；
- 读取主睡眠、单独识别 nap，并优先使用官方 `minutesAsleep`；
- 读取 reconciled 每日静息心率与每日 HRV；
- 每日 HRV 缺失时兼容样本型 RMSSD HRV；
- 使用 `HealthService` 归一化指标，单项失败不影响其他指标；
- 读取 active minutes、total calories 和 exercise minutes；
- 读取睡眠阶段、全天心率、距离、楼层、活跃区间分钟、血氧、呼吸率和 VO₂ Max；
- SQLite 按日期 upsert，并支持 7/30/90 天历史查询；
- Python 计算两个 7 天窗口的 average/min/max/trend；
- 通过 OpenAI Python SDK 的 Responses API 调用 AIHubMix；
- 支持 Discord webhook 或 SMTP 邮件通知；
- APScheduler 每日运行，并提供 Docker 常驻部署；
- 对 401、403、429、5xx、超时、分页、无数据和异常 JSON 做明确处理。

AIHubMix 与通知渠道都是第三方服务。程序只发送 Python 预先计算的汇总和报告，不发送 Google Health 原始数据集。

## 运行环境

- Python 3.12+
- 一个包含 Fitbit、Pixel Watch 或其他兼容来源健康数据的 Google 账号
- Google Cloud 项目

## 1. 配置 Google Cloud 和 OAuth

以下步骤以 Google 官方的 [Google Health API 设置文档](https://developers.google.com/health/setup) 为准：

1. 登录 [Google Cloud Console](https://console.cloud.google.com/)，新建或选择一个项目。
2. 进入 **APIs & Services → Library**，搜索并启用 **Google Health API**。不要启用已淘汰的 Google Fit API。
3. 进入 **Google Auth Platform**，配置 OAuth consent screen：
   - App name：例如 `Personal Health Assistant`
   - User type / Audience：个人项目通常选择 `External`
   - Support email 与 Developer contact：填写自己的邮箱
4. 在 **Audience → Test users** 添加将要读取健康数据的 Google 账号。测试状态下未列入的账号无法授权。
5. 在 **Data Access → Add or remove scopes** 中添加三项只读权限：
   - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
6. 进入 **Clients / Credentials → Create OAuth client ID**，类型选择 **Web application**。
7. 在 **Authorized redirect URIs** 中精确添加：

   ```text
   http://localhost:8080/oauth/callback
   ```

   协议、主机、端口和路径必须与 `.env` 完全一致。
8. 保存 Client ID 和 Client Secret。不要提交或截图分享这些值。

注意：Google 官方说明，OAuth consent screen 保持 **Testing** 状态时，refresh token 通常会在 7 天后过期。个人开发阶段可以重新执行授权；需要长期无人值守时，应理解验证要求后再切换发布状态。

## 2. 创建 Python 环境

在本项目目录执行：

```bash
cd health-assistant
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
export PYTHONPATH="$PWD/src"
```

`src` 布局不会自动加入 Python 搜索路径，因此每个新终端都要设置 `PYTHONPATH`；也可以把最后一行加入该虚拟环境的本地启动脚本。

## 3. 创建 `.env`

```bash
cp .env.example .env
```

填写：

```env
GOOGLE_CLIENT_ID=你的 OAuth Web Client ID
GOOGLE_CLIENT_SECRET=你的 OAuth Web Client Secret
GOOGLE_REDIRECT_URI=http://localhost:8080/oauth/callback

AIHUBMIX_API_KEY=你的 AIHubMix Key
AIHUBMIX_BASE_URL=https://aihubmix.com/v1
AIHUBMIX_MODEL=gpt-5

DATABASE_URL=sqlite:///data/health.db
TIMEZONE=Europe/London
REPORT_TIME=08:00

NOTIFICATION_CHANNEL=discord
DISCORD_WEBHOOK_URL=你的 Discord Webhook URL

SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TO=
SMTP_USE_SSL=false
```

`.env`、`token.json`、`secrets/` 和数据库均已加入 `.gitignore`。模型 ID 必须以 AIHubMix 模型广场当前显示的 ID 为准。

## Render 持久磁盘部署

仓库根目录的 `render.yaml` 会创建一个 Frankfurt 区域的 Starter Background Worker，使用 Docker 常驻调度器，并把 1 GB 持久磁盘挂载到 `/var/data`。SQLite 位于 `/var/data/health.db`，Google 刷新 token 位于 `/var/data/token.json`。

1. 将本目录推送到私有 GitHub 或 GitLab 仓库，确认 `.env`、`token.json`、`data/` 和 `secrets/` 没有被提交。
2. 在 Render Dashboard 选择 **New → Blueprint**，连接仓库并应用 `render.yaml`。
3. 首次创建时填写 Blueprint 提示的四个 secret：`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`AIHUBMIX_API_KEY`、`DISCORD_WEBHOOK_URL`。
4. 打开服务的 **Environment → Secret Files**，新增文件名 `token.json`，内容粘贴本机 `token.json` 的完整 JSON。运行时它出现在 `/etc/secrets/token.json`，程序会验证后仅首次复制到持久磁盘，并设置权限为 `0600`。
5. 如需迁移现有历史，在 Render 服务启用 SSH 后，将本机 `data/health.db` 安全传输为 `/var/data/health.db`；传输前先暂停 Worker，完成后再恢复，避免复制期间 SQLite 正在写入。
6. 查看 Logs，确认出现 `Scheduler started: daily at 08:00 (Europe/London)`。可在 Shell 中运行 `python -m health_assistant.main daily` 做一次真实验证。

Render 的服务文件系统默认是临时的，只有 `/var/data` 下的数据会跨重启与部署保留。持久磁盘只能连接一个服务实例，因此本 Blueprint 固定为单实例，这也符合 SQLite 与单调度器的运行方式。Render 会为磁盘每天生成一次自动快照。

## 4. 执行 OAuth

保持 8080 端口空闲，然后运行：

```bash
python -m health_assistant.main auth
```

命令会：

1. 在终端输出 Google 授权 URL；
2. 在 `localhost:8080` 等待一次回调；
3. 由你手动打开 URL、登录、确认只读权限；
4. Google 跳回 `/oauth/callback`；
5. 程序验证 OAuth state、交换 authorization code，并在项目根目录写入 `token.json`。

成功输出：

```text
Authorization successful. token.json was saved with owner-only permissions.
```

程序不会在日志中输出 access token、refresh token、client secret 或完整 OAuth response。

## 5. 第一个 Steps API 测试

先用官方 Steps `list` endpoint 做最小 GET 连通性检查（最多读取一条记录）：

```bash
python -m health_assistant.main check --date 2026-08-16
```

请求成功时，无论该日期有没有记录，都会明确输出：

```text
Google Health API connection successful: steps data found
```

或：

```text
Google Health API connection successful: no steps data for this date
```

随后验证正式的单日汇总与解析。

读取指定日期：

```bash
python -m health_assistant.main steps --date 2026-08-16
```

不带日期时默认读取 `TIMEZONE` 下的今天：

```bash
python -m health_assistant.main steps
```

有数据时预期：

```text
INFO Fetching steps for 2026-08-16
2026-08-16: 8,234 steps
```

合法但没有可用数据时预期：

```text
2026-08-16: no steps data available
```

这两种结果都代表 API 请求本身成功。实现调用官方 endpoint：

```text
POST https://health.googleapis.com/v4/users/me/dataTypes/steps/dataPoints:dailyRollUp
```

请求区间为目标日期 `00:00`（包含）到次日 `00:00`（不包含），解析 `rollupDataPoints[].steps.countSum`。

## 6. Phase 2 指标测试

Sleep 的 `--date` 表示醒来日期：

```bash
python -m health_assistant.main sleep --date 2026-08-16
```

示例输出：

```json
{
  "total_sleep_minutes": 434,
  "sleep_start": "2026-08-15T22:30:00Z",
  "sleep_end": "2026-08-16T06:30:00Z",
  "nap_minutes": null
}
```

读取静息心率与 HRV：

```bash
python -m health_assistant.main resting-heart-rate --date 2026-08-16
python -m health_assistant.main hrv --date 2026-08-16
```

读取统一的 Phase 2 汇总：

```bash
python -m health_assistant.main health --date 2026-08-16
```

输出中的 `data_quality` 会将每项标记为：

- `available`：成功取得数据；
- `missing`：API 成功，但当天无数据；
- `error`：该指标请求或解析失败，其他指标仍会继续。

## 7. 故障排查

- `Missing required setting(s)`：检查 `.env` 位于 `health-assistant/` 根目录，变量名无拼写错误。
- `redirect_uri_mismatch`：Cloud Console 中的 URI 必须精确为 `.env` 的值；修改后稍等片刻再试。
- `access_denied` 或 “app not verified”：确认登录邮箱已添加到 **Test users**，并确认 consent screen 的 Audience。
- HTTP 403：确认 Google Health API 已启用、三项 scope 已加入 Data Access，并重新执行 `auth` 产生覆盖完整 scope 的 token。
- `invalid_scope`：确认使用 `googlehealth.*.readonly` scope，而不是旧 Google Fit scope。
- `Address already in use`：8080 被其他程序占用；同时修改 Cloud Console redirect URI 与 `.env` 端口后再试。
- 有手表数据但返回 no data：确认授权的是同步该设备数据的同一个 Google 账号，并尝试过去确定有步数的一天。
- refresh 失败：测试状态的 refresh token 可能已过期，删除本地 `token.json` 后重新运行 `auth`。
- HTTP 429/5xx 或网络超时：客户端会指数退避重试；持续失败时稍后重试，并检查代理、防火墙和 Google Cloud 状态。
- AIHubMix 报错：检查 Key、余额、模型 ID 和 `AIHUBMIX_BASE_URL`；daily 会生成不含 AI 解释的安全降级报告。
- Discord 失败：重新生成 webhook，检查目标频道权限；URL 绝不能提交 Git。
- SMTP 失败：多数邮箱要求应用专用密码，不能直接使用账号登录密码。

## 8. SQLite、报告与每日运行

同步今天或指定日期：

```bash
python -m health_assistant.main sync
python -m health_assistant.main sync --date 2026-08-16
```

查询历史：

```bash
python -m health_assistant.main history --days 7
python -m health_assistant.main history --days 30
```

只生成已同步日期的报告，不发送通知：

```bash
python -m health_assistant.main report --date 2026-08-16
```

执行完整流程并发送通知：

```bash
python -m health_assistant.main daily --date 2026-08-16
```

启动常驻 scheduler；默认每天 Europe/London 08:00 同步并报告当天：

```bash
python -m health_assistant.main scheduler
```

Sleep 按醒来日期归属：例如 8 月 16 日晚上入睡、8 月 17 日醒来的主睡眠，应使用 `--date 2026-08-17`。因此默认查询今天，能取得昨晚结束于今天的睡眠。08:00 生成的步数、活跃时间和热量则是当天截至报告时刻的部分数据。

### Discord 配置

在 Discord 目标频道进入 **Edit Channel → Integrations → Webhooks → New Webhook**，复制 URL，然后设置：

```env
NOTIFICATION_CHANNEL=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Discord 收到的是最终中文报告，不是原始数据。程序关闭 `allowed_mentions`，报告中的 `@` 不会触发通知提及。

### 邮件配置

```env
NOTIFICATION_CHANNEL=email
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=you@example.com
SMTP_PASSWORD=应用专用密码
SMTP_FROM=you@example.com
SMTP_TO=you@example.com
SMTP_USE_SSL=false
```

端口 587 使用 STARTTLS；如果服务商要求隐式 TLS，通常配置端口 465 和 `SMTP_USE_SSL=true`。

## 9. Docker 部署

OAuth 必须先在本机完成。然后准备容器可写的 secret 目录：

```bash
mkdir -p secrets data
cp token.json secrets/token.json
chmod 600 secrets/token.json
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f health-assistant
```

手动验证容器内完整流程：

```bash
docker compose run --rm health-assistant python -m health_assistant.main daily
```

停止：

```bash
docker compose down
```

部署到云服务器或支持 Docker 的平台时，必须满足：

- scheduler 容器长期运行，而不是部署成会休眠的 HTTP Web Service；
- `/app/data` 使用持久卷；
- `/run/health-secrets` 使用可写持久卷，因为 access token 刷新会更新 `token.json`；
- `.env` 内容通过平台 secrets 注入，不写入镜像；
- 不公开任何入站端口；
- 只有一个 scheduler 实例，避免重复通知。

实际云平台创建需要你的平台账号与授权；Docker 版本可以先在本机或任意常驻 Linux 主机直接部署。

## 10. 运行测试

测试全部 mock Google Health 与 OAuth，不会发送真实网络请求：

```bash
pytest -q
```

当前预期为 `28 passed`，Google Health、AIHubMix 和 Discord 均使用 mock，不会发送真实请求。

## 文件职责

| 文件 | 单一职责 |
|---|---|
| `config.py` | 从 `.env` 加载并验证配置 |
| `logging_utils.py` | 安全日志配置与敏感字段脱敏 |
| `google_auth.py` | Google OAuth、token 本地保存与刷新 |
| `google_health.py` | Google Health HTTP、重试、分页与各指标显式解析 |
| `main.py` | OAuth callback 与 Phase 1–2 CLI |
| `models.py` | `SleepData` 与统一 `DailyHealthSummary` |
| `database.py` | SQLite schema、upsert 和历史查询 |
| `health_service.py` | 独立抓取、错误隔离与统一归一化 |
| `health_analyzer.py` | 统计计算与 AIHubMix Responses 调用 |
| `report_service.py` | sync、report、daily 工作流与失败降级 |
| `notification_service.py` | Discord、SMTP 与关闭通知适配器 |
| `scheduler.py` | APScheduler 每日触发器 |

## 安全边界

- 只申请 Google Health 只读权限。
- `token.json` 以原子替换方式保存，并设置为仅当前用户可读写。
- HTTP 错误只记录状态码，不记录响应正文，防止意外泄露健康数据。
- 只向 AIHubMix 发送必要汇总；选择 Discord/邮件后，最终报告会发送给相应服务商。
- 本项目只用于健康趋势总结，不进行疾病诊断。
