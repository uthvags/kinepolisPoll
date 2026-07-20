"""
Set / change the admin-editor password for the voting page.

Stores only the SHA-256 hash (never the plaintext) in `admin_pass.hash`, which
generate_voting_page() auto-loads on every future build. Also patches the hash
into an already-published index.html so the current live poll is gated too.

Usage:
    py set_admin_password.py            # prompts for the password (hidden)
    py set_admin_password.py --show     # also print the computed hash
"""
import argparse
import getpass
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HASH_FILE = os.path.join(HERE, "admin_pass.hash")
INDEX_HTML = os.path.join(HERE, "index.html")


def patch_index_html(hash_hex: str) -> bool:
    """Replace the ADMIN_PASS_HASH value in index.html if the file has it."""
    if not os.path.exists(INDEX_HTML):
        return False
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    new_html, n = re.subn(
        r'const ADMIN_PASS_HASH = "[^"]*";',
        f'const ADMIN_PASS_HASH = "{hash_hex}";',
        html,
    )
    if n == 0:
        return False
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(new_html)
    return True


def main():
    ap = argparse.ArgumentParser(description="Set the admin-editor password.")
    ap.add_argument("--show", action="store_true", help="Print the computed hash")
    args = ap.parse_args()

    pw = getpass.getpass("New admin password: ")
    if not pw:
        print("Empty password — aborted.")
        sys.exit(1)
    if getpass.getpass("Confirm password: ") != pw:
        print("Passwords did not match — aborted.")
        sys.exit(1)

    hash_hex = hashlib.sha256(pw.encode("utf-8")).hexdigest()

    with open(HASH_FILE, "w", encoding="utf-8") as f:
        f.write(hash_hex + "\n")
    print(f"Wrote {os.path.relpath(HASH_FILE, HERE)} (future builds are gated).")

    if patch_index_html(hash_hex):
        print("Patched index.html — the current published poll is now gated.")
    else:
        print("Note: index.html not patched (file missing or no ADMIN_PASS_HASH "
              "line). Regenerate the page to embed the gate.")

    if args.show:
        print(f"hash: {hash_hex}")


if __name__ == "__main__":
    main()
