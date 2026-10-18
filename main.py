"""Dual-mode chat: daily persona chat vs immersive roleplay.

Provides per-session switching between a "normal" daily-chat mode and a
"roleplay" (RP) mode whose expression styles conflict with each other
(the persona usually forbids parenthesized actions in daily chat while RP
relies on them). The plugin keeps the two styles from contaminating each
other across three channels:

1. System prompt: appends a mode-specific rule block that explicitly
   overrides (RP mode) or reinforces (normal mode) the persona's style
   constraints.
2. Conversation history: binds each mode to its own conversation so RP
   turns never leak into the daily context window, and vice versa.
3. Long-term memory recall: in normal mode, strips action parentheses
   from the memory blocks injected by memory plugins (LivingMemory,
   MemoryCompanion) before the LLM sees them, so the RP expression style
   stored in global memories does not bleed into daily chat.
4. Function tools: when rp_filter_tools is on, RP-mode requests keep only
   the tools listed in rp_keep_tools and drop every other function tool
   declaration (cron, MCP, plugin tools), because attached tool schemas
   flip Gemini-style models into assistant mode and sharply raise refusal
   rates on roleplay turns.

When TTS is enabled, RP mode also takes over speech synthesis for the
session: trigger probability, optional audio+text dual output, and
stripping action parentheses from the spoken text are all configurable
(see the rp_tts_* options and the RP TTS hook below). Daily chat keeps
using AstrBot's built-in TTS pipeline untouched.

Scene illustrations linked to astrbot_plugin_nai_image come in two
selectable flows (rp_image_flow, or /rp image per session):

* two_step (default): the text reply is sent immediately, then a detached
  background task asks the LLM for English tags (a second LLM round trip)
  and generates the image through the NAI companion API.
* one_step: the tagging rules are injected into the system prompt and the
  model calls ILLUSTRATION_TOOL ("RP_Generate_Illustration") itself right
  after finishing its reply, so the tags are produced together with the
  reply and the image request starts without a separate tagging pass (the
  agent loop still makes one short follow-up call to close the turn). The
  per-turn trigger probability and cooldown are still rolled by the plugin,
  so the model never decides on its own whether to illustrate.
"""

import asyncio
import importlib
import importlib.util
import random
import re
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import (
    AstrMessageEvent,
    MessageChain,
    MessageEventResult,
    ResultContentType,
    filter,
)
from astrbot.api.message_components import Image, Plain, Record
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core import file_token_service
from astrbot.core.provider.provider import TTSProvider
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.star.session_llm_manager import SessionServiceManager

MODE_NORMAL = "normal"
MODE_RP = "rp"

# Image prompt spec for RP illustrations, embedded so the plugin is
# self-contained on a fresh clone. A sibling "image_prompt_spec.txt" next to
# the plugins directory (same convention as astrbot_plugin_nai_image)
# overrides it when present, so both plugins can share one editable file.
_EMBEDDED_IMAGE_PROMPT_SPEC = r"""你是角色扮演（RP）剧情配图的 NAI 提示词写手。请根据给定的剧情片段、场景设定与上一张配图的提示词，输出一条可直接提交 NovelAI V4.5 / V5 的单图英文 tag 提示词。

# 一、输出契约
- 只输出一行提示词：英文 tag，逗号分隔。禁止中文、编号、解释、前后缀、markdown 代码块。
- 只画单张图：不写分镜、漫画版式、气泡台词与画面文字（comic, multiple panels, manga page, text, speech bubble 一律不写）。
- 标签用下划线或空格连词都可以；NAI 里圆括号是普通字符，不要写 \( \)。
- 人数标签永远放最前，质量词永远放最后。

# 二、主提示词顺序
人数 → 角色名（作品名）→ 外貌 → 服装 → 表情 → 动作姿势 → 场景与道具 → 机位景别 → 光照 → 色彩氛围 → 质量尾。
- 最难画的那一样（少见姿势、两人接触、方向关系）可以提到人数之后并单独加权。
- 质量尾示例：masterpiece, best quality, very aesthetic, detailed background。

# 三、角色
- 同人 / 已有作品角色：只写官方英文名（作品名）标签，如 texas the omertosa (arknights)。写了名字就不要再罗列发色瞳色，会与名字自带描述打架；常驻特征用 {{{tag}}} 单独加权强调。
- 原创角色（人物卡角色）：名字写成 original，然后把外貌写全：发型、发色、瞳色、体型、胸部大小、肤色、种族、年龄感。
- 默认不加换装 / 皮肤标签（如 kisaki (swimsuit)），改用本名 + 具体服装：款式、颜色、材质、装饰、状态（湿了、半脱、被风吹起）。
- 泳装、比基尼会把胸画大：按原设写明胸部大小（如 small breasts / flat chest），负面压 large breasts, huge breasts, cleavage。
- 用户（第一人称）进入画面：用 pov, first-person view，只画对方身体局部或视角，禁止画主角的脸与头部，禁止出现 {user}、主角名字（中文、英文、拼音都不行）。
- 角色示例：1girl, original, silver twin tails, red eyes, small breasts, white shirt, black pleated skirt, partially unbuttoned, blush, looking up, sitting on bed, knees up, from side, soft window light, warm tones, masterpiece, best quality。

# 四、标签和句子的分工
- 姿势、体位、机位、景别、视线一律用标签：sitting, on all fours, kneeling, legs up, from behind, from above, full body, looking at viewer。
- 人和人、人和物之间的关系用一句英文短句写清：谁的手碰到谁的哪里、东西插在哪里、液体从哪里流到哪里。关系只写标签常常画错，写成多个从句又会各画一遍。
- 衣服的不对称状态（只穿一只袜子、单肩滑落、半脱）用短句补。
- 关键物件单独拎出来写：transparent umbrella, wooden bench。

# 五、互斥组（每组只选一个）
- 视线：looking at viewer / looking to the side / looking up / looking down / looking away / looking back。
- 景别：close-up / portrait / upper body / cowboy shot / full body / wide shot。
- 背景：simple background / white background / detailed background / blurry background / transparent background。
- 水平机位：from side / from behind / straight on；垂直机位：from below / from above（可与水平组合）。
- 画面只有一个人时不要写 2girls；人物被裁出画面时不要写 full body。

# 六、权重语法
- 加强：1.2::tag::（常用 1.1 ~ 1.5，1.3 ~ 1.8 最值得试）；减弱：0.8::tag::；定向去掉：-1::tag::。
- 一组一起加权：1.5::long hair, red eyes::。必须闭合 ::，没写结尾的权重会一直作用到提示词末尾，整图跑偏。
- {tag} ×1.05、[tag] ÷1.05 仍可用；圆括号不是权重，(tag:1.2) 这种 ComfyUI 写法会被当成普通字符。
- 画师名以数字结尾时必须写成 0.7::artist:as109, ::，逗号不能少（数字紧贴 :: 会被读成权重）。
- 默认不加权，某个元素确实出不来再加。剧情里被强调的细节可加权（强调 1.2、非常/最 1.5）；"不要"用 -1::tag:: 表达，而不是减权重。

# 七、九层细节（禁止空泛）
每一层都要写具体，不许用一个常见词带过（括号内是反例）：
- 画风：有固定画风要求就写在最前；没有就省略。
- 角色：本名标签 + 场景会带偏的身材项（反例：只写 solo）。
- 服装：款式、颜色、材质、装饰、状态（反例：swimsuit）。
- 表情：2~4 个，神态 + 嘴 + 脸红或眼神（反例：smile）。
- 动作：姿势标签 + 一句写清动作与它造成的效果（反例：standing）。
- 场景：地点 + 2~3 个具体道具/地标 + 远景 + 时间天气（反例：beach）。
- 光照：具名布光，光从哪来、什么颜色、打出什么效果（轮廓光、焦散、反光）（反例：sunlight）。
- 镜头：景别 + 机位 + 景深/前景虚化/透视（反例：只有 full body）。
- 色彩氛围：主色调 + 粒子特效（水花、光斑、花瓣、雨丝）。
场景、光照、机位不要选最显然的那个；收尾可以加一句把"最出彩的一刻"写具体。若某一层与剧情冲突（如角色背对镜头），删掉该层看不见的 tag，不要硬写。

# 八、露骨与分级
- 露骨概念一律"标签 + 一句具体英文描述"：写清形状、位置、质感；只写标签常常画不准。
- 露骨画面必须写成年：正文加 adult / mature female / mature male，体型写成年体型；负面加 loli, child, aged down, petite, flat chest。
- 露骨提示词里不写 girl（1girl、fox girl 都不写），改用 woman / adult female。
- 需要排除容易混淆的近似概念时放进负面，或直接 -1::tag::。

# 九、排除元素
- 默认不手写负面；要排除时只写要排除的词 + 通用防崩词：bad anatomy, bad hands, extra digits, fewer digits, watermark, signature, logo。
- 画面里不该出现的东西用 no+Tag 强调（no bra、no panties）；定向去掉某个词用 -1::tag::。

# Tag_注意事项
1. 拆解复合词：月下 → moonlight, night；半脱 → clothes lift, partially removed。
2. 构图或视线变化时删 tag：正面转背面删 eye color 加 from behind；蒙眼删 eye color 加 blindfold；只剩下半身时删 shirt, expression。
3. 对话转动作："你看，我今天穿内裤了。" → lifting skirt, panties（转成可见动作，不写台词）。
4. 画面只描述客观存在的人 / 物 / 背景与正在发生的物理动作；内心活动、回忆、幻想、预告、比喻、抽象描述一律不写。
5. 不写文字、气泡、UI、水印、签名、页码，不写 Text: 字段。
6. 横构图且角色有兽耳 / 发饰时，写清头顶上方是什么（如 above her ears is a strip of the bedroom wall and the headboard），避免耳朵被裁；负面可加 head out of frame, cropped head。

# Tag 智能调整
- 个数按画面视觉占比与焦点分配：主体多给，背景少给。
- 排序按视觉占比从高到低；同一分类的相关 tag 相邻排列，避免分散。
- 人物 tag 明显多于环境 tag 时，先删环境里的次要装饰。

# 一致性规范（最重要）
1. 场景与状态连续：剧情没有明确换地点、换时间、换装时，必须重复上一张图的地点、时段、天气、光线、背景结构与主要道具 tag，只改动作、表情、视线与镜头。
2. 人物连续：同一角色沿用上一张图的发色、瞳色、发型、体型、服装 tag（相同写法、顺序与权重）。
3. 锚点用法：给定"上一张配图"时逐字沿用其中的人物与环境 tag；只替换本轮明确发生的动作、表情、视线、镜头与服装变化；只有剧情明确变化时才替换环境 tag。
4. 同人角色把官方名或常用角色 tag 放最前，常驻特征加权 {{{tag}}}，避免外形崩坏与不一致。

# 十、分镜页与画面文字（仅在本轮明确需要时）
- 默认单图；只有本轮明确要「多格漫画 / 分镜 / 带台词」时才启用，并一次说清几格。
- 分镜开头：comic, multiple panels, manga page layout, clean panel borders，随后一句 This is a 5-panel comic page showing the same scene in order；五格最稳，超过六格容易糊。
- 每格用位置词开头：First panel (top, wide): …、Second panel (middle-left): …；相邻格换景别或机位，不要重复同一构图。
- 生图设置的负面预设若带 multiple views（V5 Heavy / Light 都有）会把分镜压成单图，此时去掉分镜 tag 改单图。
- 画面文字：正面加 text, english text，并在提示词最末尾写 Text: 要显示的字，其后不再写任何 tag；台词越短越准，最多一两句。
- 某格有台词时写在该格描述末尾：A speech bubble says "…"；同一句不要既用 Text: 又用气泡重复。
- 质量词里带 no text 会压掉文字；要文字时写 masterpiece, best quality, very aesthetic，不要带 no text。
"""
_IMAGE_PROMPT_SPEC_PATH = Path(__file__).resolve().parents[1] / "image_prompt_spec.txt"
try:
    IMAGE_PROMPT_SPEC = _IMAGE_PROMPT_SPEC_PATH.read_text(encoding="utf-8-sig").strip()
