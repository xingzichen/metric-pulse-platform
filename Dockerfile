# v1.1.2-nas1 只在同依赖、同迁移的补丁发布中复用已验证的 v1.1.0 amd64 运行时。
# 正式 Dockerfile 会在该标签触发后立即恢复；此提交不得用于依赖或浏览器版本升级。
FROM ghcr.io/xingzichen/metric-pulse-platform-api:1.1.0

USER root
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
LABEL org.opencontainers.image.version="1.1.2" \
      org.opencontainers.image.revision="e2d0121357030d805a46fb982f092d0515b88096" \
      org.opencontainers.image.base.name="ghcr.io/xingzichen/metric-pulse-platform-api:1.1.0"
USER metric
