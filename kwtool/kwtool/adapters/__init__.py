from kwtool.adapters.base import SiteAdapter
from kwtool.adapters.baidu import BaiduAdapter
from kwtool.adapters.csrc import CsrcAdapter
from kwtool.adapters.cfws import CfwsAdapter
from kwtool.adapters.creditchina import CreditChinaAdapter
from kwtool.adapters.demo import DemoAdapter
from kwtool.adapters.gsxt import GsxtAdapter
from kwtool.adapters.neris import NerisAdapter
from kwtool.adapters.mof import MofAdapter
from kwtool.adapters.sse import SseAdapter
from kwtool.adapters.zxgk import ShixinAdapter, ZhixingAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (DemoAdapter(), GsxtAdapter(), CreditChinaAdapter(), CsrcAdapter(), CfwsAdapter(), NerisAdapter(), MofAdapter(), SseAdapter(), BaiduAdapter(), ShixinAdapter(), ZhixingAdapter())
    return {adapter.key: adapter for adapter in adapters}
