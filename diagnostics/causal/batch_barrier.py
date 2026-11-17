import os,sys,time,threading,traceback,json
from pathlib import Path
from contextlib import nullcontext
sys.path.insert(0,sys.argv[1])
sys.modules.pop("hmr_daemon",None)
os.environ.pop('NO_HMR_DAEMON',None)
import hmr_daemon.posix.main as daemon
from hmr_daemon.posix import worker
from reactivity.hmr.core import BaseReloader,HMR_CONTEXT
import reactivity.hmr.core as core
from reactivity.primitives import Batch
r=BaseReloader('main.py')
r.error_filter=nullcontext()
print('HANDOFF', daemon.shutdown_event.is_set(),flush=True)
# Make worker startup independent of platform scheduling.
time.sleep(1)
entered=threading.Event(); release=threading.Event()
old_enter=Batch.__enter__
def enter(self):
    old_enter(self)
    if threading.current_thread().name=='hmr-daemon':
        print('DAEMON_BATCH_ENTER', [id(b) for b in HMR_CONTEXT.batches],flush=True)
        entered.set()
        release.wait(5)
Batch.__enter__=enter
old_notify=core.notify
def notify(path):
    if threading.current_thread().name=='MainThread':
        print('MAIN_BATCH_ENTER', [id(b) for b in HMR_CONTEXT.batches],flush=True)
        Path('main.py').write_text('x=2\n')
        print('DAEMON_ENTERED',entered.wait(2),flush=True)
    return old_notify(path)
core.notify=notify
try:
    r.on_changes({Path('config.txt').resolve()})
    print('RESULT OK',flush=True)
except Exception as e:
    print('RESULT',type(e).__name__,flush=True)
    traceback.print_exc()
finally:
    release.set()
    daemon.shutdown_event.set()
    worker.terminate()
    worker.wait(5)
