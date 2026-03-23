"""
自动发送任务调度模块 - Discord 自定义内容自动发送

实现功能：
1. 从数据库读取用户选择的内容
2. 获取用户选择的多个 Discord 账号及其预配置的频道
3. 支持轮换模式和单账号模式
4. 频率控制：发送后等待指定秒数
5. 支持随时中断任务
"""
import asyncio
import logging
import os
import json
import re
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import urlparse

from config import config

logger = logging.getLogger(__name__)

# 全局变量控制任务状态
current_task: Optional[asyncio.Task] = None
stop_sender_event = asyncio.Event()
stop_sender_reason: Optional[str] = None
task_status = {
    'is_running': False,
    'is_paused': False,
    'content_ids': [],
    'account_ids': [],
    'channel_ids': [],
    'post_title': None,
    'rotation_mode': True,
    'repeat_mode': True,
    'interval': None,
    'total_contents': 0,
    'sent_count': 0,
    'current_content': None,
    'current_account': None,
    'started_at': None,
    'last_sent_at': None,
    'error': None,
    'next_content_index': 0,
    'next_account_index': 0
}


def parse_send_target_id(raw_target: object) -> Optional[int]:
    """解析发送目标，支持频道ID、帖子/线程ID、频道提及和 Discord URL。"""
    if raw_target is None:
        return None

    target_text = str(raw_target).strip()
    if not target_text:
        return None

    if target_text.isdigit():
        return int(target_text)

    mention_match = re.fullmatch(r'<#(\d+)>', target_text)
    if mention_match:
        return int(mention_match.group(1))

    parsed = urlparse(target_text)
    if parsed.scheme and parsed.netloc:
        path_parts = [part for part in parsed.path.split('/') if part]
        if len(path_parts) >= 3 and path_parts[0] == 'channels' and path_parts[2].isdigit():
            return int(path_parts[2])

    fallback_match = re.search(r'(?<!\d)(\d{15,25})(?!\d)', target_text)
    if fallback_match:
        return int(fallback_match.group(1))

    return None


async def resolve_send_target(client, raw_target: object):
    """解析并获取可发送的 Discord 目标，兼容普通频道和论坛帖子线程。"""
    target_id = parse_send_target_id(raw_target)
    if client is None or target_id is None:
        return None

    get_channel = getattr(client, 'get_channel', None)
    if callable(get_channel):
        target = get_channel(target_id)
        if target is not None:
            return target

    get_channel_or_thread = getattr(client, 'get_channel_or_thread', None)
    if callable(get_channel_or_thread):
        target = get_channel_or_thread(target_id)
        if target is not None:
            return target

    fetch_channel = getattr(client, 'fetch_channel', None)
    if callable(fetch_channel):
        try:
            return await fetch_channel(target_id)
        except Exception as exc:
            logger.warning(f"获取发送目标失败: {raw_target} -> {target_id} | {exc}")

    return None


def _is_forum_channel(target) -> bool:
    """判断目标是否是论坛频道。"""
    if target is None:
        return False

    channel_type = getattr(target, 'type', None)
    channel_type_name = getattr(channel_type, 'name', None)
    if channel_type_name and str(channel_type_name).lower() == 'forum':
        return True

    if getattr(target, 'available_tags', None) is not None and callable(getattr(target, 'create_thread', None)):
        return True

    cls_name = target.__class__.__name__.lower()
    return 'forum' in cls_name


def _build_forum_post_title(post_title: Optional[str], fallback_text: str = '') -> str:
    """构建论坛帖子标题，确保不为空且不超过 Discord 限制。"""
    title = (post_title or '').strip() or (fallback_text or '').strip() or '自动发送帖子'
    title = re.sub(r'\s+', ' ', title)
    return title[:100]


def resolve_post_title(content_title: str) -> str:
    """解析当前任务实际使用的帖子标题。"""
    configured_title = str(task_status.get('post_title') or '').strip()
    return configured_title or (content_title or '').strip() or '自动发送帖子'


