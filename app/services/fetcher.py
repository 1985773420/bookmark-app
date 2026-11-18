"""出网抓取：标题 / 描述 / 状态码 / 图标。

图标按三级降级，命中即止：
  1. 目标页 HTML 里声明的 <link rel="icon">（各站自己指定的最准）
  2. https://域名/favicon.ico（老站的标准位置）
  3. 第三方图标服务（DuckDuckGo、Google S2、favicon.im）
四级兜底是前端根据域名首字母生成色块，不需要出网。

性能设计（2026-11-13 重写，原版单个书签最坏要 40s+）：
  * HTML 边收边解析：<title> 到 </head> 就停，不等整页下完（GitHub 首页 500KB）。
  * 图标候选并发取，命中即取消其余；第三方兜底只对「声明了图标但全挂」才启用，
    且整段有总预算（ICON_BUDGET），不给任何单站点拖垮请求的机会。
  * 「空 200」图标（DuckDuckGo 对无 favicon 的站回 200 + 0 字节）判失败，
    不再白等下一个候选。
  * 异步函数里不做同步图标落盘：文件 IO 丢线程池，event loop 不被阻塞。
"""

import asyncio
import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from .. import config
from . import icons

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(
    r"""<meta\b[^>]*(?:name|property)\s*=\s*["'](?:description|og:description|twitter:description)["']"""
    r"""[^>]*content\s*=\s*["']([^"']*)["']""",
    re.I | re.S,
)
_META_DESC_REV = re.compile(
    r"""<meta\b[^>]*content\s*=\s*["']([^"']*)["'][^>]*(?:name|property)\s*=\s*"""
    r"""["'](?:description|og:description|twitter:description)["']""",
    re.I | re.S,
)
# <head> 结束位置：标题/描述/meta 全在这之前，拿到就够解析了
_HEAD_END = re.compile(rb"</head\s*>", re.I)
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I | re.S)
_ATTR = re.compile(r"""([a-zA-Z:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_WS = re.compile(r"\s+")
_ICON_REL = re.compile(r"(^|\s)((shortcut|apple-touch|mask-)?icon|apple-touch-icon(-precomposed)?)(\s|$)", re.I)

# 主文档只解析 head，读满这个量就收工（声明了巨大 <head> 的站也兜得住）
HTML_SCAN_MAX = 512 * 1024


@dataclass
class PageInfo:
    url: str
    final_url: str = ""
    status_code: int | None = None
    title: str | None = None
    description: str | None = None
    icon_candidates: list[str] = field(default_factory=list)
    favicon_path: str | None = None
    error: str | None = None


def make_client(**overrides) -> httpx.AsyncClient:
    """统一出网客户端。容器内必须走 config.PROXY 才能出 WAN。"""
    kwargs = dict(
        timeout=config.FETCH_TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*;q=0.8,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        },
        limits=httpx.Limits(
            max_connections=config.FETCH_CONCURRENCY * 2,
            max_keepalive_connections=config.FETCH_CONCURRENCY,
        ),
    )
    if config.PROXY:
        kwargs["proxy"] = config.PROXY
    # 不吃环境变量里的 HTTP_PROXY：容器里设了它，trust_env=True 时连内网 IP 的请求也会
    # 被送到公网代理，代理回 403，内网站点就永远抓不到标题和图标。
    kwargs["trust_env"] = False
    kwargs.update(overrides)
    return httpx.AsyncClient(**kwargs)


def is_internal(url: str) -> bool:
    """目标是不是内网/回环地址。是的话必须直连，走代理会被拒。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 域名（含 echosea.top）交给代理
    return ip.is_private or ip.is_loopback or ip.is_link_local


def client_for(url: str, **overrides) -> httpx.AsyncClient:
    """按目标地址挑客户端：内网直连（不带代理），公网走 config.PROXY。"""
    if is_internal(url):
        overrides.setdefault("proxy", None)
        # proxy=None 还不够：httpx 里 proxy 显式给 None 就等于不用代理
    return make_client(**overrides)


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR.finditer(tag):
        key = m.group(1).lower()
        val = m.group(2) or m.group(3) or m.group(4) or ""
        out[key] = val
    return out


def _clean_text(s: str | None, limit: int = 512) -> str | None:
    if not s:
        return None
    s = _WS.sub(" ", s).strip()
    return s[:limit] or None


def extract_icon_candidates(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """从 HTML 里挑出图标候选，返回 (远程候选, 内联 data-URI)。

    内联图标（SPA 常见）单独返回：不能当链接去请求，由调用方直接解码落盘。
    模块级的 _INLINE_ICONS 全局变量已删除——并发抓取时它是共享可写状态，
    两个请求会互相覆盖（原版的隐性 bug）。
    """
    rel_icon: list[str] = []
    apple: list[str] = []
    other: list[str] = []
    inline: list[str] = []
    for tag in _LINK_TAG.findall(html):
        a = _attrs(tag)
        rel = a.get("rel", "")
        href = a.get("href", "").strip()
        if not href or not _ICON_REL.search(rel):
            continue
        if href.lower().startswith("data:"):
            inline.append(href)
            continue
        url = urljoin(base_url, href)
        if not url.lower().startswith(("http://", "https://")):
            continue
        sizes = a.get("sizes", "")
        if "apple-touch" in rel.lower():
            apple.append(url)
        elif "shortcut" in rel.lower() or rel.strip().lower() in ("icon", "icon shortcut"):
            rel_icon.append(url)
        elif sizes:
            # 有 sizes 声明的普通 icon 优先于苹果图标（通常是标准 favicon）
            rel_icon.append(url)
        else:
            other.append(url)

    seen: set[str] = set()
    ordered: list[str] = []
    for u in rel_icon + apple + other:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered[:6], inline[:3]


def parse_page(html: str, base_url: str) -> tuple[str | None, str | None, list[str], list[str]]:
    title = None
    m = _TITLE.search(html)
    if m:
        title = _clean_text(_unescape(m.group(1)))
    desc = None
    for rx in (_META_DESC, _META_DESC_REV):
        m = rx.search(html)
        if m:
            desc = _clean_text(_unescape(m.group(1)), 1000)
            break
    cands, inline = extract_icon_candidates(html, base_url)
    return title, desc, cands, inline


def _unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


async def _read_capped(resp: httpx.Response, limit: int) -> bytes:
    """边读边截断，避免撞上几百 MB 的下载。"""
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf.extend(chunk)
        if len(buf) >= limit:
            break
    return bytes(buf[:limit])


async def _read_head_stream(resp: httpx.Response, limit: int) -> bytes:
    """边收边扫：碰到 </head> 或读满 limit 就停，尾部残留半截也一并返回。

    原版 aiter_bytes 读到 512KB 或流结束，GitHub 那种 500KB 首页要全下完才解析，
    实测 page 阶段 10~20s。绝大多数站点 </head> 在 50KB 内。
    """
    buf = bytearray()
    try:
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if _HEAD_END.search(buf) or b"</body" in buf[-64:] or len(buf) >= limit:
                break
    except httpx.HTTPError:
        pass
    return bytes(buf[:limit])


async def fetch_page(client: httpx.AsyncClient, url: str) -> PageInfo:
    info = PageInfo(url=url)
    try:
        async with client.stream("GET", url) as resp:
            info.status_code = resp.status_code
            info.final_url = str(resp.url)
            if resp.status_code >= 400:
                # 4xx/5xx 也要留下痕迹：内网地址被代理拦掉时就靠这行看出来
                info.error = f"HTTP {resp.status_code}"
                # 站点根路径要登录（401/403）时页面拿不到 <link rel=icon>，
                # 但 /favicon.ico 往往仍能匿名取到（浏览器标签页要用），补一个候选。
                if resp.status_code in (401, 403):
                    root = _root_of(url)
                    info.icon_candidates = [f"{root}/favicon.ico"]
                return info

            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "xml" not in ctype:
                # 不是网页（PDF、图片、API 等），只记状态码
                head = await _read_head_stream(resp, 2048)
                if not head.lstrip()[:1] == b"<":
                    return info
                raw = head
            else:
                raw = await _read_head_stream(resp, HTML_SCAN_MAX)
    except httpx.HTTPError as exc:
        info.error = _short(exc)
        return info

    try:
        html = raw.decode(_charset_of(resp, raw), "ignore")
    except Exception:  # 编码识别失败时按 UTF-8 硬解
        html = raw.decode("utf-8", "ignore")

    info.title, info.description, cands, inline = parse_page(html, info.final_url or url)
    info.icon_candidates = list(cands)
    if inline:
        got = _save_inline_icon(inline[0])
        if got:
            info.favicon_path = got
    # 站点没声明图标时，退回域名根部
    root = _root_of(info.final_url or url)
    fallback = f"{root}/favicon.ico"
    if fallback not in info.icon_candidates:
        info.icon_candidates.append(fallback)
    return info


def _charset_of(resp: httpx.Response, raw: bytes) -> str:
    ctype = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        return m.group(1)
    m = re.search(rb"""charset=["']?([\w-]+)""", raw[:4096], re.I)
    if m:
        return m.group(1).decode("latin-1")
    return "utf-8"


def _root_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def download_icon(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """取一个图标并落盘，返回 (文件名, 来源URL)。失败返回 None。"""
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "")
            data = await _read_capped(resp, config.ICON_MAX_BYTES)
    except httpx.HTTPError:
        return None
    if not data:
        # DuckDuckGo 对没有 favicon 的域名回 200 + 0 字节。原版会继续试下一个候选，
        # 每次都要先吃满代理往返，这是单书签卡十秒的直接来源之一。
        return None
    ext = icons.sniff(data, ctype)
    if not ext:
        return None
    # 图标落盘是同步 IO，放线程池：event loop 里直接写文件会让并发抓取的其余请求一起排队
    name = await asyncio.to_thread(icons.save, data, ext)
    return (name, url) if name else None


async def _first_icon(client: httpx.AsyncClient, urls: list[str]) -> tuple[str, str] | None:
    """并发取几个候选，第一个成功即返回（其余任务被取消）。

    原版串行 5 个候选 × 每个吃满超时 = 最坏 100s。实测 example.com 一个书签 16.7s
    全是串行等两个永远拿不到的图标地址。
    """
    if not urls:
        return None
    tasks = [asyncio.create_task(download_icon(client, u)) for u in urls]
    try:
        for fut in asyncio.as_completed(tasks):
            got = await fut
            if got:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return got
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return None


async def resolve_icon(
    client: httpx.AsyncClient, domain_url: str, candidates: list[str]
) -> tuple[str, str] | None:
    """按三级降级取图标，返回 (文件名, 来源URL)。

    整段有总预算 config.ICON_BUDGET：第三方兜底服务在境外，单次往返 5s 起，
    原版无条件把 2 个兜底都试一遍（+10s）。现在第三方只在预算内、且前两级全挂时
    才并发试一次；预算耗尽直接交给前端首字母色块。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.ICON_BUDGET

    # 前两级：站点声明的 + /favicon.ico，并发取，命中即止
    got = await _first_icon(client, candidates[: config.ICON_MAX_TRIES])
    if got:
        return got

    if not config.FALLBACK_ICON_URLS or loop.time() >= deadline:
        return None

    # 第三级：第三方图标服务。预算内并发，谁先回来用谁。
    fallback_urls = [tpl.format(domain=domain_url) for tpl in config.FALLBACK_ICON_URLS]
    remaining = max(0.5, deadline - loop.time())
    try:
        return await asyncio.wait_for(_first_icon(client, fallback_urls), timeout=remaining)
    except (asyncio.TimeoutError, TimeoutError):
        return None


def _save_inline_icon(data_uri: str) -> str | None:
    """把 data:image/...;base64,... 存成图标文件，返回文件名。"""
    import base64
    from urllib.parse import unquote

    try:
        header, _, payload = data_uri.partition(",")
        if not payload:
            return None
        if ";base64" in header:
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote(payload).encode("utf-8")
    except Exception:
        return None
    if not raw or len(raw) > config.ICON_MAX_BYTES:
        return None
    ext = icons.sniff(raw, header[5:].split(";")[0])
    return icons.save(raw, ext) if ext else None


def _short(exc: Exception) -> str:
    name = type(exc).__name__
    msg = str(exc) or name
    return msg[:200]


# ---------- 批量抓取 ----------


async def fetch_many(urls: list[str], on_done=None) -> dict[str, PageInfo]:
    """并发抓取一批 URL。on_done(url, info) 在每条完成时回调，用于推进度。"""
    sem = asyncio.Semaphore(config.FETCH_CONCURRENCY)
    results: dict[str, PageInfo] = {}

    async with make_client() as client:

        async def one(u: str) -> None:
            async with sem:
                info = await fetch_page(client, u)
                if info.favicon_path:
                    pass
                elif info.title or info.icon_candidates:
                    try:
                        got = await resolve_icon(client, dom_of(u), info.icon_candidates)
                        if got:
                            info.favicon_path = got[0]
                    except Exception:
                        pass
            results[u] = info
            if on_done:
                await _maybe_await(on_done(u, info))

        await asyncio.gather(*(one(u) for u in urls), return_exceptions=True)
    return results


def dom_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


async def _maybe_await(v):
    if asyncio.iscoroutine(v):
        await v
    return v
