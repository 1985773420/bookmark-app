"""域名 / URL 归一化。

用户的输入形态非常杂：裸域名、带协议 URL、带路径 URL、带端口、Markdown 链接、
从聊天窗口复制出来带前后噪声的一行字。统一归一化成 (url, domain) 二元组：
- url     用于唯一键与跳转
- domain  用于图标抓取、聚合统计、按站点去重
"""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

# Markdown 链接 [标题](https://example.com)
_MD_LINK = re.compile(r"^\[[^\]]*\]\(([^)\s]+)\)$")
# 行首噪声：有序列表编号、项目符号、引号
_LEAD_NOISE = re.compile(r'^[\s>*\-•·]+|^\d+[.)]\s+')
# 行尾噪声：中英文标点、收尾括号
_TAIL_NOISE = re.compile(r'[\s,;、。，；）)\]}>"\'`。]+$')
# 域名合法性：至少一个点，标签由字母数字连字符组成
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9一-鿿]([a-z0-9一-鿿-]*[a-z0-9一-鿿])?\.)+[a-z一-鿿]{2,}$", re.I)
# 需要丢掉的无意义路径片段
_JUNK_PATHS = {"/index.html", "/index.htm", "/index.php", "/default.html"}


def clean_line(raw: str) -> str:
    """剥离从聊天/文档里复制粘贴带来的行首行尾噪声。"""
    s = raw.strip()
    m = _MD_LINK.match(s)
    if m:
        s = m.group(1)
    s = _LEAD_NOISE.sub("", s).strip()
    s = _TAIL_NOISE.sub("", s).strip()
    return s


def normalize_lan(raw: str | None) -> str | None:
    """内网地址归一化。规则比外网网址松：内网服务常常没有域名、只有 IP:端口，
    所以不要求带点的一级域名，但仍然必须带协议（或 host:port 这种能被识别的形式）。

    空串返回 None；给了却认不出时抛 ValueError，让调用方把错误原样告诉用户，
    而不是悄悄把填错的内网地址丢掉。
    """
    s = clean_line(raw or "")
    if not s:
        return None
    if "://" in s:
        try:
            parts = urlsplit(s)
        except ValueError as exc:
            raise ValueError("内网地址格式不对") from exc
        if parts.scheme not in ("http", "https"):
            raise ValueError("内网地址只能是 http 或 https")
        if not parts.netloc:
            raise ValueError("内网地址缺少主机部分")
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    # 没有协议的裸写法：只给个 IP / IP:端口 / 内网主机名
    if re.fullmatch(r"[a-zA-Z0-9.\-]+(:\d{1,5})?(/[^\s]*)?", s):
        return "http://" + s.rstrip("/")
    raise ValueError("内网地址无法识别")


def normalize(raw: str) -> tuple[str, str] | None:
    """把一行输入归一化成 (url, domain)。无法识别返回 None。

    >>> normalize("github.com")
    ('https://github.com', 'github.com')
    >>> normalize(" http://Example.COM/a/b/ ")
    ('http://example.com/a/b', 'example.com')
    >>> normalize("随便写的一句话")
    """
    s = clean_line(raw)
    if not s:
        return None

    had_scheme = "://" in s
    if not had_scheme:
        s = "https://" + s

    try:
        parts = urlsplit(s)
    except ValueError:
        return None

    if parts.scheme not in ("http", "https"):
        return None

    host = (parts.hostname or "").lower()
    if not host:
        return None

    # 纯 IP：只有显式带端口时才认为是有意为之（内网服务），否则多半是版权号/版本号
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _DOMAIN.match(host):
            return None
    else:
        if parts.port is None:
            return None
        _DOMAIN.match(host)  # noqa: B018  纯 IP 不校验域名格式

    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"

    path = parts.path or ""
    if path.rstrip("/") in _JUNK_PATHS or path in _JUNK_PATHS:
        path = ""
    if path != "/":
        path = path.rstrip("/")

    url = urlunsplit((parts.scheme, netloc, path, parts.query, ""))
    return url, host


def domain_of(url: str) -> str:
    """从已归一化 URL 里取域名。"""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def display_title(url: str, title: str | None) -> str:
    """没有标题时退化成域名可读形式。"""
    if title:
        return title.strip()
    host = domain_of(url)
    for prefix in ("www.", "m.", "blog."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host or url


def safe_name(url: str) -> str:
    """把 URL 变成可用作文件名的字符串（图标缓存用）。"""
    host = domain_of(url) or url
    return re.sub(r"[^a-z0-9.-]+", "_", host.lower())
