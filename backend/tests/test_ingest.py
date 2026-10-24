"""Path repair for imported footage and readable job errors."""

import pytest

from app.api.serializers import error_brief
from app.services.ingest import locate_path


def test_locate_path_follows_a_case_only_rename(tmp_path):
    folder = tmp_path / "examples"
    folder.mkdir()
    clip = folder / "a.mp4"
    clip.write_bytes(b"x")
    asked = tmp_path / "Examples" / "a.mp4"
    found = locate_path(asked)
    assert found.is_file()
    if not asked.exists():
        assert found.resolve() == clip.resolve()


def test_locate_path_leaves_a_missing_file_unchanged(tmp_path):
    missing = tmp_path / "gone" / "a.mp4"
    assert locate_path(missing) == missing


def test_locate_path_refuses_ambiguous_case_matches(tmp_path):
    (tmp_path / "Examples").mkdir()
    if (tmp_path / "examples").exists():
        pytest.skip("filesystem is case-insensitive")
    (tmp_path / "examples").mkdir()
    asked = tmp_path / "EXAMPLES" / "a.mp4"
    assert locate_path(asked) == asked


def test_error_brief_keeps_the_exception_message():
    text = (
        "ffprobe failed (1): /tmp/a.mp4: No such file or directory\n"
        "Traceback (most recent call last):\n"
        '  File "/app/media/ffmpeg.py", line 69, in probe\n'
        "    out = run()\n"
        "MediaError: ffprobe failed (1): /tmp/a.mp4: No such file or directory\n"
    )
    assert error_brief(text) == "ffprobe failed (1): /tmp/a.mp4: No such file or directory"
    sliced = (
        'File "/app/media/ffmpeg.py", line 69, in probe\n'
        "    out = run()\n"
        "MediaError: 素材文件不存在：/tmp/a.mp4\n"
    )
    assert error_brief(sliced) == "MediaError: 素材文件不存在：/tmp/a.mp4"
