"""
A tox plugin to automatically install pdbpp with tox.

It gets skipped with isolated builds, e.g. with tox's own tests.
"""
import pluggy
from tox.config import DepConfig

hookimpl = pluggy.HookimplMarker("tox")


@hookimpl
def tox_configure(config):
    if config.isolated_build:
        return
    dep = DepConfig('pdbpp')
    for envname in config.envlist:
        envconfig = config.envconfigs[envname]
        if 'pdbpp' not in (x.name for x in envconfig.deps):
            envconfig.deps.append(dep)
