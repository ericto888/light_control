FROM homeassistant/amd64-base:latest

COPY light_control.py /app/light_control.py

RUN apk add --no-cache python3 py3-pip \
    && pip3 install requests paho-mqtt

CMD ["python3", "/app/light_control.py"]
