from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.guangdong_regional import GUANGDONG_ADAPTERS


def test_nine_guangdong_routes_have_distinct_official_searches():
    adapters = registered_adapters()
    keys = [cls.key for cls in GUANGDONG_ADAPTERS]
    assert len(keys) == len(set(keys)) == 9
    for key in keys:
        adapter = adapters[key]
        assert tuple(adapter.sites) == ("广东省",)
        assert adapter.required_kwargs == ("注册地",)
        assert adapter.default_pdf_settings.scale == 0.35
        assert adapter.sites["广东省"].hosts
        assert adapter.sites["广东省"].result_path


@pytest.mark.parametrize("province", ["北京市", "浙江省", "广东省深圳市", "深圳市"])
def test_other_locations_are_rejected_before_navigation(province):
    context = SimpleNamespace(site_settings={"max_pages": 1}, subject_kwargs={"注册地": province})
    for cls in GUANGDONG_ADAPTERS:
        with pytest.raises(ValueError):
            cls().run_one(context, "中信证券")


@pytest.mark.parametrize("key,site_id", [
    ("regional_development", "135"),
    ("regional_natural_resources", "153"),
    ("regional_housing", "233"),
    ("regional_environment", "160"),
])
def test_guangdong_government_search_uses_its_own_site_id(key, site_id):
    site = registered_adapters()[key].sites["广东省"]
    url = urlparse(site.start_url("中信证券"))
    assert url.hostname == "search.gd.gov.cn"
    assert url.path == f"/search/all/{site_id}"
    assert parse_qs(url.query)["keywords"] == ["中信证券"]
