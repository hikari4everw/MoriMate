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
    assert parser.feed('{"zh":"你好。","ja":"こんにちは。","emotion":"unknown"}')[0].emotion == "neutral"
    assert parser.finish() == []


def test_complete_object_does_not_wait_for_newline_or_next_token():
    parser = BilingualParser()
    assert parser.feed('{"zh":"括号 } 与引号 \\"","ja":"はい。"') == []
    assert parser.feed('}')[0].ja == "はい。"
    assert parser.feed('\n  {"zh":"第二句","ja":"二。"}')[0].zh == "第二句"
    assert parser.finish() == []


@pytest.mark.parametrize("suffix", [" trailing\n", ",\n", " ]\n"])
def test_trailing_non_protocol_data_is_rejected(suffix):
    parser = BilingualParser()
    parser.feed('{"zh":"你好","ja":"はい"}')
    with pytest.raises(ValueError):
        parser.feed(suffix)


def test_bounds_unterminated_generation():
    with pytest.raises(ValueError):
        BilingualParser().feed("x" * 17000)


@pytest.mark.parametrize("tone", ["shy", "combat", "self_deprecating"])
def test_voice_tone_is_independent_of_expression(tone):
    sentence = BilingualParser().feed(
        '{"zh":"嗯。","ja":"はい。","emotion":"joy","voice_tone":"' + tone + '"}\n'
    )[0]
    assert sentence.emotion == "joy"
    assert sentence.voice_tone == tone


@pytest.mark.parametrize("tone", [None, [], {}, "unknown", 1])
def test_invalid_voice_tone_falls_back_without_losing_text(tone):
    import json

    sentence = BilingualParser().feed(
        json.dumps({"zh": "嗯。", "ja": "はい。", "voice_tone": tone}) + "\n"
    )[0]
    assert sentence.voice_tone == "neutral"
