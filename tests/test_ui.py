"""
Manual UI smoke test — run in a real terminal:
    python3 test_ui.py
"""
import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from ui.abstract import FormSpec, FieldSpec
from ui.curses_backend import CursesBackend

TEST_FORM = FormSpec(
    title="Gently — UI smoke test",
    subtitle="Navigate with Tab/↑↓, edit with Enter, confirm with F10 or Alt+S",
    fields=[
        FieldSpec(
            key="hostname",
            label="Hostname",
            type="text",
            default="gentoo",
            help="The machine hostname, e.g. 'myhostbox'",
        ),
        FieldSpec(
            key="password",
            label="Root password",
            type="password",
            default=None,
        ),
        FieldSpec(
            key="timezone",
            label="Timezone",
            type="choice",
            default="Europe/Madrid",
            options=["Europe/Madrid", "Europe/London", "America/New_York", "Asia/Tokyo", "UTC"],
        ),
        FieldSpec(
            key="swap",
            label="Enable swap",
            type="bool",
            default=True,
            help="Whether to create a swap partition during installation",
        ),
        FieldSpec(
            key="extra_packages",
            label="Extra packages",
            type="list",
            default=["vim", "git"],
            help="Additional packages to install after base system",
        ),
        FieldSpec(
            key="cpu_cores",
            label="CPU cores (MAKEOPTS)",
            type="int",
            default=4,
        ),
    ],
)

SUMMARY_SECTIONS = [
    ("system",   {"hostname": "gentoo", "timezone": "Europe/Madrid"}),
    ("portage",  {"profile": "default/linux/amd64/23.0", "jobs": 4}),
    ("packages", {"extra": "vim, git"}),
]

backend = CursesBackend()

print("=== Test 1: show_form ===")
result = backend.show_form(TEST_FORM)
print("Returned values:")
for k, v in result.items():
    print(f"  {k!r}: {v!r}")

print()
print("=== Test 2: show_confirm ===")
answer = backend.show_confirm("Proceed to summary screen?")
print(f"  Confirmed: {answer}")

if answer:
    print()
    print("=== Test 3: show_summary ===")
    action = backend.show_summary(SUMMARY_SECTIONS)
    print(f"  Action chosen: {action!r}")

print()
print("=== Test 4: show_info ===")
backend.show_info("Smoke test", ["All basic tests completed.", "The backend is working correctly."])

print()
print("=== Test 5: show_error ===")
backend.show_error("This is a test error message.\nLine two of the error.")

print()
print("All tests done.")
