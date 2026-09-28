"""允许以 `python -m hcore_mujoco_local_eaios` 启动 facade 组。"""

import sys

from .facade import main

if __name__ == "__main__":
    sys.exit(main())
