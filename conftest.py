# 让 pytest 把项目根目录加入 sys.path，测试里可以直接 `from utils import db`
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.fixture
def tmp_dir():
    """
    临时目录放在工作区内。

    pytest 自带的 tmp_path 落在系统 Temp 目录，在某些受限环境下不可访问；
    放在项目里可以保证任何环境都能跑。
    """
    d = ROOT / '.pytest_tmp'
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)