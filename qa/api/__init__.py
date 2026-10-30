# qa/api/__init__.py —— 接口测试包标记文件（内容留空即可）。
# 解决的问题：让 qa/api 下的 config.py / base_page.py 以“包内模块”的身份被导入，
#            而不是被当成项目根目录的同名模块。
# 面试怎么讲：pytest 会按 __init__.py 判断模块路径，包结构决定了 sys.path 的插入位置。
