from open_llm_vtuber.companion.observation import StableText, CommentGate


def test_typewriter_settles_and_same_screen_is_not_repeated():
    tracker = StableText()
    assert tracker.feed("你好", 0) is None
    assert tracker.feed("你好，朋友", 0.4) is None
    assert tracker.feed("你好，朋友", 1.3) == "你好，朋友"
    assert tracker.feed("你好，朋友", 3) is None
    tracker.reset()
    assert tracker.feed("你好，朋友", 4) is None
    assert tracker.feed("你好，朋友", 5) == "你好，朋友"


def test_empty_or_low_confidence_text_does_not_become_observation():
    t = StableText()
    assert t.feed("", 0) is None
    assert t.feed("错字", 1, confidence=0.2) is None
    assert t.feed("错字", 3, confidence=0.2) is None


def test_user_priority_cooldown_and_expiry():
    gate = CommentGate("natural")
    assert not gate.allowed(now=50, observed_at=49, busy=True)
    assert not gate.allowed(now=50, observed_at=20, busy=False)
    assert gate.allowed(now=50, observed_at=49, busy=False)
    gate.spoke(50)
    assert not gate.allowed(now=55, observed_at=54, busy=False)
    gate.enabled = False
    assert not gate.allowed(now=1000, observed_at=999, busy=False)