def resolve_content_post_title(content: Dict, default_title: str = '') -> str:
    """解析单条内容对应的论坛帖子标题。"""
    if not isinstance(content, dict):
        return (default_title or '').strip() or '自动发送帖子'

    forum_post_title = str(content.get('forum_post_title') or '').strip()
    if forum_post_title:
        return forum_post_title

    content_title = str(content.get('title') or default_title or '').strip()
    return content_title or '自动发送帖子'


def resolve_content_send_mode(content: Dict) -> str:
    """解析单条内容对应的发送方式。"""
    if not isinstance(content, dict):
        return 'direct'

    send_mode = str(content.get('send_mode') or '').strip().lower()
    return send_mode if send_mode in {'direct', 'post'} else 'direct'


def resolve_content_forum_tags(content: Dict) -> List[str]:
    """解析单条内容对应的论坛标签配置。"""
    if not isinstance(content, dict):
        return []

    raw_forum_tags = content.get('forum_tags')
    if raw_forum_tags is None:
        return []

    if isinstance(raw_forum_tags, str):
        forum_tags_text = raw_forum_tags.strip()
        if not forum_tags_text:
            return []
        try:
            parsed_tags = json.loads(forum_tags_text)
            raw_forum_tags = parsed_tags if isinstance(parsed_tags, list) else [parsed_tags]
        except Exception:
            raw_forum_tags = re.split(r'[\n,]+', forum_tags_text)
    elif not isinstance(raw_forum_tags, (list, tuple, set)):
        raw_forum_tags = [raw_forum_tags]

    normalized_tags: List[str] = []
    seen = set()
    for tag in raw_forum_tags:
        clean_tag = str(tag or '').strip()
        if not clean_tag:
            continue
        dedupe_key = clean_tag.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_tags.append(clean_tag)

    return normalized_tags


def _forum_requires_tag(target) -> bool:
    """判断论坛频道是否要求至少选择一个标签。"""
    return bool(getattr(getattr(target, 'flags', None), 'require_tag', False))


def _resolve_forum_applied_tags(target, forum_tags: List[str], label: str):
    """根据名称或ID解析要应用到论坛帖子的标签对象。"""
    requested_tags = resolve_content_forum_tags({'forum_tags': forum_tags})
    available_tags = list(getattr(target, 'available_tags', []) or [])

    if not requested_tags:
        if _forum_requires_tag(target):
            logger.error(f"{label} 当前论坛频道要求至少选择一个标签，请先在内容管理中填写帖子标签")
            return None
        return []

    if not available_tags:
        logger.error(f"{label} 当前论坛频道没有可用标签，无法应用已配置标签")
        return None

    tags_by_id = {}
    tags_by_name = {}
    for tag in available_tags:
        tag_id = getattr(tag, 'id', None)
        if tag_id is not None:
            tags_by_id[str(tag_id)] = tag

        tag_name = str(getattr(tag, 'name', '') or '').strip()
        if tag_name:
            tags_by_name[tag_name.casefold()] = tag

    resolved_tags = []
    seen_ids = set()
    missing_tags = []

    for requested_tag in requested_tags:
        resolved_tag = tags_by_id.get(requested_tag) or tags_by_name.get(requested_tag.casefold())
        if resolved_tag is None:
            missing_tags.append(requested_tag)
            continue

        resolved_id = getattr(resolved_tag, 'id', None)
        if resolved_id in seen_ids:
            continue
        seen_ids.add(resolved_id)
        resolved_tags.append(resolved_tag)

    if missing_tags:
        logger.error(f"{label} 指定的帖子标签不存在: {', '.join(missing_tags)}")
        return None

    return resolved_tags


def _extract_thread_from_create_result(result):
    """从 create_thread 的返回值中提取 thread 对象。"""
    if result is None:
        return None

    if isinstance(result, (tuple, list)) and result:
        candidate = result[0]
        if callable(getattr(candidate, 'send', None)):
            return candidate

    thread = getattr(result, 'thread', None)
    if thread and callable(getattr(thread, 'send', None)):
        return thread

    if callable(getattr(result, 'send', None)):
        return result

    return None


