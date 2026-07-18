import asyncio
import os
import sys

# Add project root to python path so we can import backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.data.database import async_session
from backend.data.models import Trade, TradePosition
from sqlalchemy import select, delete

async def clean_ghost_trades():
    async with async_session() as session:
        # 1. Find all duplicate closed trades created by God Sync bug
        # Any trade with strategy_id="MANUAL" and status="CLOSED" that has zero pnl
        result = await session.execute(
            select(Trade).where(Trade.strategy_id == "MANUAL", Trade.status == "CLOSED", Trade.pnl == 0.0)
        )
        ghost_trades = result.scalars().all()
        ghost_ids = [t.id for t in ghost_trades]
        
        print(f"Found {len(ghost_ids)} ghost manual trades.")
        if not ghost_ids:
            return
            
        # 2. Delete their positions first (foreign key constraint)
        await session.execute(
            delete(TradePosition).where(TradePosition.parent_trade_id.in_(ghost_ids))
        )
        
        # 3. Delete the trades
        await session.execute(
            delete(Trade).where(Trade.id.in_(ghost_ids))
        )
        
        await session.commit()
        print("Successfully cleaned up database!")

if __name__ == "__main__":
    asyncio.run(clean_ghost_trades())
