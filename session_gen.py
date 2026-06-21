"""
Session String Generator
────────────────────────
Run this ONCE locally to generate a SESSION_STRING for Railway.
The session string lets the userbot log in without a phone number every time.

Run:  python session_gen.py
Then copy the printed string and paste into your Railway Variables tab as SESSION_STRING.
"""

from pyrogram import Client
import asyncio

print("=" * 55)
print("  Telegram Userbot — Session String Generator")
print("=" * 55)
print()

API_ID   = input("Enter your API_ID   (from my.telegram.org): ").strip()
API_HASH = input("Enter your API_HASH (from my.telegram.org): ").strip()

print()
print("A code will be sent to your Telegram app...")
print()

async def generate():
    async with Client(
        "session_gen_temp",
        api_id=int(API_ID),
        api_hash=API_HASH,
        in_memory=True,
    ) as app:
        string = await app.export_session_string()
        print()
        print("=" * 55)
        print("  ✅ Your SESSION_STRING (copy everything below):")
        print("=" * 55)
        print()
        print(string)
        print()
        print("=" * 55)
        print("  Paste this as SESSION_STRING in Railway Variables.")
        print("  Keep it secret — it grants full account access!")
        print("=" * 55)

asyncio.run(generate())
