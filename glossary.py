"""《边狱公司》(Limbus Company) 术语表。

机翻最大的问题是把专有名词译错。官方英文版有固定译法,这里按需注入提示词。

共享词条写在 glossary/terms.txt（纳入 git,可随时增删改）。
本地私有覆盖写在 glossary.local.txt（不进 git）。
格式详见 terms.txt 文件头注释。
"""
from __future__ import annotations

import os
import re
from typing import Iterable

MAX_GLOSSARY_HITS = 40
SOURCE_LANGS = ('中文', 'English')

# 打包/缺文件时的最小兜底,正常运行以 glossary/terms.txt 为准
_FALLBACK_PAIRS = (
    ('边狱公司', 'Limbus Company'),
    ('囚徒', 'Sinner'),
    ('罪人', 'Sinner'),
    ('人格', 'Identity'),
    ('异想体', 'Abnormality'),
    ('边狱巴士', 'Mephistopheles'),
)


def _parse_lines(lines: Iterable[str], path_label: str) -> tuple[dict, dict]:
    """解析术语文件。返回 (中→英, 英→中别名)。"""
    forward: dict[str, str] = {}
    en_aliases: dict[str, str] = {}
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=>' in line:
            left, _, right = line.partition('=>')
            en, zh = left.strip(), right.strip()
            if not en or not zh:
                continue
            en_aliases[en] = zh
            continue
        if '=' in line:
            zh, _, en = line.partition('=')
        elif '\t' in line:
            zh, _, en = line.partition('\t')
        else:
            print(f'[术语表] {path_label}:{lineno} 缺少 = ,已跳过: {line[:40]}')
            continue
        zh, en = zh.strip(), en.strip()
        if not zh or not en:
            continue
        forward[zh] = en
    return forward, en_aliases


def _read_pairs(path: str) -> tuple[dict, dict]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return _parse_lines(f, path)
    except FileNotFoundError:
        return {}, {}
    except Exception as e:
        print(f'[术语表] 读取 {path} 失败: {e}')
        return {}, {}


def _build_maps(shared_path: str, local_path: str) -> tuple[dict, dict]:
    forward: dict[str, str] = dict(_FALLBACK_PAIRS)
    en_aliases: dict[str, str] = {}
    for path in (shared_path, local_path):
        if not path:
            continue
        f, a = _read_pairs(path)
        forward.update(f)
        en_aliases.update(a)
    reverse = {en: zh for zh, en in forward.items()}
    reverse.update(en_aliases)
    return forward, reverse


def glossary_paths(app_dir: str) -> tuple[str, str]:
    """返回 (共享术语表路径, 本地覆盖路径)。"""
    shared = os.path.join(app_dir, 'glossary', 'terms.txt')
    local = os.path.join(app_dir, 'glossary.local.txt')
    return shared, local


def load_glossary(shared_path: str, source_lang: str = '中文', local_path: str = '') -> dict:
    """加载术语表:共享 terms.txt + 可选 glossary.local.txt。

    source_lang:
      \"中文\"    -> 中文→英文,子串匹配
      \"English\" -> 英文→中文,整词匹配
    """
    forward, reverse = _build_maps(shared_path, local_path)
    if source_lang == 'English':
        return reverse
    return forward


def glossary_mtime(*paths: str) -> float:
    """多个术语文件的最新 mtime;不存在则为 0。"""
    latest = 0.0
    for path in paths:
        if not path:
            continue
        try:
            latest = max(latest, os.path.getmtime(path))
        except OSError:
            pass
    return latest


def ensure_shared_glossary(shared_path: str) -> None:
    """共享术语表不存在时写一份带说明的空模板。"""
    if os.path.exists(shared_path):
        return
    os.makedirs(os.path.dirname(shared_path) or '.', exist_ok=True)
    with open(shared_path, 'w', encoding='utf-8') as f:
        f.write(
            '# 共享术语表 · 建议纳入 git\n'
            '# 格式: 中文=English\n'
            '# 英→中别名: English=>中文\n'
            '# 改完保存即可,程序会自动重载\n'
            '#\n'
            '# 边狱巴士=Mephistopheles\n'
            '# 罪人=Sinner\n'
        )


def glossary_hits(text: str, glossary: dict, source_lang: str = '中文', limit: int = MAX_GLOSSARY_HITS) -> list:
    """挑出文本中实际出现的术语,按词长从长到短返回 [(源, 目标), ...]。"""
    if source_lang == 'English':
        hits = [(en, zh) for en, zh in glossary.items() if _en_term_in(text, en)]
    else:
        hits = [(zh, en) for zh, en in glossary.items() if zh in text]
    hits.sort(key=lambda kv: (-len(kv[0]), kv[0]))
    return hits[:limit]


_CONFUSABLE = {
    'I': 'Il1|i', 'l': 'Il1|i', 'i': 'Il1|i', '1': 'Il1|i', '|': 'Il1|i',
    'O': 'O0o', 'o': 'O0o', '0': 'O0o',
    'S': 'S5s', 's': 'S5s', '5': 'S5s',
    'B': 'B8b', 'b': 'B8b', '8': 'B8b',
    'Z': 'Z2z', 'z': 'Z2z', '2': 'Z2z',
    'G': 'G6g', 'g': 'G6g', '6': 'G6g',
}
_FIRST_CHAR_EXTRA = {
    'I': 'l1|', 'l': 'I1|', 'O': '0', 'o': '0', 'S': '5', 's': '5',
    'B': '8', 'b': '8', 'Z': '2', 'z': '2', 'G': '6', 'g': '6',
}


def _term_pattern(term: str) -> str:
    parts = []
    for idx, ch in enumerate(term):
        group = ch + _FIRST_CHAR_EXTRA.get(ch, '') if idx == 0 else _CONFUSABLE.get(ch, '')
        parts.append(f'[{re.escape(group)}]' if len(group) > 1 else re.escape(ch))
    return ''.join(parts)


def _en_term_in(text: str, term: str) -> bool:
    pattern = f'(?<![A-Za-z0-9]){_term_pattern(term)}s?(?![A-Za-z0-9])'
    return re.search(pattern, text) is not None


def format_glossary_block(hits: list) -> str:
    if not hits:
        return ''
    lines = '\n'.join(f'{src} = {dst}' for src, dst in hits)
    return (
        f'\n\n【术语对照表 · 必须严格使用以下译名】\n{lines}\n'
        '以上译名优先级高于你的默认判断,出现左侧词时必须使用右侧译名。\n'
    )
