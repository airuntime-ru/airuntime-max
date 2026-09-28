"""Deployment adapters for AIRuntime.

Production implementation: `backend/src/services/deployment/docker_adapter.py`
"""

from dataclasses import dataclass


@dataclass
class DeployRequest:
    project_id: str
    image_ref: str
    subdomain: str
