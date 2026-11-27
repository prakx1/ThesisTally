from datetime import date

import httpx
import pytest

from thesistally import cli, daemon


@pytest.mark.parametrize("published", [False, True])
def test_after_close_checks_the_official_final_as_the_thesistally_client(
    tmp_path, monkeypatch, published
):
    day = date(2026, 11, 26)
    collected = []

    def exchange(request):
        assert request.method == "HEAD"
        assert request.url.path.endswith("BhavCopy_NSE_CM_0_0_0_20261126_F_0000.csv.zip")
        if request.headers.get("User-Agent") != "ThesisTally/0.1":
            raise httpx.ReadTimeout("unidentified archive probe", request=request)
        return httpx.Response(200 if published else 404)

    original = httpx.Client.__init__

    def client(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(exchange)
        original(self, *args, **kwargs)

    def run_job(root, session, name, work):
        if name == "collect":
            return work() is not False
        return True

    monkeypatch.setattr(httpx.Client, "__init__", client)
    monkeypatch.setattr(daemon, "execute", run_job)
    monkeypatch.setattr(cli, "collect_session", lambda root, d, *_: collected.append(d))
    assert daemon.scan_after_close(tmp_path, day) is published
    assert collected == ([day] if published else [])
