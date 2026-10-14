"""数据库连接。"""
import logging
from sqlalchemy import create_engine, text

from config import Config

logger = logging.getLogger(__name__)

engine = create_engine(Config.db_uri(),
                       pool_recycle=3600,
                       pool_pre_ping=True,
                       echo=False,
                       pool_size=30,
                       max_overflow=20,
                       pool_timeout=30)
