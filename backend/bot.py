import discord
import aiohttp
import logging
import time
import asyncio
import random
import os
import json
import io
import sqlite3
from datetime import datetime
try:
    from config import config
except ImportError:
    from .config import config

# 全局变量用于多账号机器人管理
bot_clients = []
bot_tasks = []

# 全局冷却管理器：(account_id, channel_id) -> timestamp (上次发送时间)
account_last_sent = {}

# 【新增】AI并发限制：最多同时2个AI推理任务，防止CPU饱和导致Flask阻塞
ai_concurrency_limit = asyncio.Semaphore(2)


def get_all_cooldowns():
    """获取所有活跃的冷却状态（供 API 查询）"""
    current_time = time.time()
    cooldowns = []

    snapshot = account_last_sent.copy()

    for key, last_sent in snapshot.items():
        try:
            acc_id, ch_id = key
            time_passed = current_time - last_sent

            if time_passed < 86400:
                cooldowns.append({
                    'account_id': int(acc_id),
                    'channel_id': str(ch_id),
                    'last_sent': last_sent,
                    'time_passed': time_passed
                })
        except Exception:
            continue

    return cooldowns

def is_account_on_cooldown(account_id, channel_id, interval):
    """检查账号在指定频道是否在冷却中"""
    key = (int(account_id), str(channel_id))

    last = account_last_sent.get(key, 0)
    time_passed = time.time() - last
    is_cooldown = time_passed < interval

    if is_cooldown:
        logger.info(f"❄️ [冷却中] 账号ID:{account_id} 频道:{channel_id} | 剩余: {interval - time_passed:.1f}秒")

    return is_cooldown

def set_account_cooldown(account_id, channel_id):
    """设置账号在指定频道的冷却时间"""
    key = (int(account_id), str(channel_id))
    account_last_sent[key] = time.time()
    logger.info(f"🔥 [设置冷却] 账号ID:{account_id} 频道:{channel_id} | Key: {key}")

def cleanup_expired_cooldowns():
    """清理过期的冷却状态"""
    current_time = time.time()
    expired_keys = []
    for key, last_sent in account_last_sent.items():
        # 如果冷却时间超过24小时，清理掉（防止内存泄漏）
        if current_time - last_sent > 86400:  # 24小时
            expired_keys.append(key)

    for key in expired_keys:
        del account_last_sent[key]
        logger.debug(f"清理过期冷却: {key}")

    if expired_keys:
        logger.info(f"清理了 {len(expired_keys)} 个过期的冷却状态")

def mark_message_as_processed(message_id):
    """检查消息是否已处理（原子操作）"""
    try:
        from database import db
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO processed_messages (message_id) VALUES (?)", (str(message_id),))
            conn.commit()
        return True  # 抢锁成功
    except sqlite3.IntegrityError:
        return False  # 已经被其他Bot抢锁

def get_response_url_for_channel(product, channel_id, user_id=None):
    """根据频道ID和网站配置决定发送哪个链接"""
    import re
    try:
        from database import db
    except ImportError:
        from .database import db

    channel_id_str = str(channel_id)

    # 1. 首先尝试根据频道绑定获取网站配置
    website_config = db.get_website_config_by_channel(channel_id_str, user_id)

    if website_config and website_config.get('url_template'):
        # 从商品URL中提取微店ID
        weidian_url = product.get('weidianUrl') or product.get('product_url') or ''
        weidian_id = None

        # 尝试从URL中提取itemID
        match = re.search(r'itemID=(\d+)', weidian_url)
        if match:
            weidian_id = match.group(1)
        else:
            # 尝试从weidianId字段获取
            weidian_id = product.get('weidianId')

        if weidian_id:
            # 使用URL模板生成链接
            url = website_config['url_template'].replace('{id}', weidian_id)
            logger.info(f"使用网站配置 '{website_config['name']}' 的URL模板生成链接: {url[:50]}...")
            return url

    # 2. 回退到旧的硬编码逻辑（兼容性）
    if config.CNFANS_CHANNEL_ID and channel_id_str == config.CNFANS_CHANNEL_ID:
        if product.get('cnfansUrl'):
            return product['cnfansUrl']
        elif product.get('acbuyUrl'):
            return product['acbuyUrl']
        else:
            return product.get('weidianUrl', '未找到相关商品')

    elif config.ACBUY_CHANNEL_ID and channel_id_str == config.ACBUY_CHANNEL_ID:
        if product.get('acbuyUrl'):
            return product['acbuyUrl']
        elif product.get('cnfansUrl'):
            return product['cnfansUrl']
        else:
            return product.get('weidianUrl', '未找到相关商品')

    # 3. 默认发送CNFans链接
    else:
        if product.get('cnfansUrl'):
            return product['cnfansUrl']
        else:
            return product.get('weidianUrl', '未找到相关商品')

