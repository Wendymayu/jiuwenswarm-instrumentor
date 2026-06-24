from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs


def test_context_roundtrip():
    token = set_request_context(session_id="s1", channel_id="c1", request_id="r1")
    try:
        attrs = current_request_attrs()
        assert attrs["jiuwenclaw.session.id"] == "s1"
        assert attrs["jiuwenclaw.channel.id"] == "c1"
        assert attrs["jiuwenclaw.request.id"] == "r1"
    finally:
        token.reset()


def test_context_empty():
    assert current_request_attrs() == {}