async def _send_direct_payload(target, content: Optional[str], files: Optional[list], text_timeout: int, image_timeout: int, label: str) -> bool:
    if content and files:
        return await _send_with_timeout(
            target.send(content=content, files=files),
            max(text_timeout, image_timeout),
            label
        )

    if content:
        return await _send_with_timeout(target.send(content), text_timeout, label)

    if files:
        return await _send_with_timeout(target.send(files=files), image_timeout, label)

    return False


async def send_content_to_target(
    target,
    send_mode: str,
    post_title: str,
    text_content: str,
    forum_tags: Optional[List[str]],
    files: Optional[list],
    text_timeout: int,
    image_timeout: int,
    label: str
) -> bool:
    """向目标发送内容；按内容配置决定新建帖子或直接发送。"""
    direct_text = (text_content or '').strip() or None
    forum_text = direct_text or _build_forum_post_title(post_title)
    files = files or []
    normalized_send_mode = send_mode if send_mode in {'direct', 'post'} else 'direct'

    if normalized_send_mode == 'post':
        if not _is_forum_channel(target):
            logger.error(f"{label} 当前内容设置为新建帖子，请填写论坛频道 ID")
            return False

        create_thread = getattr(target, 'create_thread', None)
        if not callable(create_thread):
            logger.error(f"{label} 不是可创建帖子的论坛频道")
            return False

        thread_name = _build_forum_post_title(post_title, forum_text)
        applied_tags = _resolve_forum_applied_tags(target, forum_tags or [], label)
        if applied_tags is None:
            return False
        try:
            create_kwargs = {
                'name': thread_name,
            }
            if forum_text:
                create_kwargs['content'] = forum_text
            if applied_tags:
                create_kwargs['applied_tags'] = applied_tags
            if files:
                create_kwargs['files'] = files

            await create_thread(**create_kwargs)
            return True
        except TypeError:
            try:
                fallback_kwargs = {
                    'name': thread_name
                }
                if applied_tags:
                    fallback_kwargs['applied_tags'] = applied_tags
                created = await create_thread(**fallback_kwargs)
            except Exception as exc:
                logger.error(f"{label} 新建帖子失败: {exc}")
                return False

            thread = _extract_thread_from_create_result(created)
            if not thread:
                logger.error(f"{label} 新建帖子成功但未拿到线程对象")
                return False

            return await _send_direct_payload(
                thread,
                forum_text,
                files if files else None,
                text_timeout,
                image_timeout,
                f"{label} 帖子首条消息"
            )
        except Exception as exc:
            logger.error(f"{label} 新建帖子失败: {exc}")
            return False

    if _is_forum_channel(target):
        logger.error(f"{label} 当前内容设置为直接发送，请填写普通频道或已存在帖子链接/ID")
        return False

    return await _send_direct_payload(
        target,
        direct_text,
        files if files else None,
        text_timeout,
        image_timeout,
        label
    )


async def _send_with_timeout(coro, timeout: int, label: str) -> bool:
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        logger.error(f"{label} 发送超时({timeout}s)")
        return False
    except Exception as e:
        logger.error(f"{label} 发送失败: {e}")
        return False


def get_task_status() -> Dict:
    """获取当前任务状态"""
    return task_status.copy()


def reset_task_status():
    """重置任务状态"""
    global task_status
    task_status = {
        'is_running': False,
        'is_paused': False,
        'content_ids': [],
        'account_ids': [],
        'channel_ids': [],
        'post_title': None,
        'rotation_mode': True,
        'repeat_mode': True,
        'interval': None,
        'total_contents': 0,
        'sent_count': 0,
        'current_content': None,
        'current_account': None,
        'started_at': None,
        'last_sent_at': None,
        'error': None,
        'next_content_index': 0,
        'next_account_index': 0
    }


def _persist_task_state(db) -> None:
    """持久化任务状态到数据库"""
    try:
        db.save_sender_task_state({
            'is_running': task_status.get('is_running'),
            'is_paused': task_status.get('is_paused'),
            'shop_id': json.dumps(task_status.get('content_ids', [])),  # 复用 shop_id 字段存储 content_ids
            'channel_id': str(task_status.get('rotation_mode', True)),  # 复用 channel_id 字段存储 rotation_mode
            'channel_ids': task_status.get('channel_ids', []),
            'post_title': task_status.get('post_title'),
            'repeat_mode': bool(task_status.get('repeat_mode', True)),
            'account_ids': task_status.get('account_ids', []),
            'interval': task_status.get('interval'),
            'total_products': task_status.get('total_contents', 0),
            'sent_count': task_status.get('sent_count', 0),
            'next_product_index': task_status.get('next_content_index', 0),
            'next_account_index': task_status.get('next_account_index', 0),
            'current_product': task_status.get('current_content'),
            'current_account': task_status.get('current_account'),
            'started_at': task_status.get('started_at'),
            'last_sent_at': task_status.get('last_sent_at')
        })
    except Exception:
        return


