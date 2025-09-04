import os

achievement_key = (
    open("keys/achievement_key.txt").read().strip()
    if os.path.exists("keys/achievement_key.txt")
    else 0
)

listing_key = (
    open("keys/listing_key.txt").read().strip()
    if os.path.exists("keys/listing_key.txt")
    else 0
)