from app.repositories.oauth_state_repository import OAuthStateRepository


class TestOAuthStateRepository:
    async def test_issued_state_is_consumable_once(self, db_session):
        repo = OAuthStateRepository(db_session)
        state = await repo.issue("slack_install")
        await db_session.commit()

        row = await repo.consume(state, expected_purpose="slack_install")
        await db_session.commit()
        assert row is not None
        assert row.purpose == "slack_install"

    async def test_state_cannot_be_replayed(self, db_session):
        """The CSRF control this exists for only works if a captured state
        can't be reused — see ARCHITECTURE.md's OAuth flows section."""
        repo = OAuthStateRepository(db_session)
        state = await repo.issue("slack_install")
        await db_session.commit()

        first = await repo.consume(state, expected_purpose="slack_install")
        await db_session.commit()
        assert first is not None

        second = await repo.consume(state, expected_purpose="slack_install")
        await db_session.commit()
        assert second is None

    async def test_wrong_purpose_is_rejected(self, db_session):
        repo = OAuthStateRepository(db_session)
        state = await repo.issue("google_link")
        await db_session.commit()

        row = await repo.consume(state, expected_purpose="slack_install")
        await db_session.commit()
        assert row is None

    async def test_expired_state_is_rejected(self, db_session):
        repo = OAuthStateRepository(db_session)
        state = await repo.issue("slack_install", ttl_seconds=-1)
        await db_session.commit()

        row = await repo.consume(state, expected_purpose="slack_install")
        await db_session.commit()
        assert row is None

    async def test_unknown_state_is_rejected(self, db_session):
        repo = OAuthStateRepository(db_session)
        row = await repo.consume("never-issued", expected_purpose="slack_install")
        assert row is None
