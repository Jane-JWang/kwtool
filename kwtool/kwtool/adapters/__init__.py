from kwtool.adapters.base import SiteAdapter
from kwtool.adapters.baidu import BaiduAdapter
from kwtool.adapters.csrc import CsrcAdapter
from kwtool.adapters.csrc_filing_lists import CsrcAccountingFilingAdapter, CsrcRatingFilingAdapter
from kwtool.adapters.cfws import CfwsAdapter
from kwtool.adapters.cnabs import CnabsAdapter
from kwtool.adapters.creditchina import CreditChinaAdapter
from kwtool.adapters.gsxt import GsxtAdapter
from kwtool.adapters.neris import NerisAdapter
from kwtool.adapters.mof import MofAdapter
from kwtool.adapters.mee import MeeAdapter
from kwtool.adapters.mem import MemAdapter
from kwtool.adapters.samr import SamrAdapter
from kwtool.adapters.ndrc import NdrcAdapter
from kwtool.adapters.sta import StaAdapter
from kwtool.adapters.ccdi import CcdiAdapter
from kwtool.adapters.prosecutor_12309 import Prosecutor12309Adapter
from kwtool.adapters.wenshu import WenshuAdapter
from kwtool.adapters.rmfyalk import RmfyalkAdapter
from kwtool.adapters.regional import (
    RegionalCourtAdapter, RegionalCcdiAdapter, RegionalProcuratorateAdapter, RegionalSamrAdapter,
)
from kwtool.adapters.sse import SseAdapter
from kwtool.adapters.zxgk import ShixinAdapter, ZhixingAdapter
from kwtool.adapters.zhongdeng import ZhongdengAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (
        GsxtAdapter(), CreditChinaAdapter(), CsrcAdapter(), CfwsAdapter(),
        NerisAdapter(), MofAdapter(), SseAdapter(), BaiduAdapter(),
        ShixinAdapter(), ZhixingAdapter(), WenshuAdapter(), RmfyalkAdapter(), MeeAdapter(),
        MemAdapter(), SamrAdapter(), NdrcAdapter(), StaAdapter(),
        CcdiAdapter(), Prosecutor12309Adapter(), CsrcRatingFilingAdapter(),
        CsrcAccountingFilingAdapter(), CnabsAdapter(), ZhongdengAdapter(),
        RegionalCourtAdapter(), RegionalCcdiAdapter(), RegionalProcuratorateAdapter(), RegionalSamrAdapter(),
    )
    return {adapter.key: adapter for adapter in adapters}
