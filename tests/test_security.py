import unittest

from netryx_web.security import UnsafeUrlError, validate_listing_url


class ListingUrlSecurityTests(unittest.TestCase):
    def test_accepts_public_https_url(self):
        self.assertEqual(
            validate_listing_url("https://example.com/annonce/123"),
            "https://example.com/annonce/123",
        )

    def test_rejects_unsafe_schemes_and_credentials(self):
        for value in (
            "javascript:alert(1)",
            "file:///etc/passwd",
            "http://user:password@example.com/listing",
        ):
            with self.subTest(value=value), self.assertRaises(UnsafeUrlError):
                validate_listing_url(value)

    def test_rejects_local_and_private_hosts(self):
        for value in (
            "http://localhost/listing",
            "http://127.0.0.1/listing",
            "http://10.1.2.3/listing",
            "http://[::1]/listing",
        ):
            with self.subTest(value=value), self.assertRaises(UnsafeUrlError):
                validate_listing_url(value)

    def test_rejects_missing_host_and_overlong_url(self):
        with self.assertRaises(UnsafeUrlError):
            validate_listing_url("https:///listing")
        with self.assertRaises(UnsafeUrlError):
            validate_listing_url("https://example.com/" + "x" * 2048)


if __name__ == "__main__":
    unittest.main()
