"""
Flask API 应用 - Discord 自动营销机器人系统

提供 REST API 接口：
- 账号管理 (CRUD + 启动/停止)
- 内容管理 (CRUD + 图片上传)
- 自动发送任务控制
"""
import asyncio
import threading
import logging
import re
import requests
import sqlite3
import os
import json
import sys
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import discord

from config import config
from database import Database
from bot import DiscordBotClient, bot_clients
from auto_sender import (
    start_sending_task,
    stop_sending_task,
    get_task_status,
    pause_sending_task,
    resume_sending_task,
    load_task_state
)
from license_manager import activate_license, clear_license, validate_local_license

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化 Flask 应用
app = Flask(__name__)
CORS(app)

# 初始化数据库
db = Database()
load_task_state(db)

# Discord bot 事件循环（在单独线程中运行）
bot_loop: asyncio.AbstractEventLoop = None
bot_thread: threading.Thread = None

# 简易日志缓冲区（内存）
log_buffer = deque(maxlen=1000)
log_seq = 0
log_lock = threading.Lock()


def get_runtime_snapshot() -> Dict[str, str]:
    return {
        'python_executable': sys.executable,
        'python_version': sys.version.split()[0],
        'discord_module': getattr(discord, '__file__', ''),
        'discord_version': getattr(discord, '__version__', 'unknown'),
        'backend_port': str(config.FLASK_PORT)
    }


def _append_log(log_data: Dict) -> Dict:
    global log_seq
    with log_lock:
        log_seq += 1
        entry = {'id': log_seq, **log_data}
        log_buffer.append(entry)
        return entry


def _should_store_log(level: str, message: str, module: str) -> bool:
    level_upper = (level or '').upper()
    if level_upper in {'ERROR', 'WARNING'}:
        return True

    msg = message or ''
    mod = module or ''

    # 登录/连接日志（仅 bot 模块）
    if mod == 'bot' and any(keyword in msg for keyword in ['登录', '连接', '断开', '已恢复']):
        return True

    # 发送进度（仅 auto_sender 模块）
    if mod == 'auto_sender' and '发送进度' in msg:
        return True

    return False


# ============== 许可证 API ==============

