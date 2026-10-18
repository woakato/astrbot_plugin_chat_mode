"""Deterministic keyword retrieval over the bundled NAI prompting knowledge base.

The knowledge base lives next to this file in ``prompt_kb/`` as plain
Markdown (``rules.md``, ``recipes.md``), copied from the ``nai-prompting``
skill. Nothing here calls a model: the caller passes free text (scene,
anchor tags, reply, extra requirements) and gets back the few sections whose
vocabulary overlaps it, bounded by a character budget.

Keeping the retrieval model-free matters for the one-step illustration flow:
it must not add an extra LLM round trip, and the tag writer must not need
file-system tools (attached tool schemas make models drop character).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)
_LATIN = re.compile(r"[a-z][a-z0-9_'\-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fa5]{2,}")

# Bigrams that appear everywhere in Chinese prose and carry no retrieval
# signal; dropping them keeps generic scenes from matching everything.
_CJK_STOP = frozenset(
    """
    我们 这个 那个 可以 一个 就是 因为 所以 但是 现在 已经 没有 什么 怎么
    还是 如果 然后 这样 那样 不是 只是 时候 自己 知道 起来 出来 过来 东西
    地方 时候 需要 尽量 直接 一般 一定 一次 一样 之前 之后 上面 下面 里面
    前面 后面 你的 我的 他的 她的 它 而且 并且 或者 另外 同时 比如 例如
    注意 规则 提示 词里 里面 这种 那种 有一 有点 还是 还是 不要 不能 不会
    """.split()
)


@dataclass(frozen=True)
class Section:
    """One retrievable slice of the knowledge base."""

    source: str
    title: str
    body: str
    latin: frozenset[str]
    cjk: frozenset[str]
    title_latin: frozenset[str]
    title_cjk: frozenset[str]


def _cjk_bigrams(text: str) -> set[str]:
    found: set[str] = set()
    for run in _CJK_RUN.findall(text):
        for i in range(len(run) - 1):
            pair = run[i : i + 2]
            if pair not in _CJK_STOP:
                found.add(pair)
    return found


def split_sections(text: str, source: str) -> list[Section]:
    """Split Markdown into heading-delimited sections.

    Only ``##``/``###`` headings start a section; the file preamble before
    the first heading is dropped (it is the document title and provenance
    block, not a rule).
    """
    matches = list(_HEADING.finditer(text))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue
        joined = title + "\n" + body
        sections.append(
            Section(
                source=source,
                title=title,
                body=body,
                latin=frozenset(_LATIN.findall(joined.lower())),
                cjk=frozenset(_cjk_bigrams(joined)),
                title_latin=frozenset(_LATIN.findall(title.lower())),
                title_cjk=frozenset(_cjk_bigrams(title)),
            )
        )
    return sections


_CACHE: dict[tuple, list[Section]] = {}


def load_dir(directory: Path) -> list[Section]:
    """Load every ``*.md`` in *directory*, cached until the files change."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    files = sorted(
        p
        for p in directory.glob("*.md")
        if p.is_file()
        and p.name.lower() != "readme.md"
        and not p.name.startswith("_")
    )
    signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    cached = _CACHE.get(signature)
    if cached is not None:
        return cached
    sections: list[Section] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        sections.extend(split_sections(text, path.name))
    _CACHE.clear()
    _CACHE[signature] = sections
    return sections


def _query_sets(query: str) -> tuple[set[str], set[str]]:
    return set(_LATIN.findall(query.lower())), _cjk_bigrams(query)


def score_section(section: Section, latin: set[str], cjk: set[str]) -> int:
    """Weighted overlap: title hits dominate, then Latin tags, then CJK bigrams."""
    score = 0
    score += 4 * len(latin & section.title_latin)
    score += 4 * len(cjk & section.title_cjk)
    score += 2 * len(latin & section.latin)
    score += len(cjk & section.cjk)
    return score


def select(
    sections: Iterable[Section],
    query: str,
    *,
    budget: int = 1200,
    max_sections: int = 2,
    min_score: int = 3,
) -> list[Section]:
    """Pick the highest-scoring sections for *query* under a size budget."""
    if budget <= 0 or max_sections <= 0 or not query.strip():
        return []
    latin, cjk = _query_sets(query)
    if not latin and not cjk:
        return []
    ranked: list[tuple[int, Section]] = []
    for section in sections:
        score = score_section(section, latin, cjk)
        if score >= min_score:
            ranked.append((score, section))
    ranked.sort(key=lambda item: (-item[0], item[1].source, item[1].title))
    picked: list[Section] = []
    used = 0
    for _, section in ranked:
        if len(picked) >= max_sections:
            break
        cost = len(section.title) + 4
        if cost + 120 > budget - used:
            continue
        picked.append(section)
        used += cost
    return picked


def _trim(body: str, limit: int) -> str:
    if len(body) <= limit:
        return body
    cut = body.rfind("\n", 0, limit)
    if cut < limit // 3:
        cut = limit
    return body[:cut].rstrip() + "\n…"


def render(picked: Iterable[Section], budget: int = 1200) -> str:
    """Render picked sections within *budget* characters (heading + body)."""
    blocks: list[str] = []
    used = 0
    for section in picked:
        header = f"## {section.title}"
        remaining = budget - used - len(header) - 2
        if remaining < 120:
            break
        block = f"{header}\n{_trim(section.body, remaining)}"
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)
