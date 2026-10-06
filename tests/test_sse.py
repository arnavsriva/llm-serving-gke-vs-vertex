from bench.sse import SSEParser

STREAM = (
    b'data: {"a": 1}\n\n'
    b": keep-alive\n\n"
    b"data: line1\r\ndata: line2\r\n\r\n"
    b'event: message\nid: 7\ndata: {"c": "\xc3\xa9"}\n\n'
    b"data:no-space\n\n"
    b"data: [DONE]\n\n"
)
EXPECTED = ['{"a": 1}', "line1\nline2", '{"c": "é"}', "no-space", "[DONE]"]


def feed_all(chunks):
    parser = SSEParser()
    events = []
    for chunk in chunks:
        events += parser.feed(chunk)
    return events + parser.flush()


def test_whole_stream():
    assert feed_all([STREAM]) == EXPECTED


def test_byte_by_byte():
    assert feed_all([STREAM[i : i + 1] for i in range(len(STREAM))]) == EXPECTED


def test_every_two_way_split():
    for cut in range(len(STREAM) + 1):
        assert feed_all([STREAM[:cut], STREAM[cut:]]) == EXPECTED, cut


def test_flush_dispatches_unterminated_event():
    parser = SSEParser()
    assert parser.feed(b"data: complete\n\ndata: tail") == ["complete"]
    assert parser.flush() == ["tail"]


def test_comment_only_stream_has_no_events():
    assert feed_all([b": ping\n\n: ping\n\n"]) == []
