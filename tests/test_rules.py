from qa_parser.core import _split_fields


def test_letter_mentions_inside_options_are_not_boundaries():
    raw = "实验题\nA．通电做功\nB．观察A、B管液面\nC．通过B、C装置测量\nD．比较A、E电阻\n【答案】A\n【详解】原文解析"
    fields, issues = _split_fields(raw, "choice")
    assert not issues
    assert fields["options"] == {"A": "通电做功", "B": "观察A、B管液面", "C": "通过B、C装置测量", "D": "比较A、E电阻"}
