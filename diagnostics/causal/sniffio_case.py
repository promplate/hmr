import os,sys,time,threading,traceback,asyncio
from pathlib import Path
sys.path.insert(0,sys.argv[1]); sys.modules.pop('hmr_daemon',None)
os.environ.pop('NO_HMR_DAEMON',None)
import hmr_daemon.posix.main as daemon
from hmr_daemon.posix import worker
from reactivity.hmr.core import BaseReloader,HMR_CONTEXT
from reactivity.hmr.fs import track
r=BaseReloader('main.py')
print('HANDOFF',daemon.shutdown_event.is_set(),flush=True)
seen=threading.Event()
async def run():
    async def effect():
        track(Path('main.py').resolve())
    e=HMR_CONTEXT.async_effect(effect,call_immediately=False)
    await e()
    orig=e.start
    def start(fn):
        print('ASYNC_SCHEDULE_THREAD',threading.current_thread().name,flush=True)
        try:
            return orig(fn)
        except Exception as exc:
            print('RESULT',type(exc).__name__,flush=True)
            traceback.print_exc()
            seen.set()
            raise
    e.start=start
    await asyncio.sleep(1)
    Path('main.py').write_text('x=3\n')
    await asyncio.sleep(2)
    print('DAEMON_ERROR_SEEN',seen.is_set(),flush=True)
    e.dispose()
try: asyncio.run(run())
finally:
    daemon.shutdown_event.set(); worker.terminate(); worker.wait(5)
