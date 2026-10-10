from pathlib import Path

path = Path("routes/health.py")
src = path.read_text(encoding="utf-8")

if "def healthz" in src:
    print("healthz already present - nothing to do.")
    raise SystemExit(0)

addition = '''

@router.get("/healthz")
def healthz():
    """Liveness probe: always 200 regardless of dependency state."""
    return {"ok": True}
'''

if not src.endswith("\n"):
    src += "\n"
src += addition
path.write_text(src, encoding="utf-8")
print("Added /healthz to routes/health.py")
