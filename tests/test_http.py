import httpx
import pytest

from research_agent.infrastructure.http import HttpClient, HttpError, RateLimiter, redact


def client(handler, sleeps=None, **kw):
    sl = sleeps if sleeps is not None else []
    return HttpClient("test-agent", transport=httpx.MockTransport(handler), sleep=sl.append, **kw), sl


def test_retries_then_succeeds_and_counts_requests():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={"ok": 1})

    c, sleeps = client(h)
    assert c.get_json("https://x.test/a", host="x") == {"ok": 1}
    assert c.requests["x"] == 3 and len(sleeps) >= 2


def test_retry_after_is_honoured_and_capped():
    n = []

    def h(req):
        n.append(1)
        return httpx.Response(429, headers={"Retry-After": "7"}) if len(n) == 1 else httpx.Response(200, json={})

    c, sleeps = client(h)
    c.get("https://x.test/", host="x", min_interval=0)
    assert 7 in sleeps
    c2, s2 = client(lambda r: httpx.Response(429, headers={"Retry-After": "9999"}), max_tries=2)
    with pytest.raises(HttpError):
        c2.get("https://x.test/", host="x", min_interval=0)
    assert max(s2) <= 60


def test_client_errors_are_not_retried():
    c, sleeps = client(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(HttpError) as e:
        c.get("https://x.test/", host="x")
    assert e.value.status == 404 and c.requests["x"] == 1


def test_gives_up_after_max_tries():
    c, _ = client(lambda r: httpx.Response(500), max_tries=3)
    with pytest.raises(HttpError) as e:
        c.get("https://x.test/", host="x", min_interval=0)
    assert c.requests["x"] == 3 and e.value.status is None


def test_transport_errors_are_retried():
    n = []

    def h(req):
        n.append(1)
        if len(n) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"a": 1})

    c, _ = client(h)
    assert c.get_json("https://x.test/", host="x", min_interval=0) == {"a": 1}


def test_secrets_never_reach_error_messages():
    assert "SECRET" not in redact("GET https://a/b?api_key=SECRET&x=1 and key=SECRET2 token=SECRET3")
    assert "api_key=***" in redact("https://a/b?api_key=SECRET")
    c, _ = client(lambda r: httpx.Response(400, text="bad request for api_key=SECRET"))
    with pytest.raises(HttpError) as e:
        c.get("https://x.test/?api_key=SECRET", host="x")
    assert "SECRET" not in str(e.value)


def test_rate_limiter_spaces_calls():
    now = [100.0]
    slept = []

    def sleep(d):
        slept.append(d)
        now[0] += d

    rl = RateLimiter(0.5, clock=lambda: now[0], sleep=sleep)
    rl.wait(); rl.wait(); rl.wait()
    assert slept == [pytest.approx(0.5), pytest.approx(0.5)]  # first call is free, then 0.5s apart
