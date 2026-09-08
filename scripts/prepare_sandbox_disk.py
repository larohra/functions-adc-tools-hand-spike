from __future__ import annotations

import os
import sys

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def main() -> int:
    region = required("ACA_SANDBOX_REGION")
    disk_name = required("ACA_SANDBOX_DISK")
    base_image = required("ACA_SANDBOX_BASE_IMAGE")
    credential = AzureCliCredential()
    client = SandboxGroupClient(
        endpoint_for_region(region),
        credential,
        subscription_id=required("ACA_SANDBOX_SUBSCRIPTION_ID"),
        resource_group=required("ACA_SANDBOX_RESOURCE_GROUP"),
        sandbox_group=required("ACA_SANDBOX_GROUP"),
    )
    try:
        existing = next(
            (
                disk
                for disk in client.list_disk_images()
                if disk.name == disk_name
                or (getattr(disk, "labels", {}) or {}).get("name") == disk_name
            ),
            None,
        )
        if existing:
            print(f"Sandbox disk already exists: {disk_name} ({existing.id})")
            return 0
        print(f"Creating sandbox disk {disk_name} from {base_image}...")
        disk = client.begin_create_disk_image(base_image, name=disk_name).result()
        print(f"Created sandbox disk: {disk_name} ({disk.id})")
        return 0
    finally:
        client.close()
        credential.close()


if __name__ == "__main__":
    sys.exit(main())
