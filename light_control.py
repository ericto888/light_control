import socket
import threading
import time
import paho.mqtt.client as mqtt
import logging
import json
from pathlib import Path
from enum import Enum

# ====== 配置参数 ======
TCP_IP = "192.168.0.107"
TCP_PORT = 5555
MQTT_BROKER = "192.168.0.158"
MQTT_USERNAME = "admin"
MQTT_PASSWORD = "101011"
MQTT_PORT = 1883
MAX_RETRIES = 5  # 最大重试次数

# ====== 设备枚举定义 ======
class Device(str, Enum):
    ENTRANCE = "entrance"
    DINING_MAIN = "dining_main"
    DINING_SPOT = "dining_spot"
    LIVING_ROOM = "living_room"
    LIVING_ROOM_SPOT = "living_room_spot"
    LIVING_ROOM_STRIP = "living_room_strip"
    CORRIDOR = "corridor"

# ====== 指令映射表 ======
COMMAND_MAP = {
    (Device.ENTRANCE, "on"): "EE0006060F8000360001C6FE",
    (Device.ENTRANCE, "off"): "EE0006060F8000360000C5FE",
    (Device.DINING_MAIN, "on"): "EE0006060F8000190001A9FE",
    (Device.DINING_MAIN, "off"): "EE0006060F8000190000A8FE",
    (Device.DINING_SPOT, "on"): "EE0006060F8000170001A7FE",
    (Device.DINING_SPOT, "off"): "EE0006060F8000170000A6FE",
    (Device.LIVING_ROOM, "on"): "EE0006060F8000160001A6FE",
    (Device.LIVING_ROOM, "off"): "EE0006060F8000160000A5FE",
    (Device.LIVING_ROOM_SPOT, "on"): "EE0006060F8000180001A8FE",
    (Device.LIVING_ROOM_SPOT, "off"): "EE0006060F8000180000A7FE",
    (Device.LIVING_ROOM_STRIP, "on"): "EE0006060F80001D0001ADFE",
    (Device.LIVING_ROOM_STRIP, "off"): "EE0006060F80001D0000ACFE",
    (Device.CORRIDOR, "on"): "EE0006060F80001C0001ACFE",
    (Device.CORRIDOR, "off"): "EE0006060F80001C0000ABFE",
}

REVERSE_COMMAND_MAP = {v: (k[0], k[1]) for k, v in COMMAND_MAP.items()}

# ====== 日志配置 ======
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "light_control.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ====== 日志优化 ======
def log_success(message):
    logger.info(f"✅ {message}")

def log_warning(message):
    logger.warning(f"⚠️ {message}")

def log_error(message):
    logger.error(f"❌ {message}")

# ====== TCP客户端类 ======
class TCPClient:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self._connect()
        self.pending_commands = []

    def _connect(self):
        retries = 0
        while retries < MAX_RETRIES:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect((self.ip, self.port))
                log_success(f"TCP 连接成功: {self.ip}:{self.port}")
                return True
            except Exception as e:
                retries += 1
                log_warning(f"TCP 连接失败 (重试 {retries}/{MAX_RETRIES}): {e}")
                time.sleep(5)
        log_error("TCP 连接失败，达到最大重试次数")
        return False

    def send(self, hex_cmd):
        with self.lock:
            if not self.sock:
                if not self._connect():
                    return False
            try:
                self.sock.send(bytes.fromhex(hex_cmd))
                log_success(f"指令发送成功: {hex_cmd}")
                return True
            except (socket.error, BrokenPipeError) as e:
                log_warning(f"指令发送失败，尝试重连: {e}")
                self.sock.close()
                self.sock = None
                return self._connect() and self.send(hex_cmd)

    def add_pending_command(self, hex_cmd):
        self.pending_commands.append(hex_cmd)

    def flush_pending_commands(self):
        while self.pending_commands:
            cmd = self.pending_commands.pop(0)
            self.send(cmd)

# ====== MQTT初始化 ======
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="light_controller")  # 使用新版回调API
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.will_set("home/light/status", "offline", retain=True)

