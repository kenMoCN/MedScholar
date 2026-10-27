"""Render complete PPTX slides through Microsoft PowerPoint when available."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable


class SlideRenderUnavailable(RuntimeError):
    pass


POWERSHELL_EXPORT = r"""
$ErrorActionPreference = "Stop"
$source = $args[0]
$output = $args[1]
$numbers = $args[2].Split(',') | Where-Object { $_ } | ForEach-Object { [int]$_ }
$powerpoint = $null
$presentation = $null
try {
    $powerpoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerpoint.Presentations.Open($source, $true, $true, $false)
    foreach ($number in $numbers) {
        $target = Join-Path $output ("slide-{0}.png" -f $number)
        $presentation.Slides.Item($number).Export($target, "PNG", 1600, 900)
        if (-not (Test-Path -LiteralPath $target)) {
            throw "PowerPoint did not create $target"
        }
    }
} finally {
    if ($presentation -ne $null) { $presentation.Close() }
    if ($powerpoint -ne $null) { $powerpoint.Quit() }
}
"""


def render_pptx_slides(
    source: str | Path,
    slide_numbers: Iterable[int],
    output_dir: str | Path,
) -> dict[int, Path]:
    numbers = sorted({int(value) for value in slide_numbers if int(value) > 0})
    if not numbers:
        return {}
    if os.name != "nt":
        raise SlideRenderUnavailable("完整幻灯片渲染当前仅支持 Windows PowerPoint")
    source_path = Path(source).absolute()
    destination = Path(output_dir).absolute()
    destination.mkdir(parents=True, exist_ok=True)
    script_path = destination / "_render_slides.ps1"
    script_path.write_text(POWERSHELL_EXPORT, encoding="utf-8-sig")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(source_path),
                str(destination),
                ",".join(str(value) for value in numbers),
            ],
            capture_output=True,
            text=True,
            timeout=max(120, 20 * len(numbers)),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SlideRenderUnavailable(f"PowerPoint整页渲染不可用: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "PowerPoint导出失败").strip()
        raise SlideRenderUnavailable(message[:500])
    rendered = {
        number: destination / f"slide-{number}.png" for number in numbers
    }
    missing = [number for number, path in rendered.items() if not path.is_file()]
    if missing:
        raise SlideRenderUnavailable(f"PowerPoint未导出幻灯片: {missing}")
    return rendered
