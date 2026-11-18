"""全局配置：全部可通过环境变量覆盖，便于容器化部署。"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录：数据库、图标缓存、备份都放这里，备份就是打包这个目录
DATA_DIR = Path(os.environ.get("BM_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "bookmark.db"
FAVICON_DIR = DATA_DIR / "favicons"
BACKUP_DIR = DATA_DIR / "backups"

# 出网代理：按需配置
PROXY = os.environ.get("BM_PROXY") or os.environ.get("HTTPS_PROXY") or None

# 抓取参数
FETCH_CONCURRENCY = int(os.environ.get("BM_FETCH_CONCURRENCY", "12"))
# 单次请求超时。原来 20s：一个卡住的站点会把整次「新增书签」拖到 20s 以上，
# 站点的 title/meta 都压在 <head> 里，8s 拿不到基本就不用等了。
FETCH_TIMEOUT = float(os.environ.get("BM_FETCH_TIMEOUT", "8"))
FETCH_MAX_BYTES = int(os.environ.get("BM_FETCH_MAX_BYTES", str(512 * 1024)))

# 第三方图标兜底服务（并发尝试，按顺序只是语义上的优先级）
FALLBACK_ICON_URLS = [
    "https://ico.nyaasu.top/ico/{domain}",
    "https://favicon.im/{domain}",
    "https://icons.duckduckgo.com/ip3/{domain}.ico",
] if os.environ.get("BM_THIRD_PARTY_ICONS", "0") == "1" else []

# 允许存取的图标体积上限
ICON_MAX_BYTES = int(os.environ.get("BM_ICON_MAX_BYTES", str(256 * 1024)))
# 前两级（站点声明 + /favicon.ico）最多并发几个候选
ICON_MAX_TRIES = int(os.environ.get("BM_ICON_MAX_TRIES", "5"))
# 取图标的总预算（秒）。超过就直接用前端首字母色块，不再等第三方服务。
ICON_BUDGET = float(os.environ.get("BM_ICON_BUDGET", "6"))
# 单次「新增书签」的抓取总预算（秒）。超时先入库、图标后台补，不让用户干等。
FETCH_BUDGET = float(os.environ.get("BM_FETCH_BUDGET", "10"))
ICON_CONTENT_TYPES = {
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}

# 备份保留份数
BACKUP_KEEP = int(os.environ.get("BM_BACKUP_KEEP", "14"))
# 应用内自带定时备份（不依赖系统 crontab；容器里 cron 不一定起得来）
BACKUP_ENABLED = os.environ.get("BM_BACKUP_ENABLED", "1") not in ("0", "false", "no")
BACKUP_HOUR = int(os.environ.get("BM_BACKUP_HOUR", "3"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, FAVICON_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
