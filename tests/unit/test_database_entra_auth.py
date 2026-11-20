"""Synthetic checks for the inactive database token callback; no Azure access required."""

import unittest
from types import SimpleNamespace

from finagent.database.entra_auth import POSTGRES_SCOPE, PostgresTokenCallback


class DatabaseTokenTests(unittest.TestCase):
    def test_new_connections_request_tokens_independently(self):
        class Credential:
            def __init__(self):
                self.calls = []

            def get_token(self, scope):
                self.calls.append(scope)
                return SimpleNamespace(token=f"synthetic-{len(self.calls)}")

        credential = Credential()
        callback = PostgresTokenCallback(credential)
        first, second = {}, {}
        callback(None, None, [], first)
        callback(None, None, [], second)
        self.assertEqual(credential.calls, [POSTGRES_SCOPE, POSTGRES_SCOPE])
        self.assertEqual((first["password"], second["password"]), ("synthetic-1", "synthetic-2"))

    def test_failure_does_not_replace_password_or_expose_credential_error(self):
        class Credential:
            def get_token(self, scope):
                raise ValueError("sensitive-example")

        params = {"password": "original"}
        with self.assertRaisesRegex(RuntimeError, "Could not obtain") as error:
            PostgresTokenCallback(Credential())(None, None, [], params)
        self.assertNotIn("sensitive-example", str(error.exception))
        self.assertEqual(params["password"], "original")

    def test_empty_token_is_rejected(self):
        credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token=""))
        with self.assertRaises(RuntimeError):
            PostgresTokenCallback(credential)(None, None, [], {})


if __name__ == "__main__":
    unittest.main()