def _has_online_bots(bot_clients: List, account_ids: List[int]) -> bool:
    """检查是否有在线的机器人"""
    if not account_ids:
        return False
    for client in bot_clients:
        if (
            hasattr(client, 'account_id')
            and client.account_id in account_ids
            and client.is_ready()
            and not client.is_closed()
        ):
            return True
    return False


def load_task_state(db) -> None:
    """加载上次任务状态（用于恢复）"""
    state = db.get_sender_task_state()
    if not state:
        return

    # 尝试解析 content_ids（存储在 shop_id 字段）
    try:
        content_ids = json.loads(state.get('shop_id') or '[]')
        if not isinstance(content_ids, list):
            content_ids = []
    except:
        content_ids = []

    if not content_ids:
        return

    # 解析 rotation_mode（存储在 channel_id 字段）
    rotation_mode = state.get('channel_id', 'True').lower() != 'false'
    channel_ids = []
    try:
        channel_ids = json.loads(state.get('channel_ids') or '[]')
        if not isinstance(channel_ids, list):
            channel_ids = []
    except Exception:
        channel_ids = []
    repeat_mode = bool(state.get('repeat_mode', True))

    reset_task_status()
    task_status['content_ids'] = content_ids
    task_status['rotation_mode'] = rotation_mode
    task_status['account_ids'] = state.get('account_ids', [])
    task_status['channel_ids'] = channel_ids
    task_status['post_title'] = state.get('post_title')
    task_status['interval'] = state.get('interval')
    task_status['total_contents'] = state.get('total_products', 0)
    task_status['sent_count'] = state.get('sent_count', 0)
    task_status['repeat_mode'] = repeat_mode
    task_status['current_content'] = state.get('current_product')
    task_status['current_account'] = state.get('current_account')
    task_status['started_at'] = state.get('started_at')
    task_status['last_sent_at'] = state.get('last_sent_at')
    task_status['next_content_index'] = state.get('next_product_index', 0)
    task_status['next_account_index'] = state.get('next_account_index', 0)

    if state.get('is_running'):
        task_status['is_running'] = False
        task_status['is_paused'] = True
        _persist_task_state(db)
    else:
        task_status['is_paused'] = bool(state.get('is_paused'))


