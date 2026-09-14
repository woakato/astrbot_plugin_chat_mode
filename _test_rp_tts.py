"""Standalone logic tests for the RP TTS takeover (stubs astrbot APIs)."""

import asyncio
import random
import sys
import types
from enum import Enum
from pathlib import Path

BASE = Path(__file__).parent


def stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _Group:
    def command(self, *a, **k):
        return lambda f: f


class _Filter:
    def __getattr__(self, name):
        def factory(*a, **k):
            def deco(fn):
                return _Group() if name == "command_group" else fn
            return deco
        return factory


class Star:
    def __init__(self, context):
        self.context = context

    async def get_kv_data(self, k, d=None):
        return d

    async def put_kv_data(self, k, v):
        pass


class Context:
    def __init__(self, config, tts_provider=None):
        self._config = config
        self.tts_provider = tts_provider

    def get_config(self, umo=None):
        return self._config

    def get_provider_by_id(self, pid):
        return None

    async def get_using_tts_provider_async(self, umo=None):
        return self.tts_provider


class ResultContentType(Enum):
    LLM_RESULT = 1
    GENERAL_RESULT = 2
    AGENT_RUNNER_ERROR = 3
    STREAMING_RESULT = 4
    STREAMING_FINISH = 5


class Component:
    pass


class Plain(Component):
    def __init__(self, text="", **kw):
        self.text = text


class Record(Component):
    def __init__(self, file="", **kw):
        self.file = file
        self.url = kw.get("url")
        self.text = kw.get("text")


class Image(Component):
    @classmethod
    def fromFileSystem(cls, p):
        return cls()


class MessageChain:
    def __init__(self, chain=None):
        self.chain = chain or []


class MessageEventResult(MessageChain):
    def __init__(self, chain=None):
        super().__init__(chain)
        self.result_content_type = ResultContentType.LLM_RESULT

    def is_llm_result(self):
        return self.result_content_type == ResultContentType.LLM_RESULT

    def set_result_content_type(self, typ):
        self.result_content_type = typ
        return self


class AstrMessageEvent:
    def __init__(self, result):
        self._result = result
        self.unified_msg_origin = "test:FriendMessage:1"

    def get_result(self):
        return self._result

    def set_result(self, r):
        self._result = r


class TTSProvider:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def get_audio(self, text):
        self.calls.append(text)
        return "" if self.fail else f"/tmp/audio_{len(self.calls)}.mp3"


class _Group:
    def command(self, *a, **k):
        return lambda f: f


class _Filter:
    def __getattr__(self, name):
        def factory(*a, **k):
            def deco(fn):
                return _Group() if name == "command_group" else fn
            return deco
        return factory


class SessionServiceManager:
    @staticmethod
    async def should_process_tts_request(event):
        return True


class _FTS:
    @staticmethod
    async def register_file(p):
        return "tok"


# --- build module stubs ---
astrbot = stub("astrbot")
api = stub("astrbot.api", AstrBotConfig=dict, logger=types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, debug=lambda *a, **k: None))
stub("astrbot.api.event", AstrMessageEvent=AstrMessageEvent,
     MessageChain=MessageChain, MessageEventResult=MessageEventResult,
     ResultContentType=ResultContentType, filter=_Filter())
stub("astrbot.api.message_components", Image=Image, Plain=Plain, Record=Record)
stub("astrbot.api.provider", LLMResponse=dict, ProviderRequest=dict)
stub("astrbot.api.star", Context=Context, Star=Star)
stub("astrbot.core", file_token_service=_FTS())
stub("astrbot.core.provider", )
stub("astrbot.core.provider.provider", TTSProvider=TTSProvider)
core_star = stub("astrbot.core.star")
filt = stub("astrbot.core.star.filter")
stub("astrbot.core.star.filter.command", GreedyStr=str)
stub("astrbot.core.star.session_llm_manager",
     SessionServiceManager=SessionServiceManager)

sys.path.insert(0, str(BASE))
import main  # noqa: E402


def make_plugin(tts_enabled=True, **cfg):
    config = {
        "rp_tts_probability": 1.0,
        "rp_tts_dual_output": False,
        "rp_tts_strip_brackets": True,
        "rp_tts_text_source": "stripped",
        "rp_tts_provider_id": "",
        "rp_tts_timeout": 60,
        "isolate_conversation": True,
    }
    config.update(cfg)
    gconf = {"provider_tts_settings": {
        "enable": tts_enabled, "use_file_service": False,
        "dual_output": False, "trigger_probability": 1.0}}
    ctx = Context(gconf, tts_provider=TTSProvider())
    return main.ChatModePlugin(ctx, config)


async def run_case(mode, chain_texts, plugin, probability=1.0):
    plugin._states["test:FriendMessage:1"] = {
        "mode": mode, "scene": "", "tts_probability": probability}
    result = MessageEventResult([Plain(t) for t in chain_texts])
    event = AstrMessageEvent(result)
    await plugin.rp_tts_decorate(event)
    return event.get_result()


def plain_text(res):
    return "".join(c.text for c in res.chain if isinstance(c, Plain))


def count_records(res):
    return sum(1 for c in res.chain if isinstance(c, Record))


