# 构建阶段
FROM node:26.13.0-alpine3.24 AS build
WORKDIR /app

RUN npm install --global pnpm@12.18.0

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# Vite 的环境变量在构建时写入产物,需要作为构建参数传入
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN pnpm build

# 运行阶段:nginx 提供静态文件 + 反代 /api
FROM nginx:1.30.6-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# 后端地址,容器启动时通过 -e BACKEND_ORIGIN=http://backend:8000 覆盖
ENV BACKEND_ORIGIN=http://backend:8000

EXPOSE 80
