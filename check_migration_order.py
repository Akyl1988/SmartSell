import glob
import re

migration_file = glob.glob("migrations/versions/*.py")[0]
print(f"Checking: {migration_file}\n")

with open(migration_file, encoding="utf-8") as f:
    text = f.read()

tables = [m.group(1) for m in re.finditer(r'op\.create_table\(\s*["\'](\w+)["\']', text)]
for i, t in enumerate(tables[:50], 1):
    print(f"{i}. {t}")
