#!/usr/bin/env python3
"""
Fix migration table creation order based on FK dependencies.
"""
import re
from pathlib import Path

migration_file = Path("migrations/versions/812e91819952_initial_baseline.py")
content = migration_file.read_text(encoding="utf-8")

# Extract all create_table blocks with their names and FKs
table_pattern = re.compile(
    r'op\.create_table\(\s*["\'](\w+)["\'][,\s]+(.+?)(?=\n    \)\n    op\.create_(?:table|index)|$)',
    re.DOTALL,
)

tables = {}
for match in table_pattern.finditer(content):
    table_name = match.group(1)
    table_def = match.group(2)

    # Find FK references
    fk_refs = []
    fk_pattern = re.compile(r'sa\.ForeignKeyConstraint\([^,]+,\s*\[["\']([^"\']+)\.', re.MULTILINE)
    for fk_match in fk_pattern.finditer(table_def):
        ref_table = fk_match.group(1)
        if ref_table != table_name:  # Skip self-references
            fk_refs.append(ref_table)

    full_block = match.group(0)
    tables[table_name] = {"definition": full_block, "dependencies": fk_refs}

print(f"Found {len(tables)} tables\n")


# Topological sort
def topological_sort(tables_dict):
    """Sort tables by FK dependencies."""
    sorted_tables = []
    visited = set()
    temp_mark = set()

    def visit(table_name):
        if table_name in temp_mark:
            # Circular dependency - skip this FK for now
            return
        if table_name in visited:
            return

        temp_mark.add(table_name)

        # Visit dependencies first
        if table_name in tables_dict:
            for dep in tables_dict[table_name]["dependencies"]:
                if dep in tables_dict:  # Only if dependency exists
                    visit(dep)

        temp_mark.remove(table_name)
        visited.add(table_name)
        if table_name not in sorted_tables:
            sorted_tables.append(table_name)

    for table in tables_dict.keys():
        visit(table)

    return sorted_tables


# Known circular dependencies to break
# Format: {table_name: [list of FK dependencies to defer]}
CIRCULAR_FIXES = {
    "users": ["companies"],  # users.company_id -> companies, but companies.owner_id -> users
    "companies": [],  # Will create companies first without owner_id FK
    "subscriptions": ["billing_payments"],  # subscriptions.last_payment_id -> billing_payments
    "billing_payments": [],  # Will have subscription_id FK
}

# Also track deferred FKs to add them later
deferred_fks = {}

# Remove circular FKs temporarily
for table, skip_deps in CIRCULAR_FIXES.items():
    if table in tables:
        original_deps = tables[table]["dependencies"].copy()
        tables[table]["dependencies"] = [d for d in tables[table]["dependencies"] if d not in skip_deps]
        # Track what we removed
        removed = [d for d in original_deps if d in skip_deps]
        if removed:
            deferred_fks[table] = removed

sorted_table_names = topological_sort(tables)

print("Correct order:")
for i, name in enumerate(sorted_table_names, 1):
    deps = tables[name]["dependencies"]
    print(f"{i:2}. {name:30} <- {', '.join(deps) if deps else '(no FK)'}")

print("\n" + "=" * 80)
print("Creating fixed migration...")

# Build new upgrade() function
new_upgrade_lines = [
    "def upgrade() -> None:",
    '    """Upgrade schema."""',
    "    # Fixed table creation order based on FK dependencies",
]

for table_name in sorted_table_names:
    table_def = tables[table_name]["definition"]
    new_upgrade_lines.append(f"    {table_def}")

# Find all create_index calls
index_pattern = re.compile(r"(    op\.create_index\([^)]+\))", re.MULTILINE)
indexes = index_pattern.findall(content)

for idx in indexes:
    new_upgrade_lines.append(idx)

new_upgrade_func = "\n".join(new_upgrade_lines)

# Find downgrade function
downgrade_match = re.search(r"(def downgrade\(\) -> None:.*)", content, re.DOTALL)
downgrade_func = downgrade_match.group(1) if downgrade_match else "def downgrade() -> None:\n    pass"

# Build new file
header = content.split("def upgrade() -> None:")[0]
new_content = header + new_upgrade_func + "\n\n\n" + downgrade_func

# Write fixed migration
migration_file.write_text(new_content, encoding="utf-8")
print(f"\n✅ Fixed migration saved to {migration_file}")
