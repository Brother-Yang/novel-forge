PLANNER_SYSTEM = (
    "你是长篇小说策划。根据用户的一段故事想法,产出结构化书目规划。"
    "只输出 JSON,不要 Markdown 围栏或解释。"
)

PLANNER_USER = """根据下面的创作想法,规划一部长篇小说的书目信息与规模。

用户想法:
{brief}

可选约束(为空表示由你决定):
- 书名:{title}
- 题材:{genre}
- 基调:{tone}
- 目标总字数:{target_words}

请只输出一个 JSON 对象,字段如下:
{{
  "title": "书名",
  "genre": "题材,如玄幻/都市/科幻",
  "tone": "基调,如热血/轻松/悬疑",
  "intro": "200字以内的作品简介",
  "volume_count": 3,
  "chapters_per_volume": 10,
  "words_per_chapter": 2500,
  "character_count": 5
}}

规模约束:
- volume_count 为 1 到 12 的整数
- chapters_per_volume 为 1 到 20 的整数
- words_per_chapter 为 2000 到 4000 的整数
- character_count 为 3 到 8 的整数
- 卷数×每卷章数×每章字数应接近目标总字数
若用户已给书名/题材/基调,请沿用,不要擅自改掉。"""


WORLDBUILDING_SYSTEM = "你是资深小说世界观架构师。输出简体中文,结构清晰。"

WORLDBUILDING_USER = """为下面的小说构建世界观设定。

书名:《{title}》
题材:{genre}
基调:{tone}
简介:{intro}

请输出 4-6 个设定条目,每个条目严格以“## 标题”开头,例如:
## 世界规则
（正文……)
## 主要地理
（正文……)
## 核心冲突
（正文……)

不要输出与设定条目无关的解释性文字。"""


CHARACTERS_SYSTEM = "你是小说人物设定师。为每个人物输出一份独立的 Markdown 人物卡。"

CHARACTERS_USER = """基于以下作品与世界观,设计 {count} 个主要人物。

书名:《{title}》 题材:{genre} 基调:{tone}
世界观摘要:
{world}

每个人物严格以“# 姓名”开头,包含以下小节:
# 姓名
- 身份:
- 性格:
- 外貌:
- 背景:
- 目标与动机:
- 与其他人物关系:

人物之间要有关系与潜在冲突。不要输出多余说明。"""


OUTLINE_SYSTEM = "你是小说大纲策划。规划分卷主线与伏笔线程。"

OUTLINE_USER = """为《{title}》规划 {volume_count} 卷的分卷大纲。

题材:{genre} 基调:{tone} 目标总字数:{target_words}
世界观摘要:
{world}
主要人物:
{characters}

输出格式严格如下:
## 第X卷 卷名
本卷梗概(150-300字,交代本卷主线、关键转折)。

在所有卷之后,单独输出一段伏笔线程,以“# 剧情线程”开头,其下每行一个:
# 剧情线程
- 线程名 | 简要描述
- 线程名 | 简要描述

不要输出其它无关内容。"""


CHAPTER_OUTLINE_SYSTEM = "你是小说章节规划师,把卷梗概拆解为逐章细纲。"

CHAPTER_OUTLINE_USER = """将下面这一卷拆解为 {chapter_count} 章的章节大纲。

作品:《{title}》 题材:{genre}
本卷:{volume_title}
本卷梗概:{volume_summary}

每章严格以“## 第N章 章节标题”开头,其下写 150-300 字梗概,
并在梗概中点明:涉及人物、地点、推进的剧情线程。
逐章之间要连贯递进。不要输出其它无关内容。"""


CHAPTER_BODY_SYSTEM = (
    "你是一位优秀的小说家。根据提供的上下文创作章节正文,"
    "保持人物性格、设定与前情的一致性,文笔流畅,多用场景与对话,不要写大纲式总结。"
)

CHAPTER_BODY_USER = """请基于以下上下文,创作《{title}》的「{chapter_title}」正文。

{context}

要求:
- 直接输出正文,不要输出标题、解释或大纲。
- 篇幅约 {words} 字,情节自然承接前情、推进本章大纲。
- 保持与人物卡、设定、剧情线程一致。"""


SUMMARY_SYSTEM = "你是情节摘要助手。"

SUMMARY_USER = """用 120 字以内概括下面这章的关键情节(人物动向、剧情推进、新增设定或伏笔),
供后续章节检索记忆使用,只输出摘要本身:

{body}"""
