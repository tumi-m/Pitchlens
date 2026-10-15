"""Concurrent cache misses must parse a match's result.json once, not once per request:
each full parse of a 90-minute result holds ~280 MB, and 24 parallel GET /teams took
the real worker to 6.2 GB (membomb.py)."""
import json
import threading
import time

from tests.test_team_fix import JOB, job  # noqa: F401  (fixture)


def test_concurrent_team_requests_parse_the_result_once(job, monkeypatch):  # noqa: F811
    server, client, directory = job
    parses = []
    real_loads = json.loads

    def counting_loads(text, *a, **kw):
        if isinstance(text, str) and '"frames"' in text and len(text) > 1000:
            parses.append(1)
            time.sleep(0.3)  # a large result takes about a second to parse
        return real_loads(text, *a, **kw)

    monkeypatch.setattr(server.json, "loads", counting_loads)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(client.get(f"/jobs/{JOB}/teams").status_code)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200] * 6
    # One parse for the team view and at most one for the slim view.
    assert len(parses) <= 2, f"{len(parses)} full parses for 6 concurrent requests"


def test_concurrent_reruns_read_the_source_result_one_at_a_time(job, monkeypatch):  # noqa: F811
    """POST /rerun reads the source's kit colours (a full parse) before any check."""
    import uuid

    server, client, directory = job
    real = server.kit_colours
    running, most = [0], [0]
    count = threading.Lock()

    def tracked(path):
        with count:
            running[0] += 1
            most[0] = max(most[0], running[0])
        try:
            time.sleep(0.2)  # a large result takes about a second to parse
            return real(path)
        finally:
            with count:
                running[0] -= 1

    monkeypatch.setattr(server, "kit_colours", tracked)

    def rerun():
        client.post(f"/jobs/{JOB}/rerun?mode=section&start=0&requestId={uuid.uuid4().hex}")

    threads = [threading.Thread(target=rerun) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 1, f"{most[0]} full parses at once for 4 concurrent reruns"
