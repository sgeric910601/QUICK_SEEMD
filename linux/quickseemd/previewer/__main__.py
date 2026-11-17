"""python3 -m quickseemd.previewer：空白鍵預覽服務（通常由 D-Bus 自動啟動）。"""

import sys

from .app import main

sys.exit(main())
