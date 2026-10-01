from dotenv import load_dotenv
import os
import httpx
import asyncio
import asyncpg

load_dotenv()

# # S3 storage
# AWS_ENDPOINT_URL_S3=https://br-royal-sound-b33thdcr.storage.c-4.ap-southeast-1.aws.neon.tech
# AWS_ACCESS_KEY_ID=nak_live_6e103e6bcf844fa8958ac9bb16e32fab
# AWS_SECRET_ACCESS_KEY=nsk_live_fc155568ce00b6568ecd75ee588de482d32d65fbeab3474de8b308285c4ec579
# AWS_REGION=ap-southeast-1

class neon_db:
    
    def __init__(self):
        self.BASE_URL = os.getenv("DATABASE_URL")
        self.pool = None
        
    async def connect(self):
        self.pool = await asyncpg.create_pool(self.BASE_URL)
        
    async def list_table(self):
        
        async with self.pool.acquire() as conn:
            
            rows = await conn.fetch(
                "SELECT * FROM cases"
            )
            
            return [dict(row) for row in rows]
    
    async def close(self):
        if self.pool:
            await self.pool.close()
            
            
async def main():
    db = neon_db()
    
    await db.connect()
    
    data = await db.list_table()
    
    print(data)
    
    # print(type(data[0]["case_id"]))
    
    # print(data[0]["case_id"])
    
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())