"""Run the fixed public exchange collectors with writes confined to data/fundamentals."""

from __future__ import annotations

import importlib
import os
import sys

from .discovery_guard import REPO, confined, output_root

NETWORK = {
    "d2-bse-priority-catalogues": "results_bse_priority",
    "d2-bse-priority-legacy": "results_bse_priority",
    "d2-bse-global-native": "results_bse_priority",
    "d2-bse-native-fallback": "results_bse_priority",
    "d2-bse-originals": "results_bse_census",
    "structured": "fundamental_nse",
    "historical-paths": "fundamental_alias_paths",
    "audit-mirrors": "fundamental_mirror_audit",
    "audit-old-tables": "fundamental_mirror_audit",
    "historical-aliases": "fundamental_nse",
    "alias-documents": "fundamental_notice_proof",
    "case-documents": "fundamental_notice_proof",
    "timing-documents": "fundamental_notice_proof",
    "fiscal-documents": "fundamental_notice_proof",
    "formal-documents": "fundamental_notice_proof",
    "review-identity-days": "fundamental_formal_audit",
    "category-documents": "fundamental_notice_proof",
    "notices": "fundamental_notices",
    "issuer-fallback": "fundamental_notice_fallback",
    "market-fallback": "fundamental_notice_market",
    "first-documents": "fundamental_notice_proof",
    "recover-transfers": "fundamental_retry",
    "notice-tail": "fundamental_notice_tail",
    "image-folios": "fundamental_media_recovery",
    "market-documents": "fundamental_notice_proof",
    "calendar-documents": "fundamental_notice_proof",
    "language-documents": "fundamental_notice_proof",
    "reconcile-documents": "fundamental_notice_proof",
}
OFFLINE = {
    "d2-bse-import-inline": ("results_bse_priority","import_inline"),
    "d2-bse-global-plan": ("results_bse_priority","global_plan"),
    "d2-bse-priority-plan": ("results_bse_priority", "plan_only"),
    "d2-bse-seal": ("results_bse_evidence", "seal"),
    "d2-bse-verify": ("results_bse_evidence", "verify"),
    "d2-bse-count": ("results_bse_census", "report"),
    "checkpoint": ("fundamental_checkpoint", "publish"),
    "download-readiness": ("fundamental_download_readiness", "publish"),
    "bind-formal-review": ("fundamental_formal_audit", "main"),
    "cached-originals": ("fundamental_notice_cache", "main"),
    "date-identities": ("fundamental_identity", "build"),
    "ledger": ("fundamental_nse_ledger", "publish"),
    "manifest": ("fundamental_manifest", "write_manifest"),
}


def worker_command(operation):
    return [sys.executable, "-B", "-m", "thesistally.fundamental_sources", "--worker", operation]


def run(operation):
    if operation not in NETWORK and operation not in OFFLINE:
        raise ValueError("Unknown results-source operation")
    if not (REPO / ".git").is_file():
        raise ValueError("Use the dedicated results worktree")
    destination = output_root(REPO, workstream="fundamentals")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(destination),
        "TMP": str(destination),
        "TEMP": str(destination),
    }
    result = confined(
        worker_command(operation),
        destination,
        cwd=REPO,
        env=env,
        public_collector=operation if operation in NETWORK else None,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "Results collector failed"
        )
    print(result.stdout.rstrip())


def main():
    if len(sys.argv) == 2:
        run(sys.argv[1])
    elif len(sys.argv) == 3 and sys.argv[1] == "--worker":
        operation = sys.argv[2]
        # Confirm the host really denied opening a repository source for writing,
        # without ever modifying the file. A direct --worker invocation must fail.
        try:
            handle = os.open(REPO / "pyproject.toml", os.O_WRONLY)
        except PermissionError:
            pass
        except OSError as exc:
            import errno

            if exc.errno != errno.EROFS:
                raise
        else:
            os.close(handle)
            raise RuntimeError("Run the source launcher to activate the filesystem boundary")
        if operation in NETWORK:
            module, method = (
                NETWORK[operation],
                {
                    "d2-bse-priority-catalogues": "collect",
                    "d2-bse-priority-legacy": "legacy_documents",
                    "d2-bse-global-native": "global_native",
                    "d2-bse-native-fallback": "remaining_native",
                    "market-documents": "market_documents",
                    "audit-old-tables": "legacy_tables",
                    "historical-aliases": "historical_aliases",
                    "alias-documents": "alias_documents",
                    "case-documents": "case_documents",
                    "timing-documents": "timing_documents",
                    "fiscal-documents": "fiscal_documents",
                    "formal-documents": "formal_documents",
                    "review-identity-days": "source_daily",
                    "category-documents": "category_documents",
                    "calendar-documents": "calendar_documents",
                    "language-documents": "language_documents",
                    "reconcile-documents": "reconcile_documents",
                }.get(operation, "main"),
            )
        else:
            module, method = OFFLINE[operation]
        getattr(importlib.import_module(f"thesistally.{module}"), method)()
    else:
        raise SystemExit("Use a registered results-source operation")


if __name__ == "__main__":
    main()
