from kwtool.adapters.base import SiteAdapter
from kwtool.adapters.demo import DemoAdapter


def registered_adapters() -> dict[str, SiteAdapter]:
    """Register code-defined site adapters here."""
    adapters: tuple[SiteAdapter, ...] = (DemoAdapter(),)
    return {adapter.key: adapter for adapter in adapters}
