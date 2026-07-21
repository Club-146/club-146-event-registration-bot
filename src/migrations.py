"""Database migrations framework for the meetup bot."""

from datetime import datetime
from loguru import logger
from typing import Callable, Awaitable

from botspot import get_database


MIGRATIONS_COLLECTION = "migrations"


class Migration:
    """A single database migration."""

    def __init__(self, name: str, apply_fn: Callable[..., Awaitable[None]]):
        self.name = name
        self.apply_fn = apply_fn

    async def apply(self, app):
        await self.apply_fn(app)


# Registry of all migrations in order
MIGRATION_REGISTRY: list[Migration] = []


def migration(name: str):
    """Decorator to register a migration function."""

    def decorator(fn):
        MIGRATION_REGISTRY.append(Migration(name, fn))
        return fn

    return decorator


async def run_migrations(app):
    """Run all pending migrations in order."""
    migrations_col = get_database().get_collection(MIGRATIONS_COLLECTION)

    for mig in MIGRATION_REGISTRY:
        existing = await migrations_col.find_one({"name": mig.name})
        if existing:
            logger.debug(f"Migration '{mig.name}' already applied, skipping.")
            continue

        logger.info(f"Applying migration: {mig.name}")
        try:
            await mig.apply(app)
            await migrations_col.insert_one(
                {
                    "name": mig.name,
                    "applied_at": datetime.now(),
                }
            )
            logger.info(f"Migration '{mig.name}' applied successfully.")
        except Exception as e:
            logger.error(f"Migration '{mig.name}' failed: {e}")
            raise


