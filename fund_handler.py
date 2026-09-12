# 自定义 Alpha158 handler：在基础特征后追加基本面因子字段（$roe 等）。
# 字段由 build_fund_factors 写入 QLib bin（features/{symbol}/{field}.day.bin）。
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.data.loader import Alpha158DL

FUND_FIELDS = [
    "$roe",
    "$gross_margin",
    "$rev_growth",
    "$profit_growth",
    "$pe_ttm",
    "$pb",
]


class Alpha158Fund(Alpha158):
    def get_feature_config(self):
        fields, names = Alpha158DL.get_feature_config()
        fields += FUND_FIELDS
        names += [f.lstrip("$").upper() for f in FUND_FIELDS]
        return fields, names