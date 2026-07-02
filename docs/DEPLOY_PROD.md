# 生产部署 Runbook（单机 Docker Compose + Caddy 自动 HTTPS）

> 形态：一台 Linux 云服务器（ECS/轻量），Docker Compose 全栈 ——
> Caddy(反代+自动证书) + 前端(Next.js) + 后端(FastAPI) + Postgres + Redis。
> 数据卷持久化；只有 Caddy 对公网开 80/443。LLM 用 DeepSeek + Tavily 搜索。
>
> 本地开发 / 快速验证见仓库根目录 [README.md](../README.md) 的「本地部署」一节。这里只讲上线。
> 本地接 Jaeger 观测见下方 §7 可选增强。

---

## 0. 架构一图

```
                      :443 / :80
   公网 ──► Caddy(自动 HTTPS) ──┬─ /api/*, /health ─► backend:8000  ─┬─► postgres:5432 (卷)
                               │                                    └─► redis:6379    (卷)
                               └─ 其余 ─────────────► frontend:3000
   前端请求同源打 https://<域名>/api/... ，由 Caddy 分流；WebSocket 自动透传。
```

---

## 1. 前置准备

| 项 | 说明 |
|---|---|
| 一台 Linux 服务器 | 建议 ≥ 2 vCPU / 4 GB；Ubuntu 22.04+ / Debian 12 |
| 域名 | 一个 A 记录指向服务器**公网 IP**（证书签发依赖它） |
| 放行端口 | 安全组/防火墙开 **80 + 443**（80 用于 ACME 验证，别只开 443） |
| DeepSeek API key | `DEEPSEEK_API_KEY` |
| Tavily（或 Serper）key | DeepSeek 无自带搜索，**必须**配一个搜索 key，否则采集退回 mock |

### 1.1 装 Docker（Ubuntu/Debian）

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 之后重新登录一次，让当前用户免 sudo 用 docker
docker compose version          # 确认 Compose v2 可用
```

### 1.2 DNS

把 `yourdomain.com` 的 **A 记录**指到服务器公网 IP。验证：

```bash
dig +short yourdomain.com    # 应回显你的服务器 IP
```

> 证书在首次 `up` 时由 Caddy 自动签。DNS 没生效就签不下来——先确认 `dig` 正确再启动。

---

## 2. 拉代码 + 配环境变量

```bash
git clone <你的仓库地址> cap && cd cap
git checkout main             # 或你要上线的分支

cp .env.prod.example .env.prod
vim .env.prod                 # 按下面清单填
```

`.env.prod` 必填项：

| 变量 | 填什么 |
|---|---|
| `DOMAIN` | `yourdomain.com`（不带 https://） |
| `ACME_EMAIL` | 你的邮箱（证书到期通知） |
| `CORS_ORIGINS` | `https://yourdomain.com` |
| `DEEPSEEK_API_KEY` | DeepSeek key |
| `TAVILY_API_KEY` | Tavily key（或改用 `SERPER_API_KEY`） |
| `JWT_SECRET` | `openssl rand -hex 32` 生成的强随机串（漏配后端会直接拒绝启动） |
| `POSTGRES_PASSWORD` | 强密码 |

> `STORAGE_MODE` / `POSTGRES_DSN` / `REDIS_URL` **不要**在 `.env.prod` 里设——
> compose 会注入容器内地址，手动设反而写错 host。

---

## 3. 一键起全栈

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

首次会 build 两个镜像（后端 ~1-2 min，前端 ~2-4 min）+ 拉 PG/Redis/Caddy。

看状态（全部 `healthy`/`running` 才算好）：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

后端启动日志里看到 `API started (mode=postgres, ...)` 即接上了 Postgres：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
```

---

## 4. 验证上线

```bash
# 1) 健康检查（经 Caddy，应是 200 + HTTPS 证书有效）
curl -i https://yourdomain.com/health

# 2) 版本自报（确认线上是你刚部署的 git SHA / release tag）
curl -s https://yourdomain.com/version
#   -> {"version":"v1.2.0","git_sha":"95e9a9e","schema_version":"1.2.0"}

# 3) 浏览器打开 https://yourdomain.com —— 进前端工作台
```

**首次使用要先注册账号**：项目接口都要登录（owner 取自 JWT）。
打开站点 → 注册/登录 → 新建项目 → 跑分析。

> 命令行注册示例（字段以前端注册表单为准）：
> ```bash
> curl -X POST https://yourdomain.com/api/auth/register \
>   -H 'Content-Type: application/json' \
>   -d '{"email":"you@example.com","password":"your-strong-pw"}'
> ```

---

## 5. 日常运维

```bash
C="docker compose --env-file .env.prod -f docker-compose.prod.yml"

