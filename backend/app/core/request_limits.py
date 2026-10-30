"""Transaction-local PostgreSQL deadlines, with no pooled session-state leakage."""

from time import monotonic

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.work_limits import WorkLimit

REQUEST_SECONDS = 45


@event.listens_for(Session, "after_begin")
def apply_limits(session, transaction, connection):
    deadline = session.info.get("request_deadline")
    if deadline is None or transaction.nested:
        return
    connection.info["request_deadline"] = deadline
    if connection.dialect.name == "postgresql":
        # pg_settings.setting is expressed in milliseconds for these settings.
        connection.execute(text("""
            SELECT set_config(name, (CASE WHEN setting::bigint=0 THEN budget
                        ELSE least(setting::bigint,budget) END)::text || 'ms', true)
            FROM pg_settings JOIN (VALUES
              ('lock_timeout',3000::bigint), ('statement_timeout',15000::bigint),
              ('idle_in_transaction_session_timeout',60000::bigint)) AS bounds(name,budget) USING(name)
        """))


@event.listens_for(Engine, "before_cursor_execute")
def check_deadline(connection, cursor, statement, parameters, context, executemany):
    deadline = connection.info.get("request_deadline")
    if deadline is not None and monotonic() > deadline:
        raise WorkLimit("Request work budget expired. Check the current record before resubmitting.", busy=True)


@event.listens_for(Engine, "commit")
def commit_deadline(connection):
    deadline = connection.info.pop("request_deadline", None)
    if deadline is not None and monotonic() > deadline:
        raise WorkLimit("Request work budget expired before commit. Check the current record before resubmitting.", busy=True)


@event.listens_for(Engine, "rollback")
def clear_deadline(connection):
    # Accessing .info reconnects an invalidated DBAPI connection. Rollback must
    # finish first; the discarded connection's deadline cannot leak into reuse.
    if connection.closed or connection.invalidated:
        return
    connection.info.pop("request_deadline", None)
