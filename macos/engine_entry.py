"""Frozen desktop helper entry point; no shell or CLI action dispatch."""

import os
import sys


def main():
    # A frozen Homebrew Python must not depend on the build Mac's OpenSSL CA
    # directory. Respect an explicitly supplied enterprise/custom trust bundle.
    if getattr(sys, "frozen", False):
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())

    from burnctl.desktop import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    sys.exit(main())