tcp_client = TCPClient(TCP_IP, TCP_PORT)
device_state_cache = {}

def send_heartbeat():
    while True:
        time.sleep(30)
        if tcp_client.sock:
            try:
                tcp_client.send("EE0006060F8000000000A0FE")
                logger.debug("发送心跳包")
            except Exception as e:
                logger.error(f"发送心跳包失败: {e}")

# ====== MQTT消息处理函数 ======
def publish_discovery_config():
    for device in Device:
        config_topic = f"homeassistant/light/{device.value}/config"
        config_payload = {
            "name": device.value.replace("_", " ").title(),
            "unique_id": f"light_controller_{device.value}",
            "command_topic": f"home/light/{device.value}/set",
            "state_topic": f"home/light/{device.value}/state",
            "payload_on": "on",
            "payload_off": "off",
            "retain": True,
            "availability_topic": "home/light/status",
            "device": {
                "identifiers": ["light_controller_001"],
                "name": "Light Controller",
                "manufacturer": "Custom Automation",
                "model": "v1.0"
            }
        }
        client.publish(config_topic, json.dumps(config_payload), retain=True)
        logger.info(f"已发布自动发现配置: {device.value}")

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        logger.info("MQTT连接成功")
        client.subscribe("home/light/+/set")
        publish_discovery_config()
        client.publish("home/light/status", "online", retain=True)
    else:
        logger.error(f"MQTT连接失败，错误码: {rc}")

def on_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split("/")
        device_str = topic_parts[-2]
        action = msg.payload.decode().lower()

        try:
            device = Device(device_str)
        except ValueError:
            log_warning(f"无效设备名称: {device_str}")
            return

        hex_cmd = COMMAND_MAP.get((device, action))
        if hex_cmd:
            if tcp_client.send(hex_cmd):
                client.publish(f"home/light/{device.value}/state", action, retain=True)
                log_success(f"设备控制成功: {device.value} -> {action}")
            else:
                log_warning(f"指令发送失败: {device.value} -> {action}")
        else:
            log_warning(f"无效指令: {device.value} -> {action}")
    except Exception as e:
        log_error(f"MQTT 处理消息失败: {e}")

def on_disconnect(client, userdata, rc, properties=None):
    log_warning(f"MQTT 连接断开，错误码: {rc}")
    retries = 0
    while rc != 0 and retries < MAX_RETRIES:
        time.sleep(5)
        rc = client.reconnect()
        retries += 1
    if rc == 0:
        log_success("MQTT 重新连接成功")
    else:
        log_error("MQTT 重连失败，超出最大重试次数")

# ====== TCP 监听器 ======
def tcp_listener():
    error_count = 0  # 记录连续错误次数
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((TCP_IP, TCP_PORT))
                log_success("TCP 监听连接成功")
                while True:
                    try:
                        data = sock.recv(1024)
                        if not data:
                            log_warning("TCP 连接被远程关闭")
                            break
                        hex_data = data.hex().upper()
                        if hex_data in REVERSE_COMMAND_MAP:
                            device, state = REVERSE_COMMAND_MAP[hex_data]
                            client.publish(f"home/light/{device.value}/state", state, retain=True)
                            log_success(f"设备状态更新: {device.value} -> {state}")
                        error_count = 0  # 重置错误计数
                    except socket.timeout:
                        continue
                    except Exception as e:
                        log_error(f"TCP 接收数据错误: {e}")
                        break
        except Exception as e:
            error_count += 1
            if error_count % 3 == 1:  # 每 3 次失败打印一次日志
                log_error(f"TCP 监听错误: {e}")
            time.sleep(10)  # 增加重试间隔

if __name__ == "__main__":
    threading.Thread(target=send_heartbeat, daemon=True).start()
    threading.Thread(target=tcp_listener, daemon=True).start()

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("正在关闭...")
        client.publish("home/light/status", "offline", retain=True)
        client.disconnect()
        tcp_client.sock.close()
