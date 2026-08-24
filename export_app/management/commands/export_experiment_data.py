"""Export all experiment student data to a zip archive (read-only; never deletes data)."""

from __future__ import annotations

import json
import zipfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from export_app.services import (
    aggregate_user_data,
    export_user_csv,
    export_user_json,
    is_excluded_test_username,
    iter_exportable_students,
)


class Command(BaseCommand):
    help = (
        "Export all real student experiment data (JSON + CSV, same shape as reports "
        "exports) into data_export.zip. Excludes loadtest/test accounts. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=str(Path(settings.BASE_DIR) / "data_export.zip"),
            help="Path to the output zip file (default: <project>/data_export.zip)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list users that would be exported; do not write files.",
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"]).resolve()
        students = list(iter_exportable_students())

        from accounts.models import User

        all_students = list(User.objects.filter(role="student").order_by("username"))
        excluded = [
            u.username
            for u in all_students
            if is_excluded_test_username(u.username)
        ]

        self.stdout.write(
            f"Students total={len(all_students)}, "
            f"exporting={len(students)}, excluded={len(excluded)}"
        )
        if excluded:
            self.stdout.write("Excluded: " + ", ".join(excluded))

        if options["dry_run"]:
            for u in students:
                self.stdout.write(f"  would export: {u.username}")
            return

        # Ensure parent exists; never delete existing DB data.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            # Replace only the zip artifact, never touch DB.
            output_path.unlink()

        bulk: dict = {}
        csv_bulk = StringIO()
        csv_bulk.write("\ufeff")  # BOM for Excel, same as web bulk CSV

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            readme = (
                "DBT experiment data export\n"
                "Timezone: Asia/Shanghai (UTC+8) for all timestamps\n"
                "Formats match the reports page JSON/CSV exports\n"
                f"Exported students: {len(students)}\n"
                f"Excluded test users: {', '.join(excluded) or '(none)'}\n"
                "Contents:\n"
                "  users_export.json  - bulk JSON (all users)\n"
                "  users_export.csv   - bulk CSV (all users)\n"
                "  by_user/<username>_data.json\n"
                "  by_user/<username>_data.csv\n"
            )
            zf.writestr("README.txt", readme)

            for student in students:
                data = aggregate_user_data(student)
                bulk[str(student.id)] = data

                json_str = export_user_json(student)
                csv_str = export_user_csv(student)

                safe_name = student.username.replace("/", "_")
                zf.writestr(f"by_user/{safe_name}_data.json", json_str)
                zf.writestr(f"by_user/{safe_name}_data.csv", "\ufeff" + csv_str)

                csv_bulk.write(f"=== 用户: {student.username} ===\n")
                csv_bulk.write(csv_str)
                csv_bulk.write("\n\n")

                self.stdout.write(f"  exported: {student.username}")

            zf.writestr(
                "users_export.json",
                json.dumps(bulk, ensure_ascii=False, indent=2),
            )
            zf.writestr("users_export.csv", csv_bulk.getvalue())

        self.stdout.write(self.style.SUCCESS(f"Wrote {output_path}"))
