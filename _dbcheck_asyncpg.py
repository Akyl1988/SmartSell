import asyncio
import os
import re

import asyncpg

raw = os.environ["DATABASE_URL"]
# asyncpg требует postgresql:// или postgres://
url = re.sub(r"^postgresql\+(asyncpg|psycopg2)://", "postgresql://", raw)


async def main():
    conn = await asyncpg.connect(url)
    row = await conn.fetchrow("select current_user, current_database()")
    print(dict(row))
    await conn.close()


asyncio.run(main())
