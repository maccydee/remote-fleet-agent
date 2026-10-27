import pytest

from remote_fleet_agent.registry import ClientRegistry, HostNotFoundError


def test_add_then_resolve_by_nickname(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    host = reg.resolve("asset-1")
    assert host.hostname == "asset1.local"
    assert host.nickname == "asset-1"


def test_resolve_by_hostname_also_works(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    host = reg.resolve("asset1.local")
    assert host.nickname == "asset-1"


def test_resolve_missing_raises(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    with pytest.raises(HostNotFoundError, match="not found"):
        reg.resolve("nope")


def test_list_hosts_preserves_insertion_order(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    reg.add_host("asset2.local", "asset-2")
    hosts = reg.list_hosts()
    assert [h.nickname for h in hosts] == ["asset-1", "asset-2"]


def test_duplicate_nickname_rejected(tmp_path):
    from remote_fleet_agent.registry import DuplicateNicknameError

    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("a.local", "x")
    with pytest.raises(DuplicateNicknameError, match="already"):
        reg.add_host("b.local", "x")


def test_nicknames_scoped_per_client(tmp_path):
    acme = ClientRegistry("acme", base_dir=tmp_path)
    globex = ClientRegistry("globex", base_dir=tmp_path)
    acme.add_host("a.local", "asset-1")
    globex.add_host("g.local", "asset-1")  # must not raise
    assert acme.resolve("asset-1").hostname == "a.local"
    assert globex.resolve("asset-1").hostname == "g.local"
