from wtisen_runner.storage.local_fs import LocalFilesystemStorage


def test_storage_write_list_move(tmp_path):
    s = LocalFilesystemStorage(str(tmp_path))
    s.write_bytes("landing", "a.csv", b"x")
    files = s.list_files("landing", "*.csv")
    assert [f.name for f in files] == ["a.csv"]
    moved = s.move_file(files[0], "archive")
    assert moved.exists()
    assert not files[0].exists()
