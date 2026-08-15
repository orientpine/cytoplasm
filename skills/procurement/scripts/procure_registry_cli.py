"""Template registry command handlers kept separate to keep procure_cli small."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from skills.procurement.scripts import procure_core as core
from skills.procurement.scripts import procure_registry as registry


def cmd_register(args) -> None:
    """Register a form and its analysis map exactly once, unless forced."""
    try:
        record = registry.register(args.name, Path(args.template), args.force)
    except core.UnsupportedTemplate as error:
        print(error.conversion_request)
        sys.exit(3)
    except registry.RegistryError as error:
        print(f"REGISTER-REJECTED {error}", file=sys.stderr)
        sys.exit(2)
    print(f"REGISTERED name={record.name} format={record.format} fields={','.join(record.fields)}")


def cmd_templates_list(_args) -> None:
    """List registry metadata without reading private form text."""
    records = registry.list_templates()
    if not records:
        print("TEMPLATES-EMPTY")
    for record in records:
        print(f"TEMPLATE name={record.name} format={record.format} fields={','.join(record.fields)}")


def cmd_templates_show(args) -> None:
    """Show one stored template's public generation keys and analysis path."""
    try:
        record = registry.load(args.name)
    except registry.RegistryError as error:
        print(f"TEMPLATE-NOT-FOUND {error}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"name": record.name, "format": record.format, "fields": record.fields,
                      "analysis": str(record.analysis)}, ensure_ascii=False, indent=2))
