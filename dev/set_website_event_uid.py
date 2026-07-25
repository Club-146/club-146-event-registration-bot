#!/usr/bin/env python3
"""Bind one bot event document to one website ``Event.uid``.

The website's SQL ``events`` table is the source of truth for events; its
stable ``Event.uid`` is the cross-system key (contract:
146.school/docs/events-people-data-integration.md, «Контракт по событиям»).
Migration ``010_add_website_event_uid`` declares the field as ``None`` on every
event document; this script sets an actual value.

It is deliberately a separate, explicit, per-environment step. A migration runs
identically everywhere, and staging must never inherit production's website UID
— pointing a staging bot at the production event is exactly the mistake that
would create real admissions from test registrations.

Usage (dry run first — it is the default):

    python dev/set_website_event_uid.py \\
        --bot-event-id 6a599a17a37724d81b7eadc3 \\
        --website-event-uid event-1@146.school

    # …then repeat with --apply

Unbind (rollback for one event):

    python dev/set_website_event_uid.py --bot-event-id <id> --unset --apply

Full rollback of the migration, for reference:

    db.events.updateMany({}, {$unset: {website_event_uid: ""}})

Connection comes from the same environment the bot uses:
``BOTSPOT_MONGO_DATABASE_CONN_STR`` and ``BOTSPOT_MONGO_DATABASE_DATABASE``.
"""

import argparse
import os
import sys

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from pymongo import MongoClient


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-event-id", required=True,
                        help="ObjectId of the event document in Mongo")
    parser.add_argument("--website-event-uid",
                        help="stable website Event.uid, e.g. event-1@146.school")
    parser.add_argument("--unset", action="store_true",
                        help="clear the link instead of setting it")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it the script only reports")
    args = parser.parse_args(argv)
    if args.unset == bool(args.website_event_uid):
        parser.error("give exactly one of --website-event-uid or --unset")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()

    conn = os.environ.get("BOTSPOT_MONGO_DATABASE_CONN_STR")
    database = os.environ.get("BOTSPOT_MONGO_DATABASE_DATABASE")
    if not conn or not database:
        print("BOTSPOT_MONGO_DATABASE_CONN_STR / _DATABASE are not set",
              file=sys.stderr)
        return 2

    try:
        object_id = ObjectId(args.bot_event_id)
    except (InvalidId, TypeError):
        print(f"not a valid ObjectId: {args.bot_event_id!r}", file=sys.stderr)
        return 2

    events = MongoClient(conn)[database]["events"]
    event = events.find_one({"_id": object_id})
    if not event:
        print(f"no event document with _id={args.bot_event_id}", file=sys.stderr)
        return 1

    target = None if args.unset else args.website_event_uid
    print(f"event:   {event.get('name')!r} ({event.get('status')})")
    print(f"current: website_event_uid={event.get('website_event_uid')!r}")
    print(f"new:     website_event_uid={target!r}")

    # Both sides are unique keys; refuse to point two bot events at one website
    # event, which would let two registration funnels mint admissions for it.
    if target is not None:
        clash = events.find_one(
            {"website_event_uid": target, "_id": {"$ne": object_id}})
        if clash:
            print(f"refusing: {target!r} is already bound to event "
                  f"{clash['_id']} ({clash.get('name')!r})", file=sys.stderr)
            return 1

    if not args.apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        return 0

    result = events.update_one({"_id": object_id},
                               {"$set": {"website_event_uid": target}})
    print(f"\nwritten: matched={result.matched_count} "
          f"modified={result.modified_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
