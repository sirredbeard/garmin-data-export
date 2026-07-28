import unittest
from pathlib import Path
from unittest.mock import Mock

from garmin_export import _persist_auth_tokens


class AuthTokenStorageTests(unittest.TestCase):
    def test_persists_tokens_with_legacy_garth_client(self):
        garth_client = Mock()
        garmin = Mock(garth=garth_client)

        _persist_auth_tokens(garmin, Path("tokens"))

        garth_client.dump.assert_called_once_with("tokens")

    def test_persists_tokens_with_current_native_client(self):
        native_client = Mock()

        class GarminStub:
            client = native_client

        _persist_auth_tokens(GarminStub(), Path("tokens"))

        native_client.dump.assert_called_once_with("tokens")

    def test_rejects_unknown_token_storage_api(self):
        with self.assertRaisesRegex(RuntimeError, "token storage API not found"):
            _persist_auth_tokens(object(), Path("tokens"))


if __name__ == "__main__":
    unittest.main()
