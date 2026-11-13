"""《边狱公司》实时界面翻译器 (Limbus Company Live Translator)\n\n把游戏界面上的中文实时 OCR 识别出来,调用 DeepSeek API 翻译成英文,\n译文显示在置顶的半透明悬浮窗里。全程只截屏,不注入、不修改游戏进程。\n\n用法:\n    python limbus_translator.py\n\n热键(全局生效,游戏窗口在前台时也能用):\n    F2      框选屏幕区域 -> 进入实时翻译模式\n    F3      翻译整个屏幕(单次)\n    F4      停止实时翻译 / 重新翻译上次区域\n    F6      切换鼠标穿透(穿透时点击会落到游戏上)\n    F7      隐藏 / 显示悬浮窗\n    Ctrl+S  复制当前译文\n    Ctrl+E  清空输出\n    Ctrl+Q  退出程序\n\n注意:热键依赖 keyboard 库的全局钩子。如果游戏以管理员身份运行而本程序不是,\n系统会屏蔽跨权限级的按键,此时需要以管理员身份运行本程序。\n\n配置:\n    同目录 config.json,首次运行自动生成。API Key 也可用环境变量 DEEPSEEK_API_KEY 提供。\n"""
import asyncio
import ctypes
import difflib
import io
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
import requests
from PIL import Image, ImageOps
import mss
from glossary import (
    ensure_shared_glossary,
    format_glossary_block,
    glossary_hits,
    glossary_mtime,
    glossary_paths,
    load_glossary,
)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    else:
        pass
