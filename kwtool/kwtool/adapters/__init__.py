from kwtool.adapters.base import SiteAdapter
from kwtool.adapters.demo import DemoAdapter
from kwtool.adapters.gsxt import GsxtAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (DemoAdapter(), GsxtAdapter())
    return {adapter.key: adapter for adapter in adapters}