@app.route('/api/license/status', methods=['GET'])
def get_license_status():
    """获取本地许可证状态"""
    try:
        activated, payload = validate_local_license()
        if activated:
            return jsonify({'success': True, 'activated': True, 'license': payload})
        return jsonify({'success': True, 'activated': False, 'error': payload})
    except Exception as e:
        logger.error(f"获取许可证状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/license/activate', methods=['POST'])
def activate_license_api():
    """激活许可证"""
    try:
        data = request.get_json() or {}
        license_key = data.get('key', '').strip()
        if not license_key:
            return jsonify({'success': False, 'error': '请输入许可证密钥'}), 400

        success, result = activate_license(license_key)
        if success:
            return jsonify({'success': True, **result})
        return jsonify({'success': False, 'error': result.get('message', '激活失败')}), 400
    except Exception as e:
        logger.error(f"激活许可证失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/license/clear', methods=['POST'])
def clear_license_api():
    """清除本地许可证"""
    try:
        if clear_license():
            return jsonify({'success': True, 'message': '许可证已清除'})
        return jsonify({'success': False, 'error': '清除许可证失败'}), 500
    except Exception as e:
        logger.error(f"清除许可证失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== 日志 API ==============

@app.route('/api/logs/add', methods=['POST'])
def add_log():
    """接收客户端日志"""
    try:
        data = request.get_json() or {}
        message = data.get('message', '')
        module = data.get('module', '')
        func = data.get('func', '')
        level = data.get('level', 'INFO')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        if _should_store_log(level, message, module):
            _append_log({
                'timestamp': timestamp,
                'level': level,
                'message': message,
                'module': module,
                'func': func
            })
        logger.info("BOT_LOG [%s] %s %s %s", level, module, func, message)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"接收日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/list', methods=['GET'])
def list_logs():
    """获取日志列表"""
    try:
        since = request.args.get('since', type=int) or 0
        limit = request.args.get('limit', type=int) or 200
        with log_lock:
            if since:
                logs = [entry for entry in log_buffer if entry.get('id', 0) > since]
            else:
                logs = list(log_buffer)
        if limit and len(logs) > limit:
            logs = logs[-limit:]
        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/stream', methods=['GET'])
def stream_logs():
    """日志流占位（兼容旧前端）"""
    return list_logs()


def fetch_discord_username(token: str) -> str:
    """通过 Discord API 获取账号显示名称"""
    try:
        response = requests.get(
            'https://discord.com/api/v10/users/@me',
            headers={'Authorization': token},
            timeout=8
        )
        if response.status_code != 200:
            return ''
        data = response.json()
        if data.get('global_name'):
            return data.get('global_name', '')
        username = data.get('username', '')
        discriminator = data.get('discriminator', '')
        if username and discriminator and discriminator != '0':
            return f"{username}#{discriminator}"
        return username
    except Exception:
        return ''

def _parse_channel_ids(value: object) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = re.split(r'[,\s]+', str(value))
    channel_ids = []
    seen = set()
    for item in items:
        channel_id = str(item).strip()
        if not channel_id:
            continue
        if channel_id in seen:
            continue
        channel_ids.append(channel_id)
        seen.add(channel_id)
    return channel_ids

def _get_account_by_token(token: str) -> Optional[Dict]:
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, username FROM discord_accounts WHERE token = ?',
                (token,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception:
        return None

def _start_account_by_id(account_id: int) -> Dict:
    account = db.get_account_by_id(account_id)
    if not account:
        return {'success': False, 'error': '账号不存在'}

    if not bot_loop or not bot_loop.is_running():
        return {'success': False, 'error': 'Bot 事件循环未就绪，请重启后端后重试'}

    for client in list(bot_clients):
        if client.account_id == account_id:
            if not client.is_closed() and client.is_ready():
                return {'success': False, 'error': '账号已在线'}
            if not client.is_closed():
                return {'success': False, 'error': '账号启动中'}
            bot_clients.remove(client)
    token = account['token']

    async def create_and_start_bot():
        client = DiscordBotClient(account_id=account_id)
        client.stop_requested = False
        bot_clients.append(client)

        async def start_bot():
            try:
                await client.start_with_retries(token)
            except Exception as e:
                try:
                    db.update_account_status(account_id, 'offline')
                except Exception:
                    pass
                logger.error(f"账号 {account_id} 启动失败: {e}")
            finally:
                if client in bot_clients and (client.stop_requested or client.is_closed() or not client.is_ready()):
                    try:
                        bot_clients.remove(client)
                    except ValueError:
                        pass

        try:
            asyncio.create_task(start_bot())
        except Exception:
            if client in bot_clients:
                bot_clients.remove(client)
            raise

        return client

    future = asyncio.run_coroutine_threadsafe(create_and_start_bot(), bot_loop)
    future.result(timeout=5)
    db.update_account_status(account_id, 'connecting')
    return {'success': True, 'message': '账号启动中...'}

# ============== 账号管理 API ==============

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    """获取所有 Discord 账号"""
    try:
        accounts = db.get_all_accounts()
        online_ids = {c.account_id for c in bot_clients if c.is_ready() and not c.is_closed()}
        connecting_ids = {
            c.account_id for c in bot_clients
            if not c.is_ready() and not c.is_closed()
        }
        for acc in accounts:
            runtime_status = 'offline'
            if acc['id'] in online_ids:
                runtime_status = 'online'
            elif acc['id'] in connecting_ids:
                runtime_status = 'connecting'

            acc['is_online'] = runtime_status == 'online'
            acc['status'] = runtime_status
        return jsonify({'success': True, 'accounts': accounts})
    except Exception as e:
        logger.error(f"获取账号列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts', methods=['POST'])
def add_account():
    """添加新账号 (通过 token)"""
    try:
        data = request.get_json()
        token = data.get('token', '').strip()
        username = data.get('username', '').strip()

        if not token:
            return jsonify({'success': False, 'error': 'Token 不能为空'}), 400

        existing = _get_account_by_token(token)
        if existing:
            return jsonify({'success': False, 'error': '该账号已存在'}), 400

        if not username:
            username = fetch_discord_username(token)

        account_id = db.add_account(token=token, username=username)
        return jsonify({'success': True, 'account_id': account_id, 'message': '账号添加成功'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '该账号已存在'}), 400
    except Exception as e:
        logger.error(f"添加账号失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """删除账号"""
    try:
        # 先停止该账号的连接
        for client in list(bot_clients):
            if client.account_id == account_id:
                client.stop_requested = True
                asyncio.run_coroutine_threadsafe(client.close(), bot_loop)
                bot_clients.remove(client)

        db.delete_account(account_id)
        return jsonify({'success': True, 'message': '账号已删除'})
    except Exception as e:
        logger.error(f"删除账号失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts/<int:account_id>/start', methods=['POST'])
def start_account(account_id):
    """启动账号连接"""
    try:
        result = _start_account_by_id(account_id)
        if result.get('success'):
            return jsonify(result)
        if result.get('error') == '账号不存在':
            return jsonify(result), 404
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"启动账号失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts/start_all', methods=['POST'])
def start_all_accounts():
    """一键启动所有账号"""
    try:
        accounts = db.get_all_accounts()
        started = 0
        skipped = 0
        failed = []

        for account in accounts:
            result = _start_account_by_id(account['id'])
            if result.get('success'):
                started += 1
            else:
                error = result.get('error', '')
                if error in {'账号已在线', '账号启动中'}:
                    skipped += 1
                else:
                    failed.append({'id': account['id'], 'error': error or '启动失败'})

        return jsonify({
            'success': True,
            'started': started,
            'skipped': skipped,
            'failed': failed
        })
    except Exception as e:
        logger.error(f"一键启动账号失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts/<int:account_id>/stop', methods=['POST'])
def stop_account(account_id):
    """停止账号连接"""
    try:
        stopped = False
        for client in list(bot_clients):
            if client.account_id == account_id:
                client.stop_requested = True
                asyncio.run_coroutine_threadsafe(client.close(), bot_loop)
                bot_clients.remove(client)
                stopped = True

        if stopped:
            db.update_account_status(account_id, 'offline')
            return jsonify({'success': True, 'message': '账号已停止'})

        return jsonify({'success': False, 'error': '账号未在线'}), 400
    except Exception as e:
        logger.error(f"停止账号失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== 自动发送任务 API ==============

@app.route('/api/sender/start', methods=['POST'])
def start_sender():
    """启动自动发送任务"""
    try:
        data = request.get_json()
        content_ids = data.get('contentIds', [])
        account_ids = data.get('accountIds', [])
        channel_ids = data.get('channelIds', [])
        post_title = data.get('postTitle', '')
        rotation_mode = data.get('rotationMode', True)  # 默认轮换模式
        repeat_mode = data.get('repeatMode', True)  # 默认循环发送
        interval = data.get('interval', config.DEFAULT_SEND_INTERVAL)

        # 参数验证
        if not content_ids:
            return jsonify({'success': False, 'error': '请选择至少一条内容'}), 400
        if not account_ids:
            return jsonify({'success': False, 'error': '请选择至少一个账号'}), 400
        if not channel_ids:
            return jsonify({'success': False, 'error': '请输入至少一个频道或帖子目标'}), 400

        # 非轮换模式只能选择一个账号
        if not rotation_mode and len(account_ids) > 1:
            return jsonify({'success': False, 'error': '单账号模式只能选择一个账号'}), 400

        # 验证间隔范围
        interval = max(config.MIN_SEND_INTERVAL, min(interval, config.MAX_SEND_INTERVAL))

        result = start_sending_task(
            content_ids=[int(id) for id in content_ids],
            account_ids=[int(id) for id in account_ids],
            channel_ids=[str(c).strip() for c in channel_ids if str(c).strip()],
            post_title=str(post_title).strip(),
            rotation_mode=rotation_mode,
            repeat_mode=bool(repeat_mode),
            interval=int(interval),
            db=db,
            bot_clients=bot_clients,
            bot_loop=bot_loop
        )

        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error(f"启动发送任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sender/stop', methods=['POST'])
def stop_sender():
    """停止自动发送任务"""
    try:
        result = stop_sending_task(db)
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error(f"停止发送任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sender/pause', methods=['POST'])
def pause_sender():
    """暂停自动发送任务"""
    try:
        result = pause_sending_task()
        if result['success']:
            return jsonify(result)
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"暂停发送任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sender/resume', methods=['POST'])
def resume_sender():
    """继续自动发送任务"""
    try:
        result = resume_sending_task(db=db, bot_clients=bot_clients, bot_loop=bot_loop)
        if result['success']:
            return jsonify(result)
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"继续发送任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sender/status', methods=['GET'])
def sender_status():
    """获取发送任务状态"""
    try:
        status = get_task_status()
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== 系统 API ==============

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'success': True,
        'status': 'running',
        'bot_count': len(bot_clients),
        'online_bots': len([c for c in bot_clients if c.is_ready()]),
        **get_runtime_snapshot()
    })


@app.route('/api/bot/cooldowns', methods=['GET'])
def bot_cooldowns():
    """账号冷却状态占位（兼容旧前端）"""
    return jsonify({'success': True, 'cooldowns': {}})


# ============== 内容管理 API ==============

# 内容图片存储目录
CONTENT_IMAGES_DIR = os.path.join(config.DATA_DIR, 'content_images')
os.makedirs(CONTENT_IMAGES_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_request_forum_tags(raw_tags) -> List[str]:
    """规范化请求中的论坛标签。"""
    if raw_tags is None:
        return []

    if isinstance(raw_tags, str):
        text = raw_tags.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            raw_tags = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            raw_tags = re.split(r'[\n,]+', text)
    elif not isinstance(raw_tags, list):
        raw_tags = [raw_tags]

    normalized_tags: List[str] = []
    seen = set()
    for tag in raw_tags:
        clean_tag = str(tag or '').strip()
        if not clean_tag:
            continue
        dedupe_key = clean_tag.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_tags.append(clean_tag)

    return normalized_tags


@app.route('/api/contents', methods=['GET'])
def get_contents():
    """获取所有内容"""
    try:
        contents = db.get_all_contents()
        return jsonify({'success': True, 'contents': contents})
    except Exception as e:
        logger.error(f"获取内容列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/contents', methods=['POST'])
def add_content():
    """添加新内容"""
    try:
        data = request.get_json()
        title = (data.get('title') or '').strip()
        send_mode = data.get('send_mode') or 'direct'
        forum_post_title = (data.get('forum_post_title') or '').strip()
        forum_tags = normalize_request_forum_tags(data.get('forum_tags', []))
        text_content = (data.get('text_content') or '').strip()
        image_paths = data.get('image_paths', [])

        if not title:
            return jsonify({'success': False, 'error': '标题不能为空'}), 400

        content_id = db.add_content(
            title=title,
            send_mode=send_mode,
            forum_post_title=forum_post_title,
            forum_tags=forum_tags,
            text_content=text_content,
            image_paths=image_paths
        )
        if content_id:
            return jsonify({'success': True, 'id': content_id, 'message': '内容添加成功'})
        return jsonify({'success': False, 'error': '添加内容失败'}), 500
    except Exception as e:
        logger.error(f"添加内容失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/contents/<int:content_id>', methods=['GET'])
def get_content(content_id):
    """获取单个内容"""
    try:
        content = db.get_content_by_id(content_id)
        if content:
            return jsonify({'success': True, 'content': content})
        return jsonify({'success': False, 'error': '内容不存在'}), 404
    except Exception as e:
        logger.error(f"获取内容失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/contents/<int:content_id>', methods=['PUT'])
def update_content(content_id):
    """更新内容"""
    try:
        data = request.get_json()
        title = data.get('title')
        send_mode = data.get('send_mode')
        forum_post_title = data.get('forum_post_title')
        forum_tags = normalize_request_forum_tags(data.get('forum_tags')) if 'forum_tags' in data else None
        text_content = data.get('text_content')
        image_paths = data.get('image_paths')

        if db.update_content(
            content_id,
            title=title,
            send_mode=send_mode,
            forum_post_title=forum_post_title,
            forum_tags=forum_tags,
            text_content=text_content,
            image_paths=image_paths
        ):
            return jsonify({'success': True, 'message': '内容更新成功'})
        return jsonify({'success': False, 'error': '更新失败'}), 400
    except Exception as e:
        logger.error(f"更新内容失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/contents/<int:content_id>', methods=['DELETE'])
def delete_content(content_id):
    """删除内容"""
    try:
        # 获取内容信息以删除关联的图片
        content = db.get_content_by_id(content_id)
        if content and content.get('image_paths'):
            for img_path in content['image_paths']:
                full_path = os.path.join(CONTENT_IMAGES_DIR, img_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception:
                        pass

        if db.delete_content(content_id):
            return jsonify({'success': True, 'message': '内容已删除'})
        return jsonify({'success': False, 'error': '删除失败'}), 400
    except Exception as e:
        logger.error(f"删除内容失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/contents/<int:content_id>/upload', methods=['POST'])
def upload_content_image(content_id):
    """上传内容图片"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'}), 400

        if file and allowed_file(file.filename):
            # 生成唯一文件名
            import uuid
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{content_id}_{uuid.uuid4().hex[:8]}.{ext}"
            filepath = os.path.join(CONTENT_IMAGES_DIR, filename)
            file.save(filepath)

            # 更新内容的图片列表
            content = db.get_content_by_id(content_id)
            if content:
                image_paths = content.get('image_paths', [])
                image_paths.append(filename)
                db.update_content(content_id, image_paths=image_paths)

            return jsonify({
                'success': True,
                'filename': filename,
                'url': f'/api/content_image/{filename}'
            })
        return jsonify({'success': False, 'error': '不支持的文件类型'}), 400
    except Exception as e:
        logger.error(f"上传图片失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/content_image/<filename>', methods=['GET'])
def get_content_image(filename):
    """获取内容图片"""
    try:
        filepath = os.path.join(CONTENT_IMAGES_DIR, secure_filename(filename))
        if os.path.exists(filepath):
            return send_file(filepath)
        return jsonify({'success': False, 'error': '图片不存在'}), 404
    except Exception as e:
        logger.error(f"获取图片失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== 账号频道配置 API ==============

@app.route('/api/accounts/<int:account_id>/channels', methods=['GET'])
def get_account_channels(account_id):
    """获取账号的频道配置"""
    try:
        channels = db.get_account_channels(account_id)
        return jsonify({'success': True, 'channels': channels})
    except Exception as e:
        logger.error(f"获取账号频道配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/accounts/<int:account_id>/channels', methods=['PUT'])
def update_account_channels(account_id):
    """更新账号的频道配置"""
    try:
        data = request.get_json()
        channel_ids = data.get('channel_ids', [])

        # 解析频道ID（支持逗号或换行分隔）
        if isinstance(channel_ids, str):
            channel_ids = [c.strip() for c in re.split(r'[,\s\n]+', channel_ids) if c.strip()]

        if db.update_account_channels(account_id, channel_ids):
            return jsonify({'success': True, 'message': '频道配置已更新'})
        return jsonify({'success': False, 'error': '更新失败'}), 400
    except Exception as e:
        logger.error(f"更新账号频道配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Bot 线程管理 ==============

def run_bot_loop():
    """在单独线程中运行 Discord bot 事件循环"""
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    logger.info("Discord bot 事件循环已启动")
    bot_loop.run_forever()


def start_bot_thread():
    """启动 bot 线程"""
    global bot_thread
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()
    logger.info("Bot 线程已启动")


# ============== 主入口 ==============

if __name__ == '__main__':
    import signal
    import sys
    import time

    def shutdown_handler(signum, frame):
        """处理关闭信号，清理资源"""
        logger.info("收到关闭信号，正在停止服务...")
        try:
            stop_sending_task()
        except Exception:
            pass

        if bot_loop and bot_loop.is_running():
            async def close_bots():
                for client in list(bot_clients):
                    try:
                        await client.close()
                    except Exception:
                        continue

            future = asyncio.run_coroutine_threadsafe(close_bots(), bot_loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
            try:
                bot_loop.call_soon_threadsafe(bot_loop.stop)
            except Exception:
                pass

        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # 启动 bot 事件循环线程
    start_bot_thread()

    # 等待事件循环启动
    time.sleep(1)

    runtime = get_runtime_snapshot()
    logger.info(
        "启动 Flask 服务: %s:%s | Python %s | discord.py-self %s",
        config.FLASK_HOST,
        config.FLASK_PORT,
        runtime['python_version'],
        runtime['discord_version']
    )
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )
