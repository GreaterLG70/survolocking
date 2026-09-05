"""
数据库连接与会话管理。

策略：
- 优先使用配置中的 MySQL；
- APP_ENV=dev 且 MySQL 不可达时，自动回落到本地 SQLite（data/survolocking.db），
  保证无凭据环境下依旧可以完整跑通业务流程。
"""
from __future__ import annotations

import calendar
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None
_using_sqlite_fallback = False


def _build_mysql_url() -> str:
    from urllib.parse import quote_plus

    pwd = quote_plus(settings.MYSQL_PASSWORD)
    url = (
        f"mysql+pymysql://{settings.MYSQL_USER}:{pwd}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
        f"?charset=utf8mb4"
    )
    if settings.MYSQL_SSL:
        url += "&ssl=true"
    return url


def _probe_mysql(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_engine() -> Engine:
    global _engine, _SessionFactory, _using_sqlite_fallback

    if _engine is not None:
        return _engine

    engine = create_engine(
        _build_mysql_url(),
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.DB_ECHO,
        future=True,
    )

    if not _probe_mysql(engine):
        if settings.APP_ENV != "dev":
            raise RuntimeError(
                "无法连接 MySQL，且当前非 dev 环境。请检查 .env 中的 MYSQL_* 配置。"
            )
        # dev 环境降级
        from pathlib import Path

        db_path = Path(__file__).resolve().parents[1] / "data" / "survolocking.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{db_path}", echo=settings.DB_ECHO, future=True
        )
        _using_sqlite_fallback = True

    _engine = engine

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover
        if _using_sqlite_fallback:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _SessionFactory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return _engine


def is_sqlite_fallback() -> bool:
    get_engine()
    return _using_sqlite_fallback


def SessionLocal() -> Session:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """带事务的会话上下文，自动提交/回滚。"""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入。"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    """建表（首次运行）。分区表在 MySQL 上额外处理。"""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    if not is_sqlite_fallback():
        ensure_month_partitions(engine, months_ahead=2, months_back=1)


def _month_boundary(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt: datetime, n: int) -> datetime:
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def ensure_month_partitions(
    engine: Engine, months_ahead: int = 2, months_back: int = 1
) -> list[str]:
    """
    为 intercept_logs 创建/维护按月 RANGE 分区。

    MySQL 约束：
    - 分区键必须出现在所有唯一键中，故主键为 (id, call_time)。
    - 分区必须按 LESS THAN 严格递增排列；ALTER TABLE ADD PARTITION 只能追加到
      末尾。建表时末尾有一个 p_init(MAXVALUE) 兜底分区，此时再 ADD 任何有限分区
      都会报 1493 错误。正确做法是使用 REORGANIZE PARTITION 把兜底分区“劈”出新的
      月份分区，兜底分区始终留在最后。本函数即采用该方式，可安全重复调用。
    """
    created: list[str] = []
    table = f"{settings.MYSQL_TABLE_PREFIX}intercept_logs"
    now = datetime.now()
    start = _add_months(_month_boundary(now), -months_back)

    # 目标月份（含回溯 1 个月与前向窗口）
    targets: list[tuple[str, datetime]] = []
    for i in range(months_back + months_ahead + 1):
        month_start = _add_months(start, i)
        targets.append((f"p{month_start.strftime('%Y%m')}", month_start))

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT PARTITION_NAME, PARTITION_DESCRIPTION "
                "FROM information_schema.PARTITIONS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                "AND PARTITION_NAME IS NOT NULL"
            ),
            {"t": table},
        ).fetchall()

        existing = {r[0] for r in rows}
        # 兜底分区：LESS THAN 为 MAXVALUE 的那个
        catch_all = next(
            (r[0] for r in rows if str(r[1]).strip().upper() == "MAXVALUE"), None
        )

        for part_name, month_start in targets:
            if part_name in existing:
                continue
            next_month = _add_months(month_start, 1)
            less_than = next_month.strftime("%Y-%m-%d %H:%M:%S")
            if catch_all is not None:
                # 把兜底分区劈成 [新月份, 兜底]，兜底永远留在末尾
                conn.execute(
                    text(
                        f"ALTER TABLE `{table}` REORGANIZE PARTITION `{catch_all}` INTO ("
                        f"PARTITION `{part_name}` VALUES LESS THAN (:b),"
                        f"PARTITION `{catch_all}` VALUES LESS THAN (MAXVALUE))"
                    ),
                    {"b": less_than},
                )
            else:
                # 无兜底分区时只能向后追加有限分区
                conn.execute(
                    text(
                        f"ALTER TABLE `{table}` ADD PARTITION ("
                        f"PARTITION `{part_name}` VALUES LESS THAN (:b))"
                    ),
                    {"b": less_than},
                )
            created.append(part_name)
    return created


def drop_old_partitions(engine: Engine, keep_months: int = 6) -> list[str]:
    """清理过旧的分区，配合数据保留策略使用。"""
    table = f"{settings.MYSQL_TABLE_PREFIX}intercept_logs"
    cutoff = _add_months(_month_boundary(datetime.now()), -keep_months)
    dropped: list[str] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT PARTITION_NAME FROM information_schema.PARTITIONS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                "AND PARTITION_NAME IS NOT NULL"
            ),
            {"t": table},
        ).fetchall()
        for (name,) in rows:
            if not name or not name.startswith("p"):
                continue
            try:
                ym = datetime.strptime(name[1:], "%Y%m")
            except ValueError:
                continue
            if ym < cutoff:
                conn.execute(text(f"ALTER TABLE `{table}` DROP PARTITION {name}"))
                dropped.append(name)
    return dropped


def recent_window(days: int = 1) -> datetime:
    return datetime.now() - timedelta(days=days)
