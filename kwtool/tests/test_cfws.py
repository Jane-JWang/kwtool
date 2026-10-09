import json
from urllib.parse import urlencode

from kwtool.adapters import registered_adapters
from kwtool.adapters.cfws import CfwsAdapter, _search_response


class Response:
    url = "https://cfws.samr.gov.cn/queryDoc"

    def __init__(self, conditions):
        self.request = type("Request", (), {
            "method": "POST",
            "post_data": urlencode({"queryCondition": json.dumps(conditions, ensure_ascii=False)}),
        })()


def test_cfws_matches_only_current_subject_without_old_filters() -> None:
    current = {"key": "30_s,36_s", "id": "主体乙", "name": "当事人名称：主体乙 "}
    old = {"key": "30_s,36_s", "id": "主体甲", "name": "当事人名称：主体甲 "}

    assert _search_response(Response([current]), "主体乙")
    assert not _search_response(Response([old]), "主体乙")
    assert not _search_response(Response([old, current]), "主体乙")


def test_cfws_registered_with_browser_print_defaults() -> None:
    adapter = registered_adapters()["cfws"]
    assert isinstance(adapter, CfwsAdapter)
    assert adapter.default_pdf_settings.scale == 0.35
    assert adapter.default_pdf_settings.print_background
