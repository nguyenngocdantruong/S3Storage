import os
import re


def generate_unique_slug(name, existing_slugs, requested_slug=None):
    slug_source = requested_slug or name or ''
    slug = slug_source.lower().strip().replace(' ', '-')
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    if not slug:
        slug = 'conn-' + os.urandom(4).hex()

    base_slug = slug
    count = 1
    existing = set(existing_slugs)
    while slug in existing:
        slug = f"{base_slug}-{count}"
        count += 1
    return slug
