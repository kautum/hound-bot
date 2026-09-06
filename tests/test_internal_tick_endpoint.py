from cryptography.fernet import Fernet


class TestInternalTickEndpoint:
    async def test_rejects_missing_or_wrong_secret(self, api_client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes_internal.settings.cron_shared_secret", "the-real-secret"
        )

        no_header = await api_client.post("/internal/tick")
        assert no_header.status_code == 401

        wrong_header = await api_client.post(
            "/internal/tick", headers={"X-Cron-Secret": "wrong"}
        )
        assert wrong_header.status_code == 401

    async def test_accepts_correct_secret_and_runs(self, api_client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes_internal.settings.cron_shared_secret", "the-real-secret"
        )
        monkeypatch.setattr(
            "app.api.routes_internal.settings.encryption_key", Fernet.generate_key().decode()
        )
        monkeypatch.setattr("app.api.routes_internal.settings.encryption_key_version", 1)

        response = await api_client.post(
            "/internal/tick", headers={"X-Cron-Secret": "the-real-secret"}
        )
        assert response.status_code == 200
        assert response.json() == {"reminders_sent": 0}
