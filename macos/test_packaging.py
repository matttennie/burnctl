"""Focused regression tests for the frozen helper's trust-store bootstrap."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

import engine_entry


class TrustStoreTests(unittest.TestCase):
    def run_entry(self, frozen, variables):
        desktop = Mock()
        certificates = Mock()
        certificates.where.return_value = "/bundled/certifi/cacert.pem"
        with patch.dict(sys.modules, {"burnctl.desktop": desktop, "certifi": certificates}), \
                patch.object(sys, "frozen", frozen, create=True), \
                patch.dict(os.environ, variables, clear=True):
            engine_entry.main()
            desktop.main.assert_called_once_with()
            return dict(os.environ)

    def test_frozen_helper_uses_bundled_certificates(self):
        environment = self.run_entry(True, {})
        self.assertEqual(environment["SSL_CERT_FILE"], "/bundled/certifi/cacert.pem")

    def test_explicit_custom_certificates_are_preserved(self):
        environment = self.run_entry(True, {"SSL_CERT_FILE": "/company/ca.pem"})
        self.assertEqual(environment["SSL_CERT_FILE"], "/company/ca.pem")

    def test_source_engine_leaves_trust_store_unchanged(self):
        self.assertNotIn("SSL_CERT_FILE", self.run_entry(False, {}))


if __name__ == "__main__":
    unittest.main()
