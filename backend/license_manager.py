import datetime as dt
import hashlib
import json
import os
import platform
import re
import uuid
from typing import Any, Dict, Optional, Tuple

import requests

from config import config

LICENSE_FILE = os.path.join(config.DATA_DIR, 'license.json')
DEVICE_ID_FILE = os.path.join(config.DATA_DIR, 'device_id.txt')


def _normalize_hwid(value: Optional[str]) -> str:
    text = str(value or '').strip().upper()
    if re.fullmatch(r'[0-9A-F]{32}', text):
        return text
    return ''


def _load_cached_hwid() -> str:
    try:
        with open(DEVICE_ID_FILE, 'r', encoding='utf-8') as f:
            return _normalize_hwid(f.read())
    except FileNotFoundError:
        return ''
    except Exception:
        return ''


def _save_cached_hwid(hwid: str) -> bool:
    normalized = _normalize_hwid(hwid)
    if not normalized:
        return False

    try:
        os.makedirs(os.path.dirname(DEVICE_ID_FILE), exist_ok=True)
        with open(DEVICE_ID_FILE, 'w', encoding='utf-8') as f:
            f.write(normalized)
        return True
    except Exception:
        return False


def _build_runtime_hwid() -> str:
    """基于当前系统信息生成硬件标识，仅在首次创建缓存时使用。"""
    mac = ':'.join(
        ['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0, 48, 8)]
    )[0:17]
    system_info = f"{platform.machine()}-{platform.system()}-{platform.node()}-{mac}"
    return hashlib.sha256(system_info.encode()).hexdigest()[:32].upper()


def generate_hwid() -> str:
    """生成稳定的硬件标识，用于许可证绑定。"""
    try:
        cached_hwid = _load_cached_hwid()
        if cached_hwid:
            return cached_hwid

        saved_license = load_license() or {}
        saved_hwid = _normalize_hwid(saved_license.get('hwid'))
        if saved_hwid:
            _save_cached_hwid(saved_hwid)
            return saved_hwid

        generated_hwid = _build_runtime_hwid()
        _save_cached_hwid(generated_hwid)
        return generated_hwid
    except Exception:
        return uuid.uuid4().hex[:32].upper()


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)


def load_license() -> Optional[Dict[str, Any]]:
    try:
        with open(LICENSE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def save_license(data: Dict[str, Any]) -> bool:
    try:
        _ensure_data_dir()
        with open(LICENSE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=True, indent=2)
        return True
    except Exception:
        return False


def clear_license() -> bool:
    try:
        if os.path.exists(LICENSE_FILE):
            os.remove(LICENSE_FILE)
        return True
    except Exception:
        return False


def mask_license_key(key: Optional[str]) -> str:
    if not key:
        return ''
    if len(key) <= 8:
        return f"{key[:2]}{'*' * max(len(key) - 4, 0)}{key[-2:]}"
    return f"{key[:4]}{'*' * 4}{key[-4:]}"


def _parse_datetime(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_license_required() -> bool:
    return bool(getattr(config, 'LICENSE_REQUIRED', True))


def _build_free_license_payload() -> Dict[str, Any]:
    return {
        'mode': 'free',
        'license_key': '',
        'days': -1,
        'activated_at': None,
        'expires_at': None
    }


def validate_local_license() -> Tuple[bool, Dict[str, Any]]:
    if not _is_license_required():
        return True, _build_free_license_payload()

    data = load_license()
    if not data:
        return False, {'reason': 'missing', 'message': '未找到许可证，请先激活'}

    saved_hwid = data.get('hwid')
    current_hwid = generate_hwid()
    if saved_hwid != current_hwid:
        return False, {'reason': 'hwid_mismatch', 'message': '许可证与当前设备不匹配'}

    days = data.get('days')
    activated_at = _parse_datetime(data.get('activated_at'))
    if days is None or activated_at is None:
        return False, {'reason': 'invalid', 'message': '许可证信息不完整'}

    if int(days) != -1:
        expires_at = activated_at + dt.timedelta(days=int(days))
        if dt.datetime.utcnow() > expires_at:
            return False, {'reason': 'expired', 'message': '许可证已过期，请重新激活'}
        expires_at_str = expires_at.isoformat()
    else:
        expires_at_str = None

    return True, {
        'license_key': mask_license_key(data.get('license_key')),
        'days': int(days),
        'activated_at': activated_at.isoformat(),
        'expires_at': expires_at_str
    }


def activate_license(license_key: str) -> Tuple[bool, Dict[str, Any]]:
    if not _is_license_required():
        return True, {
            'message': '当前版本无需激活',
            'days': -1,
            'mode': 'free'
        }

    normalized_key = license_key.strip().upper()
    if config.LICENSE_ALLOW_TEST_KEYS:
        allowed = {key.upper() for key in config.LICENSE_TEST_KEYS}
        if normalized_key in allowed:
            license_data = {
                'license_key': license_key,
                'hwid': generate_hwid(),
                'days': -1,
                'activated_at': dt.datetime.utcnow().isoformat()
            }
            if not save_license(license_data):
                return False, {'message': '保存许可证失败，请检查磁盘权限'}
            return True, {'message': '本地测试密钥激活成功', 'days': -1}

    hwid = generate_hwid()
    try:
        response = requests.post(
            f"{config.LICENSE_SERVER_URL}/api/activate",
            json={'key': license_key, 'hwid': hwid},
            timeout=10
        )
    except requests.exceptions.Timeout:
        return False, {'message': '连接服务器超时，请检查网络'}
    except requests.exceptions.ConnectionError:
        return False, {'message': '无法连接到服务器，请检查网络'}
    except Exception as exc:
        return False, {'message': f'激活失败: {exc}'}

    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            return False, {'message': '服务器返回无效数据'}

        if data.get('status') == 'success':
            days = data.get('days', -1)
            license_data = {
                'license_key': license_key,
                'hwid': hwid,
                'days': int(days),
                'activated_at': dt.datetime.utcnow().isoformat()
            }
            if not save_license(license_data):
                return False, {'message': '保存许可证失败，请检查磁盘权限'}
            return True, {
                'message': data.get('msg', '激活成功'),
                'days': int(days)
            }

        return False, {'message': data.get('detail', '激活失败')}

    if response.status_code == 403:
        return False, {'message': '该密钥已被其他设备激活，无法重复使用'}
    if response.status_code == 404:
        return False, {'message': '密钥不存在或已失效'}

    return False, {'message': f'服务器错误: {response.status_code}'}