import winsdk.windows.globalization as globalization
import winsdk.windows.graphics.imaging as imaging
import winsdk.windows.media.ocr as ocr
import winsdk.windows.storage.streams as streams
APP_NAME = '边狱公司实时翻译'
DEFAULT_API_URL = 'https://api.deepseek.com/chat/completions'
DEFAULT_MODEL = 'deepseek-flash'
_MSS = getattr(mss, 'MSS', None) or mss.mss
def _app_dir() -> str:
    """程序所在目录:打包成 exe 后是 exe 目录,开发时是脚本目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))
APP_DIR = _app_dir()
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
CACHE_FILE = os.path.join(APP_DIR, 'cache.json')
GLOSSARY_SHARED, GLOSSARY_LOCAL = glossary_paths(APP_DIR)
GLOSSARY_FILE = GLOSSARY_SHARED  # 兼容旧变量名:菜单/自检打开共享表
DEFAULTS = {'api_key': '', 'api_url': DEFAULT_API_URL, 'model': DEFAULT_MODEL, 'source_lang': 'English', 'target_lang': '简体中文', 'extra_prompt': '', 'text_color': '#e8c07a', 'font_size': 13, 'window_opacity': 0.82, 'bg_style': 'panel', 'live_interval': 0.8, 'similarity_threshold': 0.92, 'ocr_upscale': 2.0, 'use_glossary': True, 'use_cache': True, 'click_through': True, 'show_source': False}
SOURCE_LANGS = ['English', '中文']
TARGET_LANGS = ['简体中文', '繁體中文', '日本語', '한국어', 'English', 'Русский']
_SAME_LANGUAGE = {'中文': {'中文', '简体中文'}, 'English': {'English'}}
def same_language(a: str, b: str) -> bool:
    """判断两个语言名是不是同一门语言(用来拦掉「中译中」这类无效方向)。"""
    return b in _SAME_LANGUAGE.get(a, {a})
OCR_LANGS_BY_SOURCE = {'中文': ('zh-Hans-CN', 'zh-CN', 'zh-Hans', 'zh-Hant-TW', 'en-US'), 'English': ('en-US', 'en-GB', 'zh-Hans-CN')}
LANG_INSTRUCTION = {'English': 'Translate the following Limbus Company game UI text into English. Output only the English translation — no explanation, no prefix. Keep proper nouns in their official English spelling.', '简体中文': '把下面的《边狱公司》游戏界面文字翻译成简体中文。只输出简体中文译文,不要任何解释或前缀。', '繁體中文': '把下面的《邊獄公司》遊戲介面文字翻譯成繁體中文。只輸出繁體中文譯文,不要任何解釋或前置。', '日本語': '以下の『辺獄会社』ゲームUIテキストを日本語に翻訳してください。日本語訳だけを出力し、説明や前置きは不要です。', '한국어': '다음 『림버스 컴퍼니』 게임 UI 텍스트를 한국어로 번역하세요. 한국어 번역만 출력하고 설명이나 접두사는 붙이지 마세요.', 'Русский': 'Переведите следующий текст игрового интерфейса Limbus Company на русский язык. Выводите только перевод, без пояснений и префиксов.'}
SOURCE_NAME_FOR_PROMPT = {'中文': '简体中文', 'English': '英文'}
TARGET_NAME_FOR_PROMPT = {'English': '英文', '简体中文': '简体中文', '繁體中文': '繁體中文', '日本語': '日文', '한국어': '韩文', 'Русский': '俄文'}
BASE_SYSTEM_PROMPT = '你是《边狱公司》(Limbus Company)游戏的专业本地化译者,负责把游戏界面上的{source}翻译成{target}。\n严格遵守以下规则:\n1. 只输出{target}译文本身。不要输出解释、说明、前缀、引号、音标或任何括号注释。\n2. 严格使用《边狱公司》官方{target}版的术语。下方若给出术语对照表,其中的译名是强制性的。\n3. 保留原文的换行与条目顺序,一行对一行,不要合并或拆分。\n4. 按钮、技能名、状态名等界面短语要简短,符合游戏 UI 的习惯。\n5. 数字、百分比、符号原样保留,不要改写。\n6. 专有名词(人名、地名、组织名)使用官方译名。\n7. OCR 可能有轻微错字,请结合游戏语境推断最合理的原意,不要逐字硬译。\n8. 已经是{target}的内容原样保留,不要改写,也不要再翻译回去。'
CLEAR_KEY = '#0b0b0d'
# 悬浮窗配色:深底 + 金描边,贴近游戏 UI,又保证译文可读
UI = {
    'panel': '#121018',
    'panel_top': '#1a1520',
    'panel_line': '#3a2e22',
    'gold': '#e8c07a',
    'gold_dim': '#a88955',
    'muted': '#8a8496',
    'text': '#f0ebe3',
    'warn': '#e8a060',
    'accent_btn': '#8b3a3a',
    'accent_btn_active': '#a44848',
    'dialog_bg': '#16131c',
    'dialog_field': '#0e0c12',
    'font': 'Microsoft YaHei UI',
}
COLOR_PRESETS = [('乳白', '#f0ebe3'), ('金色', '#e8c07a'), ('琥珀', '#ffd479'), ('青色', '#7ff0e8'), ('绿色', '#8aff8a'), ('粉色', '#ff9ad5'), ('蓝色', '#7ab8ff'), ('红色', '#ff8a8a')]
FONT_SIZE_PRESETS = [('小 11', 11), ('标准 13', 13), ('大 15', 15), ('更大 18', 18), ('特大 22', 22), ('超大 28', 28)]
WELCOME_TEXT = '边狱公司 · 实时翻译\n\nF2  框选对话 / 技能 / 任务文字\nF4  停止或继续     F6  鼠标穿透\nF7  隐藏窗口       Ctrl+Q  退出\n\n右键可改 API Key、语言、字号与颜色\n'
def welcome_text(cfg: dict) -> str:
    """欢迎文案。把当前翻译方向写在最前面 —— 选错语言 OCR 会一直认不准。"""
    return f"方向  {cfg['source_lang']}  →  {cfg['target_lang']}\n英文界面请把原文设为 English（右键可改）\n\n" + WELCOME_TEXT
CLICK_THROUGH_HINT = '\n穿透已开：点击会落到游戏上，要操作本窗先按 F6\n'
API_KEY_HINT = '\n还没填 API Key：按 F6 关掉穿透 → 右键 → 设置 API Key\n'
ENGINE_FALLBACK_HINT = '\n系统缺少「{source}」OCR 语言包，正用 {tag} 代读\n设置 → 时间和语言 → 该语言选项 → 安装「光学字符识别」\n'
def load_config() -> dict:
    """读取 config.json,缺失或损坏的项回落到默认值。"""
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
                if 'source_lang' not in user:
                    cfg['source_lang'] = '中文'
            else:
                print('[配置] config.json 根节点不是对象,已忽略')
        except Exception as e:
            print(f'[配置] 读取 config.json 失败,使用默认配置: {e}')
    file_key = str(cfg['api_key'] or '')
    env_key = os.environ.get('DEEPSEEK_API_KEY', '') or ''
    cfg['api_key'] = file_key or env_key
    cfg['_key_from_env'] = not file_key and bool(env_key)
    def _num(key, lo, hi, cast):
        try:
            return max(lo, min(hi, cast(cfg[key])))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    cfg['live_interval'] = _num('live_interval', 0.2, 10.0, float)
    cfg['similarity_threshold'] = _num('similarity_threshold', 0.5, 1.0, float)
    cfg['ocr_upscale'] = _num('ocr_upscale', 1.0, 4.0, float)
    cfg['window_opacity'] = _num('window_opacity', 0.15, 1.0, float)
    cfg['font_size'] = _num('font_size', 8, 48, int)
    for key in ['use_glossary', 'use_cache', 'click_through', 'show_source']:
        cfg[key] = bool(cfg[key])
    if cfg['bg_style'] not in ['panel', 'clear']:
        cfg['bg_style'] = DEFAULTS['bg_style']
    if not re.fullmatch('#[0-9a-fA-F]{6}', str(cfg['text_color'])):
        cfg['text_color'] = DEFAULTS['text_color']
    if cfg['source_lang'] not in SOURCE_LANGS:
        cfg['source_lang'] = DEFAULTS['source_lang']
    if cfg['target_lang'] not in TARGET_LANGS:
        cfg['target_lang'] = DEFAULTS['target_lang']
    if same_language(cfg['source_lang'], cfg['target_lang']):
        cfg['source_lang'], cfg['target_lang'] = (DEFAULTS['source_lang'], DEFAULTS['target_lang'])
    for key in ['api_url', 'model', 'extra_prompt']:
        cfg[key] = str(cfg[key] or DEFAULTS[key])
    return cfg
def save_config(cfg: dict) -> None:
    """原子写入 config.json,避免中断时留下损坏的 JSON。"""
    data = {k: v for k, v in cfg.items() if k in DEFAULTS}
    if cfg.get('_key_from_env'):
        data['api_key'] = ''
    tmp = CONFIG_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        print(f'[配置] 保存失败: {e}')
def virtual_screen() -> tuple:
    """返回覆盖所有显示器的虚拟桌面 (left, top, width, height)。"""
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79))
def grab(region: tuple) -> Image.Image:
    """截取屏幕区域 (left, top, width, height),返回 PIL 图像。"""
    left, top, width, height = region
    box = {'left': int(left), 'top': int(top), 'width': max(1, int(width)), 'height': max(1, int(height))}
    with _MSS() as sct:
        raw = sct.grab(box)
    return Image.frombytes('RGB', raw.size, raw.rgb)
_thread_local = threading.local()
def _get_loop() -> asyncio.AbstractEventLoop:
    """每个线程一个常驻事件循环:WinRT 的异步对象绑定创建它的循环,不能跨线程复用。"""
    loop = getattr(_thread_local, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop
    return loop
def _get_engine(source_lang: str='中文'):
    """取当前线程的 OCR 引擎,按源语言各缓存一个。\n\n    OcrEngine 是按语言创建的实例,中英文各需要一个。语言对不上时识别率会明显下降,\n    所以切换翻译方向时要换引擎,不能一直用中文引擎去认英文界面。\n    """
    engines = getattr(_thread_local, 'engines', None)
    if engines is None:
        engines = {}
        _thread_local.engines = engines
    if source_lang in engines:
        return engines[source_lang]
    else:
        engine, tag = (None, None)
        for candidate in OCR_LANGS_BY_SOURCE.get(source_lang, OCR_LANGS_BY_SOURCE['中文']):
            try:
                engine = ocr.OcrEngine.try_create_from_language(globalization.Language(candidate))
            except Exception:
                engine = None
            if engine is not None:
                tag = candidate
                break
        engines[source_lang] = engine
        tags = getattr(_thread_local, 'engine_tags', None)
        if tags is None:
            tags = {}
            _thread_local.engine_tags = tags
        tags[source_lang] = tag
        return engine
def engine_tag(source_lang: str) -> str:
    """当前实际用上的 OCR 引擎语言标签。可能不是首选的那个,见 is_fallback_engine()。"""
    _get_engine(source_lang)
    return getattr(_thread_local, 'engine_tags', {}).get(source_lang) or ''
def is_fallback_engine(source_lang: str) -> bool:
    """首选引擎不可用、退而用别的引擎顶上。\n\n    最典型的情况是系统没装英文 OCR 语言包,于是拿中文引擎去读英文界面 ——\n    能跑,但识别率不如原生引擎。这种情况必须让用户看见,不然他会以为是\n    「这软件英文识别就是不行」,而不知道装个语言包就能好转。\n    """
    preferred = OCR_LANGS_BY_SOURCE.get(source_lang) or ()
    tag = engine_tag(source_lang)
    return bool(tag) and bool(preferred) and (tag != preferred[0])
def preprocess(image: Image.Image, scale: float=2.0) -> Image.Image:
    """OCR 前的图像增强。\n\n    游戏里的中文常常字号很小、白字压在深色背景上,直接识别错误率很高。\n    放大 + 灰度 + 自动对比度拉伸能明显提升识别率,尤其是技能说明这类小字。\n    """
    if scale and scale > 1.0:
            w, h = image.size
            tw, th = (int(w * scale), int(h * scale))
            if tw <= 6000 and th <= 6000:
                    image = image.resize((tw, th), Image.LANCZOS)
    if image.mode != 'L':
        image = image.convert('L')
    return ImageOps.autocontrast(image, cutoff=1)
async def _ocr_async(image: Image.Image, source_lang: str='中文') -> str:
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    stream = streams.InMemoryRandomAccessStream()
    writer = streams.DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(buf.getvalue())
    await writer.store_async()
    decoder = await imaging.BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = _get_engine(source_lang)
    if engine is None:
        raise RuntimeError(f'系统没有可用的 OCR 引擎(需要 {source_lang} 语言包)')
    else:
        result = await engine.recognize_async(bitmap)
        return result.text
def ocr_region(image: Image.Image, scale: float=2.0, source_lang: str='中文') -> str:
    """对图像做 OCR,返回清洗后的文本。失败时返回以 [ 开头的提示串。"""
    try:
        coro = _ocr_async(preprocess(image, scale), source_lang)
        return clean_ocr_text(_get_loop().run_until_complete(coro))
    except Exception as e:
        return f'[OCR 失败: {e}]'
_CJK = '一-鿿㐀-䶿豈-﫿'
_CJK_PUNCT = '　-〿！-･'
def clean_ocr_text(text: str) -> str:
    """把 Windows OCR 的中文输出整理成正常文本。"""
    if not text:
        return ''
    else:
        cjk = f'{_CJK}{_CJK_PUNCT}'
        ascii_punct = ''.join((re.escape(c) for c in ',.;:!?()[]{}<>/\\\\|~-_=+*&^%$#@\\\"\'`'))
        rules = [(re.compile(f'([{cjk}])[ \\t]+(?=[{cjk}])'), '\\1'), (re.compile(f'([{cjk}])[ \\t]+(?=[{ascii_punct}])'), '\\1'), (re.compile(f'([{ascii_punct}])[ \\t]+(?=[{cjk}])'), '\\1')]
        for pattern, repl in rules:
            prev = None
            while prev != text:
                prev = text
                text = pattern.sub(repl, text)
        text = re.sub('[ \\t]{2,}', ' ', text)
        lines = [line.strip() for line in text.splitlines()]
        return '\n'.join((line for line in lines if line))
def has_cjk(text: str) -> bool:
    return bool(re.search(f'[{_CJK}]', text))
def has_latin(text: str) -> bool:
    return bool(re.search('[A-Za-z]', text))
def needs_translation(text: str, source_lang: str, target_lang: str) -> bool:
    """这段文字值不值得送去翻译。\n\n    游戏界面里混着大量不需要翻译的东西:纯数字、符号、分隔线,以及已经是目标语言的\n    文字。这些直接跳过 —— 既省 API 调用,也不会在悬浮窗里堆一堆没用的行。\n    """
    if not normalize_for_compare(text):
        return False
    else:
        if source_lang == 'English':
            if target_lang in ('简体中文', '繁體中文') and has_cjk(text) and (not has_latin(text)):
                return False
            else:
                return has_latin(text)
        else:
            return has_cjk(text)
def normalize_for_compare(text: str) -> str:
    """用于「内容有没有变」比较的归一化:去掉所有空白与常见 OCR 噪声。\n\n    直接比较原始 OCR 文本很容易因为随机空格/标点抖动而误判成「变了」,\n    导致每轮都白调一次 API。归一化后比较,跳过逻辑才真正省得下钱。\n    """
    return re.sub('[\\s·・.,，。、:：;；!！?？\'\\\"“”‘’()（）\\[\\]【】~～\\-—_]+', '', text).lower()
def normalize_for_cache(text: str) -> str:
    """缓存键归一化:只折叠空白并小写,保留标点以免不同句子撞 key。"""
    return re.sub('\\s+', ' ', text).strip().lower()
class TranslationCache:
    """译文缓存:同一句话第二次出现直接命中,不再调 API。\n\n    游戏界面重复度极高(按钮、状态名、常用台词),缓存能省下大部分调用,\n    也让重复出现的文字瞬时显示。\n    """
    MAX_ENTRIES = 8000
    def __init__(self, path: str):
        self.path = path
        self.data = {}
        self._dirty = False
        self._lock = threading.Lock()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = {str(k): str(v) for k, v in loaded.items()}
        except FileNotFoundError:
            return
        except Exception as e:
            print(f'[缓存] 读取失败,从空缓存开始: {e}')
    def get(self, key: str):
        with self._lock:
            return self.data.get(key)
    def put(self, key: str, value: str) -> None:
        with self._lock:
            if len(self.data) >= self.MAX_ENTRIES:
                for old in list(self.data)[:self.MAX_ENTRIES // 4]:
                    del self.data[old]
            self.data[key] = value
            self._dirty = True
    def save(self, force: bool=False) -> None:
        with self._lock:
            if not self._dirty and (not force):
                return
            snapshot = dict(self.data)
            self._dirty = False
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            with self._lock:
                self._dirty = True
            print(f'[缓存] 保存失败: {e}')
    def clear(self) -> None:
        with self._lock:
            n = len(self.data)
            self.data.clear()
            self._dirty = True
        self.save(force=True)
        return n
class _NullCache:
    """空缓存,给「测试连接」这类一次性调用用,不读写任何文件。"""
    def get(self, key):
        return
    def put(self, key, value):
        return
def build_system_prompt(cfg: dict, text: str, glossary: dict) -> str:
    """按翻译方向 + 命中的术语拼出系统提示词。"""
    source, target = (cfg['source_lang'], cfg['target_lang'])
    prompt = BASE_SYSTEM_PROMPT.format(source=SOURCE_NAME_FOR_PROMPT.get(source, source), target=TARGET_NAME_FOR_PROMPT.get(target, target))
    prompt += '\n\n' + LANG_INSTRUCTION.get(target, LANG_INSTRUCTION['English'])
    if cfg['use_glossary']:
        prompt += format_glossary_block(glossary_hits(text, glossary, source))
    extra = cfg['extra_prompt'].strip()
    if extra:
        prompt += f'\n\n【用户附加要求】\n{extra}\n'
    return prompt
def _api_error_text(resp) -> str:
    """把 HTTP 错误翻译成用户看得懂的一句话。"""
    code = resp.status_code
    try:
        detail = resp.json().get('error', {}).get('message', '') or resp.text[:200]
    except Exception:
        detail = resp.text[:200]
    if code == 401:
        return 'API Key 无效或已失效(右键菜单重新设置)'
    else:
        if code == 402:
            return 'DeepSeek 账户余额不足,请充值'
        else:
            if code == 429:
                return '请求过于频繁,稍后自动重试'
            else:
                if code == 400:
                    return f'请求被拒绝: {detail}'
                else:
                    return f'HTTP {code}: {detail}'
def translate(text: str, cfg: dict, glossary: dict, cache: TranslationCache) -> str:
    """调用 DeepSeek 把一段文字翻译成目标语言。返回译文,出错时返回 [ 开头的提示串。"""
    if not text.strip():
        return ''
    api_key = cfg['api_key']
    if not api_key:
        return '[未配置 API Key:右键悬浮窗 → 设置 API Key]'
    cache_key = f"{cfg['source_lang']}->{cfg['target_lang']}|{normalize_for_cache(text)}"
    if cfg['use_cache']:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    payload = {
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': build_system_prompt(cfg, text, glossary)},
            {'role': 'user', 'content': text},
        ],
        'temperature': 0.2,
        'thinking': {'type': 'disabled'},
        'stream': False,
    }
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    last_error = ''
    for attempt in range(3):
        try:
            resp = requests.post(cfg['api_url'], headers=headers, json=payload, timeout=45)
            if resp.status_code == 200:
                data = resp.json()
                content = data['choices'][0]['message']['content'].strip()
                if content:
                    if cfg['use_cache']:
                        cache.put(cache_key, content)
                    return content
                last_error = 'API 返回了空内容'
            else:
                last_error = _api_error_text(resp)
                if resp.status_code in (400, 401, 402):
                    return f'[翻译失败: {last_error}]'
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.Timeout:
            last_error = '请求超时'
        except requests.exceptions.ConnectionError:
            return '[翻译失败: 无法连接网络,请检查网络或代理]'
        except Exception as e:
            last_error = str(e)
        if attempt < 2:
            time.sleep(0.6 * (attempt + 1))
    return f'[翻译失败: {last_error}]'
class Overlay:
    """置顶半透明悬浮窗:显示译文,可拖动、可调大小、可鼠标穿透。"""
    def __init__(self, master: tk.Tk, cfg: dict):
        self.master = master
        self.cfg = cfg
        self.on_menu = None
        self.visible = True
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=self._bg_color())
        font = UI['font']
        # 顶栏:标题 + 翻译方向
        self.header = tk.Frame(self.win, bg=self._header_bg(), height=28)
        self.header.pack(side='top', fill='x')
        self.header.pack_propagate(False)
        self.title_label = tk.Label(self.header, text=APP_NAME, bg=self._header_bg(), fg=UI['gold'], anchor='w', font=(font, 9, 'bold'), padx=12)
        self.title_label.pack(side='left', fill='y')
        self.dir_label = tk.Label(self.header, text='', bg=self._header_bg(), fg=UI['gold_dim'], anchor='e', font=(font, 9), padx=12)
        self.dir_label.pack(side='right', fill='y')
        self.header_rule = tk.Frame(self.win, bg=UI['panel_line'], height=1)
        self.header_rule.pack(side='top', fill='x')
        # 底栏:状态
        self.footer = tk.Frame(self.win, bg=self._header_bg())
        self.footer.pack(side='bottom', fill='x')
        self.footer_rule = tk.Frame(self.footer, bg=UI['panel_line'], height=1)
        self.footer_rule.pack(side='top', fill='x')
        self.status_label = tk.Label(self.footer, text='', bg=self._header_bg(), fg=UI['muted'], anchor='w', font=(font, 9), padx=12, pady=4)
        self.status_label.pack(side='left', fill='both', expand=True)
        self.hint_label = tk.Label(self.footer, text='右键菜单', bg=self._header_bg(), fg=UI['gold_dim'], anchor='e', font=(font, 8), padx=10)
        self.hint_label.pack(side='right', fill='y')
        # 正文
        self.body = tk.Frame(self.win, bg=self._bg_color())
        self.body.pack(side='top', fill='both', expand=True)
        self.text = tk.Text(self.body, wrap='word', bd=0, highlightthickness=0, bg=self._bg_color(), fg=cfg['text_color'], font=(font, cfg['font_size']), padx=14, pady=10, cursor='fleur', spacing1=2, spacing3=4, insertbackground=UI['gold'])
        self.scroll = tk.Scrollbar(self.body, command=self.text.yview, width=8, troughcolor=self._bg_color(), bg=UI['panel_line'], activebackground=UI['gold_dim'], relief='flat', bd=0, highlightthickness=0)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        self.text.tag_configure('src', foreground=UI['muted'], spacing3=2)
        self.text.tag_configure('welcome', foreground=UI['text'], spacing1=3, spacing3=5)
        self.text.tag_configure('sep', foreground=UI['panel_line'])
        self.text.configure(state='disabled')
        self.win.geometry(self._default_geometry())
        self._bind_events()
        self.apply_style(cfg)
    def _bg_color(self) -> str:
        return CLEAR_KEY if self.cfg['bg_style'] == 'clear' else UI['panel']
    def _header_bg(self) -> str:
        return CLEAR_KEY if self.cfg['bg_style'] == 'clear' else UI['panel_top']
    def _default_geometry(self) -> str:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w, h = (min(720, int(sw * 0.34)), 320)
        self.ov_w, self.ov_h = (w, h)
        return f'{w}x{h}+{sw - w - 36}+{sh - h - 72}'
    MIN_W, MIN_H = (260, 130)
    RESIZE_GRIP = 12
    def apply_style(self, cfg: dict) -> None:
        """应用颜色/字号/透明度/背景样式。

        两种背景模式共用同一个透明色 CLEAR_KEY,区别只在窗口背景色取什么:
          面板模式   背景 != CLEAR_KEY -> 底板可见,再由 -alpha 做半透明
          纯文字模式 背景 = CLEAR_KEY  -> 背景像素全透明,只剩文字浮在画面上

        ★ 绝对不要写 attributes("-transparentcolor", "") 去「取消」透明色。
        """
        self.cfg = cfg
        bg = self._bg_color()
        hb = self._header_bg()
        font = UI['font']
        self.win.configure(bg=bg)
        for w in (self.header, self.title_label, self.dir_label, self.footer, self.status_label, self.hint_label, self.body):
            w.configure(bg=hb if w in (self.header, self.title_label, self.dir_label, self.footer, self.status_label, self.hint_label) else bg)
        self.title_label.configure(fg=UI['gold'], font=(font, 9, 'bold'))
        self.dir_label.configure(text=f"{cfg['source_lang']}  →  {cfg['target_lang']}", fg=UI['gold_dim'], font=(font, 9))
        self.status_label.configure(fg=UI['muted'], font=(font, 9))
        self.hint_label.configure(fg=UI['gold_dim'])
        if cfg['bg_style'] == 'clear':
            self.header_rule.configure(bg=CLEAR_KEY)
            self.footer_rule.configure(bg=CLEAR_KEY)
        else:
            self.header_rule.configure(bg=UI['panel_line'])
            self.footer_rule.configure(bg=UI['panel_line'])
        self.text.configure(bg=bg, fg=cfg['text_color'], font=(font, cfg['font_size']))
        self.text.tag_configure('src', foreground=UI['muted'])
        self.scroll.configure(troughcolor=bg, bg=UI['panel_line'], activebackground=UI['gold_dim'])
        self.win.update_idletasks()
        self.win.attributes('-transparentcolor', CLEAR_KEY)
        if cfg['bg_style'] == 'clear':
            self.win.attributes('-alpha', 1.0)
            self.scroll.pack_forget()
            self.header.pack_forget()
            self.header_rule.pack_forget()
            self.hint_label.pack_forget()
        else:
            self.win.attributes('-alpha', cfg['window_opacity'])
            if not self.header.winfo_ismapped():
                self.header.pack(side='top', fill='x', before=self.body)
                self.header_rule.pack(side='top', fill='x', before=self.body)
            if not self.hint_label.winfo_ismapped():
                self.hint_label.pack(side='right', fill='y')
            if not self.scroll.winfo_ismapped():
                self.scroll.pack(side='right', fill='y')
        self.set_click_through(cfg['click_through'])
    def set_status(self, msg: str) -> None:
        self.status_label.configure(text=msg)
    def set_click_through(self, enabled: bool) -> None:
        """鼠标穿透:开启后点击会直接落到下面的游戏窗口上。

        ★ 必须等窗口真正显示出来之后再改扩展样式。
        窗口还没映射就设 WS_EX_TRANSPARENT,实测会让它此后**永远不再绘制** ——
        窗口存在、坐标正确、IsWindowVisible 也是真,但屏幕上什么都没有,
        用户看到的就是「双击了没反应」。启动时 apply_style 正好是在映射前调的,
        所以这个坑每次开机都会踩。
        """
        self._click_through = enabled
        if self.win.winfo_ismapped():
            self._apply_click_through(enabled)
        else:
            self.win.after(150, lambda: self._apply_click_through(self._click_through))
    def _apply_click_through(self, enabled: bool) -> None:
        """真正去改扩展样式。"""
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            get = getattr(u32, 'GetWindowLongPtrW', u32.GetWindowLongW)
            set_ = getattr(u32, 'SetWindowLongPtrW', u32.SetWindowLongW)
            get.restype = ctypes.c_ssize_t
            get.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_.restype = ctypes.c_ssize_t
            set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            GWL_EXSTYLE = (-20)
            WS_EX_LAYERED = 524288
            WS_EX_TRANSPARENT = 32
            WS_EX_NOACTIVATE = 134217728
            style = get(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED
            if enabled:
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            set_(hwnd, GWL_EXSTYLE, style)
        except Exception as e:
            print(f'[悬浮窗] 设置鼠标穿透失败: {e}')
    def clear(self) -> None:
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
    def show_welcome(self, text: str=WELCOME_TEXT) -> None:
        self.clear()
        self.text.configure(state='normal')
        self.text.insert('end', text + '\n', 'welcome')
        self.text.configure(state='disabled')
        self.text.see('1.0')
    def append(self, text: str, tag: str=None) -> None:
        """在末尾追加一段内容(无块分隔)。"""
        self.text.configure(state='normal')
        if self.text.index('end-1c') != '1.0':
            self.text.insert('end', '\n')
        self.text.insert('end', text + '\n', tag or ())
        self.text.configure(state='disabled')
        self.text.see('end')
    def append_block(self, translation: str, source: str=None) -> None:
        """追加一块译文;可选原文对照。块与块之间用细线隔开。"""
        self.text.configure(state='normal')
        if self.text.index('end-1c') != '1.0':
            self.text.insert('end', '────────\n', 'sep')
        if source:
            self.text.insert('end', source + '\n', 'src')
        self.text.insert('end', translation + '\n')
        self.text.configure(state='disabled')
        self.text.see('end')
    def get_text(self) -> str:
        return self.text.get('1.0', 'end-1c')
    def _size(self) -> tuple:
        """取窗口当前宽高。\n\n        不能用 winfo_width(),也不能解析 geometry():窗口尚未绘制完成时,\n        前者返回 1,后者返回 \"1x1+0+0\",都会让自动定位算错。\n        尺寸由 <Configure> 事件同步到 ov_w/ov_h,任何时候都准。\n        """
        return (getattr(self, 'ov_w', 600), getattr(self, 'ov_h', 260))
    def place_below(self, region: tuple) -> None:
        """把悬浮窗放到选区正下方(下方空间不够就放到上方),避免挡住原文。"""
        x, y, w, h = region
        ov_w, ov_h = self._size()
        sw, sh = (self.win.winfo_screenwidth(), self.win.winfo_screenheight())
        top = y + h + 8
        if top + ov_h > sh - 8:
            top = y - ov_h - 8
        if top < 8:
            if x + w + 8 + ov_w <= sw - 8:
                top, x = (max(8, y), x + w + 8)
            else:
                top = max(8, min(sh - ov_h - 8, y + h // 2))
        left = max(8, min(x, sw - ov_w - 8))
        self.win.geometry(f'+{left}+{top}')
        self.win.lift()
    def show(self) -> None:
        self.win.deiconify()
        self.win.attributes('-topmost', True)
        self.win.lift()
        self.visible = True
    def hide(self) -> None:
        self.win.withdraw()
        self.visible = False
    def _bind_events(self) -> None:
        drag_widgets = [self.win, self.text, self.status_label, self.header, self.title_label, self.dir_label, self.footer, self.hint_label]
        for w in drag_widgets:
            w.bind('<Button-3>', self._popup_menu)
            w.bind('<ButtonPress-1>', self._drag_start)
            w.bind('<B1-Motion>', self._drag_move)
            w.bind('<MouseWheel>', self._on_wheel)
        self.win.bind('<Motion>', lambda e: self._on_motion(e.x_root, e.y_root))
        self.win.bind('<Escape>', lambda e: self.hide())
        self.win.bind('<Configure>', self._on_configure)
    def _on_configure(self, event) -> None:
        if event.widget is self.win and event.width > 1 and (event.height > 1):
                    self.ov_w, self.ov_h = (event.width, event.height)
    def _popup_menu(self, event):
        if self.on_menu:
            self.on_menu(event)
    def _edge_at(self, mx: int, my: int) -> str:
        """判断鼠标压在哪个边缘上:se / e / s,不在边缘返回空串。\n\n        参数是相对窗口左上角的坐标,不是相对触发事件的控件 —— 文本框四周还有\n        滚动条和状态栏,用控件内坐标判断边缘会一直判错。\n        """
        w, h = self._size()
        right = mx >= w - self.RESIZE_GRIP
        bottom = my >= h - self.RESIZE_GRIP
        if right and bottom:
            return 'se'
        else:
            if right:
                return 'e'
            else:
                if bottom:
                    return 's'
                else:
                    return ''
    def _drag_start(self, event):
        mx = event.x_root - self.win.winfo_rootx()
        my = event.y_root - self.win.winfo_rooty()
        edge = self._edge_at(mx, my)
        if edge:
            self._resize = edge
        else:
            self._resize = None
            self._drag = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())
    def _drag_move(self, event):
        if getattr(self, '_resize', None):
            mx = event.x_root - self.win.winfo_rootx()
            my = event.y_root - self.win.winfo_rooty()
            w, h = self._size()
            if 'e' in self._resize:
                w = max(self.MIN_W, mx)
            if 's' in self._resize:
                h = max(self.MIN_H, my)
            self.ov_w, self.ov_h = (w, h)
            self.win.geometry(f'{w}x{h}')
            return
        else:
            if not hasattr(self, '_drag'):
                return
            else:
                dx, dy = self._drag
                self.win.geometry(f'+{event.x_root - dx}+{event.y_root - dy}')
    def _on_motion(self, x_root: int, y_root: int) -> None:
        """鼠标移到边缘时把光标变成缩放样式,提示这里可以拖大小。"""
        mx = x_root - self.win.winfo_rootx()
        my = y_root - self.win.winfo_rooty()
        cursor = {'se': 'size_nw_se', 'e': 'size_we', 's': 'size_ns'}.get(self._edge_at(mx, my), 'fleur')
        for widget in [self.win, self.text, self.status_label, self.header, self.title_label, self.dir_label, self.footer, self.hint_label]:
            try:
                widget.configure(cursor=cursor)
            except Exception:
                pass
    def _on_wheel(self, event):
        self.text.yview_scroll((-1) if event.delta > 0 else 1, 'units')
class RegionSelector:
    """全屏变暗的框选层,拖拽选择区域。覆盖所有显示器。"""
    def __init__(self, master: tk.Tk, on_done):
        self.on_done = on_done
        self.start = None
        vx, vy, vw, vh = virtual_screen()
        self.vx, self.vy = (vx, vy)
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.attributes('-alpha', 0.38)
        self.win.configure(bg='#0a080c', cursor='crosshair')
        self.win.geometry(f'{vw}x{vh}{vx:+d}{vy:+d}')
        self.canvas = tk.Canvas(self.win, bg='#0a080c', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        font = UI['font']
        self.hint = self.canvas.create_text(vw // 2, 56, fill=UI['gold'], font=(font, 18, 'bold'), text='拖拽框选要翻译的文字')
        self.subhint = self.canvas.create_text(vw // 2, 92, fill=UI['muted'], font=(font, 12), text='松手开始翻译    ·    Esc 取消')
        self.size_label = self.canvas.create_text(vw // 2, vh - 48, fill=UI['gold_dim'], font=(font, 11), text='')
        self.rect = None
        self.fill = None
        for widget in [self.win, self.canvas]:
            widget.bind('<ButtonPress-1>', self._press)
            widget.bind('<B1-Motion>', self._motion)
            widget.bind('<ButtonRelease-1>', self._release)
        self.win.bind('<Escape>', self._cancel)
        self.win.focus_force()
        self.win.grab_set()
    def _press(self, event):
        self.start = (event.x_root, event.y_root)
        x, y = (event.x_root - self.vx, event.y_root - self.vy)
        if self.fill is None:
            self.fill = self.canvas.create_rectangle(x, y, x, y, outline='', fill='#e8c07a', stipple='gray50')
        if self.rect is None:
            self.rect = self.canvas.create_rectangle(x, y, x, y, outline=UI['gold'], width=2)
        else:
            self.canvas.coords(self.rect, x, y, x, y)
            self.canvas.coords(self.fill, x, y, x, y)
    def _motion(self, event):
        if self.start and self.rect is not None:
            x1, y1 = (self.start[0] - self.vx, self.start[1] - self.vy)
            x2, y2 = (event.x_root - self.vx, event.y_root - self.vy)
            self.canvas.coords(self.rect, x1, y1, x2, y2)
            if self.fill is not None:
                self.canvas.coords(self.fill, x1, y1, x2, y2)
            w, h = (abs(int(x2 - x1)), abs(int(y2 - y1)))
            self.canvas.itemconfigure(self.size_label, text=f'{w} × {h}')
    def _release(self, event):
        if not self.start:
            return
        else:
            x1, y1 = self.start
            x2, y2 = (event.x_root, event.y_root)
            left, top = (min(x1, x2), min(y1, y2))
            width, height = (abs(x2 - x1), abs(y2 - y1))
            self._close()
            if width < 12 or height < 12:
                self.on_done(None)
            else:
                self.on_done((left, top, width, height))
    def _cancel(self, event=None):
        self._close()
        self.on_done(None)
    def _close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()
class LimbusTranslator:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        ensure_shared_glossary(GLOSSARY_SHARED)
        self.glossary = load_glossary(GLOSSARY_SHARED, self.cfg['source_lang'], GLOSSARY_LOCAL)
        self._glossary_mtime = glossary_mtime(GLOSSARY_SHARED, GLOSSARY_LOCAL)
        self.cache = TranslationCache(CACHE_FILE)
        self.overlay = Overlay(root, self.cfg)
        self.overlay.on_menu = self.show_menu
        self.live_stop = None
        self.live_region = None
        self.live_thread = None
        self._live_gen = 0
        self._workers = []
        self._shutting_down = False
        self._periodic_after_id = None
        self._glossary_after_id = None
        self.last_region = None
        self.last_source = ''
        self.busy = False
        self.ocr_only = False
        self._last_status = ''
        self._bind_hotkeys()
        text = welcome_text(self.cfg)
        if self.cfg['click_through']:
            text += CLICK_THROUGH_HINT
        if not self.cfg['api_key']:
            text += API_KEY_HINT
        if is_fallback_engine(self.cfg['source_lang']):
            text += ENGINE_FALLBACK_HINT.format(source=self.cfg['source_lang'], tag=engine_tag(self.cfg['source_lang']))
        self.overlay.show_welcome(text)
        self.status(f"就绪 · {self.cfg['source_lang']}→{self.cfg['target_lang']} · 按 F2 框选区域")
        save_config(self.cfg)
        self._periodic_after_id = self.root.after(30000, self._periodic_cache_save)
        self._glossary_after_id = self.root.after(2000, self._glossary_watch)
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
    def _bind_hotkeys(self) -> None:
        try:
            import keyboard
            self.keyboard = keyboard
        except Exception as e:
            self.keyboard = None
            print(f'[热键] keyboard 库不可用,全局热键已禁用: {e}')
            return
        bindings = {
            'f2': self.start_capture,
            'f3': lambda: self.translate_region(None, single=True),
            'f4': self.on_f4,
            'f6': self.toggle_click_through,
            'f7': self.toggle_overlay_visible,
            'ctrl+s': self.copy_result,
            'ctrl+e': self.clear_output,
            'ctrl+q': self.quit,
        }
        for combo, handler in bindings.items():
            try:
                self.keyboard.add_hotkey(combo, lambda h=handler: self.root.after(0, h))
            except Exception as e:
                print(f'[热键] 注册 {combo} 失败: {e}')
    def _spawn_worker(self, target, args=(), name: str='') -> threading.Thread:
        """启动可追踪的后台线程,退出时一并等待/作废。"""
        self._workers = [t for t in self._workers if t.is_alive()]
        t = threading.Thread(target=target, args=args, daemon=True, name=name or 'worker')
        self._workers.append(t)
        t.start()
        return t
    def status(self, msg: str) -> None:
        """在悬浮窗底部状态栏显示一句话。重复内容不刷新,避免实时循环里闪烁。

        可从任意线程调用:后台线程会经 root.after 切回 Tk 主线程再改 UI。
        """
        if self._shutting_down:
            return
        try:
            if threading.current_thread() is not threading.main_thread():
                self.root.after(0, lambda m=msg: self.status(m))
                return
        except RuntimeError:
            return
        if msg == self._last_status:
            return
        self._last_status = msg
        self.overlay.set_status(msg)
        print(f'[状态] {msg}')
    def start_capture(self) -> None:
        if self.live_stop is not None:
            self.status('实时翻译进行中,先按 F4 停止')
            return
        if self.busy:
            self.status('正在处理,请稍候')
            return
        self.busy = True
        self.overlay.hide()
        RegionSelector(self.root, on_done=self._on_region_selected)
    def _on_region_selected(self, region) -> None:
        self.busy = False
        self.overlay.show()
        if not region:
            self.status('已取消框选')
            return
        self.last_region = region
        self.overlay.place_below(region)
        self.start_live(region)
    def start_live(self, region: tuple) -> None:
        """进入实时翻译:后台线程每 interval 秒截一次图,内容变了才翻译。"""
        if self.live_stop is not None:
            return
        self._stop_live_worker(join=True, announce=False)
        self.live_region = region
        self.live_stop = threading.Event()
        self._live_gen += 1
        gen = self._live_gen
        self.overlay.clear()
        self.overlay.show()
        self.status(f'实时翻译中 · 区域 {region[2]}×{region[3]} · F4 停止')
        t = threading.Thread(target=self._live_loop, args=(region, self.live_stop, gen), daemon=True, name='live-loop')
        self.live_thread = t
        t.start()
    def _live_loop(self, region: tuple, stop: threading.Event, gen: int) -> None:
        """实时循环:截图 → OCR → 判断内容是否变化 → 翻译 → 追加显示。"""
        last_norm = ''
        upscale = self.effective_upscale(region)
        source = self.cfg['source_lang']
        while (not self._shutting_down) and (not stop.is_set()) and gen == self._live_gen:
            try:
                text = ocr_region(grab(region), upscale, source)
                if stop.is_set() or gen != self._live_gen:
                    break
                norm = normalize_for_compare(text)
                if text.startswith('['):
                    self.status(f'OCR 出错,检查系统{source}语言包')
                elif not norm:
                    self.status('本区域没识别到文字')
                elif not needs_translation(text, source, self.cfg['target_lang']):
                    self.status(f'本区域没有{source}内容,无需翻译')
                elif norm == last_norm:
                    pass
                elif last_norm and difflib.SequenceMatcher(None, last_norm, norm).ratio() >= self.cfg['similarity_threshold']:
                    pass
                else:
                    last_norm = norm
                    self._emit(text, stop, gen)
            except Exception as e:
                print(f'[实时] 本轮失败: {e}')
            stop.wait(self.cfg['live_interval'])
    def _emit(self, text: str, stop: threading.Event=None, gen: int=None) -> None:
        """把一轮识别结果翻译并追加到悬浮窗。"""
        if self._shutting_down:
            return
        if gen is not None and gen != self._live_gen:
            return
        if self.ocr_only:
            if not stop or not stop.is_set():
                self.root.after(0, lambda t=text: self._append_block(t, None))
            return
        if stop is not None and stop.is_set():
            return
        translation = translate(text, self.cfg, self.glossary, self.cache)
        if gen is not None and gen != self._live_gen:
            return
        if stop is not None and stop.is_set():
            return
        if not translation:
            return
        if translation.startswith('['):
            self.status(translation)
            return
        self.last_source = text
        self.root.after(0, lambda s=text, t=translation: self._append_block(t, s))
    def _append_block(self, translation: str, source) -> None:
        if self._shutting_down:
            return
        if translation.startswith('['):
            self.status(translation)
            return
        src = source if (self.cfg['show_source'] and source) else None
        self.overlay.append_block(translation, src)
        self.overlay.show()
    def _stop_live_worker(self, join: bool=True, announce: bool=False) -> None:
        """停掉当前实时线程并作废 generation,避免旧线程与新循环叠跑。"""
        if self.live_stop is not None:
            self.live_stop.set()
        self._live_gen += 1
        t = self.live_thread
        self.live_stop = None
        self.live_region = None
        self.live_thread = None
        if join and t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        if announce and not self._shutting_down:
            self.status('已停止实时翻译 · F4 重新开始')
    def stop_live(self) -> None:
        if self.live_stop is None and self.live_thread is None:
            return
        self._stop_live_worker(join=True, announce=True)
    def on_f4(self) -> None:
        if self.live_stop is not None:
            self.stop_live()
        else:
            if self.last_region:
                self.start_live(self.last_region)
            else:
                self.status('还没有框选过区域,先按 F2')
    def effective_upscale(self, region: tuple) -> float:
        """按区域大小决定 OCR 放大倍数。\n\n        小区域(对话框、技能说明)放大能显著提升小字识别率;整屏再放大 2 倍会变成\n        5000×3000 的图像,一轮 OCR 要好几秒,实时循环根本跟不上,所以自动降到 1 倍。\n        """
        return 1.0 if region[2] * region[3] > 2073600 else self.cfg['ocr_upscale']
    def translate_region(self, region, single: bool=False) -> None:
        """单次翻译整屏或指定区域(不进入循环)。"""
        if self.live_stop is not None:
            self.status('实时翻译进行中,先按 F4 停止')
            return
        if self.busy:
            self.status('正在处理,请稍候')
            return
        self.busy = True
        target = region or virtual_screen()
        self.last_region = target
        self.status('正在识别并翻译...')
        upscale = self.effective_upscale(target)
        source = self.cfg['source_lang']

        def worker():
            try:
                if self._shutting_down:
                    return
                image = grab(target)
                text = ocr_region(image, upscale, source)
                if self._shutting_down:
                    return
                if text.startswith('['):
                    self.root.after(0, lambda t=text: self.status(t))
                    return
                if not normalize_for_compare(text):
                    self.root.after(0, lambda: self.status('没识别到文字'))
                    return
                self.last_source = text
                if self.ocr_only or not needs_translation(text, source, self.cfg['target_lang']):
                    self.root.after(0, lambda t=text: (self.overlay.clear(), self._append_block(t, None)))
                    return
                translation = translate(text, self.cfg, self.glossary, self.cache)
                if self._shutting_down:
                    return
                if not translation or translation.startswith('['):
                    self.root.after(0, lambda t=translation or '[翻译失败]': self.status(t))
                    return
                self.root.after(0, lambda: self.overlay.clear())
                self.root.after(0, lambda s=text, t=translation: self._append_block(t, s))
                self.root.after(0, lambda: self.status('翻译完成'))
            except Exception as e:
                if not self._shutting_down:
                    self.root.after(0, lambda err=str(e): self.status(f'失败: {err}'))
            finally:
                self.busy = False

        self._spawn_worker(worker, name='translate-once')
        self.overlay.show()
    def toggle_click_through(self) -> None:
        self.cfg['click_through'] = not self.cfg['click_through']
        self.overlay.set_click_through(self.cfg['click_through'])
        save_config(self.cfg)
        state = '已开启(点击穿透到游戏)' if self.cfg['click_through'] else '已关闭(可拖动窗口)'
        self.status(f'鼠标穿透{state}')
    def copy_result(self) -> None:
        text = self.overlay.get_text().strip()
        if not text:
            self.status('没有可复制的内容')
            return
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status('译文已复制到剪贴板')
    def clear_output(self) -> None:
        self.overlay.clear()
        self.status('已清空输出')
    def hide_overlay(self) -> None:
        if self.live_stop is not None:
            self.stop_live()
        self.overlay.hide()
    def toggle_overlay_visible(self) -> None:
        """F7:隐藏 / 显示悬浮窗。隐藏时会一并停掉实时翻译。"""
        if self.overlay.visible:
            self.hide_overlay()
        else:
            self.overlay.show()
            self.status('悬浮窗已显示')
    def toggle_ocr_only(self) -> None:
        self.ocr_only = not self.ocr_only
        self.status('只看原文模式:' + ('开' if self.ocr_only else '关'))
    @staticmethod
    def _mark(label: str, checked: bool) -> str:
        """菜单项前的勾选标记。\n\n        不用 add_checkbutton/add_radiobutton:它们需要一个长期存活的 Tk 变量,\n        菜单每次重建、变量用完即弃,状态显示并不可靠。直接画勾更简单也更准。\n        """
        if checked:
            return '✓ ' + label
        else:
            return '　 ' + label
    def show_menu(self, event) -> None:
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label='设置 API Key...', command=self.dialog_api_key)
        m.add_command(label='测试翻译连接', command=self.test_connection)
        source_menu = tk.Menu(m, tearoff=0)
        for lang in SOURCE_LANGS:
            source_menu.add_command(label=self._mark(lang, lang == self.cfg['source_lang']), command=lambda l=lang: self.set_source_lang(l))
        m.add_cascade(label=f"界面原文语言 · {self.cfg['source_lang']}", menu=source_menu)
        lang_menu = tk.Menu(m, tearoff=0)
        for lang in TARGET_LANGS:
            lang_menu.add_command(label=self._mark(lang, lang == self.cfg['target_lang']), command=lambda l=lang: self.set_lang(l))
        m.add_cascade(label=f"译文语言 · {self.cfg['target_lang']}", menu=lang_menu)
        m.add_command(label=f"　当前方向:{self.cfg['source_lang']} → {self.cfg['target_lang']}", state='disabled')
        m.add_separator()
        m.add_command(label=self._mark('带原文对照', self.cfg['show_source']), command=self.toggle_source)
        m.add_command(label=self._mark('只看原文(不翻译)', self.ocr_only), command=self.toggle_ocr_only)
        m.add_command(label=self._mark('使用术语表', self.cfg['use_glossary']), command=self.toggle_glossary)
        m.add_command(label='附加翻译要求...', command=self.dialog_extra_prompt)
        m.add_command(label=self._mark('鼠标穿透', self.cfg['click_through']), command=self.toggle_click_through)
        m.add_separator()
        style_menu = tk.Menu(m, tearoff=0)
        for value, name in [('panel', '半透明底板'), ('clear', '纯文字(无底板)')]:
            style_menu.add_command(label=self._mark(name, self.cfg['bg_style'] == value), command=lambda v=value: self.set_style(v))
        m.add_cascade(label='背景样式', menu=style_menu)
        color_menu = tk.Menu(m, tearoff=0)
        for name, value in COLOR_PRESETS:
            color_menu.add_command(label=self._mark(name, self.cfg['text_color'] == value), command=lambda v=value: self.set_color(v))
        m.add_cascade(label='译文颜色', menu=color_menu)
        size_menu = tk.Menu(m, tearoff=0)
        for name, value in FONT_SIZE_PRESETS:
            size_menu.add_command(label=self._mark(name, self.cfg['font_size'] == value), command=lambda v=value: self.set_font_size(v))
        m.add_cascade(label='字号', menu=size_menu)
        opacity_menu = tk.Menu(m, tearoff=0)
        for pct in [30, 45, 60, 72, 85, 100]:
            opacity_menu.add_command(label=self._mark(f'{pct}%', abs(self.cfg['window_opacity'] - pct / 100) < 0.01), command=lambda p=pct: self.set_opacity(p / 100))
        m.add_cascade(label='底板透明度', menu=opacity_menu)
        m.add_separator()
        m.add_command(label='打开共享术语表 (glossary/terms.txt)', command=self.open_glossary_file)
        m.add_command(label='打开本地覆盖 (glossary.local.txt)', command=self.open_local_glossary_file)
        m.add_command(label='清空译文缓存', command=self.clear_cache)
        m.add_command(label=self._mark('显示悬浮窗 (F7)', self.overlay.visible), command=self.toggle_overlay_visible)
        m.add_separator()
        m.add_command(label='退出 (Ctrl+Q)', command=self.quit)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
    def _apply(self) -> None:
        save_config(self.cfg)
        self.overlay.apply_style(self.cfg)
    def set_lang(self, lang: str) -> None:
        """切换译文语言。和原文撞车时自动把原文挪到另一边,不会变成「中译中」。"""
        if same_language(self.cfg['source_lang'], lang):
            self.cfg['source_lang'] = 'English' if self.cfg['source_lang'] == '中文' else '中文'
            self.reload_glossary()
        self.cfg['target_lang'] = lang
        self._apply()
        self.status(f"翻译方向:{self.cfg['source_lang']} → {self.cfg['target_lang']}")
    def set_source_lang(self, lang: str) -> None:
        """切换界面原文语言。\n\n        这一步同时换两样东西:OCR 引擎(中文用 zh-Hans-CN、英文用 en-US)和术语表方向。\n        引擎按语言创建,不换的话用中文引擎认英文界面识别率会掉。\n        """
        if same_language(lang, self.cfg['target_lang']):
            self.status(f'原文和译文不能同时是「{lang}」,请先换译文语言')
            return
        else:
            self.cfg['source_lang'] = lang
            self.reload_glossary()
            self._apply()
            self.status(f"翻译方向:{lang} → {self.cfg['target_lang']}")
            self._restart_live_if_running()
    def _restart_live_if_running(self) -> None:
        """重启实时循环让新设置生效。\n\n        循环线程在开头就把源语言读进了局部变量,不重启的话,\n        换完语言旧线程还在用旧引擎认字,要一直等到下次框选才对。\n        """
        if self.live_stop is None:
            return
        region = self.live_region
        self._stop_live_worker(join=True, announce=False)
        if region:
            self.start_live(region)
    def toggle_source(self) -> None:
        self.cfg['show_source'] = not self.cfg['show_source']
        self._apply()
        self.status('原文对照:' + ('开' if self.cfg['show_source'] else '关'))
    def toggle_glossary(self) -> None:
        self.cfg['use_glossary'] = not self.cfg['use_glossary']
        self._apply()
        self.status('术语表:' + ('开' if self.cfg['use_glossary'] else '关'))
    def set_style(self, style: str) -> None:
        self.cfg['bg_style'] = style
        self._apply()
        self.status('背景样式:' + ('半透明底板' if style == 'panel' else '纯文字'))
    def set_color(self, color: str) -> None:
        self.cfg['text_color'] = color
        self._apply()
    def set_font_size(self, size: int) -> None:
        self.cfg['font_size'] = int(size)
        self._apply()
    def set_opacity(self, opacity: float) -> None:
        self.cfg['window_opacity'] = float(opacity)
        self._apply()
    def clear_cache(self) -> None:
        n = self.cache.clear()
        self.status(f'缓存已清空({n} 条被删除)')
    def open_glossary_file(self) -> None:
        """打开 git 共享术语表 glossary/terms.txt。"""
        try:
            ensure_shared_glossary(GLOSSARY_SHARED)
            os.startfile(GLOSSARY_SHARED)
            self.status('已打开共享术语表 · 保存后自动重载')
        except Exception as e:
            self.status(f'打开失败: {e}')
    def open_local_glossary_file(self) -> None:
        """打开本地覆盖表;不存在则创建模板。此文件不进 git。"""
        if not os.path.exists(GLOSSARY_LOCAL):
            try:
                with open(GLOSSARY_LOCAL, 'w', encoding='utf-8') as f:
                    f.write(
                        '# 本地术语覆盖（不进 git）\n'
                        '# 格式同 glossary/terms.txt: 中文=English 或 English=>中文\n'
                        '# 这里的条目会覆盖共享表\n'
                        '#\n'
                        '# 边狱巴士=Mephistopheles\n'
                    )
            except Exception as e:
                self.status(f'创建本地术语表失败: {e}')
                return
        try:
            os.startfile(GLOSSARY_LOCAL)
            self.status('已打开本地覆盖表 · 保存后自动重载')
        except Exception as e:
            self.status(f'打开失败: {e}')
    def reload_glossary(self, announce: bool = False) -> None:
        self.glossary = load_glossary(GLOSSARY_SHARED, self.cfg['source_lang'], GLOSSARY_LOCAL)
        self._glossary_mtime = glossary_mtime(GLOSSARY_SHARED, GLOSSARY_LOCAL)
        if announce:
            self.status(f'术语表已重载 · {len(self.glossary)} 条')
    def _maybe_reload_glossary(self) -> None:
        """文件有改动则热加载,编辑 terms.txt 后无需重启。"""
        mtime = glossary_mtime(GLOSSARY_SHARED, GLOSSARY_LOCAL)
        if mtime > self._glossary_mtime:
            self.reload_glossary(announce=True)
    def dialog_api_key(self) -> None:
        self._text_dialog('设置 API Key', '到 platform.deepseek.com 创建 API Key,粘贴到这里:\n(保存在同目录 config.json,不会上传到别处)', self.cfg['api_key'], self._save_api_key, secret=True)
    def _save_api_key(self, value: str) -> None:
        self.cfg['api_key'] = value.strip()
        self._apply()
        if self.cfg['api_key']:
            self.status('API Key 已保存,正在测试连接...')
            self.test_connection()
        else:
            self.status('已清空 API Key')
    def dialog_extra_prompt(self) -> None:
        self._text_dialog('附加翻译要求', '追加给模型的额外要求,例如:\n「译文保持简洁,不要超过原文长度」「人名保留中文」等', self.cfg['extra_prompt'], self._save_extra_prompt)
    def _save_extra_prompt(self, value: str) -> None:
        self.cfg['extra_prompt'] = value.strip()
        self._apply()
        self.status('附加要求已保存')
    def _text_dialog(self, title: str, hint: str, initial: str, on_ok, secret: bool=False) -> None:
        """通用输入对话框。\n\n        secret=True 用于密钥:单行 Entry + 掩码,可勾选「显示」核对粘贴对不对。\n        secret=False 用于提示词:多行 Text。\n\n        ★ 按钮栏必须 **先** pack(side=\"bottom\") 再放输入控件。\n        反过来的话输入控件(尤其带 expand=True 的 Text)会把竖直空间吃光,\n        按钮被挤成 0 高度 —— 用户看到的就是「对话框没有保存按钮」。\n        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.attributes('-topmost', True)
        bg, field, font = (UI['dialog_bg'], UI['dialog_field'], UI['font'])
        dlg.configure(bg=bg)
        dlg.geometry('560x260' if secret else '560x300')
        tk.Label(dlg, text=title, bg=bg, fg=UI['gold'], justify='left', anchor='w', font=(font, 12, 'bold')).pack(fill='x', padx=16, pady=(14, 2))
        tk.Label(dlg, text=hint, bg=bg, fg=UI['muted'], justify='left', anchor='w', wraplength=520, font=(font, 10)).pack(fill='x', padx=16, pady=(0, 10))
        bar = tk.Frame(dlg, bg=bg)
        bar.pack(side='bottom', fill='x', padx=16, pady=14)
        def value() -> str:
            if isinstance(entry, tk.Text):
                return entry.get('1.0', 'end-1c')
            return entry.get()
        def ok():
            on_ok(value())
            dlg.destroy()
        tk.Button(bar, text='保存', command=ok, width=10, relief='flat', bg=UI['accent_btn'], fg='white', activebackground=UI['accent_btn_active'], font=(font, 10)).pack(side='right')
        tk.Button(bar, text='取消', command=dlg.destroy, width=8, relief='flat', bg='#2a2430', fg=UI['text'], activebackground='#3a3340', font=(font, 10)).pack(side='right', padx=(0, 8))
        if secret:
            entry = tk.Entry(dlg, bg=field, fg=UI['text'], insertbackground=UI['gold'], relief='flat', font=('Consolas', 12), show='●')
            entry.pack(fill='x', padx=16, ipady=7)
            entry.insert(0, initial)
            def toggle_mask():
                entry.configure(show='' if show_var.get() else '●')
            show_var = tk.BooleanVar(value=False)
            tk.Checkbutton(dlg, text='显示密钥', variable=show_var, command=toggle_mask, bg=bg, fg=UI['muted'], selectcolor=field, activebackground=bg, activeforeground=UI['text'], font=(font, 10)).pack(anchor='w', padx=14, pady=(8, 0))
        else:
            entry = tk.Text(dlg, height=6, bg=field, fg=UI['text'], insertbackground=UI['gold'], relief='flat', font=(font, 11), wrap='word')
            entry.pack(fill='both', expand=True, padx=16)
            entry.insert('1.0', initial)
        entry.focus_set()
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        dlg.bind('<Control-Return>', lambda e: ok())
    def test_connection(self) -> None:
        """用一句固定的短文本试一次翻译,验证 Key / 网络 / 模型名是否都正常。"""
        if not self.cfg['api_key']:
            self.status('还没设置 API Key')
            return
        self.status('正在测试连接...')
        probe = '确定' if self.cfg['source_lang'] == '中文' else 'Confirm'
        cfg = dict(self.cfg, use_cache=False)

        def worker():
            if self._shutting_down:
                return
            msg = translate(probe, cfg, self.glossary, _NullCache())
            if self._shutting_down:
                return
            ok = not msg.startswith('[')
            try:
                self.root.after(0, lambda: self.status(f'连接正常 ·「{probe}」→「{msg}」' if ok else msg))
            except (RuntimeError, tk.TclError):
                return

        self._spawn_worker(worker, name='test-connection')
    def _periodic_cache_save(self) -> None:
        if self._shutting_down:
            return
        self.cache.save()
        self._maybe_reload_glossary()
        try:
            self._periodic_after_id = self.root.after(30000, self._periodic_cache_save)
        except tk.TclError:
            self._periodic_after_id = None
    def _glossary_watch(self) -> None:
        if self._shutting_down:
            return
        self._maybe_reload_glossary()
        try:
            self._glossary_after_id = self.root.after(2000, self._glossary_watch)
        except tk.TclError:
            self._glossary_after_id = None
    def quit(self) -> None:
        """完整退出:停后台、卸热键、存配置,并强制结束进程。

        keyboard 库的全局钩子在 Windows 上常会留下非 daemon 监听线程,
        仅 destroy 主窗口后进程仍会挂着。清理完后用 os._exit 确保无残留。
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._periodic_after_id is not None:
            try:
                self.root.after_cancel(self._periodic_after_id)
            except Exception:
                pass
            self._periodic_after_id = None
        if self._glossary_after_id is not None:
            try:
                self.root.after_cancel(self._glossary_after_id)
            except Exception:
                pass
            self._glossary_after_id = None
        self._stop_live_worker(join=True, announce=False)
        for t in list(self._workers):
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.5)
        self._workers.clear()
        try:
            self.cache.save(force=True)
        except Exception:
            pass
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self.keyboard:
            try:
                self.keyboard.unhook_all()
            except Exception:
                pass
            try:
                import keyboard as _kb
                hooks = getattr(_kb, '_hooks', None)
                if isinstance(hooks, dict):
                    hooks.clear()
            except Exception:
                pass
            self.keyboard = None
        try:
            self.overlay.win.destroy()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        # keyboard 钩子线程在 Windows 上经常阻止正常退出,强制结束进程以免残留
        os._exit(0)
class _NullStream:
    """打包成 --noconsole 的 exe 后,sys.stdout / sys.stderr 是 None。\n\n    此时任何 print() 都会抛 AttributeError。程序里到处用 print 打日志,\n    包括各个 except 分支 —— 真出问题时反而会因为「打印日志」这个动作再炸一次,\n    把原始错误盖掉。所以启动时先把它们换成能吞掉写入的替身。\n    """
    def write(self, *args):
        return
    def flush(self):
        return
    def isatty(self):
        return False
def selftest() -> int:
    """自检:确认 OCR 引擎、图像预处理、术语表、配置读写都正常。

    打包成 --noconsole 的 exe 后看不到任何输出,所以结果会同时写到
    程序目录下的 selftest.txt —— 出问题时让用户跑 LimbusTranslator.exe --selftest
    再把那个文件发过来,比反复问「哪一步不行」快得多。
    """
    lines = []

    def say(msg=''):
        lines.append(msg)
        print(msg)

    say(f'{APP_NAME} 自检')
    say(f'  程序目录: {APP_DIR}')
    say(f"  运行方式: {('打包的 exe' if getattr(sys, 'frozen', False) else 'Python 源码')}")
    say(f'  Python  : {sys.version.split()[0]}')
    say('\n[1] OCR 引擎')
    ok_ocr = {}
    preferred = {s: (OCR_LANGS_BY_SOURCE.get(s) or ('?',))[0] for s in SOURCE_LANGS}
    say(f"  首选: 中文 -> {preferred['中文']}    English -> {preferred['English']}")
    for source in SOURCE_LANGS:
        engine = _get_engine(source)
        tag = engine_tag(source)
        if engine is None:
            ok_ocr[source] = False
            say(f'  {source:8} 不可用 —— 系统缺少可用的 OCR 语言包')
            continue
        ok_ocr[source] = True
        note = ''
        if is_fallback_engine(source):
            note = f'  ← 首选 {preferred[source]} 未安装,退用 {tag} 代读,识别率会打折'
        say(f'  {source:8} OK (引擎 {tag}){note}')
    say('\n[2] 图像预处理 + OCR 往返测试')
    samples = {'中文': '囚徒的人格已经觉醒。', 'English': 'The Sinner has awakened.'}
    ok_round = {}
    for source, text in samples.items():
        if not ok_ocr.get(source):
            ok_round[source] = False
            say(f'  {source:8} 跳过(引擎不可用)')
            continue
        try:
            img = _render_test_image(text)
            got = clean_ocr_text(ocr_region(img, scale=2.0, source_lang=source))
        except Exception as e:
            ok_round[source] = False
            say(f'  {source:8} 失败: {type(e).__name__}: {e}')
        else:
            hit = sum((1 for ch in text if ch in got)) / max(1, len(text))
            ok_round[source] = hit >= 0.6
            say(f'  {source:8} 期望 {text!r}')
            say(f"  {'':8} 实得 {got!r}  命中率 {hit:.0%}")
    say('\n[3] 术语表')
    for source in SOURCE_LANGS:
        g = load_glossary(GLOSSARY_SHARED, source, GLOSSARY_LOCAL)
        bits = []
        if os.path.exists(GLOSSARY_SHARED):
            bits.append('共享 terms.txt')
        if os.path.exists(GLOSSARY_LOCAL):
            bits.append('本地覆盖')
        say(f'  {source:8} {len(g)} 条({"+".join(bits) or "兜底"})')
    say('\n[4] 配置读写')
    try:
        cfg = load_config()
        say(f"  方向: {cfg['source_lang']} → {cfg['target_lang']}")
        say(f"  API Key: {('已配置' if cfg['api_key'] else '未配置')}")
        save_config(cfg)
        say(f'  {CONFIG_FILE} 可写')
        ok_cfg = True
    except Exception as e:
        ok_cfg = False
        say(f'  配置读写失败: {e}')
    failed = [s for s in SOURCE_LANGS if not ok_round.get(s)]
    say('\n结论')
    if not failed and ok_cfg:
        say('  全部通过。可以正常使用。')
        code = 0
    else:
        say(f"  有问题的语言方向: {failed or '无'}")
        if not ok_cfg:
            say('  配置读写失败。')
        code = 1
    report = '\n'.join(lines) + '\n'
    try:
        with open(os.path.join(APP_DIR, 'selftest.txt'), 'w', encoding='utf-8') as f:
            f.write(report)
    except Exception:
        pass
    return code
def _render_test_image(text: str) -> Image.Image:
    """画一张深底浅字的小图当作 OCR 的测试样本。"""
    from PIL import ImageDraw, ImageFont
    img = Image.new('RGB', (700, 80), (24, 22, 28))
    d = ImageDraw.Draw(img)
    font = None
    for path in ['C:\\Windows\\Fonts\\msyh.ttc', 'C:\\Windows\\Fonts\\simhei.ttf']:
        if os.path.exists(path):
            font = ImageFont.truetype(path, 30)
            break
    if font is None:
        raise RuntimeError('找不到中文字体,无法生成测试图')
    else:
        d.text((20, 20), text, font=font, fill=(235, 225, 200))
        return img
def main() -> None:
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    root = tk.Tk()
    root.withdraw()
    app = LimbusTranslator(root)
    try:
        root.mainloop()
    finally:
        if not app._shutting_down:
            try:
                app.quit()
            except Exception:
                try:
                    app.cache.save(force=True)
                except Exception:
                    pass
                os._exit(0)
if __name__ == '__main__':
    main()