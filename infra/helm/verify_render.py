"""Check the migration bootstrap in a synthetic Helm render (no cluster required)."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helm", default="helm", help="Helm executable")
    args = parser.parse_args()
    chart = Path(__file__).resolve().parent / "finagent"
    command = [
        args.helm,
        "template",
        "finagent",
        str(chart),
        "--set",
        "workloadIdentity.clientIdMigrator=synthetic-migrator-id",
        "--set",
        "migration.databaseUrlSecretName=synthetic-migration-secret",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
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

    policies = {d["metadata"]["name"]: d["spec"] for d in documents if d["kind"] == "NetworkPolicy"}
    assert policies["finagent-default-deny"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/part-of": "finagent"
    }
    assert job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/part-of"] == "finagent"
    deployments = [d for d in documents if d["kind"] == "Deployment"]
    assert len(deployments) == 3
    assert all(
        d["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/part-of"] == "finagent"
        for d in deployments
    )
    assert policies["finagent-web-ingress"]["ingress"] == []
    for role in ("api", "worker", "migrator"):
        assert policies[f"finagent-{role}-egress"]["egress"] == []
    web_egress = policies["finagent-web-egress"]["egress"]
    assert len(web_egress) == 1
    assert web_egress[0]["ports"] == [{"port": 8000, "protocol": "TCP"}]
    assert web_egress[0]["to"][0]["podSelector"]["matchLabels"]["app"] == "finagent-api"

    peer = {"namespaceSelector": {"matchLabels": {"name": "synthetic-ingress"}}}
    egress = {"to": [{"ipBlock": {"cidr": "192.0.2.0/24"}}], "ports": [{"port": 5432, "protocol": "TCP"}]}
    configured = subprocess.run(  # noqa: S603
        [
            *command,
            "--set-json",
            f"networkPolicy.webIngressPeers={json.dumps([peer])}",
            "--set-json",
            f"networkPolicy.egress.migrator={json.dumps([egress])}",
            "--set-json",
            f"networkPolicy.egress.web={json.dumps([egress])}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    configured_docs = [item for item in yaml.safe_load_all(configured.stdout) if item]
    configured_policies = {
        d["metadata"]["name"]: d["spec"] for d in configured_docs if d["kind"] == "NetworkPolicy"
    }
    assert configured_policies["finagent-web-ingress"]["ingress"][0]["from"] == [peer]
    assert configured_policies["finagent-web-ingress"]["ingress"][0]["ports"][0]["port"] == 3000
    assert configured_policies["finagent-migrator-egress"]["egress"] == [egress]
    assert len(configured_policies["finagent-web-egress"]["egress"]) == 2
    assert configured_policies["finagent-web-egress"]["egress"][1] == egress
    for invalid in (
        "networkPolicy.webIngressPeers=[{}]",
        'networkPolicy.egress.api=[{"to":[{}],"ports":[{"port":443}]}]',
        'networkPolicy.egress.api=[{"to":[{"ipBlock":{"cidr":"192.0.2.0/24"}}]}]',
        'networkPolicy.egress.api=[{"to":[{"ipBlock":{"cidr":"192.0.2.0/24"}}],"ports":[{}]}]',
    ):
        rejected = subprocess.run(  # noqa: S603
            [*command, "--set-json", invalid], capture_output=True, text=True, check=False
        )
        assert rejected.returncode != 0, f"unsafe rule unexpectedly accepted: {invalid}"
    print("Helm migration and network-policy render checks passed (synthetic values; no cluster).")


if __name__ == "__main__":
    main()
