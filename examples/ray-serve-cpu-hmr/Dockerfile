# Official Ray CPU image. py312 rather than py311 because `hmr` requires Python >= 3.12
# (every published version, checked against the py311 image: no candidate at all).
FROM rayproject/ray:2.58.0-py312-cpu

USER ray

# The core package is NOT taken from PyPI. A floating `hmr>=0.7,<0.8` resolves to whatever the
# index serves that day (it resolved to 0.7.6.3 here), so a receipt built on it cannot name the
# source it actually tested. `hmr` lives in promplate/pyth-on-line (promplate/hmr is the
# cookbook), so the source tarball of one exact revision is fetched and installed from those
# bytes. The extracted tree is kept in the image: the install path recorded in the dist's
# PEP 610 `direct_url.json` then carries the revision itself, which is what the smoke reads
# back instead of trusting anything this Dockerfile says about itself.
ARG PYTH_ON_LINE_SHA=d410f975367e8a29b17183d108ef09a089e42b63

RUN pip install --no-cache-dir \
      "torch==2.9.1+cpu" --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
      "transformers==4.57.1" \
      "watchfiles>=0.21,<2" \
      "sniffio~=1.3" \
      "httpx>=0.27"

# The archive's single top-level directory is `pyth-on-line-<sha>`, so the installed path below
# is pinned by the same ARG rather than by a glob that could pick up something else.
RUN python -c "import pathlib, tarfile, urllib.request; sha='${PYTH_ON_LINE_SHA}'; archive='/home/ray/pyth-on-line.tar.gz'; urllib.request.urlretrieve('https://github.com/promplate/pyth-on-line/archive/'+sha+'.tar.gz', archive); root=pathlib.Path('/home/ray/pyth-src'); root.mkdir(); tarfile.open(archive).extractall(root, filter='data')"
RUN pip install --no-cache-dir --no-deps "/home/ray/pyth-src/pyth-on-line-${PYTH_ON_LINE_SHA}/packages/hmr"
