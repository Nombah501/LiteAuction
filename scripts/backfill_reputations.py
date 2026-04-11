"""One-time script to calculate reputation for all existing users."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select

from app.db.models import User
from app.db.session import SessionFactory
from app.services.reputation_service import recalculate_user_reputation


async def main() -> None:
    async with SessionFactory() as session:
        result = await session.execute(select(User.id))
        user_ids = list(result.scalars().all())

    print(f"Calculating reputation for {len(user_ids)} users...")
    updated = 0
    for user_id in user_ids:
        async with SessionFactory() as session:
            async with session.begin():
                await recalculate_user_reputation(session, user_id)
        updated += 1
        if updated % 100 == 0:
            print(f"  ...{updated}/{len(user_ids)}")

    print(f"Done. {updated} users processed.")


if __name__ == "__main__":
    asyncio.run(main())
