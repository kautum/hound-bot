"""S7: /health used to be a static {"status": "ok"} forever, even after the
worker task had silently died — a health check that can never go unhealthy
is decoration, not monitoring.
"""


class TestHealthEndpoint:
    async def test_reports_disabled_when_no_encryption_key(self, api_client, monkeypatch):
        monkeypatch.setattr("app.main.settings.encryption_key", None)
        response = await api_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "worker": "disabled"}

    async def test_reports_degraded_when_worker_has_never_polled(self, api_client, monkeypatch):
        import app.worker as worker_module

        monkeypatch.setattr("app.main.settings.encryption_key", "some-key")
        monkeypatch.setattr(worker_module, "_last_poll_completed_utc", None)

        response = await api_client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"

    async def test_reports_ok_when_worker_has_polled_recently(self, api_client, monkeypatch):
        from datetime import UTC, datetime

        import app.worker as worker_module

        monkeypatch.setattr("app.main.settings.encryption_key", "some-key")
        monkeypatch.setattr(worker_module, "_last_poll_completed_utc", datetime.now(UTC))

        response = await api_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "worker": "alive"}
