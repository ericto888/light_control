# 根据架构选择基础镜像（例如 amd64）
FROM homeassistant/amd64-base:latest

# 将脚本复制到容器中
COPY light_control.py /app/light_control.py

# 安装依赖（如果有）
RUN apk add --no-cache python3 py3-pip \
    && pip3 install requests  # 示例：安装 requests 库
RUN pip3 install paho-mqtt

# 设置启动命令
CMD ["python3", "/app/light_control.py"]
