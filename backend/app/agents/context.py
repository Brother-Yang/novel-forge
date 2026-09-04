"""决策循环的 Context Builder(架构文档里的 Context 模块)。

与 [app/memory/context.py](../memory/context.py) 不同:那个文件负责"写正文"时的 RAG
上下文(设定/人物/剧情检索),这里负责"决策循环"每一步真正喂给模型的对话
上下文——初始 system/user 消息的组装,以及避免长篇小说场景下上下文无限
增长的滑动窗口裁剪。两者服务的消费者不同,不合并。
"""

from __future__ import annotations

from .. import models
from . import prompts

# 只把最近这么多"步"的工具调用历史发给模型,避免长篇小说场景下(几十步决策)
# 对话历史无限增长导致超出上下文窗口、或每步都要重新计费全部历史。
# 书籍的实际进度都落库了(见 state.py),模型随时可以用 get_book_state 重新
# 确认状态,因此裁掉更早的对话历史不会丢失关键信息。
CONTEXT_WINDOW_STEPS = 6


def build_initial_messages(book: models.Book) -> list[dict[str, object]]:
    """组装决策循环的首轮 system/user 消息。"""
    return [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {
            "role": "user",
            "content": prompts.AGENT_USER.format(
                title=book.title,
                brief=book.brief or book.intro,
                target_words=book.target_words,
            ),
        },
    ]


def windowed_messages(
    messages: list[dict[str, object]], step_starts: list[int]
) -> list[dict[str, object]]:
    """裁剪出发给模型的对话窗口:system+user 首条消息始终保留,
    之后只保留最近 CONTEXT_WINDOW_STEPS 步的完整工具调用往返。

    裁剪点必须落在"步"的边界上(一次 assistant 工具调用 + 对应的全部 tool
    响应视为一个不可分割的块),否则会产出缺少 tool_call 配对响应的非法消息
    序列,导致 OpenAI 兼容 API 报错。调用方(runner.py)需保证 step_starts[i]
    记录的是第 i 步开始前的 len(messages),且首两条消息恒为 system/user。
    """
    if len(step_starts) <= CONTEXT_WINDOW_STEPS:
        return messages
    cutoff = step_starts[-CONTEXT_WINDOW_STEPS]
    return messages[:2] + messages[cutoff:]