except OSError:
    IMAGE_PROMPT_SPEC = _EMBEDDED_IMAGE_PROMPT_SPEC.strip()

MODE_LABELS = {MODE_NORMAL: "日常聊天", MODE_RP: "实景角色扮演"}

# RP illustration flows.
# two_step: reply first, then a background task writes the tags with a second
#   LLM call and generates the image (the original behaviour: zero reply
#   latency, one extra model round trip just for the tags).
# one_step: the tag rules and the continuity anchor go into the system prompt,
#   and the model calls ILLUSTRATION_TOOL itself right after its reply, so the
#   tags are generated together with the reply and the image request starts
#   without a separate tagging pass.
FLOW_TWO_STEP = "two_step"
FLOW_ONE_STEP = "one_step"
FLOW_LABELS = {FLOW_TWO_STEP: "两步出图", FLOW_ONE_STEP: "一步出图"}
# Accepts config values, command arguments and Chinese labels alike.
FLOW_ALIASES = {
    "two_step": FLOW_TWO_STEP,
    "twostep": FLOW_TWO_STEP,
    "two": FLOW_TWO_STEP,
    "2": FLOW_TWO_STEP,
    "两步": FLOW_TWO_STEP,
    "两步出图": FLOW_TWO_STEP,
    "one_step": FLOW_ONE_STEP,
    "onestep": FLOW_ONE_STEP,
    "one": FLOW_ONE_STEP,
    "1": FLOW_ONE_STEP,
    "一步": FLOW_ONE_STEP,
    "一步出图": FLOW_ONE_STEP,
}

# Function tool used by the one-step flow. It is force-whitelisted in RP mode
# while that flow is active so rp_filter_tools cannot strip it, and it is
# registered unconditionally (it reports itself as unused in the two-step flow).
ILLUSTRATION_TOOL = "RP_Generate_Illustration"

