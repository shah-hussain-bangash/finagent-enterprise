"""Check the migration bootstrap in a synthetic Helm render (no cluster required)."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helm", default="helm", help="Helm executable")
    args = parser.parse_args()
    chart = Path(__file__).resolve().parent / "finagent"
    result = subprocess.run(  # noqa: S603 - executable is supplied by the operator
        [
            args.helm,
            "template",
            "finagent",
            str(chart),
            "--set",
            "workloadIdentity.clientIdMigrator=synthetic-migrator-id",
            "--set",
            "migration.databaseUrlSecretName=synthetic-migration-secret",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(result.stdout) if item]
    jobs = [item for item in documents if item.get("kind") == "Job"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    pod = job["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "finagent-migrator"
    container = pod["containers"][0]
    assert not container.get("envFrom"), "migration must not import application secrets"
    assert any(m["name"] == "migration-secret" and m["readOnly"] for m in container["volumeMounts"])
    volume = next(v for v in pod["volumes"] if v["name"] == "migration-secret")
    class_name = volume["csi"]["volumeAttributes"]["secretProviderClass"]
    classes = [item for item in documents if item.get("kind") == "SecretProviderClass"]
    migration_class = next(item for item in classes if item["metadata"]["name"] == class_name)
    params = migration_class["spec"]["parameters"]
    assert params["clientID"] == "synthetic-migrator-id"
    objects = yaml.safe_load(params["objects"])["array"]
    assert len(objects) == 1
    secret = yaml.safe_load(objects[0])
    assert secret["objectName"] == "synthetic-migration-secret"
    assert secret["objectAlias"] == "migration-database-url"
    assert "FINAGENT_MIGRATION_DATABASE_URL" in container["args"][0]
    assert 'test -n "$FINAGENT_MIGRATION_DATABASE_URL"' in container["args"][0]
    assert container["command"] == ["/bin/sh", "-ec"]
    print("Helm migration bootstrap render checks passed (synthetic values; no cluster).")


if __name__ == "__main__":
    main()
