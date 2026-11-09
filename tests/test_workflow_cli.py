import json
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_fleet_agent.cli import main


def test_offline_cli_add_export_and_import_revision(tmp_path, capsys):
    config = tmp_path / "fleet.json"
    config.write_text(
        json.dumps({"client": "offline", "known_hosts": "missing", "hosts": []})
    )
    common = ["--config", str(config), "--job-dir", str(tmp_path / "data")]

    def run(args):
        with (
            patch("sys.argv", ["remote-fleet-agent", "workspace", *args, *common]),
            pytest.raises(SystemExit) as exit,
        ):
            main()
        assert exit.value.code == 0
        return json.loads(capsys.readouterr().out)

    added = run(
        [
            "manual-add",
            "--asset",
            "NAS",
            "--category",
            "nas",
            "--control",
            "2500",
            "--reviewer",
            "Operator",
            "--note",
            "Test note",
        ]
    )
    assert added["revision"] == 1
    exported = run(["dashboard", "--private", "--output", str(tmp_path / "view.html")])
    assert Path(exported["path"]).exists() and (tmp_path / "view.html").exists()
    fields = {
        n: added[n]
        for n in (
            "asset",
            "category",
            "control",
            "status",
            "reviewer",
            "note",
            "reference",
            "job_id",
        )
    }
    fields["status"] = "verified"
    document = {
        "schema": "fleet-manual-import-v1",
        "client": "offline",
        "changes": [{"id": added["id"], "expected_revision": 1, "fields": fields}],
    }
    imported = tmp_path / "updates.json"
    imported.write_text(json.dumps(document))
    result = run(["import-manual", "--input", str(imported)])
    assert result[0]["status"] == "verified" and result[0]["revision"] == 2
    assert len(run(["manual-list"])[0]["history"]) == 1


def test_wrong_client_import_is_rejected_before_writes(tmp_path, capsys):
    config = tmp_path / "fleet.json"
    config.write_text(
        json.dumps({"client": "offline", "known_hosts": "missing", "hosts": []})
    )
    document = tmp_path / "wrong.json"
    document.write_text(
        json.dumps(
            {"schema": "fleet-manual-import-v1", "client": "different", "changes": []}
        )
    )
    with (
        patch(
            "sys.argv",
            [
                "rfa",
                "workspace",
                "import-manual",
                "--config",
                str(config),
                "--job-dir",
                str(tmp_path / "data"),
                "--input",
                str(document),
            ],
        ),
        pytest.raises(SystemExit) as exit,
    ):
        main()
    assert exit.value.code == 2
    assert not (tmp_path / "data/offline/manual-evidence.json").exists()
