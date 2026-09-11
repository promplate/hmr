FROM vllm/vllm-openai-cpu:v0.28.0-x86_64

USER root
WORKDIR /opt/vllm-hmr

# promplate/hmr is the cookbook repository; the core package currently lives
# in promplate/pyth-on-line. Pin the exact source revision used by this probe.
ARG PYTH_ON_LINE_SHA=d410f975367e8a29b17183d108ef09a089e42b63
RUN /opt/venv/bin/python3 -c "import pathlib, tarfile, urllib.request; sha='${PYTH_ON_LINE_SHA}'; archive='/tmp/pyth-on-line.tar.gz'; urllib.request.urlretrieve('https://github.com/promplate/pyth-on-line/archive/'+sha+'.tar.gz', archive); root=pathlib.Path('/tmp/pyth-src'); root.mkdir(); tarfile.open(archive).extractall(root); source=next(root.glob('pyth-on-line-*'))/'packages'/'hmr'; pathlib.Path('/opt/hmr-source').symlink_to(source)"
RUN /opt/venv/bin/pip install --no-deps /opt/hmr-source

# Copy the example so the container is self-contained.
COPY examples/vllm-cpu-hmr /opt/vllm-hmr
RUN /opt/venv/bin/pip install --no-deps /opt/vllm-hmr

# pyth-on-line excludes virtualenv/site-packages paths. Keep the official
# release bytes, but expose them from an external source root for this probe.
RUN mkdir -p /opt/vllm-release-source \
    && cp -a /opt/venv/lib/python3.12/site-packages/vllm /opt/vllm-release-source/

ENV PYTHONPATH=/opt/vllm-hmr:/opt/vllm-release-source
ENTRYPOINT ["python3", "/opt/vllm-hmr/smoke.py"]
