import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auto_sender


class ParseSendTargetIdTests(unittest.TestCase):
    def test_accepts_plain_channel_or_thread_id(self):
        self.assertEqual(auto_sender.parse_send_target_id('123456789012345678'), 123456789012345678)

    def test_extracts_channel_id_from_discord_thread_url(self):
        url = 'https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333'

        self.assertEqual(auto_sender.parse_send_target_id(url), 222222222222222222)

    def test_accepts_channel_mention_style(self):
        self.assertEqual(auto_sender.parse_send_target_id('<#123456789012345678>'), 123456789012345678)

    def test_rejects_invalid_target(self):
        self.assertIsNone(auto_sender.parse_send_target_id('not-a-target'))


class FakeClient:
    def __init__(self, cached_target=None, cached_thread=None, fetched_target=None):
        self.cached_target = cached_target
        self.cached_thread = cached_thread
        self.fetched_target = fetched_target
        self.get_channel_calls = []
        self.get_channel_or_thread_calls = []
        self.fetch_channel_calls = []

    def get_channel(self, target_id):
        self.get_channel_calls.append(target_id)
        return self.cached_target

    def get_channel_or_thread(self, target_id):
        self.get_channel_or_thread_calls.append(target_id)
        return self.cached_thread

    async def fetch_channel(self, target_id):
        self.fetch_channel_calls.append(target_id)
        return self.fetched_target


class FakeForumThread:
    def __init__(self):
        self.sent_payloads = []

    async def send(self, content=None, files=None):
        self.sent_payloads.append({'content': content, 'files': files})


class FakeForumChannel:
    def __init__(self, thread=None, accept_full_payload=True, available_tags=None, require_tag=False):
        self.type = SimpleNamespace(name='forum')
        self.available_tags = available_tags or []
        self.flags = SimpleNamespace(require_tag=require_tag)
        self.created_threads = []
        self.thread = thread or FakeForumThread()
        self.accept_full_payload = accept_full_payload

    async def create_thread(self, **kwargs):
        self.created_threads.append(kwargs)
        if self.accept_full_payload:
            return self.thread
        if 'content' in kwargs or 'files' in kwargs:
            raise TypeError('create_thread() got an unexpected keyword argument')
        return self.thread


class FakeForumChannelRejectsFilesNone(FakeForumChannel):
    async def create_thread(self, **kwargs):
        self.created_threads.append(kwargs)
        if 'files' in kwargs and kwargs['files'] is None:
            raise TypeError('NoneType is not iterable')
        return self.thread


class FakeTextChannel:
    def __init__(self):
        self.sent_payloads = []

    async def send(self, content=None, files=None):
        self.sent_payloads.append({'content': content, 'files': files})


class ResolveSendTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_cached_channel(self):
        cached_target = object()
        client = FakeClient(cached_target=cached_target)

        resolved = await auto_sender.resolve_send_target(client, '123456789012345678')

        self.assertIs(resolved, cached_target)
        self.assertEqual(client.get_channel_calls, [123456789012345678])
        self.assertEqual(client.get_channel_or_thread_calls, [])
        self.assertEqual(client.fetch_channel_calls, [])

    async def test_uses_cached_thread_when_channel_cache_misses(self):
        cached_thread = object()
        client = FakeClient(cached_thread=cached_thread)

        resolved = await auto_sender.resolve_send_target(client, '123456789012345678')

        self.assertIs(resolved, cached_thread)
        self.assertEqual(client.get_channel_calls, [123456789012345678])
        self.assertEqual(client.get_channel_or_thread_calls, [123456789012345678])
        self.assertEqual(client.fetch_channel_calls, [])

    async def test_falls_back_to_fetch_channel_for_uncached_thread(self):
        fetched_target = object()
        client = FakeClient(fetched_target=fetched_target)

        resolved = await auto_sender.resolve_send_target(
            client,
            'https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333'
        )

        self.assertIs(resolved, fetched_target)
        self.assertEqual(client.get_channel_calls, [222222222222222222])
        self.assertEqual(client.get_channel_or_thread_calls, [222222222222222222])
        self.assertEqual(client.fetch_channel_calls, [222222222222222222])

    async def test_returns_none_for_invalid_target(self):
        client = FakeClient()

        resolved = await auto_sender.resolve_send_target(client, 'invalid-target')

        self.assertIsNone(resolved)
        self.assertEqual(client.get_channel_calls, [])
        self.assertEqual(client.get_channel_or_thread_calls, [])
        self.assertEqual(client.fetch_channel_calls, [])


class SendContentToTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_forum_tags_when_configured(self):
        sale_tag = SimpleNamespace(id=101, name='Sale')
        news_tag = SimpleNamespace(id=202, name='新品')
        forum_channel = FakeForumChannel(available_tags=[sale_tag, news_tag])

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新品上架标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=['sale', '202']
        )

        self.assertTrue(result)
        self.assertEqual(
            forum_channel.created_threads[0]['applied_tags'],
            [sale_tag, news_tag]
        )

    async def test_rejects_missing_configured_forum_tag(self):
        forum_channel = FakeForumChannel(
            available_tags=[SimpleNamespace(id=101, name='Sale')]
        )

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新品上架标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=['missing-tag']
        )

        self.assertFalse(result)
        self.assertEqual(forum_channel.created_threads, [])

    async def test_rejects_forum_that_requires_tag_when_none_configured(self):
        forum_channel = FakeForumChannel(require_tag=True)

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新品上架标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertFalse(result)
        self.assertEqual(forum_channel.created_threads, [])

    async def test_forum_post_omits_files_argument_when_empty(self):
        forum_channel = FakeForumChannelRejectsFilesNone()

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新帖子标题',
            text_content='首条消息',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertTrue(result)
        self.assertEqual(len(forum_channel.created_threads), 1)
        self.assertNotIn('files', forum_channel.created_threads[0])
        self.assertEqual(forum_channel.created_threads[0]['content'], '首条消息')

    async def test_creates_new_forum_post_with_title_and_body(self):
        forum_channel = FakeForumChannel()

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新品上架标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertTrue(result)
        self.assertEqual(len(forum_channel.created_threads), 1)
        self.assertEqual(forum_channel.created_threads[0]['name'], '新品上架标题')
        self.assertEqual(forum_channel.created_threads[0]['content'], '正文内容')
        self.assertEqual(forum_channel.thread.sent_payloads, [])

    async def test_falls_back_to_thread_send_when_forum_create_thread_rejects_payload(self):
        forum_channel = FakeForumChannel(accept_full_payload=False)

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='post',
            post_title='新帖子标题',
            text_content='首条消息',
            files=['image-a'],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertTrue(result)
        self.assertEqual(len(forum_channel.created_threads), 2)
        self.assertEqual(forum_channel.created_threads[0]['name'], '新帖子标题')
        self.assertNotIn('content', forum_channel.created_threads[1])
        self.assertEqual(
            forum_channel.thread.sent_payloads,
            [{'content': '首条消息', 'files': ['image-a']}]
        )

    async def test_uses_direct_send_for_regular_channel(self):
        text_channel = FakeTextChannel()

        result = await auto_sender.send_content_to_target(
            text_channel,
            send_mode='direct',
            post_title='普通标题',
            text_content='普通正文',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertTrue(result)
        self.assertEqual(text_channel.sent_payloads, [{'content': '普通正文', 'files': None}])

    async def test_direct_mode_rejects_forum_channel_target(self):
        forum_channel = FakeForumChannel()

        result = await auto_sender.send_content_to_target(
            forum_channel,
            send_mode='direct',
            post_title='帖子标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertFalse(result)
        self.assertEqual(forum_channel.created_threads, [])

    async def test_post_mode_rejects_regular_channel_target(self):
        text_channel = FakeTextChannel()

        result = await auto_sender.send_content_to_target(
            text_channel,
            send_mode='post',
            post_title='帖子标题',
            text_content='正文内容',
            files=[],
            text_timeout=5,
            image_timeout=5,
            label='测试目标',
            forum_tags=[]
        )

        self.assertFalse(result)
        self.assertEqual(text_channel.sent_payloads, [])
