# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download the three models' weights into the caches ww_infer.sl reads offline.

Run inside the GPU image by ww_cache_models.sh, once per weights version, with HF_HOME,
KAGGLEHUB_CACHE and TORCH_HOME pointing at the project's models directory. Loading each model
through the app's own services fetches exactly the files a batch job will ask for.
"""

from app.services.speciesnet_service import SpeciesNetService

SpeciesNetService()._get_model()
print("speciesnet ok", flush=True)

from app.services.bioclip_service import get_bioclip_service  # noqa: E402

get_bioclip_service()._get_tol_model()
print("bioclip ok", flush=True)

from app.services.dinov3 import DinoV3Service  # noqa: E402

DinoV3Service()._load()
print("dinov3 ok", flush=True)
