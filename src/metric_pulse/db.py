"""SQLAlchemy 引擎、会话工厂和 FastAPI 数据库依赖。

业务函数显式提交事务；请求结束只负责关闭会话。测试通过环境变量使用隔离数据库，因此
生产任务数据不会被测试夹具读取或修改。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 实体共用的 SQLAlchemy 声明式基类。"""

    pass


def _engine_kwargs(url: str) -> dict[str, object]:
    """按数据库类型选择连接参数。

    SQLite 测试/单机部署需要允许后台任务跨线程复用连接；服务型数据库则启用连接存活
    检查和有界连接池。
    """

    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session() -> Generator[Session]:
    """提供请求级会话；提交和回滚由明确的业务事务负责。"""

    with SessionLocal() as session:
        yield session


def create_schema() -> None:
    """导入全部模型后创建缺失表，主要用于本地首次启动。"""

    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