async def main_test():
    p = make_plugin()
    reply = "（轻轻握住你的手）今天也要加油哦。"

    # 1) normal mode: untouched
    r = await run_case("normal", [reply], p)
    assert r.chain[0].text == reply, "normal mode must not be touched"
    assert r.is_llm_result(), "normal mode keeps LLM_RESULT"
    print("PASS 1: normal mode untouched")

    # 2) RP hit p=1, strip on, dual off -> Record only, text preserved inside
    r = await run_case("rp", [reply], p)
    assert count_records(r) == 1 and not any(
        isinstance(c, Plain) for c in r.chain), r.chain
    assert r.chain[0].text == "今天也要加油哦。", repr(r.chain[0].text)
    assert not r.is_llm_result(), "core TTS must be suppressed"
    print("PASS 2: RP voiced, brackets stripped from audio, voice-only")

    # 3) dual output, text_source=stripped -> Record + stripped Plain
    p2 = make_plugin(rp_tts_dual_output=True)
    r = await run_case("rp", [reply], p2)
    assert count_records(r) == 1 and plain_text(r) == "今天也要加油哦。"
    print("PASS 3: dual output with stripped text")

    # 4) dual output, text_source=original -> Record + original Plain
    p3 = make_plugin(rp_tts_dual_output=True, rp_tts_text_source="original")
    r = await run_case("rp", [reply], p3)
    assert plain_text(r) == reply
    print("PASS 4: dual output keeps original text")

    # 5) probability 0 -> no Record, still suppress core TTS (GENERAL)
    r = await run_case("rp", [reply], p, probability=0.0)
    assert count_records(r) == 0
    assert not r.is_llm_result(), "core must not roll its own TTS after our miss"
    assert plain_text(r) == reply, "missed turn keeps full visible text"
    print("PASS 5: probability 0 -> text only, core TTS suppressed")

    # 6) strip off -> audio receives full text with brackets
    p4 = make_plugin(rp_tts_strip_brackets=False)
    r = await run_case("rp", [reply], p4)
    assert p4.context.tts_provider.calls == [reply], p4.context.tts_provider.calls
    assert r.chain[0].text == reply
    print("PASS 6: strip off keeps brackets in speech")

    # 7) synthesis failure -> plain text fallback, still no core re-TTS
    p5 = make_plugin()
    p5.context.tts_provider = TTSProvider(fail=True)
    r = await run_case("rp", [reply], p5)
    assert count_records(r) == 0
    assert not r.is_llm_result()
    assert plain_text(r) == reply
    print("PASS 7: TTS failure falls back to text")

    # 8) global TTS disabled -> untouched
    p6 = make_plugin(tts_enabled=False)
    r = await run_case("rp", [reply], p6)
    assert r.is_llm_result() and count_records(r) == 0
    print("PASS 8: global TTS off -> untouched")

    # 9) streaming result -> skipped
    r = await run_case("rp", [reply], p)
    # emulate streaming via fresh result
    plugin = make_plugin()
    plugin._states["test:FriendMessage:1"] = {
        "mode": "rp", "scene": "", "tts_probability": 1.0}
    res = MessageEventResult([Plain("流式")])
    res.result_content_type = ResultContentType.STREAMING_FINISH
    ev = AstrMessageEvent(res)
    await plugin.rp_tts_decorate(ev)
    assert ev.get_result().result_content_type == ResultContentType.STREAMING_FINISH
    print("PASS 9: streaming untouched")

    # 10) markdown cleaned for speech only
    plugin = make_plugin()
    plugin._states["test:FriendMessage:1"] = {
        "mode": "rp", "scene": "", "tts_probability": 1.0}
    res = MessageEventResult([Plain("**你好**（笑）")])
    ev = AstrMessageEvent(res)
    await plugin.rp_tts_decorate(ev)
    assert plugin.context.tts_provider.calls == ["你好"], plugin.context.tts_provider.calls
    print("PASS 10: markdown markers removed from speech")

    # 11) probability statistics ~0.3
    plugin = make_plugin()
    plugin._states["test:FriendMessage:1"] = {
        "mode": "rp", "scene": "", "tts_probability": 0.3}
    random.seed(42)
    voiced = 0
    for _ in range(2000):
        res = MessageEventResult([Plain("测试一下这句话")])
        ev = AstrMessageEvent(res)
        await plugin.rp_tts_decorate(ev)
        voiced += count_records(ev.get_result())
    assert 0.22 < voiced / 2000 < 0.38, voiced / 2000
    print(f"PASS 11: voiced ratio {voiced/2000:.2f} for p=0.3")

    # 12) multi-segment with image in between
    plugin = make_plugin()
    plugin._states["test:FriendMessage:1"] = {
        "mode": "rp", "scene": "", "tts_probability": 1.0}
    res = MessageEventResult([Plain("第一句（动作）"), Image(), Plain("第二句")])
    ev = AstrMessageEvent(res)
    await plugin.rp_tts_decorate(ev)
    kinds = [type(c).__name__ for c in ev.get_result().chain]
    assert kinds == ["Record", "Image", "Record"], kinds
    print("PASS 12: image segment passes through")

    print("\nALL TESTS PASSED")


asyncio.run(main_test())
