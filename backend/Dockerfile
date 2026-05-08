# 使用固定的 Python 基础镜像
FROM python:3.11-slim as builder

WORKDIR /app

# 注入构建参数
ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 配置 APT 源
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
  sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
  sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list 2>/dev/null || true; \
  fi && \
  apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  -i ${PIP_INDEX_URL} \
  --timeout 1000 \
  --retries 5

# 第二阶段：运行环境
FROM python:3.11-slim

WORKDIR /app

# 复制应用代码
COPY app/ ./app/

# 安装依赖到系统路径
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# 设置用户与权限
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# 创建日志目录并设置权限
RUN mkdir -p /app/logs && chown -R appuser:appuser /app/logs

USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
