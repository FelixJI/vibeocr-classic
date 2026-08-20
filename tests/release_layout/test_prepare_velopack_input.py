from pathlib import Path

from scripts.prepare_velopack_input import prepare_velopack_input


def test_prepare_velopack_input_excludes_mutable_smoke_state(tmp_path: Path) -> None:
    source = tmp_path / "verified-product"
    source.mkdir()
    (source / "VibeOCR.exe").write_bytes(b"MZ")
    (source / "product-release-manifest.json").write_text("{}", encoding="utf-8")
    (source / "backend").mkdir()
    (source / "backend" / "runtime-manifest.json").write_text("{}", encoding="utf-8")
    for name in ("state", ".smoke-runtime", ".smoke-data-a"):
        path = source / name
        path.mkdir()
        (path / "must-not-ship").write_text("smoke", encoding="utf-8")

    destination = tmp_path / "velopack-input"
    prepare_velopack_input(source, destination)

    assert (destination / "VibeOCR.exe").is_file()
    assert (destination / "backend" / "runtime-manifest.json").is_file()
    assert not (destination / "state").exists()
    assert not (destination / ".smoke-runtime").exists()
    assert not (destination / ".smoke-data-a").exists()
