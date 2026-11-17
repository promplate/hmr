"""Run from a fetched promplate/hmr checkout, then run.py base|patched base|fixed|disabled."""
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parent
base = '9b586ea285be243d599ce4652539ad984ea19c50'
patch = '7e90b00f1e17e4d62b6226927812a0a5738686a1'
fix = 'c22311abe21c7d97ce4620788529b2c8da139aea'
for sha in (base, patch, fix):
    subprocess.run(['git', 'fetch', 'origin', sha], check=True)
def copy(sha, path, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(['git', 'show', f'{sha}:{path}']))
for label, sha in [('base', base), ('patched', patch)]:
    copy(sha, 'packages/uvicorn-hmr/uvicorn_hmr.py', root / f'runtime-{label}' / 'uvicorn_hmr.py')
for label, sha in [('base', base), ('fixed', fix)]:
    for file in ['__init__.py', 'posix/__init__.py', 'posix/main.py', 'posix/worker.py']:
        copy(sha, f'packages/hmr-daemon/hmr_daemon/{file}', root / f'daemon-{label}' / 'hmr_daemon' / file)
