"""Small synthetic probes; these are not a substitute for reading a full novel."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityCase:
    id: str
    title: str
    prompt: str
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()


SYSTEM_PROMPT = (
    "你是长篇小说作者。只输出要求的正文，不输出分析、总结或解释。"
    "已发生事实不可随意更改，人物只能使用当前已获得的信息。"
)

CASES = (
    QualityCase(
        id="early_chapter",
        title="早期章节的信息边界",
        prompt=(
            "写一段约500字的第2章正文。主角林舟初到雾港，只知道港口夜间禁航，"
            "与摆渡人许禾初次相识。林舟想寻找失踪姐姐，但尚未获得任何线索。"
            "本章只描写抵港受阻与两人初次交谈，保持未知与悬念。"
        ),
        required_terms=("林舟", "许禾", "雾港"),
        # 独有后续事实哨兵,不放进本章输入;命中时提示人工检查。
        forbidden_terms=("赤月钥匙", "天镜会"),
    ),
    QualityCase(
        id="distant_memory",
        title="跨章节设定和远期承诺",
        prompt=(
            "写一段约500字的第40章正文。已确认事实:第3章林舟答应许禾，"
            "无论遇到什么危险，都要先送信给码头医师；第12章林舟左手受伤，"
            "至今不能握刀；第39章许禾发现旧船票上的暗号指向北堤仓库。"
            "本章林舟准备前往仓库，必须通过动作兑现旧承诺并体现左手限制，"
            "不要大段复述摘要，不得突然治好伤势或获得未交代的帮手。"
        ),
        required_terms=("林舟", "许禾", "左手"),
    ),
    QualityCase(
        id="revision",
        title="修订后的因果一致性",
        prompt=(
            "根据新版本事实重写一段约500字的第41章正文:林舟已把信交给医师，"
            "许禾留在诊所照看伤员，没有跟随林舟。林舟独自到北堤仓库，"
            "左手仍不能握刀。旧稿中许禾在仓库帮忙开门的情节已被删除，"
            "不得延续。描写林舟如何自行进入仓库，并在结尾留下一个具体悬念。"
        ),
        required_terms=("林舟", "仓库"),
    ),
    QualityCase(
        id="late_arc",
        title="长篇后期的线程收束",
        prompt=(
            "写一段约500字的第180章正文。已确认事实:姐姐已在第176章平安归来，"
            "寻找姐姐的线程已经结束；天镜会掌控禁航名单的阴谋尚未揭开。"
            "许禾是可信的伙伴，林舟左手伤势已经在第90章治好。"
            "本章应从已收束的亲情线自然转向禁航名单线，体现人物经历后的变化，"
            "不要再次把姐姐写成失踪，不要重复使用初到雾港的陌生反应。"
        ),
        required_terms=("林舟", "天镜会"),
    ),
)

REVIEW_AXES = (
    "人物行为和已知信息是否一致",
    "旧承诺、伤势与修订事实是否持续有效",
    "已闭合线程是否被无故重新打开",
    "冲突与场景是否推进而非复述上下文",
    "语言是否自然且避免重复段落和套话",
)
