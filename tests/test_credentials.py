import os
import subprocess
import sys
import unittest

from novel_studio.credentials import protect_key, unprotect_key


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
class CredentialTests(unittest.TestCase):
    def test_encrypted_key_readable_in_new_process(self):
        key = 'test-credential-not-a-real-key'
        encrypted = protect_key(key)
        self.assertNotIn(key, encrypted)
        env = os.environ.copy()
        env['STUDIO_TEST_PROTECTED_KEY'] = encrypted
        result = subprocess.run([sys.executable, '-c',
            'import os; from novel_studio.credentials import unprotect_key; '
            'assert unprotect_key(os.environ["STUDIO_TEST_PROTECTED_KEY"]) == "test-credential-not-a-real-key"'],
            env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)

    def test_empty_key_clears_and_bad_data_fails(self):
        self.assertEqual(protect_key(''), '')
        self.assertEqual(unprotect_key(''), '')
        with self.assertRaises(Exception):
            unprotect_key('invalid ciphertext')
