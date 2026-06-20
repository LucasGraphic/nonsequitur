import sys
import subprocess

PATCH_FILE = sys.argv[1] if len(sys.argv) > 1 else r"pipeline\generate_run.py"

# Try to import ftfy, install if missing
try:
    import ftfy
except ImportError:
    print("Installing ftfy...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ftfy", "--quiet"])
    import ftfy

with open(PATCH_FILE, "rb") as f:
    raw = f.read()

# Decode as latin-1 to get unicode with mojibake chars
content_latin1 = raw.decode("latin-1")

# Fix with ftfy
fixed = ftfy.fix_text(content_latin1)

if fixed == content_latin1:
    print("  No changes made by ftfy.")
else:
    diff = sum(1 for a, b in zip(content_latin1, fixed) if a != b)
    print(f"  ftfy fixed ~{diff} characters")
    with open(PATCH_FILE, "w", encoding="utf-8") as f:
        f.write(fixed)
    print(f"OK: written to {PATCH_FILE} (clean UTF-8)")