# ============================================================
# Migration: Archive 2025 events
# ============================================================
@migration("001_archive_2025_events")
async def archive_2025_events(app):
    """Create archived Event documents for all 2025 events and link existing registrations."""
    old_events = [
        {
            "name": "Пермь (Весенняя встреча 2025)",
            "city": "Пермь",
            "city_prepositional": "Перми",
            "date": datetime(2025, 3, 29, 17, 0),
            "date_display": "29 Марта, Сб",
            "time_display": "17:00",
            "venue": "Пермское бистро",
            "address": "ул. Сибирская, 8",
            "status": "archived",
            "enabled": False,
            "pricing_type": "formula",
            "price_formula_base": 500,
            "price_formula_rate": 100,
            "price_formula_reference_year": 2025,
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "target_city_value": "Пермь",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Москва (Весенняя встреча 2025)",
            "city": "Москва",
            "city_prepositional": "Москве",
            "date": datetime(2025, 4, 5, 18, 0),
            "date_display": "5 Апреля, Сб",
            "time_display": "18:00",
            "venue": "People Loft",
            "address": "1-я ул. Энтузиастов, 12, метро Авиамоторная",
            "status": "archived",
            "enabled": False,
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2025,
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "target_city_value": "Москва",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Санкт-Петербург (Весенняя встреча 2025)",
            "city": "Санкт-Петербург",
            "city_prepositional": "Санкт-Петербурге",
            "date": datetime(2025, 4, 5, 17, 0),
            "date_display": "5 Апреля, Сб",
            "time_display": "17:00",
            "venue": "Family Loft",
            "address": "Кожевенная линия, 34, Метро горный институт",
            "status": "archived",
            "enabled": False,
            "pricing_type": "free",
            "free_for_types": [],
            "target_city_value": "Санкт-Петербург",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Белград (Весенняя встреча 2025)",
            "city": "Белград",
            "city_prepositional": "Белграде",
            "date": datetime(2025, 4, 5, 17, 0),
            "date_display": "5 Апреля, Сб",
            "time_display": "Уточняется",
            "venue": "Уточняется",
            "address": "Уточняется",
            "status": "archived",
            "enabled": False,
            "pricing_type": "free",
            "free_for_types": [],
            "target_city_value": "Белград",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Пермь (Летняя встреча 2025)",
            "city": "Пермь",
            "city_prepositional": "Перми",
            "date": datetime(2025, 8, 2, 18, 0),
            "date_display": "2 Августа, Сб",
            "time_display": "18:00-24:00",
            "venue": 'База "Чайка", Беседка 11',
            "address": "г. Пермь, ул. Встречная 33",
            "status": "archived",
            "enabled": False,
            "pricing_type": "fixed_by_year",
            "year_price_map": {
                "2025": 1300,
                "2024": 1300,
                "2023": 1300,
                "2022": 1400,
                "2021": 1400,
                "2020": 1400,
                "2019": 1500,
                "2018": 1500,
                "2017": 1500,
                "2016": 1600,
                "2015": 1600,
                "2014": 1600,
                "2013": 1700,
                "2012": 1700,
                "2011": 1700,
                "2010": 1800,
                "2009": 1800,
                "2008": 1800,
                "2007": 1900,
                "2006": 1900,
                "2005": 1900,
                "2004": 2000,
                "2003": 2000,
                "2002": 2000,
                "2001": 2100,
                "2000": 2100,
                "1999": 2100,
            },
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "target_city_value": "Пермь (Летняя встреча 2025)",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
    ]

    for event_data in old_events:
        target_city_value = event_data.pop("target_city_value")

        # Check if already exists
        existing = await app.events_col.find_one(
            {"name": event_data["name"], "status": "archived"}
        )
        if existing:
            event_id = str(existing["_id"])
            logger.info(
                f"Archived event already exists: {event_data['name']}, skipping insert."
            )
        else:
            result = await app.events_col.insert_one(event_data)
            event_id = str(result.inserted_id)
            logger.info(f"Created archived event: {event_data['name']}")

        # Link existing registrations to this archived event
        link_result = await app.collection.update_many(
            {"target_city": target_city_value, "event_id": {"$exists": False}},
            {"$set": {"event_id": event_id}},
        )
        if link_result.modified_count > 0:
            logger.info(
                f"Linked {link_result.modified_count} registrations to archived event "
                f"'{event_data['name']}'"
            )


# ============================================================
# Migration: Seed 2026 spring events
# ============================================================
@migration("002_seed_2026_spring_events")
async def seed_2026_spring_events(app):
    """Seed the three 2026 spring events."""
    events = [
        {
            "name": "Москва (Весенняя встреча 2026)",
            "city": "Москва",
            "city_prepositional": "Москве",
            "date": datetime(2026, 3, 21, 18, 0),
            "date_display": "21 Марта, Сб",
            "time_display": "18:00",
            "venue": None,
            "address": None,
            "status": "upcoming",
            "enabled": True,
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Санкт-Петербург (Весенняя встреча 2026)",
            "city": "Санкт-Петербург",
            "city_prepositional": "Санкт-Петербурге",
            "date": datetime(2026, 3, 28, 17, 0),
            "date_display": "28 Марта, Сб",
            "time_display": "17:00",
            "venue": None,
            "address": None,
            "status": "upcoming",
            "enabled": True,
            "pricing_type": "free",
            "free_for_types": [],
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
        {
            "name": "Пермь (Весенняя встреча 2026)",
            "city": "Пермь",
            "city_prepositional": "Перми",
            "date": datetime(2026, 3, 28, 17, 0),
            "date_display": "28 Марта, Сб",
            "time_display": "17:00",
            "venue": None,
            "address": None,
            "status": "upcoming",
            "enabled": True,
            "pricing_type": "formula",
            "price_formula_base": 500,
            "price_formula_rate": 100,
            "price_formula_reference_year": 2026,
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        },
    ]

    for event_data in events:
        existing = await app.events_col.find_one(
            {"city": event_data["city"], "date": event_data["date"]}
        )
        if not existing:
            await app.events_col.insert_one(event_data)
            logger.info(f"Seeded event: {event_data['name']}")
        else:
            logger.info(f"Event already exists: {event_data['name']}, skipping.")


# ============================================================
# Migration: Add guest fields to events and registrations
# ============================================================
@migration("003_add_guest_fields")
async def add_guest_fields(app):
    """Add default guest fields to all existing events and registrations."""
    # Add guest fields to all events that don't have them yet
    event_result = await app.events_col.update_many(
        {"guests_enabled": {"$exists": False}},
        {
            "$set": {
                "guests_enabled": False,
                "max_guests_per_person": 3,
                "guest_price_minimum": 0,
            }
        },
    )
    if event_result.modified_count > 0:
        logger.info(f"Added guest fields to {event_result.modified_count} events.")

    # Add guest fields to all registrations that don't have them yet
    reg_result = await app.collection.update_many(
        {"guests": {"$exists": False}},
        {
            "$set": {
                "guests": [],
                "guest_count": 0,
            }
        },
    )
    if reg_result.modified_count > 0:
        logger.info(f"Added guest fields to {reg_result.modified_count} registrations.")


# ============================================================
# Migration: Update pricing for Moscow/SPb + add early bird + stepped formula
# ============================================================
@migration("004_update_pricing_and_early_bird")
async def update_pricing_and_early_bird(app):
    """Update Moscow/SPb pricing with stepped formula and add early bird discount."""
    # Update Moscow event (spring 2026)
    moscow_result = await app.events_col.update_one(
        {"city": "Москва", "date": datetime(2026, 3, 21, 18, 0)},
        {
            "$set": {
                "price_formula_base": 1500,
                "price_formula_rate": 500,
                "price_formula_reference_year": 2025,
                "price_formula_step": 3,
                "early_bird_discount": 500,
                "early_bird_deadline": datetime(2026, 3, 18),
                "free_for_types": ["TEACHER", "ORGANIZER"],
            }
        },
    )
    if moscow_result.modified_count > 0:
        logger.info("Updated Moscow event pricing: stepped formula + early bird.")

    # Update SPb event (spring 2026) — no longer free!
    spb_result = await app.events_col.update_one(
        {"city": "Санкт-Петербург", "date": datetime(2026, 3, 28, 17, 0)},
        {
            "$set": {
                "pricing_type": "formula",
                "price_formula_base": 1000,
                "price_formula_rate": 400,
                "price_formula_reference_year": 2025,
                "price_formula_step": 3,
                "early_bird_discount": 500,
                "early_bird_deadline": datetime(2026, 3, 25),
                "free_for_types": ["TEACHER", "ORGANIZER"],
            }
        },
    )
    if spb_result.modified_count > 0:
        logger.info("Updated SPb event: free -> formula pricing + early bird.")

    # Add default price_formula_step to all events missing it
    step_result = await app.events_col.update_many(
        {"price_formula_step": {"$exists": False}},
        {"$set": {"price_formula_step": 1}},
    )
    if step_result.modified_count > 0:
        logger.info(
            f"Added default price_formula_step=1 to {step_result.modified_count} events."
        )

    # Add default early bird fields to all events missing them
    eb_result = await app.events_col.update_many(
        {"early_bird_discount": {"$exists": False}},
        {"$set": {"early_bird_discount": 0, "early_bird_deadline": None}},
    )
    if eb_result.modified_count > 0:
        logger.info(
            f"Added default early bird fields to {eb_result.modified_count} events."
        )


# ============================================================
# Migration: Update venue/time details for all 2026 events + Perm pricing
# ============================================================
@migration("005_update_event_details_and_perm_pricing")
async def update_event_details_and_perm_pricing(app):
    """Update venue/address/time for 2026 spring events and fix Perm pricing."""
    # Moscow: add venue and address, update time display
    moscow_result = await app.events_col.update_one(
        {"city": "Москва", "date": datetime(2026, 3, 21, 18, 0)},
        {
            "$set": {
                "venue": "Лофт «Мультирум»",
                "address": "ул. Смирновская, 25с3",
                "time_display": "18:00-00:00",
            }
        },
    )
    if moscow_result.modified_count > 0:
        logger.info("Updated Moscow event: venue, address, time.")

    # SPb: add venue and address, update time display
    spb_result = await app.events_col.update_one(
        {"city": "Санкт-Петербург", "date": datetime(2026, 3, 28, 17, 0)},
        {
            "$set": {
                "venue": "Лофт «МыВместе»",
                "address": "ул. Дивенская, 5",
                "time_display": "18:00-23:00",
            }
        },
    )
    if spb_result.modified_count > 0:
        logger.info("Updated SPb event: venue, address, time.")

    # Perm: update time, pricing formula (base 500->1500), add early bird
    perm_result = await app.events_col.update_one(
        {"city": "Пермь", "date": datetime(2026, 3, 28, 17, 0)},
        {
            "$set": {
                "time_display": "18:00-00:00",
                "price_formula_base": 1500,
                "price_formula_rate": 100,
                "price_formula_reference_year": 2026,
                "price_formula_step": 1,
                "early_bird_discount": 500,
                "early_bird_deadline": datetime(2026, 3, 25),
            }
        },
    )
    if perm_result.modified_count > 0:
        logger.info("Updated Perm event: time, pricing, early bird.")


# ============================================================
# Migration: Bump Moscow & SPb base price (+500) for correct discount math
# ============================================================
@migration("006_bump_moscow_spb_base_price")
async def bump_moscow_spb_base_price(app):
    """Increase base price by 500 for Moscow and SPb so early bird discount is applied to higher base.

    Moscow: 1500 → 2000 (early bird 500 → effective 1500)
    SPb: 1000 → 1500 (early bird 500 → effective 1000)
    Perm stays unchanged.
    """
    moscow_result = await app.events_col.update_one(
        {"city": "Москва", "date": datetime(2026, 3, 21, 18, 0)},
        {"$set": {"price_formula_base": 2000}},
    )
    if moscow_result.modified_count > 0:
        logger.info("Bumped Moscow base price: 1500 → 2000.")

    spb_result = await app.events_col.update_one(
        {"city": "Санкт-Петербург", "date": datetime(2026, 3, 28, 17, 0)},
        {"$set": {"price_formula_base": 1500}},
    )
    if spb_result.modified_count > 0:
        logger.info("Bumped SPb base price: 1000 → 1500.")


# ============================================================
# Migration: Seed first_source=before_tracking for known users
# ============================================================
@migration("007_backfill_before_tracking_sources")
async def backfill_before_tracking_sources(app):
    """Mark every known Telegram user with first_source=before_tracking if missing.

    Users who already interacted with the bot before deep-link attribution
    must keep a stable original source so later campaign clicks only update
    last_source + history, never overwrite first_source.
    """
    source = app.BEFORE_TRACKING_SOURCE
    now = datetime.now().isoformat()

    active_ids = set(await app.collection.distinct("user_id"))
    deleted_ids = set(await app.deleted_users.distinct("user_id"))
    all_ids = {uid for uid in (active_ids | deleted_ids) if uid is not None}
    already = set(await app.user_sources.distinct("user_id"))
    missing = sorted(all_ids - already)

    if not missing:
        logger.info("before_tracking backfill: nothing to seed.")
        return

    docs = [
        {
            "user_id": uid,
            "username": None,
            "first_source": source,
            "first_source_at": now,
            "first_utm_source": source,
            "first_utm_campaign": None,
            "first_utm_content": None,
            "last_source": source,
            "last_source_at": now,
            "last_utm_source": source,
            "last_utm_campaign": None,
            "last_utm_content": None,
            "history": [],
            "click_count": 0,
            "seeded": "before_tracking_backfill",
        }
        for uid in missing
    ]
    # insert_many in chunks to avoid oversized batches
    chunk = 500
    inserted = 0
    for i in range(0, len(docs), chunk):
        result = await app.user_sources.insert_many(docs[i : i + chunk])
        inserted += len(result.inserted_ids)
    logger.info(
        f"before_tracking backfill: seeded {inserted} users "
        f"(known={len(all_ids)}, already_tracked={len(already)})."
    )


# ============================================================
# Migration: Fill utm_* fields on rows created before structured parse
# ============================================================
@migration("008_backfill_utm_fields_from_raw")
async def backfill_utm_fields_from_raw(app):
    """Populate first/last utm_* from raw first_source/last_source when missing.

    Migration 007 originally wrote only first_source/last_source. Later code
    expects first_utm_source / first_utm_campaign for stats. Re-parse raw
    payloads (including synthetic before_tracking / direct).
    """
    cursor = app.user_sources.find(
        {
            "$or": [
                {"first_utm_source": {"$exists": False}},
                {"first_utm_source": None},
                {"last_utm_source": {"$exists": False}},
                {"last_utm_source": None},
            ]
        }
    )
    rows = await cursor.to_list(length=None)
    fixed = 0
    for row in rows:
        updates = {}
        if not row.get("first_utm_source"):
            attrs = app.parse_start_attribution(row.get("first_source")) or {}
            if attrs.get("utm_source"):
                updates["first_utm_source"] = attrs["utm_source"]
                updates["first_utm_campaign"] = attrs.get("utm_campaign")
                updates["first_utm_content"] = attrs.get("utm_content")
        if not row.get("last_utm_source"):
            attrs = app.parse_start_attribution(row.get("last_source")) or {}
            if attrs.get("utm_source"):
                updates["last_utm_source"] = attrs["utm_source"]
                updates["last_utm_campaign"] = attrs.get("utm_campaign")
                updates["last_utm_content"] = attrs.get("utm_content")
        if updates:
            await app.user_sources.update_one({"_id": row["_id"]}, {"$set": updates})
            fixed += 1
    logger.info(f"utm field backfill: updated {fixed} of {len(rows)} candidate rows.")
