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
from kwtool.adapters.national_sites import (
    CcdiAdapter, MeeAdapter, MemAdapter, NdrcAdapter, Prosecutor12309Adapter,
    SamrAdapter, StaAdapter, WenshuAdapter,
)
from kwtool.adapters.sse import SseAdapter
from kwtool.adapters.zxgk import ShixinAdapter, ZhixingAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (
        GsxtAdapter(), CreditChinaAdapter(), CsrcAdapter(), CfwsAdapter(),
        NerisAdapter(), MofAdapter(), SseAdapter(), BaiduAdapter(),
        ShixinAdapter(), ZhixingAdapter(), WenshuAdapter(), MeeAdapter(),
        MemAdapter(), SamrAdapter(), NdrcAdapter(), StaAdapter(),
        CcdiAdapter(), Prosecutor12309Adapter(), CsrcRatingFilingAdapter(),
        CsrcAccountingFilingAdapter(), CnabsAdapter(),
    )
    return {adapter.key: adapter for adapter in adapters}
