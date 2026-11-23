"""Verify the independent local research signature and all evidence fingerprints."""

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

review = Path(__file__).resolve().parent
root = review.parents[2]
attestation = json.loads((review / "final-attestation.json").read_bytes())
payload = attestation["payload"]
message = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
key = Ed25519PublicKey.from_public_bytes(
    base64.b64decode(attestation["public_key_base64"], validate=True)
)
key.verify(base64.b64decode(attestation["signature_base64"], validate=True), message)
for rel, expected in payload["reviewed_artifacts_sha256"].items():
    p = (root / rel).resolve(strict=True)
    if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Reviewed evidence changed at {rel}")
print(
    f"Strategy D reviewer signature and {len(payload['reviewed_artifacts_sha256'])} evidence files verified."
)
