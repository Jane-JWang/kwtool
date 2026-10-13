import pytest

from kwtool.subjects import parse_subject


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("张三", ("张三", {})),
        (" 张三 [ 身份证 ： foo ， 公司 : bar ] ", ("张三", {"身份证": "foo", "公司": "bar"})),
        ("张三[身份证:foo,公司:bar]", ("张三", {"身份证": "foo", "公司": "bar"})),
        ("容诚会计师事务所（特殊普通合伙）", ("容诚会计师事务所（特殊普通合伙）", {})),
        ("张三(身份证:foo)", ("张三(身份证:foo)", {})),
    ],
)
def test_parse_subject(source, expected):
    assert parse_subject(source) == expected


@pytest.mark.parametrize("source", [
    "张三[身份证]", "张三[身份证:]", "张三[身份证:a,身份证:b]",
    "张三[身份证:a", "[身份证:a]", "张三[身份证:a]]",
])
def test_invalid_subject(source):
    with pytest.raises(ValueError):
        parse_subject(source)
