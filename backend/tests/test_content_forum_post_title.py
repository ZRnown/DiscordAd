import tempfile
import os
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auto_sender


class ResolveContentPostTitleTests(unittest.TestCase):
    def test_prefers_content_forum_post_title(self):
        content = {
            'title': '内容标题',
            'forum_post_title': '帖子标题'
        }

        self.assertEqual(
            auto_sender.resolve_content_post_title(content, default_title=''),
            '帖子标题'
        )

    def test_falls_back_to_content_title_when_forum_title_is_empty(self):
        content = {
            'title': '内容标题',
            'forum_post_title': '   '
        }

        self.assertEqual(
            auto_sender.resolve_content_post_title(content, default_title=''),
            '内容标题'
        )


class ResolveContentSendModeTests(unittest.TestCase):
    def test_prefers_content_send_mode(self):
        content = {
            'send_mode': 'post'
        }

        self.assertEqual(auto_sender.resolve_content_send_mode(content), 'post')

    def test_falls_back_to_direct_when_send_mode_is_invalid(self):
        content = {
            'send_mode': 'unknown'
        }

        self.assertEqual(auto_sender.resolve_content_send_mode(content), 'direct')


class ResolveContentForumTagsTests(unittest.TestCase):
    def test_returns_cleaned_string_tags(self):
        content = {
            'forum_tags': [' Sale ', '', '新品']
        }

        self.assertEqual(
            auto_sender.resolve_content_forum_tags(content),
            ['Sale', '新品']
        )

    def test_accepts_json_string_and_filters_blank_values(self):
        content = {
            'forum_tags': '["Tag A", "  ", "Tag B"]'
        }

        self.assertEqual(
            auto_sender.resolve_content_forum_tags(content),
            ['Tag A', 'Tag B']
        )


class ContentForumPostTitleStorageTests(unittest.TestCase):
    def test_add_and_update_content_persist_forum_post_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            import importlib
            config_module = importlib.import_module('config')
            importlib.reload(config_module)
            database_module = importlib.import_module('database')
            database_module = importlib.reload(database_module)

            try:
                content_id = database_module.add_content(
                    title='内容标题',
                    text_content='正文',
                    image_paths=[],
                    forum_post_title='帖子标题',
                    send_mode='post',
                    forum_tags=['Sale', '新品']
                )
                self.assertIsNotNone(content_id)

                content = database_module.get_content_by_id(content_id)
                self.assertEqual(content.get('forum_post_title'), '帖子标题')
                self.assertEqual(content.get('send_mode'), 'post')
                self.assertEqual(content.get('forum_tags'), ['Sale', '新品'])

                updated = database_module.update_content(
                    content_id,
                    forum_post_title='新帖子标题',
                    send_mode='direct',
                    forum_tags=['清仓']
                )
                self.assertTrue(updated)

                updated_content = database_module.get_content_by_id(content_id)
                self.assertEqual(updated_content.get('forum_post_title'), '新帖子标题')
                self.assertEqual(updated_content.get('send_mode'), 'direct')
                self.assertEqual(updated_content.get('forum_tags'), ['清仓'])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

    def test_database_instance_methods_accept_send_mode_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            import importlib
            config_module = importlib.import_module('config')
            importlib.reload(config_module)
            database_module = importlib.import_module('database')
            database_module = importlib.reload(database_module)

            try:
                db_instance = database_module.Database()
                content_id = db_instance.add_content(
                    title='内容标题',
                    text_content='正文',
                    image_paths=[],
                    forum_post_title='帖子标题',
                    send_mode='post',
                    forum_tags=['Sale']
                )
                self.assertIsNotNone(content_id)

                updated = db_instance.update_content(
                    content_id,
                    send_mode='direct',
                    forum_post_title='新帖子标题',
                    forum_tags=['新品', '活动']
                )
                self.assertTrue(updated)

                content = db_instance.get_content_by_id(content_id)
                self.assertEqual(content.get('send_mode'), 'direct')
                self.assertEqual(content.get('forum_post_title'), '新帖子标题')
                self.assertEqual(content.get('forum_tags'), ['新品', '活动'])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)
