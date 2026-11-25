"""Real MySQL checks in a uniquely created disposable database.

Opt in using FORMAAI_TEST_MYSQL_URL, pointing to a dedicated test MySQL 8.4
server with CREATE/DROP DATABASE permission. No existing database is migrated
or dropped. The fixture touches only its generated formaai_test_<uuid> schema.
"""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from formaai.infrastructure.database import build_engine
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Category

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mysql_engine():
    raw_url = os.environ.get("FORMAAI_TEST_MYSQL_URL")
    if not raw_url:
        pytest.skip("Real MySQL NOT TESTED: FORMAAI_TEST_MYSQL_URL not set")
    url = make_url(raw_url)
    if url.drivername != "mysql+pymysql":
        pytest.fail("Tests require the selected mysql+pymysql driver")
    database_name = f"formaai_test_{uuid4().hex}"
    admin = build_engine(url.set(database=None))
    with admin.begin() as connection:
        connection.execute(text(f"CREATE DATABASE `{database_name}`"))
    engine = build_engine(url.set(database=database_name))
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with engine.begin() as connection:
            version = str(connection.scalar(text("SELECT VERSION()")))
            assert version.startswith("8.4."), f"Expected MySQL 8.4, got {version}"
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP DATABASE `{database_name}`"))
        admin.dispose()


@pytest.fixture
def session(mysql_engine):
    with mysql_engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        transaction.rollback()


def add_user(session, email):
    user = UserAccount(email=email, password_hash="synthetic-test-hash")
    session.add(user)
    session.flush()
    return user


@contextmanager
def check_violation(session, constraint_name):
    """PyMySQL reports MySQL CHECK violation 3819 as OperationalError."""
    with pytest.raises(OperationalError) as captured, session.begin_nested():
        yield
    assert captured.value.orig.args[0] == 3819
    assert constraint_name in str(captured.value.orig)


def test_business_tables_and_utc_connection(mysql_engine):
    assert set(inspect(mysql_engine).get_table_names()) == {
        "alembic_version",
        "user_account",
        "category",
        "material",
        "asset",
        "video_task",
        "video_shot",
        "video_attempt",
        "video_decision",
        "usage_record",
    }
    with mysql_engine.connect() as connection:
        assert connection.scalar(text("SELECT @@session.time_zone")) == "+00:00"


def test_deleted_email_remains_reserved_case_insensitively(session):
    user = add_user(session, "owner@example.test")
    user.status = "deleted"
    user.deleted_at = datetime.now(UTC)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        add_user(session, "OWNER@example.test")
    user.status = "active"
    user.deleted_at = None
    session.flush()
    assert session.scalar(select(UserAccount.id)) == user.id


def test_category_unique_within_owner_and_type(session):
    first = add_user(session, "first@example.test")
    second = add_user(session, "second@example.test")
    session.add_all(
        [
            Category(user_id=first.id, content_type="commerce", name="Tech"),
            Category(user_id=second.id, content_type="commerce", name="Tech"),
            Category(user_id=first.id, content_type="knowledge", name="Tech"),
        ]
    )
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Category(user_id=first.id, content_type="commerce", name="tech"))
        session.flush()


def test_account_state_and_deletion_timestamp_must_agree(session):
    user = add_user(session, "state@example.test")
    with check_violation(session, "ck_user_account_deleted_at"):
        user.status = "deleted"
        session.flush()
    with check_violation(session, "ck_user_account_status"):
        user.status = "invented"
        session.flush()


def test_category_requires_existing_user_and_nonblank_name(session):
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Category(user_id=9223372036854775806, content_type="story", name="A")
        )
        session.flush()
    user = add_user(session, "category@example.test")
    with check_violation(session, "ck_category_name"):
        session.add(Category(user_id=user.id, content_type="story", name="   "))
        session.flush()


def test_physical_account_deletion_does_not_cascade_categories(session):
    user = add_user(session, "retained@example.test")
    session.add(Category(user_id=user.id, content_type="brand", name="Brand"))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.delete(user)
        session.flush()


def test_microsecond_timestamp_round_trip(session):
    user = add_user(session, "timestamp@example.test")
    instant = datetime(
        2026, 11, 21, 13, 14, 15, 123456, tzinfo=timezone(timedelta(hours=8))
    )
    user.email_verified_at = instant
    session.flush()
    session.expire(user)
    assert user.email_verified_at == instant
    assert user.email_verified_at.tzinfo is UTC
    assert user.email_verified_at.microsecond == 123456


def test_invalid_role_version_and_content_type_are_rejected(session):
    user = add_user(session, "constraints@example.test")
    with check_violation(session, "ck_user_account_role"):
        user.role = "superuser"
        session.flush()
    with check_violation(session, "ck_user_account_version"):
        user.version = -1
        session.flush()
    with check_violation(session, "ck_category_content_type"):
        session.add(Category(user_id=user.id, content_type="invented", name="A"))
        session.flush()


def test_reapplying_head_preserves_existing_rows_and_schema(mysql_engine):
    """There is one revision so far: verify safe reapplication, not an old upgrade."""
    with Session(mysql_engine) as seeded:
        user = add_user(seeded, "migration-reapply@example.test")
        category = Category(user_id=user.id, content_type="knowledge", name="Retained")
        seeded.add(category)
        seeded.commit()
        user_id, category_id = user.id, category.id
    before = {
        table: inspect(mysql_engine).get_indexes(table)
        for table in ("user_account", "category")
    }
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with mysql_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20261123_07"
            )
        with Session(mysql_engine) as checked:
            assert (
                checked.get(UserAccount, user_id).email
                == "migration-reapply@example.test"
            )
            assert checked.get(Category, category_id).name == "Retained"
        after = {
            table: inspect(mysql_engine).get_indexes(table)
            for table in ("user_account", "category")
        }
        assert before == after
    finally:
        with mysql_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM category WHERE id = :id"), {"id": category_id}
            )
            connection.execute(
                text("DELETE FROM user_account WHERE id = :id"), {"id": user_id}
            )
