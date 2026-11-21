"""Package simulator : active truststore (magasin de certificats du système) AVANT tout import
de requests/urllib3, sinon l'injection tardive provoque une RecursionError."""

import aquamind_core  # noqa: F401
