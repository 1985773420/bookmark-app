"""导入解析器：纯文本 / CSV / 浏览器导出的 Netscape 书签 HTML / Markdown 列表。

所有格式最终收敛成同一种中间结构 ParsedItem:
    {"url", "domain", "title", "path": [目录层级...], "valid", "reason"}

path 是"这条书签在源文件里所处的目录路径"，导入时用它自动挂载到分类树上。
"""

import csv
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from . import domains as dom


@dataclass
class ParsedItem:
    url: str
    domain: str
    title: str | None = None
    path: list[str] = field(default_factory=list)
    valid: bool = True
    reason: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)


# CSV 表头识别用词表
_HEADER_WORDS = {
    "url", "link", "domain", "site", "website", "href", "address",
    "网址", "域名", "链接", "地址", "站点", "网站",
}
_CAT_WORDS = {
    "category", "categories", "folder", "path", "group", "collection", "tag", "tags",
    "分类", "目录", "分组", "类别", "标签", "文件夹", "栏目",
}
_TITLE_WORDS = {
    "title", "name", "label", "caption", "desc", "description", "note", "notes",
    "标题", "名称", "名字", "描述", "备注", "说明",
}


def _mk(raw: str, title: str | None = None, path: list[str] | None = None) -> ParsedItem:
    norm = dom.normalize(raw)
    if norm is None:
        return ParsedItem(url=raw.strip()[:500], domain="", title=title,
                          path=path or [], valid=False, reason="不是合法域名或 URL")
    url, domain = norm
    return ParsedItem(url=url, domain=domain, title=(title or None), path=path or [])


# ---------- 纯文本 ----------


def parse_text(text: str) -> list[ParsedItem]:
    """每行一个域名。`#` 开头为注释；行内逗号或制表符后面跟的是标签。"""
    out: list[ParsedItem] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        parts = re.split(r"[,\t]", line)
        head = parts[0].strip()
        if not head:
            continue
        item = _mk(head)
        if item.valid:
            item.tags = [p.strip()[:64] for p in parts[1:] if p.strip()][:20]
        out.append(item)
    return out


# ---------- Markdown ----------


_MD_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[(?P<title>[^\]]*)\]\((?P<url>[^)\s]+)\)")
_MD_HEAD = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")


def parse_markdown(text: str) -> list[ParsedItem]:
    """`- [标题](url)` 列表；`##` 标题作为后续条目的目录层级。"""
    out: list[ParsedItem] = []
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        head = _MD_HEAD.match(line)
        if head:
            level = len(head.group("hashes"))
            title = head.group("title").strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        m = _MD_ITEM.match(line)
        if m:
            out.append(_mk(m.group("url"), m.group("title"), [t for _, t in stack]))
    return out


# ---------- CSV / TSV ----------


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def parse_csv_file(text: str, domain_col: int = 0, category_col: int | None = None,
                   title_col: int | None = None, delimiter: str | None = None) -> list[ParsedItem]:
    delim = delimiter or sniff_delimiter(text)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return []

    # 没显式指定列号时，先看表头里有没有「分类 / 目录 / 标签」这类字样
    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in _HEADER_WORDS for h in header) or header[:1] in (["url"], ["link"])
    if category_col is None and has_header:
        for i, h in enumerate(header):
            if h in _CAT_WORDS:
                category_col = i
                break
        else:
            if len(header) > 1:
                category_col = 1
    if title_col is None and has_header:
        for i, h in enumerate(header):
            if h in _TITLE_WORDS:
                title_col = i
                break

    out: list[ParsedItem] = []
    for row in rows:
        if has_header and row[0].strip().lower() in _HEADER_WORDS:
            continue
        if row is rows[0] and has_header:
            continue
        cell = row[domain_col].strip() if domain_col < len(row) else ""
        if not cell:
            continue
        path: list[str] = []
        if category_col is not None and category_col < len(row):
            raw_cat = row[category_col].strip()
            if raw_cat:
                path = [p.strip() for p in re.split(r"[/>|]", raw_cat) if p.strip()]
        title = None
        if title_col is not None and title_col < len(row):
            title = row[title_col].strip() or None
        out.append(_mk(cell, title, path))
    return out