async def auto_send_loop(
    content_ids: List[int],
    selected_account_ids: List[int],
    channel_ids: List[str],
    post_title: Optional[str],
    rotation_mode: bool,
    repeat_mode: bool,
    interval: int,
    db,
    bot_clients: List,
    start_content_index: int = 0,
    start_account_index: int = 0
):
    """
    自动发送循环任务

    :param content_ids: 选中的内容ID列表
    :param selected_account_ids: 用户勾选的 Account ID 列表
    :param channel_ids: 发送目标频道ID列表
    :param rotation_mode: 是否轮换模式
    :param repeat_mode: 是否循环发送
    :param interval: 发送间隔（秒）
    :param db: 数据库实例
    :param bot_clients: 机器人客户端列表
    """
    global task_status

    task_status['is_running'] = True
    task_status['is_paused'] = False
    task_status['content_ids'] = content_ids
    task_status['account_ids'] = selected_account_ids
    task_status['post_title'] = (post_title or '').strip() or None
    task_status['rotation_mode'] = rotation_mode
    task_status['repeat_mode'] = repeat_mode
    task_status['interval'] = interval
    task_status['started_at'] = datetime.now().isoformat()
    task_status['error'] = None
    task_status['next_content_index'] = max(0, start_content_index)
    task_status['next_account_index'] = max(0, start_account_index)
    _persist_task_state(db)

    logger.info(
        f"启动自动发送: 内容数={len(content_ids)}，账号数={len(selected_account_ids)}，"
        f"轮换模式={rotation_mode}，循环发送={repeat_mode}，间隔{interval}s，"
        f"任务帖子标题={task_status['post_title'] or '未设置'}"
    )

    try:
        # 1. 获取所有选中的内容
        contents = []
        for cid in content_ids:
            content = db.get_content_by_id(cid)
            if content:
                contents.append(content)

        if not contents:
            task_status['error'] = "没有找到选中的内容"
            logger.error(task_status['error'])
            return

        task_status['total_contents'] = len(contents)
        logger.info(f"待发送内容数: {len(contents)}")

        if task_status['next_content_index'] >= len(contents):
            if repeat_mode:
                task_status['next_content_index'] = 0
                _persist_task_state(db)
            else:
                task_status['sent_count'] = len(contents)
                task_status['current_content'] = None
                _persist_task_state(db)
                logger.info("内容已发送完毕，无需继续")
                return

        # 2. 筛选出可用的在线 Bot 客户端
        active_bots = []
        for client in bot_clients:
            if (
                hasattr(client, 'account_id')
                and client.account_id in selected_account_ids
                and client.is_ready()
                and not client.is_closed()
            ):
                active_bots.append(client)

        if not active_bots:
            task_status['error'] = "没有选中的账号在线，请先启动账号"
            logger.error(task_status['error'])
            return

        logger.info(f"可用账号数: {len(active_bots)}, 目标频道数: {len(channel_ids)}")

        content_idx = task_status['next_content_index']
        bot_idx = task_status['next_account_index']
        if task_status['sent_count'] < content_idx:
            task_status['sent_count'] = content_idx

        # 3. 循环发送
        while not stop_sender_event.is_set():
            # 动态检查可用的 bot（过滤掉已断开的）
            active_bots = [
                c for c in active_bots
                if c.is_ready() and not c.is_closed()
            ]
            if not active_bots:
                task_status['error'] = "所有账号已断开连接，任务停止"
                logger.error(task_status['error'])
                break

            if content_idx >= len(contents):
                if repeat_mode:
                    logger.info("内容已发送完毕，开始新一轮")
                    content_idx = 0
                    task_status['next_content_index'] = 0
                    _persist_task_state(db)
                else:
                    logger.info("所有内容已发送完毕，任务结束")
                    break

            # 获取当前要发的内容
            content = contents[content_idx]
            title = content.get('title', '未知内容')
            text_content = content.get('text_content', '')
            image_paths = content.get('image_paths', [])
            send_mode = resolve_content_send_mode(content)
            default_post_title = task_status['post_title'] or title
            forum_post_title = resolve_content_post_title(content, default_title=default_post_title)
            forum_tags = resolve_content_forum_tags(content)

            # 获取当前轮换的账号
            if rotation_mode:
                current_bot = active_bots[bot_idx % len(active_bots)]
            else:
                current_bot = active_bots[0]  # 单账号模式只用第一个

            task_status['current_content'] = title[:50]
            task_status['current_account'] = getattr(current_bot, 'user', None)
            if task_status['current_account']:
                task_status['current_account'] = str(task_status['current_account'])
            task_status['next_content_index'] = content_idx
            task_status['next_account_index'] = bot_idx
            _persist_task_state(db)

            # 发送到所有配置的频道
            failed_channels = 0
            any_success = False
            text_timeout = int(getattr(config, 'AUTO_SENDER_TEXT_TIMEOUT', 30))
            image_timeout = int(getattr(config, 'AUTO_SENDER_IMAGE_TIMEOUT', 60))
            for channel_id in channel_ids:
                target_label = str(channel_id).strip()
                if not target_label:
                    continue
                target_id = parse_send_target_id(target_label)
                if target_id is None:
                    failed_channels += 1
                    logger.error(f"无效发送目标: {target_label}")
                    continue

                target = await resolve_send_target(current_bot, target_label)
                if not target:
                    failed_channels += 1
                    logger.error(
                        f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                        f"找不到频道/帖子 {target_label}"
                    )
                    continue

                can_send_direct = callable(getattr(target, 'send', None))
                can_create_thread = _is_forum_channel(target) and callable(getattr(target, 'create_thread', None))
                if not can_send_direct and not can_create_thread:
                    failed_channels += 1
                    logger.error(
                        f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                        f"目标 {target_label} 不支持发送，请填写频道ID或论坛频道ID"
                    )
                    continue

                target_name = getattr(target, 'name', None) or str(target_id)
                import discord
                content_images_dir = os.path.join(config.DATA_DIR, 'content_images')
                files = []
                for img_filename in image_paths:
                    img_path = os.path.join(content_images_dir, img_filename)
                    if os.path.exists(img_path):
                        files.append(discord.File(img_path))

                channel_label = f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} 目标 {target_name}"
                channel_success = await send_content_to_target(
                    target=target,
                    send_mode=send_mode,
                    post_title=forum_post_title,
                    text_content=text_content,
                    forum_tags=forum_tags,
                    files=files,
                    text_timeout=text_timeout,
                    image_timeout=image_timeout,
                    label=channel_label
                )
                channel_failed = not channel_success

                if channel_failed:
                    failed_channels += 1
                if channel_success:
                    any_success = True

            task_status['sent_count'] += 1
            if any_success:
                task_status['last_sent_at'] = datetime.now().isoformat()
            task_status['next_content_index'] = content_idx + 1
            task_status['next_account_index'] = bot_idx + 1
            _persist_task_state(db)
            if not any_success:
                logger.warning(
                    f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                    f"本轮所有频道发送失败"
                )
            if failed_channels:
                logger.warning(
                    f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                    f"本轮发送有失败频道 {failed_channels}/{len(channel_ids)}"
                )
            if repeat_mode:
                progress_text = f"发送进度(累计): {task_status['sent_count']}"
            else:
                progress_text = (
                    f"发送进度: {task_status['sent_count']}/{task_status['total_contents']}"
                )
            logger.info(
                f"{progress_text} | 账号 "
                f"{current_bot.user.name if current_bot.user else 'Unknown'} | 内容: {title[:30]}"
            )

            # 索引递增
            content_idx += 1
            if rotation_mode:
                bot_idx += 1

            # 等待间隔（支持随时中断）
            try:
                await asyncio.wait_for(
                    stop_sender_event.wait(),
                    timeout=float(interval)
                )
                logger.info("收到停止信号，任务中断")
                break
            except asyncio.TimeoutError:
                continue

    except asyncio.CancelledError:
        logger.info("任务被取消")
    except Exception as e:
        task_status['error'] = str(e)
        logger.error(f"自动发送任务异常: {e}")
    finally:
        task_status['is_running'] = False
        if stop_sender_reason == 'pause':
            task_status['is_paused'] = True
            _persist_task_state(db)
        else:
            task_status['is_paused'] = False
            db.clear_sender_task_state()
        logger.info("自动发送任务结束")


