"""Earliest-possible HMR injection point, activated only by explicit environment.

`xinference-hmr` prepends this directory to `PYTHONPATH`, which makes CPython import
this file during `site` initialization -- before xinference, torch, or any model module.
Because this shadows any other `sitecustomize` further down the path, the first thing
we do is chain to that one, so an environment that already relies on its own
`sitecustomize` keeps working.

Nothing is injected unless `HMR_XINFERENCE_ENABLE=1` and `HMR_XINFERENCE_RUNTIME` are
both set, and the default runtime then installs only in a model-owning sub-pool: every
process in the Xinference tree inherits this `PYTHONPATH`, so the role gate, not the
environment, is what scopes the injection.
"""

from xinference_hmr.shim import chain_to_next_sitecustomize, install_from_env

chain_to_next_sitecustomize(__file__)
install_from_env()
