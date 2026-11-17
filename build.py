"""Copy frontend/ into public/ so Vercel serves the UI from the CDN."""
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent
source = root / "frontend"
dest = root / "public"

if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(source, dest)
