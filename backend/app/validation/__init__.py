"""M5 验证与输出层 — BD 合法性校验 + 格式化输出。

模块构成：
  - rules.py: 综合 BD 验证规则集
  - formatter.py: BuildCard 输出格式化（Markdown / PoB / API JSON）
"""

from app.validation.rules import BuildValidator
from app.validation.formatter import BuildFormatter

__all__ = ["BuildValidator", "BuildFormatter"]
