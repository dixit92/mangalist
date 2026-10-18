"""config.save() writes back only what its copy changed: a window's settings read at startup never put old values over
keys other parts of MangaList wrote meanwhile (owner, 2026-10-18: converted libraries lost after a rename)."""

from __future__ import annotations

from mangalist import config, store


def test_a_stale_copy_does_not_overwrite_a_key_written_meanwhile():
    db = store.get_store()
    db.set_setting("renamer.guided_roots", [1, 2])
    window = config.load()                                  # the window's copy, read at startup
    db.set_setting("renamer.guided_roots", [1, 2, 6])       # the Renamer marks a library converted meanwhile
    window["examined"] = ["/library/Quiet Harbor"]          # the window then saves its own change
    config.save(window)
    assert db.get_setting("renamer.guided_roots") == [1, 2, 6]
    assert config.load()["examined"] == ["/library/Quiet Harbor"]


def test_nested_changes_are_saved_and_unchanged_keys_are_not_written_again(monkeypatch):
    window = config.load()
    window["window"]["w"] = 1600                            # changed in place, as the window does
    written = []
    real = store.get_store().set_settings
    monkeypatch.setattr(store.get_store(), "set_settings", lambda values: (written.append(dict(values)), real(values)))
    config.save(window)
    assert written == [{"window": {"w": 1600, "h": 720}}]
    config.save(window)                                     # nothing changed since: nothing written
    assert written == [{"window": {"w": 1600, "h": 720}}]
    assert config.load()["window"]["w"] == 1600


def test_a_plain_dict_still_saves_every_key():
    config.save({"mu_autostart": True, "last_root": "/library/Lantern Road"})
    cfg = config.load()
    assert cfg["mu_autostart"] is True and cfg["last_root"] == "/library/Lantern Road"
