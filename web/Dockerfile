FROM node:24-alpine AS build
# Node.js 25 起不再随镜像分发 Corepack；显式固定 pnpm，保证本地与 CI 构建一致。
ARG PNPM_VERSION=10.32.1
RUN npm install --global pnpm@${PNPM_VERSION}
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
