#!/usr/bin/env bash
# 一键部署：切到指定版本 → 注入 git SHA / release tag → 重建后端镜像 → 起栈 → 验证 /version。
#
# 在服务器的「部署检出」(git clone 出来的 /opt/cap) 里跑。前提：
#   - 当前目录是同一个仓库的 git 检出（不是散文件拷贝）
#   - .env.prod 已就位（真实密钥，只在服务器，不进 git）
#
# 用法：
#   scripts/deploy.sh            # 部署当前 checkout（默认，不切版本）
#   scripts/deploy.sh v1.2.0     # 部署某个 release tag
#   scripts/deploy.sh main       # 部署某个分支最新
#   REF=v1.2.0 scripts/deploy.sh # 等价
#
# 回滚 = 重新部署上一个 tag：scripts/deploy.sh v1.1.0
set -euo pipefail

cd "$(dirname "$0")/.."

# ---- 前置校验 ----
if [[ ! -d .git ]]; then
  echo "!! 当前目录不是 git 检出——本脚本要求服务器用 git clone 部署，而不是散文件拷贝。" >&2
  echo "   见 docs/DEPLOY_PROD.md「服务器版本管理」。" >&2
  exit 1
fi
if [[ ! -f .env.prod ]]; then
  echo "!! 缺 .env.prod（真实密钥），无法部署。参考 .env.prod.example 准备好再跑。" >&2
  exit 1
fi

COMPOSE=(docker compose --env-file .env.prod -f docker-compose.prod.yml)
REF="${1:-${REF:-}}"

# ---- 切版本 ----
if [[ -n "$REF" ]]; then
  echo "==> 拉取并切到 $REF"
  git fetch --tags --prune origin
  git checkout "$REF"
  # REF 是分支时跟一下远端；是 tag（detached）则忽略
  git pull --ff-only origin "$REF" 2>/dev/null || true
fi

GIT_SHA="$(git rev-parse --short HEAD)"
# 精确命中当前 commit 的 tag 作为 release 版本号，否则用 dev-<sha>
APP_VERSION="$(git describe --tags --exact-match 2>/dev/null || echo "dev-$GIT_SHA")"
export GIT_SHA APP_VERSION

echo "==> 构建并启动：version=$APP_VERSION sha=$GIT_SHA"
"${COMPOSE[@]}" up -d --build

# ---- 验证：后端只在内网（仅 Caddy 对公网），从容器内自检 /version ----
echo "==> 等待后端健康并自报版本..."
ok=""
for _ in $(seq 1 30); do
  if out="$("${COMPOSE[@]}" exec -T backend curl -fsS http://localhost:8000/version 2>/dev/null)"; then
    ok=1
    break
  fi
  sleep 2
done

if [[ -z "$ok" ]]; then
  echo "!! /version 始终不可达，查日志：${COMPOSE[*]} logs --tail=50 backend" >&2
  exit 1
fi

echo "==> 线上 /version：$out"
case "$out" in
  *"$GIT_SHA"*) echo "✅ 部署完成，线上 SHA 与本次一致（$GIT_SHA）" ;;
  *) echo "!! 线上 SHA 与本次构建不一致，可能旧容器没换掉——检查上面输出" >&2; exit 1 ;;
esac
