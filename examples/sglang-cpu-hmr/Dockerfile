FROM lmsysorg/sglang:v0.5.16-xeon

USER root
WORKDIR /opt/sglang-hmr

# promplate/hmr is the cookbook repository; the core package currently lives
# in promplate/pyth-on-line. Pin the exact source revision used by this probe.
ARG PYTH_ON_LINE_SHA=d410f975367e8a29b17183d108ef09a089e42b63
RUN /opt/.venv/bin/python3 -c "import pathlib, tarfile, urllib.request; sha='${PYTH_ON_LINE_SHA}'; archive='/tmp/pyth-on-line.tar.gz'; urllib.request.urlretrieve('https://github.com/promplate/pyth-on-line/archive/'+sha+'.tar.gz', archive); root=pathlib.Path('/tmp/pyth-src'); root.mkdir(); tarfile.open(archive).extractall(root); source=next(root.glob('pyth-on-line-*'))/'packages'/'hmr'; pathlib.Path('/opt/hmr-source').symlink_to(source)"
RUN uv pip install --python /opt/.venv/bin/python3 --no-deps /opt/hmr-source
RUN uv pip install --python /opt/.venv/bin/python3 "watchfiles>=0.21,<2"

# The CLI under test comes from this checkout, never from an index: the smoke
# must exercise the `sglang-hmr` console script exactly as it exists here.
COPY packages/sglang-hmr /opt/sglang-hmr-cli
RUN uv pip install --python /opt/.venv/bin/python3 --no-deps /opt/sglang-hmr-cli

# Copy the example so the container is self-contained.
COPY examples/sglang-cpu-hmr /opt/sglang-hmr
RUN uv pip install --python /opt/.venv/bin/python3 --no-deps /opt/sglang-hmr

# pyth-on-line excludes virtualenv/site-packages paths. Keep the official
# release bytes, but expose them from an external source root for this probe.
RUN mkdir -p /opt/sglang-release-source/python \
    && cp -a /opt/.venv/lib/python3.12/site-packages/sglang /opt/sglang-release-source/python/

ENV PYTHONPATH=/opt/sglang-hmr:/opt/sglang-release-source/python
ENTRYPOINT ["python3", "/opt/sglang-hmr/smoke.py"]
