"""ImageMagick（只給 Pillow 讀不懂的少見格式用；對應原版 CVExternalTools.magickArguments）。

為了安全：
- 輸入與輸出都明確寫出格式（「XCF:檔案」），不讓 ImageMagick 依內容猜格式；
- 檔名一律換成暫存資料夾裡的安全名稱（in.副檔名 → out.副檔名），避免檔名裡的 [、%、@ 被解讀；
- 每次執行都帶自己的限制性 policy.xml（MAGICK_CONFIGURE_PATH）：禁止 MVG、MSL、TEXT、
  PostScript／PDF／XPS、網址、EPHEMERAL 與「@檔案」間接讀取；
- 加上記憶體、映射、面積、磁碟、時間上限。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .. import backends, errors

LIMITS: tuple[str, ...] = (
    "-limit", "memory", "1GiB",
    "-limit", "map", "2GiB",
    "-limit", "area", "256MP",
    "-limit", "disk", "4GiB",
    "-limit", "time", "120",
)  # fmt: skip
DENIED_CODERS = (
    "MVG,MSL,TEXT,TXT,PS,PS2,PS3,EPS,EPSF,EPSI,EPI,EPT,PDF,PDFA,AI,XPS,"
    "URL,HTTP,HTTPS,FTP,EPHEMERAL,LABEL,CAPTION,SHOW,WIN,X,SCAN,VID,MAGICK"
)
POLICY = f"""<?xml version="1.0" encoding="UTF-8"?>
<policymap>
  <policy domain="resource" name="memory" value="1GiB"/>
  <policy domain="resource" name="map" value="2GiB"/>
  <policy domain="resource" name="area" value="256MP"/>
  <policy domain="resource" name="disk" value="4GiB"/>
  <policy domain="resource" name="time" value="120"/>
  <policy domain="coder" rights="none" pattern="{{{DENIED_CODERS}}}"/>
  <policy domain="module" rights="none" pattern="{{MVG,MSL,PS,PDF,XPS,URL,TXT,LABEL}}"/>
  <policy domain="delegate" rights="none" pattern="{{gs,ps,eps,pdf,xps,http,https,ftp,url}}"/>
  <policy domain="path" rights="none" pattern="@*"/>
</policymap>
"""


def coder(ext: str) -> str:
    """副檔名 → ImageMagick 的格式代碼（只允許英數字）。"""
    code = "".join(ch for ch in ext.upper() if ch.isascii() and ch.isalnum())
    if not code:
        raise errors.QSError("ImageMagick 需要明確的檔案格式。")
    return code


def arguments(
    source: str, output: str, options: dict, keep_frames: bool, src_ext: str, out_ext: str
) -> list[str]:  # noqa: E501
    """輸入與輸出都明確指定格式；不保留多格時只讀第一格（[0]）。"""
    src = f"{coder(src_ext)}:{source}" + ("" if keep_frames else "[0]")
    args = [*LIMITS, src, "-auto-orient"]
    max_side = options.get("image_max_pixel")
    if max_side:
        args += ["-resize", f"{int(max_side)}x{int(max_side)}>"]
    quality = int(round(float(options.get("image_quality", 0.85)) * 100))
    return args + ["-quality", str(quality), f"{coder(out_ext)}:{output}"]


def convert(
    path: Path, out: Path, fid: str, options: dict, work: Path, ctx
) -> None:  # noqa: ANN001
    """用安全的暫存檔名與限制性設定執行 ImageMagick，成功後把結果改名成 out。"""
    scratch = Path(tempfile.mkdtemp(prefix=".magick-", dir=work))
    (scratch / "policy.xml").write_text(POLICY, "utf-8")
    src_ext = path.suffix[1:].lower()
    out_ext = out.suffix[1:].lower()
    link = scratch / f"in.{coder(src_ext).lower()}"
    os.symlink(path.resolve(), link)
    produced = scratch / f"out.{coder(out_ext).lower()}"
    keep = fid in ("gif", "webp", "tiff")
    args = arguments(str(link), str(produced), options, keep, src_ext, out_ext)
    env = {"MAGICK_CONFIGURE_PATH": str(scratch)}
    ctx.run_process([backends.require("magick"), *args], "ImageMagick", env=env)
    if not produced.exists():
        raise errors.tool_failed("ImageMagick", "沒有產生輸出檔。")
    os.rename(produced, out)
