"""
配置文件 - Discord 自动营销机器人系统
"""
import json
import os
import platform
from typing import Optional


DATA_DIR_ENV_VAR = "DISCORD_AUTO_SENDER_DATA_DIR"


def get_app_data_root() -> str:
    """获取跨平台的应用配置根目录。"""
    system = platform.system()
    if system == "Windows":
        base_path = os.getenv("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    elif system == "Darwin":
        base_path = os.path.expanduser("~/Library/Application Support")
    else:
        base_path = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    os.makedirs(base_path, exist_ok=True)
    return base_path


def get_default_data_dir(app_name: str) -> str:
    data_dir = os.path.join(get_app_data_root(), app_name)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_storage_config_path(app_name: str) -> str:
    app_home = get_default_data_dir(app_name)
    return os.path.join(app_home, 'storage_config.json')


def _normalize_data_dir(path: Optional[str]) -> str:
    normalized = os.path.abspath(os.path.expanduser(str(path or '').strip()))
    if not normalized:
        raise ValueError('数据目录不能为空')
    return normalized


def read_persisted_data_dir(app_name: str) -> str:
    storage_config_path = get_storage_config_path(app_name)
    try:
        with open(storage_config_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return _normalize_data_dir(payload.get('data_dir'))
    except FileNotFoundError:
        return ''
    except Exception:
        return ''


def resolve_data_dir(app_name: str) -> str:
    env_data_dir = os.getenv(DATA_DIR_ENV_VAR)
    if env_data_dir:
        resolved = _normalize_data_dir(env_data_dir)
        os.makedirs(resolved, exist_ok=True)
        return resolved

    persisted_data_dir = read_persisted_data_dir(app_name)
    if persisted_data_dir:
        os.makedirs(persisted_data_dir, exist_ok=True)
        return persisted_data_dir

    return get_default_data_dir(app_name)


class Config:
    """应用配置类"""

    def __init__(self):
        self.APP_NAME = "DiscordAutoSender"
        self.DEFAULT_DATA_DIR = get_default_data_dir(self.APP_NAME)
        self.STORAGE_CONFIG_PATH = get_storage_config_path(self.APP_NAME)
        self.DATA_DIR = resolve_data_dir(self.APP_NAME)
        self.DATABASE_PATH = os.path.join(self.DATA_DIR, 'metadata.db')

    # Flask 服务配置
    FLASK_HOST = "127.0.0.1"
    FLASK_PORT = 5013
    FLASK_DEBUG = False

    # 后端 URL (用于内部通信)
    BACKEND_BASE_URL = f"http://{FLASK_HOST}:{FLASK_PORT}"
    BACKEND_API_URL = f"{BACKEND_BASE_URL}/api"

    # Discord 配置
    DISCORD_SIMILARITY_THRESHOLD = 0.6  # 图片相似度阈值
    ACCOUNT_LOGIN_RETRY_TIMES = 3  # 账号登录重试次数
    ACCOUNT_LOGIN_TIMEOUT = 180  # 单次登录超时（秒）
    ACCOUNT_LOGIN_RETRY_DELAY = 5  # 登录失败后的重试等待（秒）

    # 下载配置
    DOWNLOAD_THREADS = 4  # 下载线程数
    FEATURE_EXTRACT_THREADS = 4  # 特征提取线程数
    SCRAPE_THREADS = 2  # 抓取线程数
    SHOP_SCRAPE_PAGE_SIZE = 40  # 店铺列表分页大小
    SHOP_SCRAPE_MAX_PAGES = 200  # 店铺最大抓取页数

    # 消息转发配置 (可选)
    FORWARD_KEYWORDS = []  # 触发转发的关键词列表
    FORWARD_TARGET_CHANNEL_ID = None  # 转发目标频道 ID

    # 特定平台频道 ID (可选，用于发送特定平台链接)
    CNFANS_CHANNEL_ID = None
    ACBUY_CHANNEL_ID = None

    # 自动发送默认配置
    DEFAULT_SEND_INTERVAL = 60  # 默认发送间隔（秒）
    MIN_SEND_INTERVAL = 10  # 最小发送间隔（秒）
    MAX_SEND_INTERVAL = 3600  # 最大发送间隔（秒）
    AUTO_SENDER_TEXT_TIMEOUT = 30  # 单条文本发送超时（秒）
    AUTO_SENDER_IMAGE_TIMEOUT = 60  # 单次图片发送超时（秒）

    # 许可证激活服务配置
    LICENSE_REQUIRED = False
    LICENSE_SERVER_URL = "http://107.172.1.7:8888"
    LICENSE_ALLOW_TEST_KEYS = True
    LICENSE_TEST_KEYS = [
        "TEST-FOREVER-0001",
        "TEST-FOREVER-0002",
        "TEST-FOREVER-0003"
    ]

    def set_data_dir(self, data_dir: str, persist: bool = True) -> str:
        normalized = _normalize_data_dir(data_dir)
        os.makedirs(normalized, exist_ok=True)
        self.DATA_DIR = normalized
        self.DATABASE_PATH = os.path.join(self.DATA_DIR, 'metadata.db')

        if persist:
            os.makedirs(os.path.dirname(self.STORAGE_CONFIG_PATH), exist_ok=True)
            with open(self.STORAGE_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump({'data_dir': normalized}, f, ensure_ascii=True, indent=2)

        return self.DATA_DIR


# 全局配置实例
config = Config()