# Injected into the RP system prompt only on turns the plugin has already
# approved for an illustration. The continuity anchor is appended after it.
DEFAULT_ONESTEP_PROMPT = """# 本轮场景配图任务（一步出图）
本轮已经抽中配图，请严格按顺序执行，不要先出图：
1. 先把本轮正文回复写完（维持当前模式的人设与括号动作风格），不要为了配图压缩或省略回复。
2. 正文写完后，调用工具 RP_Generate_Illustration，只调用一次。prompt 参数填英文 NAI 绘画 Tag，逗号分隔，禁止中文、禁止整句描述。
3. 调用成功后不要再解释、不要复述 Tag、不要说「图片已发送」，本轮直接结束。

Tag 精简规范（完整版见「Tag 生图规范」，冲突时以完整版为准）：
- 顺序：人数 → 角色名（作品名）→ 外貌 → 服装 → 表情 → 动作 → 场景 → 机位景别 → 光照 → 质量尾（masterpiece, best quality, very aesthetic, detailed background）。
- 同人角色只写官方英文名（作品名），不要再罗列发色瞳色；原创角色写 original 并把外貌写全。
- 姿势、机位、景别、视线用 Tag；人和人 / 人和物的关系用一句英文短句写清（谁的手碰到谁的哪里）。
- 互斥组每组只选一个：视线 / 景别 / 背景 / 水平机位 / 垂直机位。
- 权重写法：1.2::tag::、-1::tag::，必须闭合 ::；默认不加权。
- 场景写具体：地点 + 2~3 个道具 + 时间天气 + 具名布光（光从哪来、什么颜色）；不要只写 beach、sunlight 这类空泛词。
- 当前构图看不到的部位要删掉对应 Tag（背影删 eye color、加 from behind；蒙眼删 eye color、加 blindfold）。
- 只画客观可见的人、物、背景与正在发生的物理动作；禁止内心活动、回忆、幻想、比喻、剧情预告；默认单图，不写分镜、气泡与画面文字。
- 用户（第一人称）出现在画面里时用 pov, first-person view，不画主角的脸和头部，禁止出现 {user} 或主角名字。
- 露骨画面必须写成年：adult / mature female，不写 girl；负面压 loli, child, aged down。
- 需要排除的元素写成 no+Tag（如 no bra、no panties）；不要靠堆叠镜头照不到的部位凑长度。
- 连续性：沿用下方「上一张配图」中的人物、服装、环境 Tag（相同写法、顺序与权重），只按本轮正文替换动作、表情、视线与镜头类 Tag；只有剧情明确换地点、换装或推进时间时才改环境 Tag。
- 只有用户本轮明确要多格漫画 / 分镜 / 带台词时才改用分镜：comic, multiple panels, manga page layout + 一句 This is a N-panel comic page…，每格前写 First panel (top, wide): 这类位置词，五格最稳；带文字时在最末尾写 Text: …，其后不再写任何 Tag。生图负面预设带 multiple views 时不要用分镜。

如果本轮确实没有可画的画面（例如纯寒暄、纯文字说明），就只回一句「（这次先不画了）」，不要调用工具，也不要编造生图失败的理由。"""

# Default function tools that survive RP-mode filtering (see the
# rp_filter_tools / rp_keep_tools config options). LivingMemory's recall
# tool is whitelisted so the character can still look up long-term
# memories; every other declaration (cron, MCP, plugin tools) is stripped,
# because attached tool schemas flip Gemini-style models into assistant
# mode and make them refuse roleplay content far more often.
DEFAULT_RP_KEEP_TOOLS = ["recall_long_term_memory"]

# Memory plugins wrap recalled memories in these markers regardless of the
# injection channel (extra user content parts / prompt / fake tool call):
# LivingMemory -> <RAG-Faiss-Memory>, MemoryCompanion -> <MemoryCompanion-Context>.
MEMORY_MARKERS = ("<RAG-Faiss-Memory>", "<MemoryCompanion-Context>")
MEMORY_BLOCK_RE = re.compile(
    r"<(RAG-Faiss-Memory|MemoryCompanion-Context)>.*?</\1>", re.DOTALL
)
# Action parentheses: full/half-width pairs, non-nested, single-line, and
# short enough to be an action beat rather than a meaningful aside.
PAREN_ACTION_RE = re.compile(r"[（(]([^()（）\n]{1,40})[)）]")
# Parentheses to keep: plugin metadata like LivingMemory's "(Importance:
# 0.85)" and MemoryCompanion's "（证据：...）", plus pure numbers/dates and
# day-of-week annotations, are never action descriptions.
KEEP_PAREN_RE = re.compile(
    r"Importance:|^[\d\s.:/%,~-]+$|证据[:：]|周[一二三四五六日天末]|星期[一二三四五六日天]"
)
# Parenthesized text inside an RP reply for the spoken version: any
# non-nested full/half-width paren group, regardless of length or line
# breaks — RP action beats can be long. Whatever survives is what TTS reads.
RP_TTS_PAREN_RE = re.compile(r"[（(][^（）()]*[)）]", re.DOTALL)

# Rule-book retrieval for RP illustrations. ``prompt_kb/`` ships the Markdown
# rule book (rules.md / recipes.md from the nai-prompting skill) and
# ``chat_mode_prompt_kb.py`` implements model-free section retrieval, so a
# turn only pays for the sections its scene actually needs. The module is
# loaded by path because AstrBot may import plugin main.py as a package
# submodule or as a script.
PROMPT_KB_DIR = Path(__file__).resolve().parent / "prompt_kb"
_KB_MODULE = None


