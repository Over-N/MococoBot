from typing import Any, Dict

from database.connection import DatabaseManager


async def get_party_core(db: DatabaseManager, party_id: int) -> Dict[str, Any]:
    rows = await db.execute(
        """
        SELECT p.id, p.title, p.guild_id, p.raid_id, p.start_date, p.owner, p.message,
               p.thread_manage_id, p.is_dealer_closed, p.is_supporter_closed,
               r.name AS raid_name, r.difficulty, r.min_lvl, r.dealer, r.supporter
        FROM party p
        LEFT JOIN raid r ON p.raid_id = r.id
        WHERE p.id = ?
        LIMIT 1
        """,
        (party_id,),
    ) or []
    if not rows:
        return {}
    return dict(rows[0])
