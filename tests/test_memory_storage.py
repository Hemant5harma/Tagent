import json
from datetime import UTC, datetime, timedelta

from trade_agent.db import ConversationMemoryRecord, Database
from trade_agent.memory import context, forget, prune_expired, remember


def database(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'memory.db'}")
    db.create_all()
    return db


def write(db, owner, update, user="sell 0.05 lots", assistant="Which symbol?"):
    with db.session() as session:
        remember(session, owner, str(update), user, assistant)


def test_persistent_scoped_turns_and_duplicate_updates(tmp_path):
    db = database(tmp_path)
    write(db, "owner1:account1", 1)
    write(db, "owner1:account1", 1, "DUPLICATE")
    write(db, "owner2:account1", 1, "unrelated")
    restarted = Database(str(db.engine.url))
    result = context(restarted, "owner1:account1")
    assert len(result["recent_messages"]) == 2
    assert result["recent_messages"][0]["content"] == "sell 0.05 lots"
    assert not context(db, "owner1:account2")["recent_messages"]


def test_memory_is_bounded_and_old_replay_cannot_resurrect(tmp_path):
    db = database(tmp_path)
    for update in range(1, 301):
        write(db, "owner:account", update, "user " + "x" * 1000, "reply " + "y" * 1000)
    write(db, "owner:account", 1, "OLD_REPLAY")
    result = context(db, "owner:account")
    assert len(result["recent_messages"]) <= 24
    assert sum(len(m["content"]) for m in result["recent_messages"]) <= 16000
    assert len(result["summary"]) <= 4000
    assert "OLD_REPLAY" not in json.dumps(result)
    assert "not current broker facts" in result["label"]


def test_forget_only_affects_selected_memory(tmp_path):
    db = database(tmp_path)
    write(db, "one", 1)
    write(db, "two", 2)
    forget(db, "one")
    assert not context(db, "one")["recent_messages"]
    assert context(db, "two")["recent_messages"]
    with db.session() as session:
        assert session.get(ConversationMemoryRecord, "one") is None


def test_ttl_purges_recent_and_summary_but_retains_replay_barrier(tmp_path):
    db = database(tmp_path)
    write(db, "owner", 42)
    with db.session() as session:
        record = session.get(ConversationMemoryRecord, "owner")
        payload = dict(record.payload)
        expired = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        payload["recent"][0]["at"] = expired
        payload["recap"] = [{"at": expired, "text": "expired summary"}]
        record.payload = payload
    prune_expired(db)
    result = context(db, "owner")
    assert not result["summary"] and not result["recent_messages"]
    write(db, "owner", 42)
    assert not context(db, "owner")["recent_messages"]


def test_sensitive_content_redacted_before_storage_and_provider_context(tmp_path):
    db = database(tmp_path)
    values = [
        "password is sneaky-secret",
        "bot token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Login code: ABCD1234EFGH",
        "-----BEGIN RSA PRIVATE KEY----- private material",
        "https://example.test/login?code=secret-code",
    ]
    for i, value in enumerate(values):
        write(db, "owner", i, value, value)
    with db.session() as session:
        remember(session, "owner", "10", "my known-secret", "reply known-secret", ("known-secret",))
    with db.session() as session:
        stored = json.dumps(session.get(ConversationMemoryRecord, "owner").payload)
    for secret in (
        "sneaky-secret",
        "AAAAAAAAAAAAAAAA",
        "ABCD1234EFGH",
        "private material",
        "secret-code",
        "known-secret",
    ):
        assert secret not in stored
        assert secret not in json.dumps(context(db, "owner"))


def test_turn_rolls_back_with_reply_transaction(tmp_path):
    db = database(tmp_path)
    try:
        with db.session() as session:
            remember(session, "owner", "1", "hello", "hello")
            raise RuntimeError("reply not committed")
    except RuntimeError:
        pass
    assert not context(db, "owner")["recent_messages"]
