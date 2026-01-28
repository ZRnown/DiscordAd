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
from typing import List, Dict, Optional
from datetime import datetime

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
        f"轮换模式={rotation_mode}，循环发送={repeat_mode}，间隔{interval}s"
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
            for channel_id in channel_ids:
                channel_label = str(channel_id).strip()
                if not channel_label:
                    continue
                try:
                    channel_id_int = int(channel_label)
                except (TypeError, ValueError):
                    failed_channels += 1
                    logger.error(f"无效频道ID: {channel_label}")
                    continue

                channel = current_bot.get_channel(channel_id_int)
                if not channel:
                    failed_channels += 1
                    logger.error(
                        f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                        f"找不到频道 {channel_id_int}"
                    )
                    continue

                try:
                    # 发送文字内容
                    if text_content:
                        await channel.send(text_content)

                    # 发送图片
                    if image_paths:
                        from config import config
                        import discord
                        content_images_dir = os.path.join(config.DATA_DIR, 'content_images')
                        files = []
                        for img_filename in image_paths:
                            img_path = os.path.join(content_images_dir, img_filename)
                            if os.path.exists(img_path):
                                files.append(discord.File(img_path))
                        if files:
                            await channel.send(files=files)
                except Exception as e:
                    failed_channels += 1
                    logger.error(
                        f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                        f"频道 {channel_id_int} 发送失败: {e}"
                    )

            task_status['sent_count'] += 1
            task_status['last_sent_at'] = datetime.now().isoformat()
            task_status['next_content_index'] = content_idx + 1
            task_status['next_account_index'] = bot_idx + 1
            _persist_task_state(db)
            if failed_channels:
                logger.warning(
                    f"账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                    f"本轮发送有失败频道 {failed_channels}/{len(channel_ids)}"
                )
            logger.info(
                f"✅ 账号 {current_bot.user.name if current_bot.user else 'Unknown'} "
                f"发送成功 ({task_status['sent_count']}/{task_status['total_contents']}): {title[:30]}..."
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