class HTTPLogHandler(logging.Handler):
    """通过HTTP发送日志到Flask应用"""
    def __init__(self):
        super().__init__()
        self.pending_logs = []
        self.is_sending = False

    def emit(self, record):
        try:
            if record.name.startswith('werkzeug'):
                return
            if record.module == 'app' and record.funcName == 'add_log':
                return
            message = record.getMessage()
            if message.startswith('BOT_LOG'):
                return
            # 只发送我们关心的日志级别
            if record.levelno >= logging.INFO:
                log_data = {
                    'timestamp': datetime.now().isoformat(),
                    'level': record.levelname,
                    'message': message,
                    'module': record.module,
                    'func': record.funcName
                }

                # 添加到待发送队列
                self.pending_logs.append(log_data)

                # 如果没有正在发送，启动发送任务
                if not self.is_sending:
                    # 在机器人的事件循环中创建任务
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(self.send_pending_logs())
                        else:
                            # 如果循环没有运行，直接发送（同步方式）
                            self.send_sync(log_data)
                    except RuntimeError:
                        # 没有事件循环，直接同步发送
                        self.send_sync(log_data)

        except Exception as e:
            print(f"HTTP日志处理器错误: {e}")

    def send_sync(self, log_data):
        """同步发送日志（作为fallback）"""
        try:
            import requests
            # 【修复】强制使用 127.0.0.1，因为这是进程间通信，不应走公网
            local_api_url = 'http://127.0.0.1:5001/api'
            response = requests.post(f'{local_api_url}/logs/add',
                                   json=log_data, timeout=2, proxies={'http': None, 'https': None, 'all': None})
            if response.status_code != 200:
                print(f"同步发送日志失败: {response.status_code}")
        except Exception as e:
            # 这里的 print 可能会被重定向，但至少不会抛出 ConnectionRefusedError 炸断流程
            pass

    async def send_pending_logs(self):
        """异步发送待处理的日志"""
        if self.is_sending:
            return

        self.is_sending = True

        # 【修复】强制使用 127.0.0.1
        local_api_url = 'http://127.0.0.1:5001/api'

        try:
            while self.pending_logs:
                log_data = self.pending_logs.pop(0)

                try:
                    async with aiohttp.ClientSession(trust_env=False) as session:
                        async with session.post(f'{local_api_url}/logs/add',
                                              json=log_data, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                            if resp.status != 200:
                                print(f"发送日志失败: {resp.status}")
                except Exception as e:
                    # 队列满了就丢弃，不要无限堆积
                    if len(self.pending_logs) < 1000:
                        self.pending_logs.insert(0, log_data)
                    break

                # 小延迟避免发送太快
                await asyncio.sleep(0.01) # 加快发送速度，减少积压

        finally:
            self.is_sending = False

# 配置日志
logging.basicConfig(level=logging.INFO)

# 添加HTTP日志处理器
http_handler = HTTPLogHandler()
http_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(http_handler)

logger = logging.getLogger(__name__)

# 确保discord库也使用我们的日志配置
logging.getLogger('discord').setLevel(logging.INFO)

class DiscordBotClient(discord.Client):
    # 【新增】频道白名单缓存（类级别共享，所有Bot实例共用）
    _bound_channels_cache = set()  # 已绑定的频道ID集合
    _last_cache_update = 0  # 上次缓存更新时间戳
    _cache_ttl = 60  # 缓存有效期（秒）

    def __init__(self, account_id=None, user_id=None, user_shops=None, role='both'):
        # discord.py-self 可能不需要 intents，或者使用不同的语法
        try:
            # 尝试使用标准的 intents
            intents = discord.Intents.default()
            intents.message_content = True
            intents.messages = True
            intents.guilds = True
            super().__init__(intents=intents)
        except AttributeError:
            # 如果 Intents 不存在，直接初始化（discord.py-self 可能不需要）
            super().__init__()
        self.current_token = None
        self.running = False
        self.account_id = account_id
        self.user_id = user_id  # 用户ID，用于获取个性化设置
        self.user_shops = user_shops  # 用户管理的店铺列表
        self.role = role  # 'listener', 'sender', 'both' - 账号角色
        self.stop_requested = False
        self._login_ready_event = None
        self.supervisor_task = None

    def _reset_login_ready_event(self):
        self._login_ready_event = asyncio.Event()

    async def _wait_for_login_ready(self):
        if self._login_ready_event is None:
            self._reset_login_ready_event()
        await self._login_ready_event.wait()

    async def start_with_retries(self, token, max_retries=None, timeout=None, retry_delay=None):
        if max_retries is None:
            max_retries = config.ACCOUNT_LOGIN_RETRY_TIMES
        if timeout is None:
            timeout = config.ACCOUNT_LOGIN_TIMEOUT
        if retry_delay is None:
            retry_delay = config.ACCOUNT_LOGIN_RETRY_DELAY

        attempt = 0
        while True:
            if self.stop_requested:
                logger.info(f'账号 {self.account_id} 已请求停止，取消登录流程')
                return
            if max_retries and attempt >= max_retries:
                logger.error(f'账号 {self.account_id} 登录失败，已达到最大重试次数 {max_retries}')
                self.running = False
                try:
                    try:
                        from database import db
                    except ImportError:
                        from .database import db
                    if hasattr(self, 'account_id'):
                        db.update_account_status(self.account_id, 'offline')
                except Exception:
                    pass
                return

            attempt += 1
            self._reset_login_ready_event()
            attempt_started = time.monotonic()
            logger.info(
                f'账号 {self.account_id} 登录尝试 {attempt}/{max_retries} '
                f'(超时 {timeout}s, 重试等待 {retry_delay}s)'
            )

            login_task = asyncio.create_task(self._wait_for_login_ready())
            start_task = asyncio.create_task(self.start(token, reconnect=True))

            done, _pending = await asyncio.wait(
                {login_task, start_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED
            )
            elapsed = time.monotonic() - attempt_started

            if login_task in done:
                try:
                    login_task.result()
                except Exception as e:
                    logger.error(f'账号 {self.account_id} 登录事件异常: {e}')
                else:
                    logger.info(f'账号 {self.account_id} 登录成功，用时 {elapsed:.1f}s')
                self.current_token = token
                try:
                    await start_task
                except asyncio.CancelledError:
                    logger.warning(f'账号 {self.account_id} 连接被取消')
                except Exception as e:
                    logger.error(f'账号 {self.account_id} 运行中断: {e}')
                if self.stop_requested:
                    return
                logger.warning(f'账号 {self.account_id} 连接断开，准备重新登录...')
                attempt = 0
                if retry_delay:
                    await asyncio.sleep(retry_delay)
                continue

            if start_task in done:
                try:
                    exc = start_task.exception()
                except asyncio.CancelledError:
                    exc = None
                if exc:
                    logger.error(f'账号 {self.account_id} 登录失败，用时 {elapsed:.1f}s: {exc}')
                else:
                    logger.warning(f'账号 {self.account_id} 登录提前结束，用时 {elapsed:.1f}s，准备重试')
            else:
                logger.warning(
                    f'账号 {self.account_id} 登录超时({timeout}s)，已用 {elapsed:.1f}s，准备重试'
                )

            if not start_task.done():
                start_task.cancel()
                try:
                    await start_task
                except (Exception, asyncio.CancelledError):
                    pass

            if not login_task.done():
                login_task.cancel()
                try:
                    await login_task
                except (Exception, asyncio.CancelledError):
                    pass

            try:
                if not self.is_closed():
                    await self.close()
            except Exception:
                pass

            if self.stop_requested:
                return
            if retry_delay:
                await asyncio.sleep(retry_delay)

    async def on_connect(self):
        logger.info(f'账号 {self.account_id} 已连接到 Discord 网关')

    async def on_disconnect(self):
        if self.stop_requested:
            logger.info(f'账号 {self.account_id} 已主动断开连接')
        else:
            logger.warning(f'账号 {self.account_id} 连接断开，将自动重连...')

    async def on_resumed(self):
        logger.info(f'账号 {self.account_id} 连接已恢复')

    async def _refresh_channel_cache(self):
        """【新增】刷新频道白名单缓存（60秒TTL）

        从数据库获取所有已绑定的频道ID，更新类级别缓存。
        使用TTL机制避免频繁查询数据库。
        """
        current_time = time.time()

        # 检查缓存是否过期
        if current_time - DiscordBotClient._last_cache_update < DiscordBotClient._cache_ttl:
            return  # 缓存仍然有效，无需刷新

        try:
            # 在线程池中执行数据库查询（避免阻塞事件循环）
            try:
                from database import db
            except ImportError:
                from .database import db

            channel_ids = await asyncio.get_event_loop().run_in_executor(
                None, db.get_all_bound_channel_ids
            )

            # 更新类级别缓存
            DiscordBotClient._bound_channels_cache = channel_ids
            DiscordBotClient._last_cache_update = current_time

            logger.debug(f"✅ 频道白名单缓存已刷新，共 {len(channel_ids)} 个频道")

        except Exception as e:
            logger.error(f"❌ 刷新频道白名单缓存失败: {e}")
            # 失败时不更新时间戳，下次会重试

    async def schedule_reply(self, message, product, custom_reply=None):
        """调度回复到合适的发送账号 (增强版：带详细状态诊断)"""

        try:
            # 清理过期的冷却状态
            cleanup_expired_cooldowns()

            try:
                from database import db
            except ImportError:
                from .database import db

            # 获取用户设置以确定延迟时间
            user_settings = await asyncio.get_event_loop().run_in_executor(None, db.get_user_settings, self.user_id)
            min_delay = user_settings.get('global_reply_min_delay', 3.0)
            max_delay = user_settings.get('global_reply_max_delay', 8.0)

            # 生成回复内容
            response_content = self._generate_reply_content(product, message.channel.id, custom_reply)

            # 1. 尝试获取网站配置（必须绑定，否则不回复）
            website_config = await self.get_website_config_by_channel_async(message.channel.id)

            if not website_config:
                logger.info(f"频道 {message.channel.id} 未绑定网站配置，跳过回复")
                return

            target_client = None

            # 2. 获取数据库配置的发送者 ID
            db_sender_ids = await asyncio.get_event_loop().run_in_executor(
                None, db.get_website_senders, website_config['id']
            )

            if not db_sender_ids:
                logger.warning(
                    f"❌ [配置错误] 网站配置 '{website_config.get('name')}' 未绑定任何【发送】账号。请在网站配置中绑定账号。"
                )
                return

            # === 获取当前真正在线的机器人账号 ID ===
            online_client_ids = [c.account_id for c in bot_clients if c.is_ready() and not c.is_closed()]

            # 调试信息：打印当前状态
            logger.info(f"配置账号ID: {db_sender_ids} | 在线账号ID: {online_client_ids}")

            # 取交集：既在数据库配置了，又是当前在线的
            valid_senders = [uid for uid in db_sender_ids if uid in online_client_ids]

            if not valid_senders:
                logger.warning("❌ [状态错误] 配置的发送账号均不在线。请检查 Discord 账号连接状态。")
                return

            # 3. 轮换/冷却逻辑 - 使用用户级别设置
            # 优先使用用户个性化设置，如果没有则使用全局配置
            rotation_enabled = website_config.get('rotation_enabled', 1)
            rotation_interval = website_config.get('rotation_interval', 180)

            if self.user_id and website_config.get('id'):
                user_website_settings = await asyncio.get_event_loop().run_in_executor(
                    None, db.get_user_website_settings, self.user_id, website_config['id']
                )
                if user_website_settings:
                    rotation_enabled = user_website_settings.get('rotation_enabled', rotation_enabled)
                    rotation_interval = user_website_settings.get('rotation_interval', rotation_interval)
                    logger.info(f"📋 使用用户级别设置: rotation_interval={rotation_interval}秒, rotation_enabled={rotation_enabled}")

            available_senders = []

            if rotation_enabled:
                # 筛选非冷却的（按频道区分冷却）
                available_senders = [
                    uid for uid in valid_senders
                    if not is_account_on_cooldown(uid, message.channel.id, rotation_interval)
                ]

                # 只有 valid_senders 有值但 available_senders 为空，才是真正的“冷却中”
                if not available_senders:
                    logger.info(
                        f"⏳ [冷却中] 频道 {message.channel.id} 所有在线账号 ({len(valid_senders)}个) "
                        f"均处于 {rotation_interval}秒 冷却期内，跳过发送"
                    )
                    return

            else:
                available_senders = valid_senders

            # 4. 选中一个 ID
            if available_senders:
                selected_id = random.choice(available_senders)
                target_client = next((c for c in bot_clients if c.account_id == selected_id), None)
                logger.info(
                    f"✅ 本次选中发送账号: {target_client.user.name if target_client else selected_id} (ID: {selected_id})"
                )
            else:
                logger.warning("❌ 逻辑异常：有 valid_senders 但无可用发送账号")
                return

            # 5. 执行发送
            if target_client:
                try:
                    target_channel = target_client.get_channel(message.channel.id)

                    if target_channel:
                        async with target_channel.typing():
                            await asyncio.sleep(random.uniform(min_delay, max_delay))

                        # 【关键修复】
                        # 不要使用 message.reply()，因为 message 绑定的是监听者(Listener)客户端
                        # 必须用 target_channel.send(..., reference=message) 才会使用 target_client(Sender) 的 token
                        try:
                            # === 1. 收集所有要发送的图片文件 ===
                            files = []

                            # 检查是否是自定义模式，且有图片
                            is_custom_mode = custom_reply and (
                                custom_reply.get('reply_type') == 'custom_only' or
                                custom_reply.get('reply_type') == 'text'
                            )

                            if is_custom_mode:
                                # 获取图片信息
                                # 注意：如果是从 search_similar_text 返回的 product，字段名可能已经格式化
                                # 需要兼容处理

                                # 1. 尝试获取自定义图片链接
                                custom_urls = product.get('customImageUrls', []) or product.get('custom_image_urls', [])
                                if isinstance(custom_urls, str):
                                    try:
                                        custom_urls = json.loads(custom_urls)
                                    except:
                                        custom_urls = []

                                image_source = product.get('imageSource') or product.get('image_source') or 'product'

                                # 收集图片文件（Discord限制最多10个文件）
                                if image_source == 'custom' and custom_urls:
                                    for url in custom_urls[:10]:  # 限制最多10张
                                        if len(files) >= 10:
                                            break
                                        try:
                                            async with aiohttp.ClientSession() as session:
                                                async with session.get(url) as resp:
                                                    if resp.status == 200:
                                                        data = await resp.read()
                                                        filename = url.split('/')[-1] or 'image.jpg'
                                                        files.append(discord.File(io.BytesIO(data), filename))
                                        except Exception as e:
                                            logger.error(f"下载自定义图片失败: {e}")

                                elif image_source == 'upload':
                                    # 处理上传的自定义回复图片
                                    pid = product.get('id')

                                    # 从 uploaded_reply_images 字段获取上传的图片文件名列表
                                    uploaded_filenames = product.get('uploaded_reply_images', [])
                                    if isinstance(uploaded_filenames, str):
                                        try:
                                            uploaded_filenames = json.loads(uploaded_filenames)
                                        except:
                                            # 如果解析失败，且它本身就是列表，则保持原样，否则置空
                                            uploaded_filenames = uploaded_filenames if isinstance(uploaded_filenames, list) else []

                                    if pid and uploaded_filenames:
                                        # 使用新的API端点获取上传的自定义回复图片
                                        for filename in uploaded_filenames[:10]:  # 限制最多10张
                                            if len(files) >= 10:
                                                break
                                            img_url = f"{config.BACKEND_API_URL}/api/custom_reply_image/{pid}/{filename}"
                                            try:
                                                async with aiohttp.ClientSession() as session:
                                                    async with session.get(img_url) as resp:
                                                        if resp.status == 200:
                                                            data = await resp.read()
                                                            files.append(discord.File(io.BytesIO(data), filename))
                                            except Exception as e:
                                                logger.error(f"下载上传的自定义回复图片失败: {e}")

                                elif image_source == 'product':
                                    # 处理商品图集中的图片
                                    pid = product.get('id')
                                    indexes = product.get('selectedImageIndexes', []) or product.get('custom_reply_images', [])

                                    if isinstance(indexes, str):
                                        try:
                                            indexes = json.loads(indexes)
                                        except:
                                            indexes = []

                                    if pid and indexes:
                                        # 使用原有的API端点获取商品图集中的图片
                                        for idx in indexes[:10]:  # 限制最多10张
                                            if len(files) >= 10:
                                                break
                                            img_url = f"{config.BACKEND_API_URL}/api/image/{pid}/{idx}"
                                            try:
                                                async with aiohttp.ClientSession() as session:
                                                    async with session.get(img_url) as resp:
                                                        if resp.status == 200:
                                                            data = await resp.read()
                                                            files.append(discord.File(io.BytesIO(data), f"{pid}_{idx}.jpg"))
                                            except Exception as e:
                                                logger.error(f"下载商品图片失败: {e}")

                            # === 2. 发送文字和所有图片（合并为一条消息） ===
                            if not response_content and not files:
                                logger.warning(
                                    f"⚠️ 无可发送内容: 商品ID={product.get('id')}，未生成文字且无图片"
                                )
                                return

                            await target_channel.send(
                                content=response_content if response_content else None,
                                files=files if files else None,
                                reference=message,
                                mention_author=True
                            )

                            if hasattr(target_client, 'account_id') and target_client.account_id:
                                set_account_cooldown(target_client.account_id, message.channel.id)

                            logger.info(
                                f"✅ [回复成功] 真实发送账号: {target_client.user.name} (ID: {target_client.account_id}) | 商品ID: {product.get('id')} | 图片数量: {len(files)}"
                            )

                        except Exception as reply_error:
                            logger.warning(f"回复失败，尝试直接发送: {reply_error}")
                            if response_content:
                                await target_channel.send(response_content)

                            if hasattr(target_client, 'account_id') and target_client.account_id:
                                set_account_cooldown(target_client.account_id, message.channel.id)

                            logger.info(
                                f"✅ [发送成功] 真实发送账号: {target_client.user.name} | 商品ID: {product.get('id')}"
                            )

                    else:
                        logger.warning(
                            f"❌ 选中的账号 {target_client.user.name} 无法访问频道 {message.channel.id} (可能不在该服务器)"
                        )
                        return

                except Exception as e:
                    logger.error(f"❌ 发送异常: {e}")

        except Exception as e:
            logger.error(f"❌ 严重错误: {e}")

    def _generate_reply_content(self, product, channel_id, custom_reply=None):
        """生成回复内容"""
        if custom_reply:
            reply_type = custom_reply.get('reply_type')

            if reply_type == 'custom_only':
                # 只发送自定义内容，不发送链接
                return custom_reply.get('content', '')

            elif reply_type == 'text_and_link':
                # 发送文字 + 链接
                response = get_response_url_for_channel(product, channel_id, self.user_id)
                return f"{custom_reply.get('content', '')}\n{response}".strip()

            elif reply_type == 'text':
                # 只发送文字
                return custom_reply.get('content', '')

        # 默认行为：发送链接
        return get_response_url_for_channel(product, channel_id, self.user_id)

    def get_website_config_by_channel(self, channel_id):
        """根据频道ID获取对应的网站配置"""
        try:
            try:
                from database import db
            except ImportError:
                from .database import db

            # 查询频道绑定的网站配置
            configs = db.get_website_configs()
            for config in configs:
                channels = config.get('channels', [])
                if str(channel_id) in channels:
                    return config
            return None
        except Exception as e:
            logger.error(f"获取频道网站配置失败: {e}")
            return None

    async def get_website_config_by_channel_async(self, channel_id):
        """异步版本：根据频道ID获取对应的网站配置"""
        try:
            try:
                from database import db
            except ImportError:
                from .database import db

            # 异步查询频道绑定的网站配置
            configs = await asyncio.get_event_loop().run_in_executor(None, db.get_website_configs)
            for config in configs:
                channels = config.get('channels', [])
                if str(channel_id) in channels:
                    return config
            return None
        except Exception as e:
            logger.error(f"异步获取频道网站配置失败: {e}")
            return None

    def _should_filter_message(self, message):
        """检查消息是否应该被过滤"""
        try:
            try:
                from database import db
            except ImportError:
                from .database import db

            # 1. 检查全局消息过滤规则
            filters = db.get_message_filters()
            message_content = message.content.lower()

            for filter_rule in filters:
                filter_value = filter_rule['filter_value'].lower()
                filter_type = filter_rule['filter_type']

                if filter_type == 'contains':
                    if filter_value in message_content:
                        logger.info(f'消息被过滤: 包含 "{filter_value}"')
                        return True
                elif filter_type == 'starts_with':
                    if message_content.startswith(filter_value):
                        logger.info(f'消息被过滤: 以 "{filter_value}" 开头')
                        return True
                elif filter_type == 'ends_with':
                    if message_content.endswith(filter_value):
                        logger.info(f'消息被过滤: 以 "{filter_value}" 结尾')
                        return True
                elif filter_type == 'regex':
                    import re
                    try:
                        if re.search(filter_value, message_content, re.IGNORECASE):
                            logger.info(f'消息被过滤: 匹配正则 "{filter_value}"')
                            return True
                    except re.error:
                        logger.warning(f'无效的正则表达式: {filter_value}')
                elif filter_type == 'user_id':
                    # 检查用户ID过滤
                    filter_user_ids = [uid.strip() for uid in filter_value.split(',') if uid.strip()]
                    sender_id = str(message.author.id)
                    sender_name = str(message.author.name).lower()

                    for blocked_id in filter_user_ids:
                        blocked_id = blocked_id.strip()
                        if blocked_id == sender_id or blocked_id.lower() in sender_name:
                            logger.info(f'消息被过滤: 用户 {message.author.name} (ID: {sender_id}) 在过滤列表中')
                            return True

            # 2. 检查用户个性化设置的过滤规则
            if self.user_id:
                user_settings = db.get_user_settings(self.user_id)
                if user_settings:
                    # 检查用户黑名单
                    user_blacklist = user_settings.get('user_blacklist', '')
                    if user_blacklist:
                        blacklist_users = [u.strip().lower() for u in user_blacklist.split(',') if u.strip()]
                        sender_name = str(message.author.name).lower()
                        sender_id = str(message.author.id).lower()

                        for blocked_user in blacklist_users:
                            blocked_user = blocked_user.lower()
                            if blocked_user in sender_name or blocked_user == sender_id:
                                logger.info(f'消息被过滤: 用户 {message.author.name} 在黑名单中')
                                return True

                    # 检查关键词过滤
                    keyword_filters = user_settings.get('keyword_filters', '')
                    if keyword_filters:
                        filter_keywords = [k.strip().lower() for k in keyword_filters.split(',') if k.strip()]

                        for keyword in filter_keywords:
                            if keyword in message_content:
                                logger.info(f'消息被过滤: 包含关键词 "{keyword}"')
                                return True

        except Exception as e:
            logger.error(f'检查消息过滤失败: {e}')

        return False

    def _get_custom_reply(self):
        """获取自定义回复内容"""
        try:
            try:
                from database import db
            except ImportError:
                from .database import db
            replies = db.get_custom_replies()

            if replies:
                # 返回优先级最高的活跃回复
                return replies[0]
        except Exception as e:
            logger.error(f'获取自定义回复失败: {e}')

        return None

    async def on_ready(self):
        logger.info(f'Discord机器人已登录: {self.user} (ID: {self.user.id})')
        logger.info(f'机器人已就绪，开始监听消息')
        try:
            try:
                from database import db
            except ImportError:
                from .database import db
            bound_channels = await asyncio.get_event_loop().run_in_executor(None, db.get_all_bound_channel_ids)
            if bound_channels:
                bound_list = sorted(bound_channels)
                preview = ", ".join(bound_list[:5])
                suffix = " ..." if len(bound_list) > 5 else ""
                logger.info(f'监听频道: 已绑定 {len(bound_list)} 个 ({preview}{suffix})')
            else:
                logger.info('监听频道: 未绑定频道')
        except Exception as e:
            logger.error(f'获取监听频道失败: {e}')
        self.running = True
        if self._login_ready_event and not self._login_ready_event.is_set():
            self._login_ready_event.set()

        # 更新数据库中的账号状态为在线
        try:
            try:
                from database import db
            except ImportError:
                from .database import db
            if hasattr(self, 'account_id'):
                db.update_account_status(self.account_id, 'online')
                logger.info(f'账号 {self.account_id} 状态已更新为在线')
        except Exception as e:
            logger.error(f'更新账号状态失败: {e}')

    async def on_message(self, message):
        if not self.running:
            return

        # 忽略自己的消息
        if message.author == self.user:
            return

        # 忽略机器人和webhook的消息
        if message.author.bot or message.webhook_id:
            return

        # 1. 忽略 @别人的信息
        if message.mentions:
            return

        # 2. 忽略回复别人的信息
        if message.reference is not None:
            return

        # 3. 角色过滤：纯 sender 账号完全不处理消息
        if self.role == 'sender':
            return

        # =================================================================
        # 【核心修复】先检查：这条消息所在的频道，是否归当前账号"监听"？
        # =================================================================
        try:
            # 异步获取该频道绑定的网站配置
            website_config = await self.get_website_config_by_channel_async(message.channel.id)

            # 如果这个频道没有绑定任何配置，直接忽略
            if not website_config:
                # logger.debug(f"频道 {message.channel.id} 未绑定配置，账号 {self.account_id} 忽略此消息")
                return

            # 进一步检查：当前账号是否是该配置的合法监听者？
            # 这是一个关键步骤，防止未绑定的账号处理已绑定频道的消息
            try:
                from database import db
            except ImportError:
                from .database import db

            # 获取该网站配置绑定的所有监听者ID
            listener_ids = await asyncio.get_event_loop().run_in_executor(
                None, db.get_website_listeners, website_config['id']
            )

            # 如果当前账号不在监听列表中，直接忽略
            if self.account_id not in listener_ids:
                # logger.debug(f"账号 {self.account_id} 不是频道 {message.channel.id} 的监听者，忽略")
                return

        except Exception as e:
            logger.error(f"检查频道绑定权限失败: {e}")
            return

        # =================================================================
        # 【核心修复】确认我有资格处理后，再抢全局锁
        # =================================================================
        try:
            if not mark_message_as_processed(message.id):
                logger.info(f"消息 {message.id} 已被其他(合法的)Bot处理，跳过")
                return
        except Exception as e:
            logger.error(f"消息去重检查失败: {e}")
            return

        # 4. 触发内容过滤规则
        if self._should_filter_message(message):
            return

        logger.info(f'📨 [接收] 账号:{self.user.name} | 频道:{message.channel.name} | 内容: "{message.content[:50]}..."')

        # 获取用户设置
        keyword_reply_enabled = True
        image_reply_enabled = True
        if self.user_id:
            try:
                user_settings = await asyncio.get_event_loop().run_in_executor(
                    None, db.get_user_settings, self.user_id
                )
                keyword_reply_enabled = user_settings.get('keyword_reply_enabled', 1) == 1
                image_reply_enabled = user_settings.get('image_reply_enabled', 1) == 1
            except Exception as e:
                logger.error(f'获取用户回复开关设置失败: {e}')

        # 处理关键词消息转发
        await self.handle_keyword_forward(message)

        # 处理关键词搜索
        if keyword_reply_enabled:
            await self.handle_keyword_search(message)

        # 处理图片
        if image_reply_enabled and message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    logger.info(f"📷 检测到图片，开始处理: {attachment.filename}")
                    await self.handle_image(message, attachment)

    async def handle_image(self, message, attachment):
        try:
            # 【增强稳定性】增加超时时间，添加代理支持
            timeout = aiohttp.ClientTimeout(total=30, connect=10)  # 30秒总超时，10秒连接超时
            image_data = None

            # 【代理配置】从环境变量获取代理（支持国内网络环境）
            proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None

            # 【伪装头】添加 User-Agent 防止被 Discord CDN 拒绝
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            # 重试最多3次
            for attempt in range(3):
                try:
                    logger.info(f"下载Discord图片 (尝试 {attempt + 1}/3): {attachment.filename}")
                    # 【关键修复】trust_env=True 允许使用系统代理
                    async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
                        async with session.get(attachment.url, proxy=proxy_url) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                logger.info(f"图片下载成功，大小: {len(image_data)} bytes")
                                break
                            else:
                                logger.warning(f"图片下载失败，状态码: {resp.status}")
                except aiohttp.ClientError as e:
                    logger.warning(f"图片下载网络错误 (尝试 {attempt + 1}/3): {e}")
                    if attempt < 2:  # 不是最后一次尝试
                        await asyncio.sleep(2)  # 【增强】等待2秒后重试
                except Exception as e:
                    logger.error(f"图片下载未知错误 (尝试 {attempt + 1}/3): {e}")
                    break

            if image_data is None:
                logger.error("图片下载失败，已达到最大重试次数")
                return  # 静默失败，不发送错误消息

            # 【新增】AI并发限制：最多同时2个AI推理任务
            # 使用Semaphore控制并发，防止CPU饱和导致Flask主线程阻塞
            async with ai_concurrency_limit:
                logger.debug(f"🔒 获取AI并发锁，当前等待队列: {ai_concurrency_limit._value}")

                # 调用 DINOv2 服务识别图片，不使用店铺过滤（所有用户都能识别所有商品）
                result = await self.recognize_image(image_data, user_shops=None)

                logger.debug(f"🔓 释放AI并发锁")

            logger.info(f'图片识别结果: success={result.get("success") if result else False}, results_count={len(result.get("results", [])) if result else 0}')

            if result and result.get('success') and result.get('results'):
                # 获取最佳匹配结果
                best_match = result['results'][0]
                similarity = best_match.get('similarity', 0)

                # 获取用户个性化相似度阈值，如果没有则使用全局默认值
                user_threshold = config.DISCORD_SIMILARITY_THRESHOLD  # 默认值
                if self.user_id:
                    try:
                        try:
                            from database import db
                        except ImportError:
                            from .database import db
                        # 异步获取用户设置
                        user_settings = await asyncio.get_event_loop().run_in_executor(None, db.get_user_settings, self.user_id)
                        if user_settings and 'discord_similarity_threshold' in user_settings:
                            user_threshold = user_settings['discord_similarity_threshold']
                    except Exception as e:
                        logger.error(f'获取用户相似度设置失败: {e}')

                logger.info(f'最佳匹配相似度: {similarity:.4f}, 用户阈值: {user_threshold:.4f}')

                # 严格执行用户设置的阈值
                if similarity >= user_threshold:
                    product = best_match.get('product', {})
                    logger.info(f'✅ 匹配成功! 相似度: {similarity:.2f} | 商品: {product.get("id")} | 频道: {message.channel.name}')

                    # 检查商品是否启用了自动回复规则
                    product_rule_enabled = product.get('ruleEnabled', True)

                    if product_rule_enabled:
                        # 使用全局自定义回复
                        custom_reply = self._get_custom_reply()

                        # 使用调度机制回复，而不是直接回复
                        await self.schedule_reply(message, product, custom_reply)
                    else:
                        # 商品级自定义回复
                        custom_text = product.get('custom_reply_text', '').strip()
                        custom_image_indexes = product.get('selectedImageIndexes', [])
                        custom_image_urls = product.get('customImageUrls', [])

                        # 发送自定义文本消息
                        if custom_text:
                            await message.reply(custom_text)

                        # 发送图片（按优先级：本地上传 > 自定义链接 > 商品图片）
                        images_sent = False

                        # 优先检查图片来源类型
                        image_source = product.get('image_source', 'product')

                        if image_source == 'upload':
                            # 发送本地上传的图片
                            try:
                                from database import db
                                # 获取该商品的所有图片（包括上传的）
                                product_images = db.get_product_images(product['id'])
                                if product_images:
                                    for img_data in product_images[:10]:  # 最多发送10张图片
                                        try:
                                            image_path = img_data.get('image_path')
                                            # 如果是相对路径，构建完整路径
                                            if image_path and not os.path.isabs(image_path):
                                                if image_path.startswith('data/'):
                                                    image_path = image_path[len('data/'):]
                                                image_path = os.path.join(config.DATA_DIR, image_path)
                                            if image_path and os.path.exists(image_path):
                                                await message.reply(file=discord.File(image_path, os.path.basename(image_path)))
                                                images_sent = True
                                        except Exception as e:
                                            logger.error(f'发送本地上传图片失败: {e}')
                            except Exception as e:
                                logger.error(f'处理本地上传图片回复失败: {e}')

                        elif image_source == 'custom' and custom_image_urls and len(custom_image_urls) > 0:
                            # 发送自定义图片链接
                            try:
                                # 【代理配置】从环境变量获取代理
                                proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
                                # 【伪装头】添加 User-Agent
                                headers = {
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                                }
                                timeout = aiohttp.ClientTimeout(total=30, connect=10)

                                for url in custom_image_urls[:10]:  # 最多发送10张图片
                                    try:
                                        # 【关键修复】trust_env=True 允许使用系统代理
                                        async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
                                            async with session.get(url.strip(), proxy=proxy_url) as resp:
                                                if resp.status == 200:
                                                    image_data = await resp.read()
                                                    # 从URL提取文件名
                                                    filename = url.split('/')[-1].split('?')[0] or f"image_{custom_image_urls.index(url)}.jpg"
                                                    if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                                                        filename += '.jpg'
                                                    await message.reply(file=discord.File(io.BytesIO(image_data), filename))
                                                    images_sent = True
                                    except Exception as e:
                                        logger.error(f'发送自定义图片失败 {url}: {e}')
                            except Exception as e:
                                logger.error(f'处理自定义图片回复失败: {e}')

                        elif custom_image_indexes and len(custom_image_indexes) > 0:
                            # 发送选中的商品图片
                            try:
                                import aiofiles
                                from database import db

                                for image_index in custom_image_indexes:
                                    try:
                                        # 获取图片路径
                                        image_path = db.get_product_image_path(product['id'], image_index)
                                        if image_path and os.path.exists(image_path):
                                            # 发送图片文件
                                            await message.reply(file=discord.File(image_path, f"image_{image_index}.jpg"))
                                            images_sent = True
                                    except Exception as e:
                                        logger.error(f'发送商品图片失败: {e}')
                            except Exception as e:
                                logger.error(f'处理商品图片回复失败: {e}')

                        # 如果既没有文本也没有图片，则发送默认链接
                        if not custom_text and not images_sent:
                            response = get_response_url_for_channel(product, message.channel.id, self.user_id)
                            await message.reply(response)

                    logger.info(f'图片识别成功，相似度: {similarity:.4f}')
                else:
                    # 相似度低于阈值，不回复任何消息
                    logger.info(f'图片识别相似度 {similarity:.4f} 低于用户阈值 {user_threshold:.4f}，不回复')

        except Exception as e:
            logger.error(f'Error handling image: {e}')
            # 不发送错误消息到Discord，只记录日志

    async def handle_keyword_forward(self, message):
        """处理关键词消息转发"""
        try:
            # 检查消息内容是否包含关键词
            message_content = message.content.lower() if message.content else ""
            has_keyword = any(keyword.strip().lower() in message_content for keyword in config.FORWARD_KEYWORDS)

            if has_keyword and config.FORWARD_TARGET_CHANNEL_ID:
                # 获取目标频道
                target_channel = self.get_channel(config.FORWARD_TARGET_CHANNEL_ID)
                if target_channel:
                    # 构建转发消息
                    forward_embed = discord.Embed(
                        title="📢 商品相关消息转发",
                        description=f"**原始消息:** {message.content[:500]}{'...' if len(message.content) > 500 else ''}",
                        color=0x00ff00,
                        timestamp=message.created_at
                    )

                    forward_embed.add_field(
                        name="发送者",
                        value=f"{message.author.name}#{message.author.discriminator}",
                        inline=True
                    )

                    forward_embed.add_field(
                        name="来源频道",
                        value=f"#{message.channel.name}",
                        inline=True
                    )

                    forward_embed.add_field(
                        name="服务器",
                        value=message.guild.name if message.guild else "DM",
                        inline=True
                    )

                    # 如果有附件，添加到embed中
                    if message.attachments:
                        attachment_urls = [att.url for att in message.attachments]
                        forward_embed.add_field(
                            name="附件",
                            value="\n".join(attachment_urls),
                            inline=False
                        )

                    forward_embed.set_footer(text=f"消息ID: {message.id}")

                    await target_channel.send(embed=forward_embed)
                    logger.info(f"转发了包含关键词的消息: {message.content[:100]}...")
                else:
                    logger.warning(f"找不到目标频道: {config.FORWARD_TARGET_CHANNEL_ID}")

        except Exception as e:
            logger.error(f'Error handling keyword forward: {e}')

    async def handle_keyword_search(self, message):
        """处理关键词商品搜索"""
        try:
            # 只处理纯文字消息（不包含图片的）
            if not message.content or message.attachments:
                return

            search_query = message.content.strip()
            if not search_query:
                return

            # 过滤太短的消息（至少需要2个字符）
            if len(search_query) < 2:
                return

            # 过滤纯数字消息（如 "1", "2", "123"）
            if search_query.isdigit():
                return

            # 过滤只包含数字和空格的消息（如 "1 2 3"）
            if search_query.replace(' ', '').isdigit():
                return

            # 过滤常见的无意义短消息
            meaningless_patterns = {'ok', 'no', 'yes', 'hi', 'hey', 'lol', 'lmao', 'wtf', 'omg', 'bruh'}
            if search_query.lower() in meaningless_patterns:
                return

            # 调用搜索API
            result = await self.search_products_by_keyword(search_query)

            products = []
            if result and result.get('success') and result.get('products'):
                products = result['products'][:5]  # 最多显示5个结果

            # 只在找到商品时回复和记录日志
            if products:
                logger.info(f'关键词搜索成功: "{search_query}" -> 找到 {len(products)} 个商品')
                product = products[0]

                # 检查频道是否绑定了网站配置（必须绑定才能回复）
                website_config = await self.get_website_config_by_channel_async(message.channel.id)
                if not website_config:
                    logger.info(f"频道 {message.channel.id} 未绑定网站配置，跳过关键词回复")
                    return

                # === 关键修复逻辑 ===
                # 检查规则是否启用（兼容字符串/数字）
                # 注意：后端API返回的 autoReplyEnabled 即 ruleEnabled
                rule_enabled = product.get('autoReplyEnabled', True)
                if isinstance(rule_enabled, str):
                    rule_enabled = rule_enabled.strip().lower() not in {'0', 'false', 'no', 'off'}
                elif isinstance(rule_enabled, (int, float)):
                    rule_enabled = bool(rule_enabled)

                custom_reply = None

                # 检查是否配置了自定义图片
                def _coerce_list(value):
                    if not value:
                        return []
                    if isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                        except json.JSONDecodeError:
                            return []
                        return parsed if isinstance(parsed, list) else []
                    if isinstance(value, list):
                        return value
                    return []

                has_custom_images = False
                image_source = product.get('imageSource') or product.get('image_source')

                if image_source == 'upload':
                    uploaded_imgs = _coerce_list(product.get('uploaded_reply_images'))
                    product['uploaded_reply_images'] = uploaded_imgs
                    has_custom_images = bool(uploaded_imgs)
                elif image_source == 'custom':
                    custom_urls = _coerce_list(product.get('customImageUrls')) or _coerce_list(product.get('custom_image_urls'))
                    if custom_urls:
                        product['customImageUrls'] = custom_urls
                    has_custom_images = bool(custom_urls)
                elif image_source == 'product':
                    selected_indexes = _coerce_list(product.get('selectedImageIndexes')) or _coerce_list(product.get('custom_reply_images'))
                    if selected_indexes:
                        product['selectedImageIndexes'] = selected_indexes
                    has_custom_images = bool(selected_indexes)

                # 如果规则禁用了，或者配置了自定义图片，都需要创建 custom_reply
                if not rule_enabled or has_custom_images:
                    # 构造 custom_reply 对象供 schedule_reply 使用
                    custom_text = (product.get('custom_reply_text') or '').strip()

                    # 即使没有文本，只要是要发图片，也需要传递 custom_reply 信号
                    # schedule_reply 会进一步处理图片逻辑
                    custom_reply = {
                        'reply_type': 'text' if custom_text else 'custom_only', # custom_only 表示不发默认链接
                        'content': custom_text,
                        # 传递图片信息供 schedule_reply 内部处理
                        'product_data': product
                    }
                    if not rule_enabled:
                        logger.info(f"商品 {product['id']} 规则已禁用，准备发送自定义回复")
                    elif has_custom_images:
                        logger.info(f"商品 {product['id']} 配置了自定义图片，准备发送自定义回复")

                # 使用 schedule_reply 统一发送
                await self.schedule_reply(message, product, custom_reply)
            else:
                # 没有找到商品，不回复任何消息
                logger.info(f'关键词搜索无结果: {search_query}')

        except Exception as e:
            logger.error(f'Error handling keyword search: {e}')
            # 不发送错误消息到Discord，只记录日志

    async def search_products_by_keyword(self, keyword):
        """根据关键词搜索商品"""
        try:
            # 设置超时时间
            timeout = aiohttp.ClientTimeout(total=10)  # 10秒超时
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 构建搜索请求
                search_data = {
                    'query': keyword,
                    'limit': 10  # 搜索更多结果，但只显示前5个
                }

                # 调用后端搜索API
                async with session.post(f'{config.BACKEND_API_URL}/api/search_similar_text',
                                      json=search_data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result
                    else:
                        logger.error(f'Keyword search API error: {resp.status}')
                        return None

        except Exception as e:
            logger.error(f'Error searching products by keyword: {e}')
            return None

    async def recognize_image(self, image_data, user_shops=None):
        try:
            # 增加超时时间，FAISS搜索可能需要更长时间
            timeout = aiohttp.ClientTimeout(total=30)  # 30秒超时
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 准备图片数据
                form_data = aiohttp.FormData()
                form_data.add_field('image', image_data, filename='image.jpg', content_type='image/jpeg')
                # 使用配置的阈值
                # 使用用户个性化阈值，如果没有则使用全局默认值
                api_threshold = config.DISCORD_SIMILARITY_THRESHOLD
                if self.user_id:
                    try:
                        try:
                            from database import db
                        except ImportError:
                            from .database import db
                        # 异步获取用户设置
                        user_settings = await asyncio.get_event_loop().run_in_executor(None, db.get_user_settings, self.user_id)
                        if user_settings and 'discord_similarity_threshold' in user_settings:
                            api_threshold = user_settings['discord_similarity_threshold']
                    except Exception as e:
                        logger.error(f'获取用户相似度设置失败: {e}')

                form_data.add_field('threshold', str(api_threshold))
                form_data.add_field('limit', '1')  # Discord只返回最相似的一个结果

                # 如果指定了用户店铺权限，添加到请求中
                if user_shops:
                    form_data.add_field('user_shops', json.dumps(user_shops))

                # 调用 DINOv2 + FAISS 服务（本地）
                async with session.post(f'{config.BACKEND_API_URL.replace("/api", "")}/search_similar', data=form_data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result
                    else:
                        return None

        except asyncio.TimeoutError:
            logger.error('Error recognizing image: Request timeout (30s)')
            return None
        except aiohttp.ClientError as e:
            logger.error(f'Error recognizing image: Network error - {type(e).__name__}: {e}')
            return None
        except Exception as e:
            logger.error(f'Error recognizing image: {type(e).__name__}: {e}')
            return None

async def get_all_accounts_from_backend():
    """从后端 API 获取所有可用的 Discord 账号"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{config.BACKEND_API_URL}/accounts') as resp:
                if resp.status == 200:
                    result = await resp.json()
                    accounts = result.get('accounts', [])
                    # 只返回状态为online的账号
                    return [account for account in accounts if account.get('status') == 'online']
    except Exception as e:
        logger.error(f'Failed to get accounts from backend: {e}')
    return []

async def bot_loop(client):
    """主循环，定期检查并重连"""
    while True:
        try:
            token = await get_token_from_backend()
            if token:
                if not client.is_ready():
                    logger.info('Starting Discord bot with token from database...')
                    await client.start(token, reconnect=True)
                elif client.current_token != token:
                    logger.info('Token changed, reconnecting...')
                    await client.close()
                    await asyncio.sleep(2)
                    client.current_token = token
                    await client.start(token, reconnect=True)
            else:
                logger.warning('No active token found in database, waiting...')
                if client.is_ready():
                    await client.close()
                client.current_token = None

        except Exception as e:
            logger.error(f'Bot loop error: {e}')
            if client.is_ready():
                await client.close()

        # 等待 30 秒后再次检查
        await asyncio.sleep(30)

async def start_multi_bot_loop():
    """启动多账号机器人循环，定期检查账号状态"""
    global bot_clients, bot_tasks

    while True:
        try:
            # 获取当前所有账号
            accounts = await get_all_accounts_from_backend()
            current_account_ids = {account['id'] for account in accounts}

            # 停止已删除账号的机器人
            to_remove = []
            for i, client in enumerate(bot_clients):
                if client.account_id not in current_account_ids:
                    logger.info(f'停止已删除账号的机器人: {client.account_id}')
                    try:
                        if not client.is_closed():
                            await client.close()
                    except Exception as e:
                        logger.error(f'停止机器人时出错: {e}')

                    # 取消对应的任务
                    if i < len(bot_tasks) and bot_tasks[i] and not bot_tasks[i].done():
                        bot_tasks[i].cancel()

                    to_remove.append(i)

            # 从列表中移除已停止的机器人
            for i in reversed(to_remove):
                bot_clients.pop(i)
                if i < len(bot_tasks):
                    bot_tasks.pop(i)

            # 为新账号启动机器人
            existing_account_ids = {client.account_id for client in bot_clients}
            for account in accounts:
                account_id = account['id']
                if account_id not in existing_account_ids:
                    token = account['token']
                    username = account.get('username', f'account_{account_id}')

                    logger.info(f'启动新账号机器人: {username}')

                    # 创建机器人实例
                    client = DiscordBotClient(account_id=account_id)

                    # 启动机器人
                    try:
                        task = asyncio.create_task(client.start(token, reconnect=True))
                        bot_clients.append(client)
                        bot_tasks.append(task)
                        logger.info(f'机器人启动成功: {username}')
                    except Exception as e:
                        logger.error(f'启动机器人失败 {username}: {e}')

            # 等待一段时间后再次检查
            await asyncio.sleep(30)

        except Exception as e:
            logger.error(f'多账号机器人循环错误: {e}')
            await asyncio.sleep(30)

async def main():
    client = DiscordBotClient()

    # 启动主循环
    await bot_loop(client)

if __name__ == '__main__':
    asyncio.run(main())
