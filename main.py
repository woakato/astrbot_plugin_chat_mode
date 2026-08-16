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
"""

import re
from collections.abc import AsyncGenerator

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

MODE_NORMAL = "normal"
MODE_RP = "rp"

MODE_LABELS = {MODE_NORMAL: "日常聊天", MODE_RP: "实景角色扮演"}

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


class ChatModePlugin(Star):
    """Switch between daily chat and immersive roleplay per session."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        # Per-session mode state cache, write-through to the plugin KV store.
        self._states: dict[str, dict] = {}

    # ==================== state persistence ====================

    async def _get_state(self, umo: str) -> dict:
        """Load the per-session mode state (cached, KV-backed).

        Args:
            umo: Unified message origin, e.g. "aiocqhttp:FriendMessage:12345".

        Returns:
            A dict with keys "mode" (MODE_NORMAL | MODE_RP) and "scene" (str).
        """
        state = self._states.get(umo)
        if state is None:
            raw = await self.get_kv_data(f"chat_mode:{umo}", None)
            state = raw if isinstance(raw, dict) else {}
            state = {
                "mode": state.get("mode", MODE_NORMAL),
                "scene": str(state.get("scene") or ""),
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
                block = str(self.config.get("rp_prompt", "")).strip()
                scene = str(state.get("scene", "")).strip()
                if scene:
                    scene_block = f"# Current Scene / 当前场景设定\n{scene}"
                    block = f"{block}\n\n{scene_block}" if block else scene_block
                if block:
                    req.system_prompt = (
                        f"{req.system_prompt.rstrip()}\n\n{block}"
                        if req.system_prompt.strip()
                        else block
                    )
                return

            guard = str(self.config.get("normal_guard_prompt", "")).strip()
            if self.config.get("inject_normal_guard", True) and guard:
                req.system_prompt = (
                    f"{req.system_prompt.rstrip()}\n\n{guard}"
                    if req.system_prompt.strip()
                    else guard
                )
            if self.config.get("filter_memory_style", True):
                self._sanitize_memory_injection(req, umo)
        except Exception:
            logger.error("chat-mode decoration failed", exc_info=True)

    # ==================== commands ====================

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        return not (self.config.get("admin_only", False) and not event.is_admin)

    @filter.command_group("rp", alias={"roleplay"})
    def rp_group(self):
        """切换日常聊天/实景角色扮演模式：/rp on [场景] · /rp off · /rp status"""

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
                yield event.plain_result(f"已在角色扮演模式中，场景已更新为：{scene}")
            else:
                yield event.plain_result("已经处于实景角色扮演模式中了。")
            return

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
