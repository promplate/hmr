import os,sys,unittest,json
from pathlib import Path
import source_case as c
base=Path(__file__).parent
runtime,daemon=sys.argv[1:]
c.PACKAGE=base/f'runtime-{runtime}'
real_popen=c.subprocess.Popen
bootstrap='''
import threading,traceback,os
daemon_done=threading.Event()
from reactivity.hmr.core import BaseReloader
old=BaseReloader.on_changes
def on_changes(self,files):
 if any(p.name=='main.py' for p in files):
  print('EVENT_TRACE',threading.current_thread().name,type(self).__qualname__,[str(p) for p in files],flush=True)
  traceback.print_stack(limit=7)
 if any(p.name=='main.py' for p in files):
  if threading.current_thread().name=='MainThread' and os.environ.get('EXPECT_DAEMON')=='1':
   if not daemon_done.wait(5):raise RuntimeError('daemon callback missing')
  try:return old(self,files)
  finally:
   if threading.current_thread().name=='hmr-daemon':daemon_done.set()
 return old(self,files)
BaseReloader.on_changes=on_changes
import uvicorn_hmr
uvicorn_hmr.app()
'''
def popen(args,**kwargs):
 kwargs['env']['EXPECT_DAEMON']='1' if daemon=='base' else '0'
 kwargs['env']['PYTHONPATH']+=os.pathsep+str(base/f'daemon-{daemon if daemon != "disabled" else "base"}')
 if daemon=='disabled':kwargs['env']['NO_HMR_DAEMON']='1'
 else:kwargs['env'].pop('NO_HMR_DAEMON',None)
 args=[args[0],'-c',bootstrap,*args[3:]]
 return real_popen(args,**kwargs)
c.subprocess.Popen=popen
oldstop=c.StartupWatchTests.stop
def stop(self):
 try:oldstop(self)
 finally:
  text=(self.root/'server.log').read_text()
  (base/f'trace-{runtime}-{daemon}.log').write_text(text)
  if daemon=='base':
   self.assertIn('sniffio._impl.AsyncLibraryNotFoundError',text)
  else:
   self.assertNotIn('Traceback (most recent call last)',text)
   self.assertNotIn('EVENT_TRACE hmr-daemon',text)
  print('\n'.join(l for l in text.splitlines() if 'EVENT_TRACE' in l))
c.StartupWatchTests.stop=stop
result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([c.StartupWatchTests('test_blocking_startup_buffers_changes')]))
print('RESULT',runtime,daemon,result.wasSuccessful())
