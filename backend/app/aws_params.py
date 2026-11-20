# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secrets from SSM Parameter Store, loaded into the environment before ``Settings()`` reads it.

On Lambda the SAM template sets ``WW_SSM_PREFIX`` (``/ww/<env>/``) and grants
``ssm:GetParametersByPath`` on it. Each SecureString under the prefix becomes an environment
variable named after its last path segment (``/ww/dev/SUPABASE_URL`` becomes ``SUPABASE_URL``),
once per cold start. A variable already set wins, so the template can pin a value. With the
prefix unset (Azure, local, tests, the cluster) this does nothing and imports nothing.
"""

from __future__ import annotations

import os
from typing import Any, Optional

_loaded = False


def load_parameters_into_environ(client: Optional[Any] = None) -> int:
    """Copy the parameters under ``WW_SSM_PREFIX`` into ``os.environ``; returns how many were set."""
    global _loaded
    prefix = os.environ.get("WW_SSM_PREFIX", "")
    if not prefix or (_loaded and client is None):
        return 0
    if client is None:
        import boto3

        client = boto3.client("ssm")
    if not prefix.endswith("/"):
        prefix += "/"
    count = 0
    token: Optional[str] = None
    while True:
        kwargs: dict = {"Path": prefix, "Recursive": True, "WithDecryption": True}
        if token:
            kwargs["NextToken"] = token
        page = client.get_parameters_by_path(**kwargs)
        for param in page.get("Parameters", []):
            name = str(param["Name"]).rsplit("/", 1)[-1]
            if name and name not in os.environ:
                os.environ[name] = str(param.get("Value", ""))
                count += 1
        token = page.get("NextToken")
        if not token:
            break
    _loaded = True
    return count
