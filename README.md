# 书签库（bookmark-app）

基于 FastAPI、SQLAlchemy、SQLite 和 Jinja2 的单用户书签管理工具。前端使用原生 JavaScript 和 CSS，无需 Node.js 构建。

## 功能

- 添加、搜索和批量整理书签；支持多级分类、标签及内网备用地址。
- 导入域名清单、CSV/TSV、Markdown 链接和浏览器导出的 Netscape HTML。导入前可预览，执行中可查看进度或取消。浏览器目录可转换为分类。
- 可抓取网页标题、描述和图标。游客首页只显示公开书签，管理、导入和备份需要登录。**新增书签默认不是私密书签**；存放内部地址前请检查可见性。
- 支持 PWA、手动及定时备份、整库恢复，以及 JSON 和浏览器 HTML 导出。

## Docker Compose 部署

需要 Docker 和 Compose v2。在项目目录执行：

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs bookmark-app
```

打开 `http://127.0.0.1:8094/login`。首次对空数据库启动时，程序创建 `admin` 并仅在该次启动日志中打印随机密码。不要分享日志；登录后通过侧栏的「改密」更换密码。已有数据库沿用旧凭据，改密码后旧会话失效。用 `curl -fsS http://127.0.0.1:8094/healthz` 检查服务。若修改 `BM_PORT`，对应替换 URL 端口。

容器内监听 `8094`，宿主仅映射到回环地址。默认 `./data` 挂载到 `/app/data`，重建容器不会清除数据。第一次构建需要获取基础镜像和 Python 包，或使用可信的本地缓存及镜像源。运行时代理与构建时代理相互独立。

## 不使用容器

建议使用 Python 3.12（Dockerfile 使用此版本）。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

`run.sh` 默认在 `127.0.0.1:8094` 启动，可用 `HOST` 和 `PORT` 覆盖。脚本不读取 `.env`，请在启动前通过 shell 环境传入配置。请保持单进程运行：导入任务状态和定时备份调度在进程内。

## 配置

| 变量 | 默认值和用途 |
| --- | --- |
| `BM_PORT` | `8094`；Compose 的宿主端口 |
| `TZ` | Compose 默认 `UTC`；设置备份调度时区，例如 `Asia/Shanghai`，需运行环境提供对应时区数据 |
| `BM_DATA_DIR` | 项目下的 `data/`；Compose 固定为 `/app/data` |
| `BM_PROXY` | 空；可选的运行时抓取代理，未设时应用也会检查 `HTTPS_PROXY`。被识别为内网的 URL 使用直连 |
| `BM_THIRD_PARTY_ICONS` | `0`；设为 `1` 才允许第三方图标兜底，该服务会收到书签域名 |
| `BM_BACKUP_ENABLED` | `1`；设为 `0` 关闭定时备份 |
| `BM_BACKUP_HOUR` | `3`；按进程本地时区，范围 `0` 至 `23` |
| `BM_BACKUP_KEEP` | `14`；最近保留的常规 ZIP 备份数 |

应用还支持 `BM_FETCH_TIMEOUT`（单请求默认 8 秒）、`BM_FETCH_BUDGET`（新增时默认总预算 10 秒）、`BM_FETCH_CONCURRENCY`、`BM_FETCH_MAX_BYTES`，以及 `BM_ICON_BUDGET`、`BM_ICON_MAX_BYTES` 和 `BM_ICON_MAX_TRIES`。使用 Compose 时，需把额外的应用变量显式加入服务的 `environment`，只写入 `.env` 不会传进容器。

## 备份、迁移和恢复

- 在「备份」页面生成并下载 ZIP。归档包含一致的 SQLite 快照 `bookmark.db`、JSON、Netscape HTML 和图标文件（如有）。默认每天自动备份到 `data/backups/`，只在应用运行时调度。
- 整库恢复前先下载一份当前备份。把可信 ZIP 上传至「备份」页面，再执行恢复。恢复会替换数据库并使用备份中的账号凭据；操作前会另存 `pre-restore-*.db`。恢复后必要时重新登录，并核对书签、分类和图标。
- 只迁移书签可用本应用的 JSON 导出/导入；与浏览器互通则使用 HTML。不要把这些导出视为脱敏文件。
- 也可以先停止应用，再归档整个数据目录。把加密备份存到独立位置，定期在隔离实例验证可恢复性。库、ZIP 和导出都不应进入 Git；数据库及整库备份包含登录哈希和会话签名材料。

## 安全边界

- 默认仅供本机使用。远程使用建议放在 VPN 或带访问控制的 HTTPS 反向代理后，配置可信的转发代理地址，并核对 HTTPS 下会话 Cookie 带 `Secure`。不要把应用直接开放到公网，也不要把单用户登录当作多租户隔离。
- 启动不访问任何外部网站；但新增、修改 URL、刷新及勾选抓取的导入可在服务端请求站点、重定向目标和图标。仅允许可信人员管理，并在网络层限制应用访问敏感内网服务和云元数据；关闭第三方图标不等于禁用站点抓取。导入时可取消「同时抓取标题与图标」。
- 审核公开书签、内网备用地址和备份文件，限制数据目录、日志及代理凭据的访问权限。只恢复可信归档，不要接受陌生人上传的备份。


## 开源许可证

Copyright 2026 1985773420。原创代码和文档采用 [Apache License 2.0](LICENSE)，允许商用、修改和分发，并含明确的专利授权及终止条款；使用和分发须遵守许可、保留相关声明并标明修改。软件按原样提供，不提供担保。第三方依赖及资源不因本项目许可而改变其原有条款，见 [第三方说明](THIRD_PARTY_NOTICES.md)。
