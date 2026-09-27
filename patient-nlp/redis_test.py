"""Basic connection example.
"""

import redis

r = redis.Redis(
    host='crow-texture-upcycled-65736.db.redis.io',
    port=14745,
    decode_responses=True,
    username="default",
    password="e3wp40teNrhZU634o2Vxe8DY3AeA3xwA",
)

success = r.set('foo', 'bar')
# True

result = r.get('foo')
print(result)
# >>> bar

