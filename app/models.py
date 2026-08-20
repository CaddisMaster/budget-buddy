from flask_login import UserMixin

from app.db import db_cursor


class User(UserMixin):
    def __init__(self, id, username, password_hash, is_admin, session_token):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.session_token = session_token

    def get_id(self):
        """The value Flask-Login stores in the session and remember cookies.

        ⚠️ Overrides UserMixin's default, which returns the bare primary key —
        i.e. a cookie saying only "user 42", with nothing in it the server can
        invalidate. Since `login_user(..., remember=True)` is unconditional and
        no REMEMBER_COOKIE_DURATION is set, that cookie authenticated for
        Flask-Login's default 365 days, and a password change revoked none of
        it (#224 → #272).

        Appending the token makes the session revocable: rotating
        `users.session_token` makes every cookie carrying the old one fail to
        load. `app.load_user` is the other half and must fail CLOSED.
        """
        return f"{self.id}:{self.session_token}"

    @staticmethod
    def get_by_id(user_id):
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, is_admin, session_token "
                "FROM users WHERE id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
        if row:
            return User(row[0], row[1], row[2], row[3], row[4])
        return None

    @staticmethod
    def get_by_username(username):
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, is_admin, session_token "
                "FROM users WHERE username = %s",
                (username,)
            )
            row = cursor.fetchone()
        if row:
            return User(row[0], row[1], row[2], row[3], row[4])
        return None
