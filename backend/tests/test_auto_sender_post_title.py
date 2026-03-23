import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auto_sender


class FakeTaskStateDb:
    def __init__(self, state):
        self.state = state
        self.saved_state = None

    def get_sender_task_state(self):
        return self.state

    def save_sender_task_state(self, state):
        self.saved_state = state
        return True

    def get_content_by_id(self, content_id):
        return {
            'id': content_id,
            'title': '内容标题',
            'text_content': '正文内容',
            'image_paths': [],
            'send_mode': 'post',
            'forum_post_title': '内容帖子标题'
        }

    def clear_sender_task_state(self):
        return True


class ResolvePostTitleTests(unittest.TestCase):
    def tearDown(self):
        auto_sender.reset_task_status()

    def test_uses_configured_post_title_when_present(self):
        auto_sender.task_status['post_title'] = '论坛标题'

        self.assertEqual(auto_sender.resolve_post_title('内容标题'), '论坛标题')

    def test_falls_back_to_content_title_when_configured_title_is_empty(self):
        auto_sender.task_status['post_title'] = '   '

        self.assertEqual(auto_sender.resolve_post_title('内容标题'), '内容标题')


class TaskStatePostTitleTests(unittest.TestCase):
    def tearDown(self):
        auto_sender.reset_task_status()

    def test_persist_task_state_includes_post_title(self):
        auto_sender.task_status['is_running'] = True
        auto_sender.task_status['post_title'] = '新帖子标题'
        auto_sender.task_status['content_ids'] = [11, 22]

        db = FakeTaskStateDb({})

        auto_sender._persist_task_state(db)

        self.assertEqual(db.saved_state['post_title'], '新帖子标题')

    def test_load_task_state_restores_post_title(self):
        db = FakeTaskStateDb({
            'shop_id': '[1]',
            'channel_id': 'True',
            'channel_ids': '["123"]',
            'repeat_mode': 1,
            'account_ids': '["7"]',
            'interval': 60,
            'total_products': 1,
            'sent_count': 0,
            'next_product_index': 0,
            'next_account_index': 0,
            'current_product': None,
            'current_account': None,
            'started_at': '2026-03-22T10:00:00',
            'last_sent_at': None,
            'is_running': 0,
            'is_paused': 0,
            'post_title': '保存的帖子标题'
        })

        auto_sender.load_task_state(db)

        self.assertEqual(auto_sender.task_status['post_title'], '保存的帖子标题')


class AutoSendLoopPostTitleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        auto_sender.reset_task_status()
        auto_sender.stop_sender_event.clear()

    async def test_auto_send_loop_accepts_post_title_argument(self):
        db = FakeTaskStateDb({})

        await auto_sender.auto_send_loop(
            content_ids=[1],
            selected_account_ids=[7],
            channel_ids=['123456789012345678'],
            post_title='任务级帖子标题',
            rotation_mode=True,
            repeat_mode=False,
            interval=1,
            db=db,
            bot_clients=[]
        )

        self.assertEqual(auto_sender.task_status['post_title'], '任务级帖子标题')
        self.assertEqual(auto_sender.task_status['error'], '没有选中的账号在线，请先启动账号')