def start_sending_task(
    content_ids: List[int],
    account_ids: List[int],
    channel_ids: List[str],
    post_title: Optional[str],
    rotation_mode: bool,
    repeat_mode: bool,
    interval: int,
    db,
    bot_clients: List,
    bot_loop: asyncio.AbstractEventLoop,
    start_content_index: int = 0,
    start_account_index: int = 0,
    resume: bool = False
) -> Dict:
    """
    启动自动发送任务（从 Flask 线程调用）

    :param content_ids: 内容 ID 列表
    :param account_ids: 账号 ID 列表
    :param channel_ids: 发送目标频道 ID 列表
    :param rotation_mode: 是否轮换模式
    :param repeat_mode: 是否循环发送
    :param interval: 发送间隔（秒）
    :param db: 数据库实例
    :param bot_clients: 机器人客户端列表
    :param bot_loop: Discord bot 的事件循环
    :return: 操作结果
    """
    global current_task, stop_sender_event

    if task_status['is_running'] or (task_status['is_paused'] and not resume):
        return {'success': False, 'error': '已有任务正在运行或已暂停，请先停止或继续'}

    if not content_ids:
        return {'success': False, 'error': '请选择至少一条内容'}

    if not channel_ids:
        return {'success': False, 'error': '请输入至少一个频道ID'}

    if not _has_online_bots(bot_clients, account_ids):
        return {'success': False, 'error': '没有选中的账号在线，请先启动账号'}

    # 重置停止事件
    stop_sender_event.clear()
    global stop_sender_reason
    stop_sender_reason = None
    reset_task_status()
    task_status['content_ids'] = content_ids
    task_status['account_ids'] = account_ids
    task_status['channel_ids'] = channel_ids
    task_status['post_title'] = (post_title or '').strip() or None
    task_status['rotation_mode'] = rotation_mode
    task_status['repeat_mode'] = repeat_mode
    task_status['interval'] = interval
    task_status['next_content_index'] = max(0, start_content_index)
    task_status['next_account_index'] = max(0, start_account_index)
    if resume:
        task_status['sent_count'] = max(0, start_content_index)

    # 在 bot 的事件循环中创建任务
    try:
        future = asyncio.run_coroutine_threadsafe(
            auto_send_loop(
                content_ids=content_ids,
                selected_account_ids=account_ids,
                channel_ids=channel_ids,
                post_title=task_status['post_title'],
                rotation_mode=rotation_mode,
                repeat_mode=repeat_mode,
                interval=interval,
                db=db,
                bot_clients=bot_clients,
                start_content_index=start_content_index,
                start_account_index=start_account_index
            ),
            bot_loop
        )
        logger.info("自动发送任务已提交到事件循环")
        return {'success': True, 'message': '自动发送任务已启动'}
    except Exception as e:
        logger.error(f"启动任务失败: {e}")
        return {'success': False, 'error': str(e)}


