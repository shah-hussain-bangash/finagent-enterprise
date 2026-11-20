"""Opt-in database identity tests; never connects to a database or Azure."""

import sys
import types
import unittest
from unittest.mock import patch

from sqlalchemy import event

from finagent.database.session import get_engine


class DatabaseAuthModeTests(unittest.TestCase):
    def tearDown(self):
        get_engine.cache_clear()

    def test_password_mode_preserves_existing_default(self):
        url = "postgresql+psycopg://local:secret@localhost/finagent_test"
        engine = get_engine(url)
        self.assertEqual(engine.url.host, "localhost")
        self.assertEqual(engine.url.password, "secret")
        engine.dispose()

    def test_unknown_mode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            get_engine("postgresql+psycopg://localhost/db", "unknown")

    def test_azure_mode_attaches_token_before_driver_connect(self):
        azure = types.ModuleType("azure")
        azure.__path__ = []
        identity = types.ModuleType("azure.identity")
        scopes = []

        class Credential:
            def get_token(self, scope):
                scopes.append(scope)
                return types.SimpleNamespace(token="synthetic-token")

            def close(self):
                pass

        identity.WorkloadIdentityCredential = Credential
        url = "postgresql+psycopg://identity@demo.postgres.database.azure.com/db?sslmode=verify-full"
        with patch.dict(sys.modules, {"azure": azure, "azure.identity": identity}):
            engine = get_engine(url, "azure_workload_identity")

        seen = []

        def stop(dialect, record, args, params):
            seen.append(params["password"])
            raise RuntimeError("stopped before network")

        event.listen(engine, "do_connect", stop)
        try:
            with self.assertRaisesRegex(RuntimeError, "stopped before network"):
                engine.connect()
        finally:
            engine.dispose()
        self.assertEqual(seen, ["synthetic-token"])
        self.assertEqual(scopes, ["https://ossrdbms-aad.database.windows.net/.default"])

    def test_azure_mode_rejects_local_url_before_credential_creation(self):
        azure = types.ModuleType("azure")
        azure.__path__ = []
        identity = types.ModuleType("azure.identity")

        def never_create():
            self.fail("credential should not be created")

        identity.WorkloadIdentityCredential = never_create
        with patch.dict(sys.modules, {"azure": azure, "azure.identity": identity}):
            with self.assertRaises(ValueError):
                get_engine("postgresql+psycopg://localhost/db", "azure_workload_identity")


if __name__ == "__main__":
    unittest.main()
