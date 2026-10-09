from kwtool.adapters.base import SiteAdapter
from kwtool.adapters.csrc import CsrcAdapter
from kwtool.adapters.demo import DemoAdapter
from kwtool.adapters.gsxt import GsxtAdapter
from kwtool.adapters.neris import NerisAdapter
from kwtool.adapters.mof import MofAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (DemoAdapter(), GsxtAdapter(), CsrcAdapter(), NerisAdapter(), MofAdapter())
    return {adapter.key: adapter for adapter in adapters}
