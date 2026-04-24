import asyncio
from database import client

async def test():
    r = await client.admin.command('ping')
    print('MongoDB Atlas connection OK:', r)

asyncio.run(test())