$C ps                       # 状态
$C logs -f backend          # 跟后端日志
$C logs -f caddy            # 证书/反代日志（签证书失败看这里）
$C restart backend          # 重启单个服务
$C down                     # 停（保留数据卷）
$C up -d --build            # 改代码后重新部署

# 进 Postgres 看表 / 备份
$C exec postgres psql -U app -d app -c '\dt'
$C exec postgres pg_dump -U app app > backup_$(date +%F).sql
```

### 版本管理 / 部署 / 回滚

**服务器是「同一个仓库的部署检出」**（见 §2 的 `git clone`），不是散文件拷贝——
这样 `git rev-parse HEAD` 永远能答「线上是哪一版」，绝不和 GitHub 分叉。
**永远不在服务器上改代码 / commit**，只拉 GitHub 上定好的版本。

用 `scripts/deploy.sh` 一键部署（它会切版本、把 git SHA / release tag 烤进镜像、
重建、并从容器内自检 `/version` 确认新容器真的换上了）：

```bash
scripts/deploy.sh            # 部署当前 checkout
scripts/deploy.sh v1.2.0     # 部署某个 release tag（推荐：上线打 tag）
scripts/deploy.sh main       # 部署 main 最新
scripts/deploy.sh v1.1.0     # 回滚 = 重新部署上一个 tag
```

发版流程：本地 PR 合进 `main` → 打 tag（`git tag v1.2.0 && git push --tags`，
版本号对齐 `SCHEMA_VERSION`）→ 服务器 `scripts/deploy.sh v1.2.0`。

**确认线上版本**（不用再猜文件 mtime）：

```bash
C="docker compose --env-file .env.prod -f docker-compose.prod.yml"
$C exec backend curl -fsS http://localhost:8000/version
# -> {"version":"v1.2.0","git_sha":"95e9a9e","schema_version":"1.2.0"}
```

> 改了 `DOMAIN` 必须 `--build`：前端把 `NEXT_PUBLIC_API_BASE` 烘焙进了产物
> （`deploy.sh` 始终带 `--build`，无需操心）。

---

## 6. 故障排查

| 现象 | 原因 / 修法 |
|---|---|
| `https` 打不开 / 证书错误 | DNS 没指对，或安全组没开 80。`dig +short DOMAIN` 核对 IP；`logs caddy` 看 ACME 报错 |
| 站点开了但调接口 401/跨域 | 没登录（先注册）；或 `CORS_ORIGINS` 没设成 `https://<DOMAIN>` |
| 采集只出 mock、没真实数据 | 没配 `TAVILY_API_KEY`（DeepSeek 无自带搜索）。补上重启 backend |
| 后端起不来 `no LLM API key` | `.env.prod` 里 `DEEPSEEK_API_KEY` 没填 |
| `mode=memory` 而非 postgres | compose 的 `environment` 没生效——确认用了 `-f docker-compose.prod.yml` 且 `POSTGRES_PASSWORD` 已设 |
| WebSocket 实时进度不刷新 | Caddy 已自动透传 Upgrade；检查浏览器是否走 `wss://<DOMAIN>/api/.../events`，及 `logs caddy` |
| Collector 抓 SPA 内容空 | 本镜像不含 Chromium（精简）。需要真渲染时见 §7 |

---

## 7. 可选增强

- **SPA 真渲染抓取**：本后端镜像不装 Playwright/Chromium（collector 自动降级）。
  需要时基于 `Dockerfile.backend` 加 `pip install '.[tools-crawl4ai]'` +
  `python -m playwright install --with-deps chromium`，并在 collector 开 `enable_crawl4ai=True`。镜像会大很多。
- **托管数据库**：把 PG/Redis 换成云托管，删掉 compose 里这两个 service，
  把真实连接串写进 `.env.prod` 的 `POSTGRES_DSN` / `REDIS_URL`，并删掉 compose `backend.environment` 里对应的覆盖。
- **可观测**：配 `OTEL_EXPORTER_OTLP_ENDPOINT` 指向 Jaeger/Tempo，agent span + LLM call 全量入外部 trace。
- **横向扩展**：state 都在 PG/Redis，后端可多副本 + 负载均衡；但进程内 LLM-call 实时缓冲是 per-worker（已持久化的不受影响）。当前默认 `--workers 1`。

---

## 8. 安全清单

- [ ] `JWT_SECRET` 是 `openssl rand -hex 32`，不是示例值
- [ ] `POSTGRES_PASSWORD` 是强密码
- [ ] `.env.prod` 没提交进 git（已被 `.gitignore` 排除，`git status` 确认）
- [ ] 安全组只开 80/443（PG/Redis 不对公网暴露——compose 里它们没有 `ports`）
- [ ] 服务器开了系统防火墙 / fail2ban（SSH 防爆破）
