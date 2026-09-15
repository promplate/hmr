# Official Xinference CPU image, plus this repo's HMR packages. No Xinference source is
# modified at build time; the smoke edits one file at runtime and restores it byte-for-byte.
FROM xprobe/xinference:latest-cpu

# Pin the exact pyth-on-line source revision used by the experiment. The extracted
# path carries the revision into PEP 610 direct_url.json and remains inspectable.
ARG PYTH_ON_LINE_SHA=d410f975367e8a29b17183d108ef09a089e42b63
RUN python -c "import pathlib, tarfile, urllib.request; sha='${PYTH_ON_LINE_SHA}'; archive='/tmp/pyth-on-line.tar.gz'; urllib.request.urlretrieve('https://github.com/promplate/pyth-on-line/archive/'+sha+'.tar.gz', archive); root=pathlib.Path('/opt/pyth-src'); root.mkdir(); tarfile.open(archive).extractall(root, filter='data')"
RUN pip install --no-cache-dir --no-deps "/opt/pyth-src/pyth-on-line-${PYTH_ON_LINE_SHA}/packages/hmr" \
    && pip install --no-cache-dir "watchfiles>=0.21,<2"

COPY packages/xinference-hmr /opt/xinference-hmr
RUN pip install --no-cache-dir --no-deps /opt/xinference-hmr

COPY examples/xinference-cpu-hmr /opt/xinference-cpu-hmr

# The sub-pool inherits the worker's cwd and starts with `python -m`, which puts that cwd
# on `sys.path[0]`. The official image's WORKDIR is /opt/inference, the source tree pip
# installed from, so that is the copy the model process actually imports -- and therefore
# the copy the smoke mutates. Keeping WORKDIR unchanged keeps that true.
WORKDIR /opt/inference