def load_prompt_kb_module():
    """Return the retrieval module shipped next to this plugin (cached)."""
    global _KB_MODULE
    if _KB_MODULE is None:
        module_path = Path(__file__).resolve().parent / "chat_mode_prompt_kb.py"
        spec = importlib.util.spec_from_file_location(
            "chat_mode_prompt_kb", module_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _KB_MODULE = module
    return _KB_MODULE


class ChatModePlugin(Star):
    """Switch between daily chat and immersive roleplay per session."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        # Per-session mode state cache, write-through to the plugin KV store.
        self._states: dict[str, dict] = {}
        # Per-session timestamp of the last RP illustration, for cooldown.
        self._image_last_ts: dict[str, float] = {}
        # Strong references to pending background illustration tasks to prevent GC.
        self._bg_tasks: set[asyncio.Task] = set()

    # ==================== state persistence ====================

    async def _get_state(self, umo: str) -> dict:
        """Load the per-session mode state (cached, KV-backed).

        Args:
            umo: Unified message origin, e.g. "aiocqhttp:FriendMessage:12345".

        Returns:
            A dict with keys "mode" (MODE_NORMAL | MODE_RP), "scene" (str),
            "tts_probability" (float | None, per-session RP TTS override) and
            "image_flow" (str | None, per-session illustration flow override).
        """
        state = self._states.get(umo)
        if state is None:
            raw = await self.get_kv_data(f"chat_mode:{umo}", None)
            state = raw if isinstance(raw, dict) else {}
            prob = state.get("tts_probability")
            state = {
                "mode": state.get("mode", MODE_NORMAL),
                "scene": str(state.get("scene") or ""),
                "tts_probability": (
                    float(prob) if isinstance(prob, (int, float)) else None
                ),
                "image_flow": str(state.get("image_flow") or "").strip() or None,
            }
            self._states[umo] = state
        return state

    async def _save_state(self, umo: str, state: dict) -> None:
        self._states[umo] = state
        await self.put_kv_data(f"chat_mode:{umo}", state)

    async def _get_conv_id(self, umo: str, mode: str) -> str | None:
        return await self.get_kv_data(f"conv_id:{mode}:{umo}", None)

    async def _set_conv_id(self, umo: str, mode: str, cid: str) -> None:
        await self.put_kv_data(f"conv_id:{mode}:{umo}", cid)

    # ==================== RP image continuity anchor ====================

    async def _get_image_anchor(self, umo: str) -> str:
        """Return the previous illustration's full tag prompt ("" when none)."""
        return str(await self.get_kv_data(f"rp_image_anchor:{umo}", "") or "")

    async def _set_image_anchor(self, umo: str, prompt: str) -> None:
        """Store the tag prompt, or clear the anchor with an empty string."""
        await self.put_kv_data(f"rp_image_anchor:{umo}", prompt)

    # ==================== illustration flow selection ====================

    @staticmethod
    def _normalize_flow(value: object) -> str:
        """Map a config value / command argument / label to a flow constant."""
        raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "")
        return FLOW_ALIASES.get(raw, FLOW_TWO_STEP)

    def _image_flow(self, state: dict) -> str:
        """Effective illustration flow: session override, else the config value."""
        override = state.get("image_flow")
        if override:
            return self._normalize_flow(override)
        return self._normalize_flow(self.config.get("rp_image_flow", FLOW_TWO_STEP))

    def _roll_illustration(self, umo: str) -> bool:
        """Roll probability + cooldown for one RP turn (one-step flow only).

        Returns:
            True when this turn may illustrate. A hit immediately consumes the
            cooldown timestamp, so a model that declines to call the tool still
            cannot be asked again within the cooldown window.
        """
        try:
            probability = float(self.config.get("rp_image_probability", 0.2) or 0)
        except (TypeError, ValueError):
            probability = 0.0
        if probability <= 0:
            return False
        cooldown = max(0, int(self.config.get("rp_image_cooldown", 300) or 0))
        now = time.time()
        if now - self._image_last_ts.get(umo, 0.0) < cooldown:
            return False
        if random.random() >= probability:
            return False
        self._image_last_ts[umo] = now
        return True

    def _image_roll(self, event: AstrMessageEvent, umo: str) -> bool:
        """Decide once per turn whether the one-step flow may illustrate.

        The decision is cached on the event: a tool call triggers another
        ``on_llm_request`` pass for the same turn, and re-rolling there would
        inject a second (possibly contradictory) illustration instruction.
        """
        decision = event.get_extra("chat_mode_illustration_roll")
        if decision is None:
            decision = "approved" if self._roll_illustration(umo) else "skip"
            event.set_extra("chat_mode_illustration_roll", decision)
        if decision != "approved":
            return False
        return not event.get_extra("chat_mode_illustration_used")

    def _rule_block(self, query: str) -> str:
        """Retrieve the rule-book sections matching this turn.

        The bundled ``prompt_kb/`` (rules.md / recipes.md from the
        nai-prompting skill) is far larger than any system prompt should
        carry, so only the sections whose vocabulary overlaps the scene,
        anchor tags and reply text are injected, under a hard character
        budget. Retrieval is model-free: it adds no LLM round trip and needs
        no file-system tool inside the tagging call.

        Args:
            query: Free text describing this turn (scene, anchor, reply).

        Returns:
            The rendered rule block, or "" when disabled / nothing matched.
        """
        if not bool(self.config.get("rp_image_rule_book", True)):
            return ""
        query = " ".join(str(query or "").split())[:1200]
        if not query:
            return ""
        budget = max(300, int(self.config.get("rp_image_rule_budget", 1200) or 1200))
        limit = max(1, int(self.config.get("rp_image_rule_max_sections", 2) or 2))
        configured = str(self.config.get("rp_image_rule_path", "")).strip()
        directory = Path(configured) if configured else PROMPT_KB_DIR
        try:
            kb = load_prompt_kb_module()
            sections = kb.load_dir(directory)
            picked = kb.select(sections, query, budget=budget, max_sections=limit)
            if not picked:
                return ""
            titles = "、".join(section.title for section in picked)
            logger.info(f"image rule book hit | {titles}")
            return f"# 相关规则（本轮命中：{titles}）\n{kb.render(picked, budget)}"
        except Exception:
            logger.error("failed to load the image rule book", exc_info=True)
            return ""

    async def _build_onestep_instruction(
        self, umo: str, state: dict, query: str = ""
    ) -> str:
        """Build the one-step system prompt block for an approved turn.

        The block carries the tagging rules, the previous illustration's full
        tag prompt as the continuity anchor (or the static scene on the very
        first image), the optional extra requirements, the optional full
        ``rp_image_prompt_spec``, and the rule-book sections this turn needs.
        """
        template = str(self.config.get("rp_image_onestep_prompt", "")).strip()
        parts = [template or DEFAULT_ONESTEP_PROMPT]

        anchor = await self._get_image_anchor(umo)
        if anchor:
            parts.append(
                "# 上一张配图的完整提示词（延续基准，逐字沿用人物与场景 Tag）\n"
                f"{anchor}"
            )
        else:
            scene = str(state.get("scene") or "").strip()
            if scene:
                parts.append(f"# 本次 RP 的场景设定（首张图的引导）\n{scene}")

        if self.config.get("rp_image_onestep_include_spec", False):
            spec = str(self.config.get("rp_image_prompt_spec", "")).strip()
            parts.append(f"# 完整 Tag 生图规范\n{spec or IMAGE_PROMPT_SPEC}")

        rule_query = " ".join(
            item
            for item in (
                query,
                str(state.get("scene") or ""),
                anchor,
                str(self.config.get("rp_image_prompt_extra", "")),
            )
            if item
        )
        rule_block = self._rule_block(rule_query)
        if rule_block:
            parts.append(rule_block)

        extra = str(self.config.get("rp_image_prompt_extra", "")).strip()
        if extra:
            parts.append(f"# 附加要求（每条提示词都要带上）\n{extra}")
        return "\n\n".join(parts)

    # ==================== conversation isolation ====================

    async def _switch_conversation(self, umo: str, target_mode: str) -> None:
        """Switch the session to the conversation bound to ``target_mode``.

        Remembers the conversation being left (so switching back restores
        it) and creates the target conversation on first use, inheriting
        the persona of the current conversation.

        Args:
            umo: Unified message origin of the session.
            target_mode: MODE_RP or MODE_NORMAL to switch into.
        """
        cm = self.context.conversation_manager
        curr_cid = await cm.get_curr_conversation_id(umo)
        leaving_mode = MODE_NORMAL if target_mode == MODE_RP else MODE_RP

        if curr_cid and not await self._get_conv_id(umo, leaving_mode):
            await self._set_conv_id(umo, leaving_mode, curr_cid)

        target_cid = await self._get_conv_id(umo, target_mode)
        if target_cid == curr_cid:
            return
        if not target_cid:
            persona_id = None
            if curr_cid:
                conv = await cm.get_conversation(umo, curr_cid)
                persona_id = (conv.persona_id if conv else None) or None
            title = "实景角色扮演" if target_mode == MODE_RP else "日常聊天"
            target_cid = await cm.new_conversation(
                umo, persona_id=persona_id, title=title
            )
            await self._set_conv_id(umo, target_mode, target_cid)
        else:
            await cm.switch_conversation(umo, target_cid)

    # ==================== memory recall sanitizing ====================

    @staticmethod
    def _has_memory_marker(text: str) -> bool:
        """Return whether the text contains a memory injection block."""
        return any(marker in text for marker in MEMORY_MARKERS)

    @classmethod
    def _strip_memory_block(cls, text: str) -> tuple[str, int]:
        """Remove action parentheses inside memory injection blocks only.

        Args:
            text: Text that may contain memory injection blocks wrapped in
                the markers of LivingMemory or MemoryCompanion.

        Returns:
            A tuple of the sanitized text and the number of removals.
        """
        count = 0

        def _drop_action(m: re.Match) -> str:
            nonlocal count
            if KEEP_PAREN_RE.match(m.group(1)):
                return m.group(0)
            count += 1
            return ""

        def _clean_block(match: re.Match) -> str:
            before = count
            block = PAREN_ACTION_RE.sub(_drop_action, match.group(0))
            if count > before:
                block = re.sub(r"[ \t]{2,}", " ", block)
            return block

        return MEMORY_BLOCK_RE.sub(_clean_block, text), count

    def _sanitize_memory_injection(self, req: ProviderRequest, umo: str) -> None:
        """Strip RP-style action parentheses from recalled memories.

        Covers every injection channel used by the memory plugins: extra
        user content parts (default), the user prompt (before/after methods)
        and fake tool-call context messages.
        """
        total = 0
        for part in req.extra_user_content_parts or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and self._has_memory_marker(text):
                text, n = self._strip_memory_block(text)
                part.text = text
                total += n
        if isinstance(req.prompt, str) and self._has_memory_marker(req.prompt):
            req.prompt, n = self._strip_memory_block(req.prompt)
            total += n
        for msg in req.contexts or []:
            if not (isinstance(msg, dict) and msg.get("role") == "tool"):
                continue
            content = msg.get("content")
            if isinstance(content, str) and self._has_memory_marker(content):
                msg["content"], n = self._strip_memory_block(content)
                total += n
        if total:
            logger.info(
                f"[{umo}] stripped {total} parenthesized action(s) from "
                "recalled memories (normal chat mode)"
            )

    # ==================== LLM request hook ====================

    @filter.on_llm_request(priority=-100)
    async def decorate_chat_mode(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Inject mode rules into the system prompt and sanitize memories.

        The priority is deliberately low so this hook runs after the memory
        plugins' recall hooks (LivingMemory: 0, MemoryCompanion: -20), which
        lets us strip RP-style parentheses from the memory text they just
        injected. This hook fires after the persona has been assembled into
        ``req.system_prompt``, so appended mode rules land after it.
        """
        try:
            umo = event.unified_msg_origin
            state = await self._get_state(umo)
            mode = state.get("mode", MODE_NORMAL)
            event.set_extra("chat_mode", mode)

            if mode == MODE_RP:
                flow = self._image_flow(state)
                image_enabled = bool(self.config.get("rp_image_enabled", True))
                # One-step flow: decide here (probability + cooldown) and let
                # the model call the tool right after its reply. The decision
                # is event-cached so a following tool-result request does not
                # re-roll or re-inject the block.
                approved = (
                    image_enabled
                    and flow == FLOW_ONE_STEP
                    and self._image_roll(event, umo)
                )
                event.set_extra("chat_mode_illustration_approved", approved)
                # Keep only the whitelisted tools (rp_filter_tools off
                # disables the filtering entirely; rp_keep_tools edits it).
                if self.config.get("rp_filter_tools", True) and req.func_tool:
                    keep = {
                        name.strip()
                        for name in self.config.get(
                            "rp_keep_tools", DEFAULT_RP_KEEP_TOOLS
                        )
                        if isinstance(name, str) and name.strip()
                    }
                    if image_enabled and flow == FLOW_ONE_STEP:
                        keep.add(ILLUSTRATION_TOOL)
                    for tool in list(req.func_tool.tools):
                        if tool.name not in keep:
                            req.func_tool.remove_tool(tool.name)
                    if req.func_tool.empty():
                        req.func_tool = None
                block = str(self.config.get("rp_prompt", "")).strip()
                scene = str(state.get("scene", "")).strip()
                if scene:
                    scene_block = f"# Current Scene / 当前场景设定\n{scene}"
                    block = f"{block}\n\n{scene_block}" if block else scene_block
                if approved:
                    try:
                        turn_text = (
                            req.prompt if isinstance(req.prompt, str) else ""
                        )
                        instruction = await self._build_onestep_instruction(
                            umo, state, query=turn_text
                        )
                        block = (
                            f"{block}\n\n{instruction}" if block else instruction
                        )
                    except Exception:
                        logger.error(
                            "failed to build the one-step illustration prompt",
                            exc_info=True,
                        )
                if block:
                    orig_sp = (req.system_prompt or "").strip()
                    req.system_prompt = f"{orig_sp}\n\n{block}" if orig_sp else block
                return

            guard = str(self.config.get("normal_guard_prompt", "")).strip()
            if self.config.get("inject_normal_guard", True) and guard:
                orig_sp = (req.system_prompt or "").strip()
                req.system_prompt = f"{orig_sp}\n\n{guard}" if orig_sp else guard
            if self.config.get("filter_memory_style", True):
                self._sanitize_memory_injection(req, umo)
        except Exception:
            logger.error("chat-mode decoration failed", exc_info=True)

    # ==================== RP mode TTS takeover ====================

    @staticmethod
    def _strip_rp_parentheses(text: str) -> str:
        """Remove all non-nested parenthesized segments from reply text."""
        return RP_TTS_PAREN_RE.sub("", text)

    @staticmethod
    def _tidy_spoken(text: str) -> str:
        """Collapse whitespace left behind by removed parentheses."""
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        return text.strip()

    @staticmethod
    def _clean_tts_text(text: str) -> str:
        """Normalize text that should not be sent to TTS synthesis.

        Markdown emphasis markers would be read aloud by some engines, so
        they are removed before synthesis (the visible text keeps them).
        """
        return text.replace("**", "").replace("__", "")

    def _tts_probability(self, state: dict) -> float:
        """Session override from /rp tts, else the configured RP probability."""
        prob = state.get("tts_probability")
        if prob is None:
            try:
                prob = float(self.config.get("rp_tts_probability", 1.0))
            except (TypeError, ValueError):
                prob = 1.0
        return min(max(prob, 0.0), 1.0)

    async def _resolve_tts_provider(self, umo: str) -> TTSProvider | None:
        """Configured rp_tts_provider_id first, else the session's default TTS."""
        provider_id = str(self.config.get("rp_tts_provider_id", "")).strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if isinstance(provider, TTSProvider):
                return provider
            logger.warning(
                f"[{umo}] rp_tts_provider_id '{provider_id}' is not an available "
                "TTS provider, falling back to the session default"
            )
        try:
            provider = await self.context.get_using_tts_provider_async(umo)
        except Exception:
            logger.error("failed to resolve the TTS provider", exc_info=True)
            return None
        return provider if isinstance(provider, TTSProvider) else None

    async def _synthesize(self, provider: TTSProvider, text: str) -> str | None:
        """Return the audio file path for text, or None on failure/timeout."""
        timeout = max(10, int(self.config.get("rp_tts_timeout", 60) or 60))
        try:
            return await asyncio.wait_for(provider.get_audio(text), timeout=timeout)
        except TimeoutError:
            logger.warning(f"RP TTS synthesis timed out after {timeout}s")
            return None
        except Exception:
            logger.error("RP TTS synthesis failed", exc_info=True)
            return None

    async def _make_record(
        self,
        text: str,
        audio_path: str,
        use_file_service: bool,
        callback_base: str,
    ) -> Record:
        """Build a Record like the core pipeline does, with optional URL."""
        url = audio_path
        if use_file_service and callback_base:
            try:
                token = await file_token_service.register_file(audio_path)
                url = f"{callback_base}/api/file/{token}"
            except Exception:
                logger.error(
                    "RP TTS file service registration failed, "
                    "using the local audio path",
                    exc_info=True,
                )
        return Record(file=url, url=url, text=text)

    @filter.on_decorating_result()
    async def rp_tts_decorate(self, event: AstrMessageEvent) -> None:
        """Take over TTS for RP sessions: probability, dual output, paren strip.

        The core pipeline (ResultDecorateStage) decides TTS purely from the
        global ``provider_tts_settings`` and cannot know the session's chat
        mode, so in RP mode this hook rolls the session's own probability and,
        on a hit, synthesizes the audio itself. Either way the result is
        marked GENERAL_RESULT so the core never double-voices the reply.
        Daily-chat sessions are left completely untouched.

        The visible text is never modified: ``rp_tts_strip_brackets`` only
        affects what the TTS engine reads, so RP action parentheses keep
        their on-screen immersion while the voice skips them.
        """
        try:
            umo = event.unified_msg_origin
            state = await self._get_state(umo)
            if state.get("mode") != MODE_RP:
                return
            global_cfg = self.context.get_config()
            tts_cfg = global_cfg.get("provider_tts_settings") or {}
            if not tts_cfg.get("enable", False):
                return
            if not await SessionServiceManager.should_process_tts_request(event):
                return

            result = event.get_result()
            if result is None or not result.chain:
                return
            if result.result_content_type in (
                ResultContentType.STREAMING_RESULT,
                ResultContentType.STREAMING_FINISH,
            ):
                # Streaming replies bypass the core's TTS branch as well.
                logger.debug(
                    f"[{umo}] streaming output active, RP TTS takeover skipped"
                )
                return
            if not result.is_llm_result():
                return

            provider = await self._resolve_tts_provider(umo)
            voiced_turn = provider is not None and (
                random.random() <= self._tts_probability(state)
            )
            if voiced_turn:
                strip_on = bool(self.config.get("rp_tts_strip_brackets", True))
                keep_text = bool(self.config.get("rp_tts_dual_output", False))
                text_source = (
                    str(self.config.get("rp_tts_text_source", "stripped"))
                    .strip()
                    .lower()
                )
                use_file_service = bool(tts_cfg.get("use_file_service", False))
                callback_base = str(global_cfg.get("callback_api_base", "") or "")
                new_chain = []
                synthesized = 0
                for comp in result.chain:
                    if not (
                        isinstance(comp, Plain) and comp.text and len(comp.text) > 1
                    ):
                        # Non-text segments (images, ...) pass through; their
                        # presence already stops the core from voicing them.
                        new_chain.append(comp)
                        continue
                    spoken = (
                        self._tidy_spoken(self._strip_rp_parentheses(comp.text))
                        if strip_on
                        else comp.text
                    )
                    audio_path = (
                        await self._synthesize(provider, self._clean_tts_text(spoken))
                        if spoken.strip()
                        else None
                    )
                    if audio_path:
                        new_chain.append(
                            await self._make_record(
                                spoken, audio_path, use_file_service, callback_base
                            )
                        )
                        synthesized += 1
                        if keep_text:
                            # "stripped" mirrors exactly what the voice said;
                            # "original" keeps the immersive full text.
                            shown = spoken if text_source == "stripped" else comp.text
                            new_chain.append(Plain(shown))
                    else:
                        new_chain.append(Plain(comp.text))
                if synthesized:
                    result.chain = new_chain
                    logger.info(
                        f"[{umo}] RP TTS voiced {synthesized} segment(s), "
                        f"dual_output={keep_text}, strip_brackets={strip_on}"
                    )
                # else: every segment failed synthesis — plain-text fallback.
            # In RP mode with TTS on, the core must not roll its own dice on
            # top of ours: clear the LLM_RESULT flag for every handled turn.
            result.set_result_content_type(ResultContentType.GENERAL_RESULT)
            event.set_result(result)
        except Exception:
            logger.error("RP TTS decoration failed", exc_info=True)

    # ==================== RP illustration (NAI integration) ====================

    def _nai_api(self):
        """Resolve the NAI image plugin's companion extension API, if installed."""
        for module_name in (
            "data.plugins.astrbot_plugin_nai_image.main",
            "astrbot_plugin_nai_image.main",
        ):
            try:
                module = importlib.import_module(module_name)
                getter = getattr(module, "get_nai_image_api", None)
                api = getter() if callable(getter) else None
                if api is not None:
                    return api
            except Exception:
                continue
        try:
            metadata = self.context.get_registered_star("astrbot_plugin_nai_image")
            instance = getattr(metadata, "star_cls", None)
            return getattr(instance, "extension_api", None)
        except Exception:
            return None

    @filter.on_llm_response()
    async def maybe_rp_illustration(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """Probabilistically illustrate an RP turn via the NAI image plugin.

        Only drives the two-step flow; in the one-step flow the model calls
        ``ILLUSTRATION_TOOL`` itself and this hook stays out of the way.
        """
        try:
            if not self.config.get("rp_image_enabled", True):
                return
            probability = float(self.config.get("rp_image_probability", 0.2) or 0)
            if probability <= 0:
                return
            umo = event.unified_msg_origin
            state = await self._get_state(umo)
            if state.get("mode") != MODE_RP:
                return
            if self._image_flow(state) == FLOW_ONE_STEP:
                return
            reply_text = (resp.completion_text or "").strip() if resp else ""
            if not reply_text:
                return
            cooldown = max(0, int(self.config.get("rp_image_cooldown", 300) or 0))
            now = time.time()
            if now - self._image_last_ts.get(umo, 0.0) < cooldown:
                return
            if random.random() >= probability:
                return
            self._image_last_ts[umo] = now
            scene = str(state.get("scene") or "").strip()
            user_text = event.get_message_str().strip()
            task = asyncio.create_task(
                self._generate_rp_illustration(event, umo, scene, user_text, reply_text)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception:
            logger.error("RP illustration scheduling failed", exc_info=True)

    async def _generate_rp_illustration(
        self,
        event: AstrMessageEvent,
        umo: str,
        scene: str,
        user_text: str,
        reply_text: str,
    ) -> None:
        """Write an image prompt from the RP turn, generate via NAI, then send it.

        Runs as a detached background task so the text reply is not delayed
        by prompt writing or image generation. On success the submitted
        prompt is stored as the session's continuity anchor for the next
        illustration.
        """
        try:
            api = self._nai_api()
            if api is None:
                logger.info(
                    f"[{umo}] NAI image plugin not available, skip RP illustration"
                )
                return
            prompt = await self._compose_image_prompt(umo, scene, user_text, reply_text)
            if not prompt:
                return
            gen_timeout = max(
                10, int(self.config.get("rp_image_gen_timeout", 240) or 240)
            )
            try:
                result = await asyncio.wait_for(
                    api.generate_for_companion(
                        self,
                        {
                            "prompt_text": prompt,
                            "prompt_format": "nai",
                            "workflow_kind": "rp_illustration",
                            "session_key": umo,
                        },
                    ),
                    timeout=gen_timeout,
                )
            except TimeoutError:
                logger.warning(
                    f"[{umo}] NAI image generation timed out after {gen_timeout}s"
                )
                return
            image_path = str((result or {}).get("image_path") or "")
            if not image_path:
                logger.info(
                    f"[{umo}] RP illustration skipped: "
                    f"{(result or {}).get('note', 'no image')}"
                )
                return
            # Anchor the continuity chain only to prompts that produced a
            # real image, so failed/skipped turns never poison it.
            await self._set_image_anchor(umo, prompt)
            await event.send(MessageChain(chain=[Image.fromFileSystem(image_path)]))
            logger.info(f"[{umo}] RP illustration sent | path={image_path}")
        except Exception:
            logger.error("RP illustration generation failed", exc_info=True)

    async def _compose_image_prompt(
        self, umo: str, scene: str, user_text: str, reply_text: str
    ) -> str:
        """Ask the selected or current LLM to write an English image prompt for the RP turn.

        With a continuity anchor present (the previous illustration's tag
        prompt), it is fed back so character/environment tags carry over
        verbatim and only action/expression/camera tags follow the turn
        text. The static scene setting only bootstraps the first image.

        Args:
            umo: Unified message origin of the session.
            scene: Static scene text set via "/rp on"; bootstrap only.
            user_text: The current turn's user message text.
            reply_text: The current turn's LLM reply text.

        Returns:
            The English tag prompt, or "" when no provider is available or
            the prompt-writing call times out.
        """
        provider_id = str(self.config.get("rp_image_prompt_provider_id", "")).strip()
        provider = None
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            provider = self.context.get_using_provider(umo)
        if provider is None:
            logger.info(f"[{umo}] no LLM provider available, skip RP illustration")
            return ""
        user_spec = str(self.config.get("rp_image_prompt_spec", "")).strip()
        system_prompt = user_spec if user_spec else IMAGE_PROMPT_SPEC
        anchor = await self._get_image_anchor(umo)
        if anchor:
            logger.info(
                f"[{umo}] RP illustration anchored to previous image prompt "
                f"({len(anchor)} chars)"
            )
            lines = [
                f"上一张图的完整提示词：{anchor}",
                "一致性要求：这是同一剧情的连续配图。上一张图提示词中的人物与"
                "环境Tag必须延续（相同Tag、顺序与权重），仅允许根据本轮正文调整"
                "动作、表情、视线与镜头构图类Tag，或按系统规范移除当前构图下"
                "不可见的Tag；只有本轮正文明确发生地点、时间或着装变化时，才"
                "替换对应的环境/服装Tag。",
            ]
        else:
            lines = [f"场景设定：{scene or '未设定'}"]
        if user_text:
            lines.append(f"用户这一轮：{user_text[:300]}")
        if reply_text:
            lines.append(f"角色回复：{reply_text[:500]}")
        extra = str(self.config.get("rp_image_prompt_extra", "")).strip()
        if extra:
            lines.append(f"附加要求：{extra}")
        # Retrieve only the rule-book sections this turn's vocabulary needs,
        # so the huge recipes/rules corpus never has to be injected whole.
        rule_block = self._rule_block(
            " ".join([scene, anchor, user_text, reply_text, extra])
        )
        if rule_block:
            system_prompt = f"{system_prompt}\n\n{rule_block}"
        prompt_timeout = max(
            5, int(self.config.get("rp_image_prompt_timeout", 60) or 60)
        )
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt="\n".join(lines), system_prompt=system_prompt
                ),
                timeout=prompt_timeout,
            )
            return (resp.completion_text or "").strip().strip('"“”‘’')[:600]
        except TimeoutError:
            logger.warning(
                f"[{umo}] LLM image prompt extraction timed out after {prompt_timeout}s"
            )
            return ""

    # ==================== one-step illustration tool ====================

    @staticmethod
    def _clean_tool_prompt(text: str) -> str:
        """Strip wrapper noise (quotes, code fences, labels) from model tags."""
        cleaned = (text or "").strip()
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = re.sub(
            r"^(?:prompt|tags?|提示词)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE
        )
        return cleaned.strip().strip('"“”‘’`').strip()[:1500]

    @filter.llm_tool(name=ILLUSTRATION_TOOL)
    async def rp_illustration_tool(
        self, event: AstrMessageEvent, prompt: str = ""
    ) -> AsyncGenerator[str, None]:
        """为当前角色扮演剧情生成并直接发送一张场景插图（一步出图专用工具）。

        本轮是否配图由插件按概率与冷却决定：只有插件已批准配图时才调用；每轮最多
        调用一次。请先写完本轮正文回复再调用，生成成功后图片由本工具直接发送，不
        要再调用 send_message_to_user，也不要再用文字描述这张图。

        Args:
            prompt(string): 英文 NAI 绘画 Tag，逗号分隔，禁止中文。以 masterpiece, best quality, highly detailed 开头，依次写人物（1girl/1boy、外貌、服装、表情动作）、构图镜头、环境与光线；必须延续上一张配图的人物与场景 Tag，只改本轮的动作、表情、视线与镜头。
        """
        try:
            umo = event.unified_msg_origin
            if not self.config.get("rp_image_enabled", True):
                yield "配图功能未开启，本轮不生成图片。"
                return
            state = await self._get_state(umo)
            if state.get("mode") != MODE_RP:
                yield "当前不在角色扮演模式，本轮不生成图片。"
                return
            if self._image_flow(state) != FLOW_ONE_STEP:
                yield (
                    "当前是两步出图流程，插图会由插件在回复发出后自动补充，"
                    "本轮无需调用本工具。"
                )
                return
            if event.get_extra("chat_mode_illustration_used"):
                yield "本轮已经生成过一张插图，请勿重复调用。"
                return
            if not event.get_extra("chat_mode_illustration_approved"):
                yield "本轮未触发配图（概率或冷却限制），请直接结束本轮回复。"
                return

            tags = self._clean_tool_prompt(prompt)
            if not tags:
                yield "prompt 为空，本轮未生成图片。"
                return
            api = self._nai_api()
            if api is None:
                yield "NAI 生图插件不可用，本轮不生成图片。"
                return

            # Reserve this turn before the (slow) upstream call so a malformed
            # extra tool call cannot queue a second image.
            event.set_extra("chat_mode_illustration_used", True)
            gen_timeout = max(
                10, int(self.config.get("rp_image_gen_timeout", 240) or 240)
            )
            try:
                result = await asyncio.wait_for(
                    api.generate_for_companion(
                        self,
                        {
                            "prompt_text": tags,
                            "prompt_format": "nai",
                            "workflow_kind": "rp_illustration_one_step",
                            "session_key": umo,
                        },
                    ),
                    timeout=gen_timeout,
                )
            except TimeoutError:
                logger.warning(
                    f"[{umo}] one-step NAI generation timed out after {gen_timeout}s"
                )
                yield "生图超时，本轮跳过插图，请直接结束回复。"
                return

            image_path = str((result or {}).get("image_path") or "")
            if not image_path:
                note = str((result or {}).get("note") or "no image")
                logger.info(f"[{umo}] one-step illustration skipped: {note}")
                yield f"生图失败（{note}），本轮跳过插图，请直接结束回复。"
                return

            # Anchor the continuity chain only to prompts that produced a real
            # image, so failed turns never poison it.
            await self._set_image_anchor(umo, tags)
            await event.send(MessageChain(chain=[Image.fromFileSystem(image_path)]))
            logger.info(f"[{umo}] one-step RP illustration sent | path={image_path}")
            yield "插图已生成并发送，本轮无需再描述图片。"
        except Exception:
            logger.error("one-step RP illustration failed", exc_info=True)
            yield "生图异常，本轮跳过插图，请直接结束回复。"

    # ==================== commands ====================

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        return not (self.config.get("admin_only", False) and not event.is_admin)

    @filter.command_group("rp", alias={"roleplay"})
    def rp_group(self):
        """切换日常聊天/实景角色扮演模式：/rp on [场景] · /rp off · /rp status · /rp tts [概率]"""

    @rp_group.command("on", alias={"开启", "开始", "进入"})
    async def rp_on(
        self, event: AstrMessageEvent, scene: GreedyStr
    ) -> AsyncGenerator[MessageEventResult, None]:
        """进入实景角色扮演模式，可附带场景描述，如：/rp on 深夜的便利店"""
        if not self._check_permission(event):
            yield event.plain_result("只有管理员可以切换聊天模式。")
            return

        umo = event.unified_msg_origin
        state = await self._get_state(umo)
        scene = (scene or "").strip()

        if state["mode"] == MODE_RP:
            if scene:
                state["scene"] = scene
                await self._save_state(umo, state)
                # Scene changed mid-session: the old visual anchor no longer holds.
                await self._set_image_anchor(umo, "")
                yield event.plain_result(f"已在角色扮演模式中，场景已更新为：{scene}")
            else:
                yield event.plain_result("已经处于实景角色扮演模式中了。")
            return

        # Fresh RP entry: reset the visual continuity anchor so the next
        # illustration bootstraps from the scene setting.
        await self._set_image_anchor(umo, "")

        if self.config.get("isolate_conversation", True):
            try:
                await self._switch_conversation(umo, MODE_RP)
            except Exception:
                logger.error("failed to switch to the RP conversation", exc_info=True)
                yield event.plain_result(
                    "已进入角色扮演模式，但切换剧情对话失败，详见日志。"
                )
                state["mode"] = MODE_RP
                state["scene"] = scene
                await self._save_state(umo, state)
                return

        state["mode"] = MODE_RP
        state["scene"] = scene
        await self._save_state(umo, state)

        msg = "已进入角色扮演模式。"
        if scene:
            msg += f" 场景：{scene}"
        yield event.plain_result(msg)

    @rp_group.command("off", alias={"关闭", "结束", "退出"})
    async def rp_off(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """退出角色扮演模式，恢复日常聊天"""
        if not self._check_permission(event):
            yield event.plain_result("只有管理员可以切换聊天模式。")
            return

        umo = event.unified_msg_origin
        state = await self._get_state(umo)

        if state["mode"] == MODE_NORMAL:
            yield event.plain_result("当前已经是日常聊天模式。")
            return

        if self.config.get("isolate_conversation", True):
            try:
                await self._switch_conversation(umo, MODE_NORMAL)
            except Exception:
                logger.error(
                    "failed to switch back to the daily conversation", exc_info=True
                )
                yield event.plain_result(
                    "已回到日常聊天模式，但切换日常对话失败，详见日志。"
                )
                state["mode"] = MODE_NORMAL
                state["scene"] = ""
                await self._save_state(umo, state)
                return

        state["mode"] = MODE_NORMAL
        state["scene"] = ""
        await self._save_state(umo, state)

        yield event.plain_result("已回到日常聊天模式。")

    @rp_group.command("status", alias={"状态"})
    async def rp_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看当前会话的聊天模式与对话绑定状态"""
        umo = event.unified_msg_origin
        state = await self._get_state(umo)
        mode = state.get("mode", MODE_NORMAL)

        lines = [f"当前模式：{MODE_LABELS.get(mode, mode)}"]
        if mode == MODE_RP:
            lines.append(f"场景设定：{state.get('scene') or '（未设置）'}")
            flow = self._image_flow(state)
            lines.append(
                f"配图流程：{FLOW_LABELS.get(flow, flow)}"
                + ("（会话临时）" if state.get("image_flow") else "")
            )
            lines.append(
                "配图触发：概率 "
                f"{float(self.config.get('rp_image_probability', 0.2) or 0):.0%}"
                " / 冷却 "
                f"{int(self.config.get('rp_image_cooldown', 300) or 0)} 秒"
            )
            lines.append(
                "TTS 触发概率："
                f"{self._tts_probability(state):.0%}"
                + ("（会话临时）" if state.get("tts_probability") is not None else "")
            )
            lines.append(
                "TTS 语音+文字双输出："
                f"{'开' if self.config.get('rp_tts_dual_output', False) else '关'}"
            )
            lines.append(
                "TTS 剔除括号动作："
                f"{'开' if self.config.get('rp_tts_strip_brackets', True) else '关'}"
            )

        if self.config.get("isolate_conversation", True):
            cm = self.context.conversation_manager
            curr_cid = await cm.get_curr_conversation_id(umo)
            for bind_mode in (MODE_NORMAL, MODE_RP):
                cid = await self._get_conv_id(umo, bind_mode)
                label = MODE_LABELS[bind_mode]
                if cid:
                    mark = "（当前）" if cid == curr_cid else ""
                    lines.append(f"{label}对话：{cid[:8]}...{mark}")
                else:
                    lines.append(f"{label}对话：未创建")
        yield event.plain_result("\n".join(lines))

    @rp_group.command("tts", alias={"语音", "频率"})
    async def rp_tts(
        self, event: AstrMessageEvent, percentage: GreedyStr
    ) -> AsyncGenerator[MessageEventResult, None]:
        """调整本会话 RP 模式的 TTS 触发频率，如：/rp tts 30；/rp tts 恢复默认"""
        if not self._check_permission(event):
            yield event.plain_result("只有管理员可以调整 TTS 设置。")
            return

        state = await self._get_state(event.unified_msg_origin)
        if state.get("mode") != MODE_RP:
            yield event.plain_result(
                "TTS 频率调整仅在角色扮演模式中可用，请先 /rp on。"
            )
            return

        arg = (percentage or "").strip()
        if not arg:
            yield event.plain_result(
                "用法：/rp tts 30 —— 将本会话 RP 模式的语音触发概率设为每轮 30%；"
                "/rp tts 默认 —— 恢复配置文件中的概率。"
            )
            return

        if arg in ("默认", "恢复", "reset", "default"):
            state["tts_probability"] = None
            await self._save_state(event.unified_msg_origin, state)
            yield event.plain_result(
                f"已恢复配置文件中的 RP TTS 概率：{self._tts_probability(state):.0%}。"
            )
            return

        try:
            prob = float(arg.strip("%")) / 100.0
        except ValueError:
            yield event.plain_result(f"无法识别的概率：{arg}，请使用 0~100 的数字。")
            return
        if not 0.0 <= prob <= 1.0:
            yield event.plain_result("概率须在 0~100 之间。")
            return

        state["tts_probability"] = prob
        await self._save_state(event.unified_msg_origin, state)
        note = (
            "（每轮都会发语音）"
            if prob >= 1
            else ("（本模式不再发语音）" if prob <= 0 else "")
        )
        yield event.plain_result(
            f"本会话 RP 模式的 TTS 触发概率已设为 {prob:.0%}{note}。"
        )

    @rp_group.command("image", alias={"配图", "插图"})
    async def rp_image(
        self, event: AstrMessageEvent, choice: GreedyStr
    ) -> AsyncGenerator[MessageEventResult, None]:
        """切换本会话 RP 模式的配图流程，如：/rp image 一步；/rp image 默认"""
        if not self._check_permission(event):
            yield event.plain_result("只有管理员可以切换配图流程。")
            return

        state = await self._get_state(event.unified_msg_origin)
        arg = (choice or "").strip()
        if not arg:
            flow = self._image_flow(state)
            note = "（会话临时）" if state.get("image_flow") else "（跟随配置文件）"
            yield event.plain_result(
                f"当前配图流程：{FLOW_LABELS.get(flow, flow)}{note}\n"
                "用法：/rp image 一步 —— 模型在写完回复后直接调用工具生图，Tag 随"
                "正文一起生成，省掉一次独立的 Tag 提炼往返；\n"
                "/rp image 两步 —— 回复先发出，插件再后台补图（原有流程，更稳）；\n"
                "/rp image 默认 —— 恢复配置文件中的流程。"
            )
            return

        if arg in ("默认", "恢复", "reset", "default"):
            state["image_flow"] = None
            await self._save_state(event.unified_msg_origin, state)
            flow = self._image_flow(state)
            yield event.plain_result(
                f"已恢复配置文件中的配图流程：{FLOW_LABELS.get(flow, flow)}。"
            )
            return

        key = arg.lower().replace("-", "_").replace(" ", "")
        if key not in FLOW_ALIASES:
            yield event.plain_result(
                "无法识别的流程，请使用「一步」「两步」或「默认」。"
            )
            return

        state["image_flow"] = FLOW_ALIASES[key]
        await self._save_state(event.unified_msg_origin, state)
        flow = state["image_flow"]
        if flow == FLOW_ONE_STEP:
            note = "（模型会在写完回复后自动调用生图工具，生效需已启用配图且装上 NAI 插件）"
        else:
            note = "（回复先发出，插件再后台补充插图）"
        yield event.plain_result(
            f"本会话配图流程已切换为{FLOW_LABELS[flow]}{note}。"
        )