# ---------- Netscape 书签 HTML ----------


class _NetscapeParser(HTMLParser):
    """浏览器导出的书签 HTML：<H3> 开目录、<DL> 进出层级、<A HREF> 是书签。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[ParsedItem] = []
        self._stack: list[str] = []
        self._pending_folder: str | None = None
        self._in_h3 = False
        self._h3_buf: list[str] = []
        self._in_a = False
        self._a_buf: list[str] = []
        self._a_href = ""
        self._a_tags: list[str] = []
        self._pending_desc: ParsedItem | None = None
        self._in_dd = False
        self._dd_buf: list[str] = []

    # -- 标签 --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._flush_desc()
            self._in_h3, self._h3_buf = True, []
        elif tag == "dl":
            if self._pending_folder is not None:
                self._stack.append(self._pending_folder)
                self._pending_folder = None
            else:
                self._stack.append("")
        elif tag == "a":
            d = dict(attrs)
            href = d.get("href") or ""
            if href:
                self._in_a, self._a_buf, self._a_href = True, [], href
                # linkding / Pinboard 这类导出的标签写在 TAGS="a,b" 属性里
                # （属性名已被 HTMLParser 转成小写）。别丢，否则迁移过来标签全空。
                self._a_tags = [
                    t.strip()[:64] for t in (d.get("tags") or "").split(",") if t.strip()
                ][:20]
        elif tag == "dd":
            self._flush_desc()
            self._in_dd, self._dd_buf = True, []
        elif tag == "dt":
            self._flush_desc()

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_h3 = False
            name = "".join(self._h3_buf).strip()
            if name:
                self._pending_folder = name
        elif tag == "dl":
            if self._stack:
                self._stack.pop()
        elif tag == "a":
            if self._in_a:
                self._in_a = False
                path = [p for p in self._stack if p]
                item = _mk(self._a_href, "".join(self._a_buf).strip() or None, path)
                item.tags = self._a_tags
                self.items.append(item)
                self._pending_desc = item
                self._a_tags = []
        elif tag == "dd":
            self._flush_desc()

    def _flush_desc(self) -> None:
        """Netscape 格式的 <DD> 通常不闭合，所以要靠下一个标签来收尾。"""
        if not self._in_dd:
            return
        self._in_dd = False
        desc = "".join(self._dd_buf).strip()
        if desc and self._pending_desc is not None and self._pending_desc.valid:
            self._pending_desc.description = desc[:1000]
        self._dd_buf = []
        self._pending_desc = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_buf.append(data)
        elif self._in_a:
            self._a_buf.append(data)
        elif self._in_dd:
            self._dd_buf.append(data)


def parse_netscape_html(text: str) -> list[ParsedItem]:
    p = _NetscapeParser()
    p.feed(text)
    p._flush_desc()
    return p.items


# ---------- 统一入口 ----------


def detect_format(filename: str, text: str) -> str:
    name = (filename or "").lower()
    head = text.lstrip()[:2000].lower()
    if name.endswith((".html", ".htm")) or "netscape-bookmark" in head or "<dt><a href" in head:
        return "html"
    if name.endswith((".csv", ".tsv")):
        return "csv"
    if name.endswith((".md", ".markdown")):
        return "markdown"
    if name.endswith((".txt", ".list", ".text")):
        return "text"
    # 没有扩展名时按内容猜
    if re.search(r"^\s*(?:[-*+]|\d+[.)])\s+\[[^\]]*\]\(", text, re.M):
        return "markdown"
    if sniff_delimiter(text) != "," or text.count(",") >= max(1, text.count("\n") // 2):
        return "csv"
    return "text"


def parse_any(text: str, filename: str = "", domain_col: int = 0,
              category_col: int | None = None, title_col: int | None = None) -> tuple[str, list[ParsedItem]]:
    fmt = detect_format(filename, text)
    if fmt == "html":
        return fmt, parse_netscape_html(text)
    if fmt == "csv":
        return fmt, parse_csv_file(text, domain_col, category_col, title_col)
    if fmt == "markdown":
        return fmt, parse_markdown(text)
    return fmt, parse_text(text)
