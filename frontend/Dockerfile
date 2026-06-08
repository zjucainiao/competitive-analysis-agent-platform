# syntax=docker/dockerfile:1
# 前端镜像（Next.js 16）。多阶段：deps → build → run。
# 注意：NEXT_PUBLIC_* 是 BUILD 期烘焙进产物的，所以 API 域名要在 build 时传进来
# （docker-compose.prod.yml 用 build.args 把 https://<DOMAIN> 传进 NEXT_PUBLIC_API_BASE）。
# 改域名 = 重新 build。

# ---- deps：装全部依赖（含 dev，build 需要） ----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder：注入 NEXT_PUBLIC_* 并产出 .next ----
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN npm run build

# ---- runner：next start ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 10001 app && adduser -u 10001 -G app -S app
COPY --from=builder /app/package.json /app/package-lock.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./
USER app
EXPOSE 3000
# next start 默认绑 0.0.0.0:3000；Caddy 反代到这里（不对外暴露端口）
CMD ["npm", "run", "start"]
