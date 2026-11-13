"""聊天记录导入的测试，不联网。"""

import json

import pytest

from group_agent.ingest import load_chat, parse_qq_export

SAMPLE = """消息记录（此消息记录为文本格式，不支持重新导入）

================================================================
消息分组:我的好友
================================================================
消息对象:周六下午聚一下
================================================================

2023-11-04 15:23:45 小明(10001)
周六下午大家有空吗

2023-11-04 15:24:12 小王(10002)
有空，但我三点之前不行
第二行也算同一条

2023-11-04 15:25:00 小李(10003)
我都可以
"""


def test_parses_qq_export():
    chat = parse_qq_export(SAMPLE, chat_id="e01", title="周六下午聚一下")
    assert chat.chat_id == "e01"
    assert chat.title == "周六下午聚一下"
    assert chat.date == "2023-11-04"
    assert [m.i for m in chat.messages] == [1, 2, 3]
    assert [m.who for m in chat.messages] == ["小明", "小王", "小李"]
    assert chat.messages[0].min == 15 * 60 + 23


def test_multi_line_message_is_joined():
    chat = parse_qq_export(SAMPLE)
    assert chat.messages[1].text == "有空，但我三点之前不行 第二行也算同一条"


def test_file_header_is_not_a_message():
    chat = parse_qq_export(SAMPLE)
    assert all(m.who for m in chat.messages)
    assert "消息记录" not in chat.messages[0].text


def test_keeps_the_day_with_most_messages():
    extra = SAMPLE + "\n2023-11-05 09:00:00 小张(10004)\n另一天的一句话\n"
    chat = parse_qq_export(extra)
    assert chat.date == "2023-11-04"
    assert len(chat.messages) == 3


def test_empty_input_raises():
    with pytest.raises(ValueError):
        parse_qq_export("这里没有任何消息头\n只有一行普通文字")


def test_load_chat_json(tmp_path):
    path = tmp_path / "chat.json"
    path.write_text(
        json.dumps({"chat_id": "t1", "title": "标题", "messages": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    chat = load_chat(path)
    assert chat.chat_id == "t1"
    assert chat.title == "标题"


def test_load_chat_txt_uses_file_name(tmp_path):
    path = tmp_path / "群聊记录.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    chat = load_chat(path)
    assert chat.chat_id == "群聊记录"
    assert chat.date == "2023-11-04"
    assert len(chat.messages) == 3


def test_load_chat_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_chat(tmp_path / "nope.txt")
