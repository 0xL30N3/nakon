import mysql.connector
import json
import paramiko
import os
import shlex
import tempfile
import requests
from dotenv import load_dotenv
import configurations

load_dotenv()

with open("config.json") as f:
    config = json.load(f)

db_config = {
    "host":     os.getenv("host"),
    "user":     os.getenv("user"),
    "password": os.getenv("password"),
    "database": os.getenv("database"),
}

VULNDB_UI_URL = os.getenv("VULNDB_UI_URL", "").rstrip("/")


def sudo_pass(stdin, password: str):
    stdin.write(password + "\n")
    stdin.flush()


def wait_for_dpkg_lock(client, password: str):
    """Wait out any boot-time apt/dpkg lock before running a machine's scripts.

    Service configurations (apache/nginx/bind/ssh/dovecot) call apt-get directly and, unlike
    configurations.install_package, have no lock-wait of their own. On a freshly-booted box
    unattended-upgrades holds /var/lib/dpkg/lock-frontend for up to ~2 min, so those installs
    would fail. Block until the lock clears (or ~2 min elapses)."""
    cmd = (
        "sudo -S bash -c 'for i in $(seq 1 60); do "
        "fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; sleep 2; done'"
    )
    stdin, stdout, stderr = client.exec_command(cmd)
    sudo_pass(stdin, password)
    stdout.read()


def build_script(cfg):
    """Prepend any vars as shell-quoted exports so the script can reference $VAR_NAME."""
    if not cfg["vars"]:
        return cfg["script"]
    exports = "\n".join(f"{key}={shlex.quote(value)}" for key, value in cfg["vars"].items())
    return f"{exports}\n{cfg['script']}"


def stage_attachment(attachment, staging_dir):
    """Download an attachment from vulndb-ui (follows the 302 -> MinIO presigned URL) into
    a local staging directory. Returns the local file path."""
    if not VULNDB_UI_URL:
        raise RuntimeError(
            f"VULNDB_UI_URL is not set in .env — needed to download "
            f"'{attachment['original_name']}'. Set it to the base URL of your "
            f"vulndb-ui server (e.g. http://10.0.0.118:3000)."
        )
    local_path = os.path.join(staging_dir, f"{attachment['id']}-{attachment['original_name']}")
    url = f"{VULNDB_UI_URL}/api/attachments/{attachment['id']}/download"
    response = requests.get(url, allow_redirects=True, timeout=30)
    response.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(response.content)
    return local_path


def push_attachment(client, local_path, original_name):
    """SFTP a staged attachment onto the remote host, alongside the script in /tmp."""
    sftp = client.open_sftp()
    try:
        sftp.put(local_path, f"/tmp/{original_name}")
    finally:
        sftp.close()


mydb = mysql.connector.connect(**db_config)
cursor = mydb.cursor()

with tempfile.TemporaryDirectory(prefix="nakon-staging-") as staging_dir:
    for machine in config["machines"]:
        print(f"\n[deploy] ── Machine: {machine['ip']} ──────────────────────────────")

        ordered, fallback_packages = configurations.resolve(cursor, machine["configurations"])

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=machine["ip"],
            username=machine["user"],
            password=machine["password"],
        )

        # Clear any boot-time apt lock before the configs' direct apt-get calls run.
        wait_for_dpkg_lock(client, machine["password"])

        for package in dict.fromkeys(fallback_packages):
            print(f"[deploy] Installing package '{package}' via package manager")
            configurations.install_package(client, machine["password"], package)

        for cfg in ordered:
            print(f"[deploy] Running configuration '{cfg['name']}'")

            for attachment in cfg["attachments"]:
                print(f"[deploy]   Staging attachment '{attachment['original_name']}'")
                local_path = stage_attachment(attachment, staging_dir)
                push_attachment(client, local_path, attachment["original_name"])

            script = build_script(cfg)

            # Write the script to /tmp on the remote host
            stdin, stdout, stderr = client.exec_command(
                f"cat << 'DEPLOY_EOF' > /tmp/cmd.sh\n{script}\nDEPLOY_EOF"
            )
            stdout.read()

            stdin, stdout, stderr = client.exec_command("chmod +x /tmp/cmd.sh")
            stdout.read()

            # cd into /tmp first so a script can reference staged attachments by relative path
            if cfg["run_as"] == "root":
                stdin, stdout, stderr = client.exec_command("cd /tmp && sudo -S sh /tmp/cmd.sh")
                sudo_pass(stdin, machine["password"])
            else:
                stdin, stdout, stderr = client.exec_command("cd /tmp && sh /tmp/cmd.sh")

            out = stdout.read().decode()
            err = stderr.read().decode()
            if out: print(f"[deploy] {out.strip()}")
            if err: print(f"[deploy] stderr: {err.strip()}")

        client.close()

cursor.close()
mydb.close()
