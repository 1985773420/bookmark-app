"""数据库会话管理。SQLite + WAL，兼顾并发读与单文件备份。"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

config.ensure_dirs()

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _record):
    """开启 WAL 与外键约束：WAL 让读写不互相阻塞，外键保证分类树级联正确。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 老库升级用的「加列」清单：create_all 只建缺的表，不会给已有的表补列，
# 这个项目又没引 Alembic，所以新增的列在这里登记一条，启动时补上。
# 只加不删、且默认值可空/有默认——这样回滚到旧版本代码也能照常跑。
_ADDED_COLUMNS = (
    ("bookmark", "url_lan", "VARCHAR(2048)"),
)


def _add_missing_columns() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    for table, column, ddl in _ADDED_COLUMNS:
        if table not in insp.get_table_names():
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        if column in have:
            continue
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    _add_missing_columns()


def reset_engine() -> None:
    """数据库文件被整体替换后（恢复备份）必须调用。

    连接池里缓存的是旧 inode 的句柄，光换磁盘上的文件，进程还是会继续读写那个
    已经被 unlink 的库，后续写入谁都不看见。dispose 后新建的连接才会指向新文件。
    """
    engine.dispose()
