# setup.py — only exists to inject the wheel-root .pth (the autoload hook).
#
# pyproject.toml carries all package metadata (PEP 621); this file only overrides
# the build_py command so `jiuwenswarm_instrumentor.pth` is copied into build_lib
# (the wheel root). Files at the wheel root are installed by pip into site-packages,
# where CPython's site module executes the .pth's `import` line at every interpreter
# startup. `data-files` cannot do this reliably for wheels (it lands files in a
# prefix-relative data dir, not site-packages), hence the custom command.
#
# Editable installs do not run build_py the same way and will not ship the .pth;
# for dev use `jiuwen-instrument <module>` or set PYTHONPATH to a dir containing
# a sitecustomize.py that calls jiuwenswarm_instrumentor.activate.activate().
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_PTH = "jiuwenswarm_instrumentor.pth"


class build_py(_build_py):
    def run(self):
        super().run()
        src = os.path.join(os.path.dirname(__file__), _PTH)
        if not os.path.exists(src):
            return
        target = os.path.join(self.build_lib, _PTH)
        self.mkpath(os.path.dirname(target))
        shutil.copy2(src, target)


setup(cmdclass={"build_py": build_py})
