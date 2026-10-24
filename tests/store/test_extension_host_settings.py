"""The built-in extensions' settings (lane U, 2026-10-22): the repository list as the owner last left it (None: never
edited - an older single repository, or the environment, applies) and Auto-update (off unless turned on)."""

from __future__ import annotations

from mangalist import store
from mangalist.downloads.extensions_backend import repo_urls
from mangalist.store.downloads import SETTING_EXTENSION_HOST, ExtensionHostConfig, SuwayomiSettings


def settings() -> SuwayomiSettings:
    store.reset_stores()
    return SuwayomiSettings(store.get_store())


def test_the_repository_list_round_trips_and_none_means_never_edited():
    s = settings()
    assert s.extension_host().repos is None
    s.save_extension_host(ExtensionHostConfig(repos=(" https://a/index.json ", "https://b/index.json")))
    assert s.extension_host().repos == ("https://a/index.json", "https://b/index.json")
    s.save_extension_host(ExtensionHostConfig(repos=()))
    assert s.extension_host().repos == ()                           # emptied by the owner: not "never edited"
    s.store.set_setting(SETTING_EXTENSION_HOST, {"url": "http://127.0.0.1:4570", "repo_url": "https://old/index.json"})
    old = s.extension_host()
    assert old.repos is None and repo_urls(old, {}) == ("https://old/index.json",)      # an older version's setting
    assert repo_urls(ExtensionHostConfig(), {"MANGALIST_EXTENSION_REPO": "https://x/i.json;https://y/i.json"}) == (
        "https://x/i.json", "https://y/i.json")
    assert repo_urls(ExtensionHostConfig(), {}) == ()


def test_auto_update_is_off_until_turned_on():
    s = settings()
    assert s.extension_auto_update() is False
    s.set_extension_auto_update(True)
    assert s.extension_auto_update() is True
    s.set_extension_auto_update(False)
    assert s.extension_auto_update() is False
