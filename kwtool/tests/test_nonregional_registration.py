from kwtool.adapters import registered_adapters


def test_new_nonregional_sites_are_selectable_with_print_defaults() -> None:
    adapters = registered_adapters()
    keys = {
        "wenshu", "mee", "mem", "samr", "ndrc", "sta", "ccdi",
        "prosecutor_12309", "csrc_rating_filing", "csrc_accounting_filing",
        "cnabs", "rmfyalk",
    }
    assert keys <= adapters.keys()
    for key in keys:
        pdf = adapters[key].default_pdf_settings
        assert pdf.scale == 0.35
        assert pdf.print_background is True
