"""The suite refuses to run against a database that already holds users (#400).

`test.sh` rebuilds `budget_test` from sql/schema.sql before each runner and CI
loads a fresh database, so a run always STARTS with no users. The guard is what
turns a run pointed at the wrong database — the dev one, whose `DB_NAME` the
test process inherits unless test.sh overrides it — into a refusal instead of a
sweep across real users' ledgers.

The guard's passing side needs no test of its own: every run exercises it, in
`pytest_configure` and in behave's `before_all`. What is tested here is the
refusing side, which a normal run never reaches.
"""
import pytest

from tests.helpers import refuse_a_database_that_holds_users


@pytest.mark.criterion(400, "A run that sweeps every user reaches only test users")
def test_a_database_that_holds_users_is_refused(users):
    """`users` has just created this worker's test users, so the database is
    exactly as non-empty as a real one — and must be refused."""
    with pytest.raises(RuntimeError) as refused:
        refuse_a_database_that_holds_users()
    message = str(refused.value)
    assert "Refusing to run the tests" in message
    assert "./test.sh" in message, "the refusal should say how to run the suite"
