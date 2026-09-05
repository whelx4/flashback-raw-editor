from types import SimpleNamespace

from ui.editor import FlashbackEditor


class FakeSettings:
    def __init__(self):
        self.values = {}
        self.synced = 0

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.synced += 1


class FakeLabel:
    def __init__(self):
        self.text = None
        self.tooltip = None

    def setText(self, value):
        self.text = value

    def setToolTip(self, value):
        self.tooltip = value


def test_open_directory_is_persisted_and_flushed(tmp_path):
    image = tmp_path / "frame.dng"
    image.touch()
    stub = SimpleNamespace(app_settings=FakeSettings())

    FlashbackEditor._remember_open_directory(stub, image)

    assert stub.app_settings.values["last_open_dir"] == str(tmp_path)
    assert stub.app_settings.synced == 1


def test_selected_export_directory_becomes_next_launch_default(tmp_path, monkeypatch):
    import ui.editor as editor_module

    destination = tmp_path / "exports"
    destination.mkdir()
    monkeypatch.setattr(
        editor_module.QFileDialog,
        "getExistingDirectory",
        lambda *args: str(destination),
    )
    stub = SimpleNamespace(
        output_dir=str(tmp_path),
        app_settings=FakeSettings(),
        label_output=FakeLabel(),
        _short_output_path=FlashbackEditor._short_output_path,
    )

    FlashbackEditor.select_output_dir(stub)

    assert stub.output_dir == str(destination)
    assert stub.app_settings.values["default_export_dir"] == str(destination)
    assert stub.app_settings.synced == 1
    assert stub.label_output.tooltip == str(destination)
