import pytest

from open_llm_vtuber.companion.bilingual import BilingualParser


def test_waits_for_complete_pair_and_preserves_languages():
    parser = BilingualParser()
    assert parser.feed('{"zh":"又翻车啦。","ja":') == []
    result = parser.feed('"また失敗したね。","emotion":"smirk"}\n')
    assert [(s.zh, s.ja, s.emotion) for s in result] == [
        ("又翻车啦。", "また失敗したね。", "smirk")
    ]


def test_rejects_thinking_and_missing_japanese_instead_of_speaking_json():
    parser = BilingualParser()
    with pytest.raises(ValueError):
        parser.feed("<think>secret</think>\n")
    with pytest.raises(ValueError):
        BilingualParser().feed('{"zh":"你好"}\n')


def test_final_line_without_newline_and_unknown_emotion():
    parser = BilingualParser()
    assert parser.feed('{"zh":"你好。","ja":"こんにちは。","emotion":"unknown"}') == []
    assert parser.finish()[0].emotion == "neutral"


def test_bounds_unterminated_generation():
    with pytest.raises(ValueError):
        BilingualParser().feed("x" * 17000)
