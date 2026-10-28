# qa/__init__.py（骨架里的 骨架/__init__.py）—— 把 qa 目录标记成 Python 包。
# 解决的问题：测试文件里才能写 from qa.api.client import ApiClient，避免和 Flask_app
#            根目录的 config.py、views/ 等模块重名。
# 面试怎么讲：测试代码与业务代码同仓但独立成包（qa/），互不污染导入路径。