def stop_sending_task(db=None) -> Dict:
    """停止自动发送任务"""
    global stop_sender_event, stop_sender_reason

    if not task_status['is_running'] and not task_status['is_paused']:
        return {'success': False, 'error': '当前没有运行中的任务'}

    stop_sender_reason = 'stop'
    stop_sender_event.set()
    task_status['is_paused'] = False
    if db:
        db.clear_sender_task_state()
    logger.info("已发送停止信号")
    return {'success': True, 'message': '任务停止指令已发送'}


def pause_sending_task() -> Dict:
    """暂停自动发送任务"""
    global stop_sender_event, stop_sender_reason

    if not task_status['is_running']:
        return {'success': False, 'error': '当前没有运行中的任务'}

    stop_sender_reason = 'pause'
    stop_sender_event.set()
    logger.info("已发送暂停信号")
    return {'success': True, 'message': '任务暂停指令已发送'}


def resume_sending_task(db, bot_clients: List, bot_loop: asyncio.AbstractEventLoop) -> Dict:
    """继续自动发送任务"""
    state = db.get_sender_task_state()
    if not state:
        return {'success': False, 'error': '没有可继续的任务'}

    # 解析 content_ids
    try:
        content_ids = json.loads(state.get('shop_id') or '[]')
        if not isinstance(content_ids, list):
            content_ids = []
    except:
        content_ids = []

    if not content_ids:
        return {'success': False, 'error': '没有可继续的任务'}

    if task_status['is_running']:
        return {'success': False, 'error': '任务正在运行中'}

    # 解析 rotation_mode
    rotation_mode = state.get('channel_id', 'True').lower() != 'false'
    repeat_mode = bool(state.get('repeat_mode', True))

    account_ids = [int(item) for item in state.get('account_ids', [])]
    channel_ids = [str(item).strip() for item in state.get('channel_ids', []) if str(item).strip()]
    if not _has_online_bots(bot_clients, account_ids):
        return {'success': False, 'error': '没有选中的账号在线，请先启动账号'}

    return start_sending_task(
        content_ids=content_ids,
        account_ids=account_ids,
        channel_ids=channel_ids,
        post_title=state.get('post_title'),
        rotation_mode=rotation_mode,
        repeat_mode=repeat_mode,
        interval=int(state.get('interval') or 60),
        db=db,
        bot_clients=bot_clients,
        bot_loop=bot_loop,
        start_content_index=int(state.get('next_product_index') or 0),
        start_account_index=int(state.get('next_account_index') or 0),
        resume=True
    )
