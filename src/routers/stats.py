import io
from collections import defaultdict

import pandas as pd
import seaborn as sns
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from matplotlib import pyplot as plt
from src.app import App
from src.routers.admin import _format_graduate_type
from src.routers.crm import router
from botspot import commands_menu, send_safe
from botspot.components.qol.bot_commands_menu import Visibility
from botspot.utils.admin_filter import AdminFilter


def get_median(ratios):
    if not ratios:
        return 0
    ratios.sort()
    return ratios[len(ratios) // 2]


# ---------------------------------------------------------------------------
# Data-fetching helpers
# ---------------------------------------------------------------------------


async def _fetch_event_metadata(app: App):
    all_events = await app.get_all_events()
    event_name_map = {str(e["_id"]): e.get("city", str(e["_id"])) for e in all_events}
    enabled_event_ids = {
        str(e["_id"])
        for e in all_events
        if e.get("enabled") and e.get("status") == "upcoming"
    }
    free_event_ids = {
        str(e["_id"])
        for e in all_events
        if e.get("pricing_type") == "free" and str(e["_id"]) in enabled_event_ids
    }
    return all_events, event_name_map, enabled_event_ids, free_event_ids


async def _fetch_city_counts(app: App):
    city_cursor = app.collection.aggregate(
        [{"$group": {"_id": "$event_id", "count": {"$sum": 1}}}]
    )
    active_city_stats = await city_cursor.to_list(length=None)

    deleted_city_cursor = app.deleted_users.aggregate(
        [{"$group": {"_id": "$event_id", "count": {"$sum": 1}}}]
    )
    deleted_city_stats = await deleted_city_cursor.to_list(length=None)
    return active_city_stats, deleted_city_stats


def _combine_count_stats(active_list, deleted_list):
    combined = {}
    for stat in active_list:
        eid = stat["_id"]
        combined[eid] = {"active": stat["count"], "deleted": 0}
    for stat in deleted_list:
        eid = stat["_id"]
        if eid in combined:
            combined[eid]["deleted"] = stat["count"]
        else:
            combined[eid] = {"active": 0, "deleted": stat["count"]}
    return combined


def _combine_stat_pairs(active_list, deleted_list):
    combined = {}
    for stat in active_list:
        eid = stat["_id"]
        combined[eid] = {"active": stat, "deleted": None}
    for stat in deleted_list:
        eid = stat["_id"]
        if eid in combined:
            combined[eid]["deleted"] = stat
        else:
            combined[eid] = {"active": None, "deleted": stat}
    return combined


_GRAD_TYPE_PIPELINE = [
    {
        "$addFields": {
            "normalized_type": {
                "$toUpper": {
                    "$cond": [
                        {
                            "$or": [
                                {"$eq": ["$graduate_type", None]},
                                {
                                    "$eq": [
                                        {"$toUpper": "$graduate_type"},
                                        "GRADUATE",
                                    ]
                                },
                            ]
                        },
                        "GRADUATE",
                        "$graduate_type",
                    ]
                }
            }
        }
    },
    {"$group": {"_id": "$normalized_type", "count": {"$sum": 1}}},
]


async def _fetch_grad_type_stats(app: App):
    active_cursor = app.collection.aggregate(_GRAD_TYPE_PIPELINE)
    active_grad_stats = await active_cursor.to_list(length=None)

    deleted_cursor = app.deleted_users.aggregate(_GRAD_TYPE_PIPELINE)
    deleted_grad_stats = await deleted_cursor.to_list(length=None)
    return active_grad_stats, deleted_grad_stats


def _combine_grad_type_stats(active_grad_stats, deleted_grad_stats):
    combined = {}
    for stat in active_grad_stats:
        grad_type = stat["_id"] or "GRADUATE"
        combined[grad_type] = {"active": stat["count"], "deleted": 0}
    for stat in deleted_grad_stats:
        grad_type = stat["_id"] or "GRADUATE"
        if grad_type in combined:
            combined[grad_type]["deleted"] = stat["count"]
        else:
            combined[grad_type] = {"active": 0, "deleted": stat["count"]}
    return combined


_PAYMENT_STATUS_PIPELINE_FIELDS = {
    "confirmed_count": {
        "$sum": {
            "$cond": [{"$eq": ["$payment_status", "confirmed"]}, 1, 0]
        }
    },
    "pending_count": {
        "$sum": {
            "$cond": [
                {"$or": [{"$eq": ["$payment_status", "pending"]}]},
                1,
                0,
            ]
        }
    },
    "declined_count": {
        "$sum": {
            "$cond": [{"$eq": ["$payment_status", "declined"]}, 1, 0]
        }
    },
    "unpaid_count": {
        "$sum": {
            "$cond": [
                {
                    "$or": [
                        {"$eq": ["$payment_status", None]},
                        {"$eq": ["$payment_status", "Не оплачено"]},
                        {"$not": "$payment_status"},
                    ]
                },
                1,
                0,
            ]
        }
    },
    "total_paid": {"$sum": {"$ifNull": ["$payment_amount", 0]}},
}


def _build_payment_pipeline(free_event_ids, include_amounts: bool):
    group_fields = dict(_PAYMENT_STATUS_PIPELINE_FIELDS)
    if include_amounts:
        group_fields["payments"] = {
            "$push": {
                "payment": {"$ifNull": ["$payment_amount", 0]},
                "formula": {"$ifNull": ["$formula_payment_amount", 0]},
                "regular": {"$ifNull": ["$regular_payment_amount", 0]},
                "discounted": {"$ifNull": ["$discounted_payment_amount", 0]},
            }
        }
    return [
        {"$match": {"event_id": {"$nin": list(free_event_ids)}}},
        {"$match": {"graduate_type": {"$ne": "TEACHER"}}},
        {"$group": {"_id": "$event_id", **group_fields}},
    ]


async def _fetch_payment_stats(app: App, free_event_ids, include_amounts: bool = False):
    pipeline = _build_payment_pipeline(free_event_ids, include_amounts)
    active_cursor = app.collection.aggregate(pipeline)
    active_payment_stats = await active_cursor.to_list(length=None)

    deleted_cursor = app.deleted_users.aggregate(pipeline)
    deleted_payment_stats = await deleted_cursor.to_list(length=None)
    return active_payment_stats, deleted_payment_stats


# ---------------------------------------------------------------------------
# Text-formatting helpers
# ---------------------------------------------------------------------------


def _format_city_section(city_stats_combined, event_name_map):
    lines = ["<b>🌆 По городам:</b>"]
    total_active = 0
    total_deleted = 0

    for eid, counts in sorted(
        city_stats_combined.items(),
        key=lambda x: event_name_map.get(x[0], x[0] or ""),
    ):
        active_count = counts["active"]
        deleted_count = counts["deleted"]
        total_count = active_count + deleted_count
        total_active += active_count
        total_deleted += deleted_count

        city_name = event_name_map.get(eid, eid or "Неизвестно")
        deleted_note = f" (из них {deleted_count} удал.)" if deleted_count > 0 else ""
        lines.append(f"• {city_name}: <b>{total_count}</b> человек{deleted_note}")

    total = total_active + total_deleted
    deleted_percentage = (
        f" ({total_deleted / total:.1%} удаленных)" if total > 0 else ""
    )
    lines.append(f"\nВсего: <b>{total}</b> человек{deleted_percentage}")
    return "\n".join(lines), total_active, total_deleted


def _format_grad_type_section(grad_stats_combined):
    lines = ["<b>👥 По статусу:</b>"]
    for grad_type, counts in sorted(grad_stats_combined.items()):
        active_count = counts["active"]
        deleted_count = counts["deleted"]
        total_count = active_count + deleted_count
        text = _format_graduate_type(grad_type.upper(), plural=total_count != 1)
        deleted_note = f" (из них {deleted_count} удал.)" if deleted_count > 0 else ""
        lines.append(f"• {text}: <b>{total_count}</b>{deleted_note}")
    return "\n".join(lines)


def _compute_payment_ratios(payments):
    ratios_formula = []
    ratios_regular = []
    ratios_discounted = []
    for p in payments:
        if p["payment"] > 0:
            if p["formula"] > 0:
                ratios_formula.append((p["payment"] / p["formula"]) * 100)
            if p["regular"] > 0:
                ratios_regular.append((p["payment"] / p["regular"]) * 100)
            if p["discounted"] > 0:
                ratios_discounted.append((p["payment"] / p["discounted"]) * 100)
    return ratios_formula, ratios_regular, ratios_discounted


def _format_payment_status_block(stat, PAYMENT_STATUS_MAP, label_prefix=""):
    lines = []
    if stat:
        lines.append(
            f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{stat['confirmed_count']}</b>"
        )
        lines.append(
            f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{stat['pending_count']}</b>"
        )
        lines.append(
            f"⚪️ {PAYMENT_STATUS_MAP[None]}: <b>{stat['declined_count'] + stat['unpaid_count']}</b>"
        )
    else:
        lines.append("Нет активных пользователей")
    return "\n".join(lines)


def _format_deleted_payment_block(stat, PAYMENT_STATUS_MAP):
    lines = []
    if stat and (stat["confirmed_count"] > 0 or stat["pending_count"] > 0):
        lines.append("\n<u>Удаленные пользователи с оплатами:</u>")
        if stat["confirmed_count"] > 0:
            lines.append(
                f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{stat['confirmed_count']}</b>"
            )
        if stat["pending_count"] > 0:
            lines.append(
                f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{stat['pending_count']}</b>"
            )
    return "\n".join(lines)


def _format_full_payment_section(payment_stats_combined, event_name_map, PAYMENT_STATUS_MAP):
    lines = ["<b>💰 Статистика оплат:</b>"]
    total_paid_active = 0
    total_paid_deleted = 0
    all_ratios_formula = []
    all_ratios_regular = []
    all_ratios_discounted = []

    for eid, stats in sorted(
        payment_stats_combined.items(),
        key=lambda x: event_name_map.get(x[0], x[0] or ""),
    ):
        active_stat = stats["active"]
        deleted_stat = stats["deleted"]

        active_paid = active_stat["total_paid"] if active_stat else 0
        active_payments = active_stat["payments"] if active_stat else []
        deleted_paid = deleted_stat["total_paid"] if deleted_stat else 0
        deleted_payments = deleted_stat["payments"] if deleted_stat else []

        all_payments = active_payments + deleted_payments
        rf, rr, rd = _compute_payment_ratios(all_payments)
        all_ratios_formula.extend(rf)
        all_ratios_regular.extend(rr)
        all_ratios_discounted.extend(rd)

        active_formula_total = sum(p["formula"] for p in active_payments)
        active_regular_total = sum(p["regular"] for p in active_payments)
        active_discounted_total = sum(p["discounted"] for p in active_payments)
        deleted_formula_total = sum(p["formula"] for p in deleted_payments)
        deleted_regular_total = sum(p["regular"] for p in deleted_payments)
        deleted_discounted_total = sum(p["discounted"] for p in deleted_payments)

        total_paid_active += active_paid
        total_paid_deleted += deleted_paid

        median_formula = get_median(rf)
        median_regular = get_median(rr)
        median_discounted = get_median(rd)

        total_paid = active_paid + deleted_paid
        deleted_note = f" (из них {deleted_paid:,} от удал.)" if deleted_paid > 0 else ""

        city_name = event_name_map.get(eid, eid or "Неизвестно")
        lines.append(f"\n<b>{city_name}:</b>")
        lines.append(f"💵 Собрано: <b>{total_paid:,}</b> руб.{deleted_note}")
        lines.append(f"📊 Медиана % от формулы: <i>{median_formula:.1f}%</i>")
        lines.append(f"📊 Медиана % от регулярной: <i>{median_regular:.1f}%</i>")
        lines.append(f"📊 Медиана % от мин. со скидкой: <i>{median_discounted:.1f}%</i>\n")
        lines.append("<u>Статусы платежей (активные пользователи):</u>")
        lines.append(_format_payment_status_block(active_stat, PAYMENT_STATUS_MAP))
        lines.append(_format_deleted_payment_block(deleted_stat, PAYMENT_STATUS_MAP))

    total_paid = total_paid_active + total_paid_deleted
    if total_paid > 0:
        deleted_percentage = (
            f" ({total_paid_deleted / total_paid:.1%} от удаленных)"
            if total_paid > 0
            else ""
        )
        lines.append(f"\n<b>💵 Итого собрано: {total_paid:,} руб.</b>{deleted_percentage}")

        total_median_formula = get_median(all_ratios_formula)
        total_median_regular = get_median(all_ratios_regular)
        total_median_discounted = get_median(all_ratios_discounted)

        lines.append(
            f"📊 Общая медиана % от формулы: <i>{total_median_formula:.1f}%</i>"
        )
        lines.append(
            f"📊 Общая медиана % от регулярной: <i>{total_median_regular:.1f}%</i>"
        )
        lines.append(
            f"📊 Общая медиана % от мин. со скидкой: <i>{total_median_discounted:.1f}%</i>"
        )

    return "\n".join(lines)


def _format_simple_payment_section(payment_stats_combined, event_name_map, PAYMENT_STATUS_MAP):
    lines = ["<b>💰 Статусы оплат:</b>"]
    total_active_confirmed = 0
    total_active_pending = 0
    total_active_declined = 0
    total_active_unpaid = 0
    total_deleted_confirmed = 0
    total_deleted_pending = 0
    total_paid_active = 0
    total_paid_deleted = 0

    for eid, stats in sorted(
        payment_stats_combined.items(),
        key=lambda x: event_name_map.get(x[0], x[0] or ""),
    ):
        active_stat = stats["active"]
        deleted_stat = stats["deleted"]

        active_confirmed = active_stat["confirmed_count"] if active_stat else 0
        active_pending = active_stat["pending_count"] if active_stat else 0
        active_declined = active_stat["declined_count"] if active_stat else 0
        active_unpaid = active_stat["unpaid_count"] if active_stat else 0
        active_paid = active_stat["total_paid"] if active_stat else 0

        deleted_confirmed = deleted_stat["confirmed_count"] if deleted_stat else 0
        deleted_pending = deleted_stat["pending_count"] if deleted_stat else 0
        deleted_paid = deleted_stat["total_paid"] if deleted_stat else 0

        total_active_confirmed += active_confirmed
        total_active_pending += active_pending
        total_active_declined += active_declined
        total_active_unpaid += active_unpaid
        total_deleted_confirmed += deleted_confirmed
        total_deleted_pending += deleted_pending
        total_paid_active += active_paid
        total_paid_deleted += deleted_paid

        city_name = event_name_map.get(eid, eid or "Неизвестно")
        lines.append(f"\n<b>{city_name}:</b>")

        total_active_statuses = (
            active_confirmed + active_pending + active_declined + active_unpaid
        )
        if total_active_statuses > 0:
            lines.append(
                f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{active_confirmed}</b>"
            )
            lines.append(
                f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{active_pending}</b>"
            )
            lines.append(
                f"⚪️ {PAYMENT_STATUS_MAP[None]}: <b>{active_declined + active_unpaid}</b>"
            )
        else:
            lines.append("Нет активных пользователей")

        if deleted_confirmed > 0 or deleted_pending > 0:
            lines.append("\n<u>Удаленные пользователи с оплатами:</u>")
            if deleted_confirmed > 0:
                lines.append(
                    f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{deleted_confirmed}</b>"
                )
            if deleted_pending > 0:
                lines.append(
                    f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{deleted_pending}</b>"
                )

        if active_paid > 0 or deleted_paid > 0:
            lines.append("\n<u>Суммы платежей:</u>")
            if active_paid > 0:
                lines.append(f"💰 Активные: <b>{active_paid:,}</b> руб.")
            if deleted_paid > 0:
                lines.append(f"💰 Удаленные: <b>{deleted_paid:,}</b> руб.")

    total_with_payment = (
        total_active_confirmed
        + total_active_pending
        + total_active_declined
        + total_active_unpaid
        + total_deleted_confirmed
        + total_deleted_pending
    )

    if total_with_payment > 0:
        lines.append("\n<b>Всего по статусам:</b>")
        lines.append("<u>Активные пользователи:</u>")
        lines.append(
            f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{total_active_confirmed}</b>"
        )
        lines.append(
            f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{total_active_pending}</b>"
        )
        lines.append(
            f"⚪️ {PAYMENT_STATUS_MAP[None]}: <b>{total_active_declined + total_active_unpaid}</b>"
        )

        if total_deleted_confirmed > 0 or total_deleted_pending > 0:
            lines.append("\n<u>Удаленные пользователи с оплатами:</u>")
            if total_deleted_confirmed > 0:
                lines.append(
                    f"✅ {PAYMENT_STATUS_MAP['confirmed']}: <b>{total_deleted_confirmed}</b>"
                )
            if total_deleted_pending > 0:
                lines.append(
                    f"⏳ {PAYMENT_STATUS_MAP['pending']}: <b>{total_deleted_pending}</b>"
                )

        total_paid = total_paid_active + total_paid_deleted
        if total_paid > 0:
            deleted_percentage = (
                f" ({total_paid_deleted / total_paid:.1%} от удаленных)"
                if total_paid > 0
                else ""
            )
            lines.append(
                f"\n<b>💵 Итого собрано: {total_paid:,} руб.</b>{deleted_percentage}"
            )

    return "\n".join(lines)


def _build_city_palette(df):
    _known_colors = {
        "Москва": "#FF6666",
        "Пермь": "#5599FF",
        "Санкт-Петербург": "#66CC66",
        "Белград": "#FF00FF",
    }
    _extra_colors = ["#FFB347", "#77DD77", "#AEC6CF", "#FDFD96"]
    _extra_idx = 0
    city_palette = {}
    for c in df["Город"].unique():
        if c in _known_colors:
            city_palette[c] = _known_colors[c]
        else:
            city_palette[c] = _extra_colors[_extra_idx % len(_extra_colors)]
            _extra_idx += 1
    return city_palette


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------


def _render_year_bar_chart(df):
    city_palette = _build_city_palette(df)
    sns.set_style("whitegrid")
    plt.figure(figsize=(15, 8), dpi=100)
    ax = sns.barplot(
        data=df,
        x="Год выпуска",
        y="Количество",
        hue="Город",
        palette=city_palette,
        errorbar=None,
    )
    for container in ax.containers:
        ax.bar_label(container, fontsize=9, fontweight="bold", padding=3)  # type: ignore[arg-type]
    plt.title(
        "Количество регистраций по годам выпуска и городам\n(только активные)",
        fontsize=18,
        pad=20,
    )
    plt.xlabel("Год выпуска", fontsize=14, labelpad=10)
    plt.ylabel("Количество человек", fontsize=14, labelpad=10)
    plt.xticks(rotation=45)
    plt.legend(title="Город", fontsize=12, title_fontsize=14)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf


# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------


@commands_menu.add_command(
    "stats", "Статистика регистраций", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("stats"), AdminFilter())
async def show_stats(message: Message, app: App):
    """Показать статистику регистраций"""

    from src.app import PAYMENT_STATUS_MAP

    stats_text = "<b>📊 Статистика регистраций</b> (включая удаленных)\n\n"

    all_events, event_name_map, enabled_event_ids, free_event_ids = (
        await _fetch_event_metadata(app)
    )

    # City stats
    active_city_stats, deleted_city_stats = await _fetch_city_counts(app)
    city_stats_combined = _combine_count_stats(active_city_stats, deleted_city_stats)
    city_stats_combined = {
        k: v for k, v in city_stats_combined.items() if k in enabled_event_ids
    }
    city_section, total_active, total_deleted = _format_city_section(
        city_stats_combined, event_name_map
    )
    stats_text += city_section

    # Guest count
    guest_cursor = app.collection.aggregate(
        [
            {
                "$group": {
                    "_id": None,
                    "total_guests": {"$sum": {"$ifNull": ["$guest_count", 0]}},
                }
            }
        ]
    )
    guest_agg_result = await guest_cursor.to_list(length=None)
    total_guests = guest_agg_result[0]["total_guests"] if guest_agg_result else 0
    if total_guests > 0:
        stats_text += f"\n👥 Гостей: <b>{total_guests}</b>\n"
        stats_text += f"🎯 Всего участников (рег. + гости): <b>{total_active + total_guests}</b>\n"
    stats_text += "\n"

    # Grad type stats
    active_grad_stats, deleted_grad_stats = await _fetch_grad_type_stats(app)
    grad_stats_combined = _combine_grad_type_stats(active_grad_stats, deleted_grad_stats)
    stats_text += _format_grad_type_section(grad_stats_combined)
    stats_text += "\n\n"

    # Payment stats (with amounts for median calculation)
    active_payment_stats, deleted_payment_stats = await _fetch_payment_stats(
        app, free_event_ids, include_amounts=True
    )
    payment_stats_combined = _combine_stat_pairs(active_payment_stats, deleted_payment_stats)
    payment_stats_combined = {
        k: v for k, v in payment_stats_combined.items() if k in enabled_event_ids
    }
    stats_text += _format_full_payment_section(
        payment_stats_combined, event_name_map, PAYMENT_STATUS_MAP
    )

    await send_safe(message.chat.id, stats_text)


@commands_menu.add_command(
    "simple_stats", "Краткая статистика регистраций", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("simple_stats"), AdminFilter())
async def show_simple_stats(message: Message, app: App):
    """Показать краткую статистику регистраций"""
    from src.app import PAYMENT_STATUS_MAP

    stats_text = "<b>📊 Краткая статистика регистраций</b> (включая удаленных)\n\n"

    all_events, event_name_map, enabled_event_ids, free_event_ids = (
        await _fetch_event_metadata(app)
    )

    # City stats
    active_city_stats, deleted_city_stats = await _fetch_city_counts(app)
    city_stats_combined = _combine_count_stats(active_city_stats, deleted_city_stats)
    city_stats_combined = {
        k: v for k, v in city_stats_combined.items() if k in enabled_event_ids
    }
    city_section, _, _ = _format_city_section(city_stats_combined, event_name_map)
    stats_text += city_section
    stats_text += "\n\n"

    # Grad type stats
    active_grad_stats, deleted_grad_stats = await _fetch_grad_type_stats(app)
    grad_stats_combined = _combine_grad_type_stats(active_grad_stats, deleted_grad_stats)
    stats_text += _format_grad_type_section(grad_stats_combined)
    stats_text += "\n\n"

    # Payment stats (status + totals only, no median amounts)
    active_payment_stats, deleted_payment_stats = await _fetch_payment_stats(
        app, free_event_ids, include_amounts=False
    )
    payment_stats_combined = _combine_stat_pairs(active_payment_stats, deleted_payment_stats)
    payment_stats_combined = {
        k: v for k, v in payment_stats_combined.items() if k in enabled_event_ids
    }
    stats_text += _format_simple_payment_section(
        payment_stats_combined, event_name_map, PAYMENT_STATUS_MAP
    )

    await send_safe(message.chat.id, stats_text)


@commands_menu.add_command(
    "year_stats",
    "Статистика регистраций по годам выпуска",
    visibility=Visibility.ADMIN_ONLY,
)
@router.message(Command("year_stats"), AdminFilter())
async def show_year_stats(message: Message, app: App):
    """Show registration statistics by graduation year with matplotlib diagrams"""

    status_msg = await send_safe(
        message.chat.id, "⏳ Генерация статистики по годам выпуска..."
    )

    cursor = app.collection.find(
        {"graduation_year": {"$exists": True, "$ne": 0}}
    )
    active_registrations = await cursor.to_list(length=None)

    deleted_cursor = app.deleted_users.find(
        {"graduation_year": {"$exists": True, "$ne": 0}}
    )
    deleted_registrations = await deleted_cursor.to_list(length=None)

    for reg in active_registrations:
        reg["is_deleted"] = False
    for reg in deleted_registrations:
        reg["is_deleted"] = True

    all_registrations = active_registrations + deleted_registrations

    if not active_registrations:
        await status_msg.edit_text(
            "❌ Нет данных о регистрациях с указанным годом выпуска."
        )
        return

    all_events = await app.get_all_events()
    event_name_map = {str(e["_id"]): e.get("city", str(e["_id"])) for e in all_events}

    cities = sorted(
        {
            e.get("city", str(e["_id"]))
            for e in all_events
            if e.get("enabled") and e.get("status") == "upcoming"
        }
    )

    city_year_counts, city_year_counts_deleted, all_years = _collect_year_counts(
        all_registrations, cities, event_name_map
    )

    periods, period_labels = _build_periods(all_years)
    period_counts, period_counts_deleted = _aggregate_period_counts(
        cities, city_year_counts, city_year_counts_deleted, periods
    )

    stats_text = _format_year_stats_text(
        cities, period_labels, period_counts, period_counts_deleted
    )

    df = _build_year_df(cities, sorted(all_years), city_year_counts)
    buf_all_cities = _render_year_bar_chart(df)

    await status_msg.delete()
    await send_safe(message.chat.id, stats_text, parse_mode="HTML")
    await message.answer_photo(
        BufferedInputFile(
            buf_all_cities.getvalue(), filename="registration_stats_by_city.png"
        ),
        caption="📊 Регистрации по годам выпуска и городам",
    )


def _collect_year_counts(all_registrations, cities, event_name_map):
    city_year_counts = {city: defaultdict(int) for city in cities}
    city_year_counts_deleted = {city: defaultdict(int) for city in cities}
    all_years = set()

    for reg in all_registrations:
        city = event_name_map.get(reg.get("event_id"), reg.get("target_city"))
        year = reg.get("graduation_year")
        is_deleted = reg.get("is_deleted", False)

        if not year or year == 0 or city not in cities:
            continue

        if is_deleted:
            city_year_counts_deleted[city][year] += 1
        else:
            city_year_counts[city][year] += 1
        all_years.add(year)

    return city_year_counts, city_year_counts_deleted, all_years


def _build_periods(all_years):
    min_year = min(all_years)
    max_year = max(all_years)
    period_start = min_year - (min_year % 5)
    periods = []
    period_labels = []
    current = period_start
    while current <= max_year:
        period_end = current + 4
        periods.append((current, period_end))
        period_labels.append(f"{current}-{period_end}")
        current += 5
    return periods, period_labels


def _aggregate_period_counts(cities, city_year_counts, city_year_counts_deleted, periods):
    period_counts = {city: [0] * len(periods) for city in cities}
    period_counts_deleted = {city: [0] * len(periods) for city in cities}

    for city in cities:
        for year, count in city_year_counts[city].items():
            for i, (start, end) in enumerate(periods):
                if start <= year <= end:
                    period_counts[city][i] += count
                    break
        for year, count in city_year_counts_deleted[city].items():
            for i, (start, end) in enumerate(periods):
                if start <= year <= end:
                    period_counts_deleted[city][i] += count
                    break

    return period_counts, period_counts_deleted


def _format_year_stats_text(cities, period_labels, period_counts, period_counts_deleted):
    lines = [
        "<b>📊 Статистика регистраций по годам выпуска</b> (текст включает удаленных)\n",
        "<b>🎓 По периодам (все города):</b>",
    ]
    for i, period in enumerate(period_labels):
        period_total_active = sum(period_counts[city][i] for city in cities)
        period_total_deleted = sum(period_counts_deleted[city][i] for city in cities)
        period_total = period_total_active + period_total_deleted
        deleted_note = (
            f" (из них {period_total_deleted} удал.)"
            if period_total_deleted > 0
            else ""
        )
        lines.append(f"• {period}: <b>{period_total}</b> человек{deleted_note}")

    for city in cities:
        lines.append(f"\n<b>🏙️ {city}:</b>")
        for i, period in enumerate(period_labels):
            active_count = period_counts[city][i]
            deleted_count = period_counts_deleted[city][i]
            total_count = active_count + deleted_count
            deleted_note = (
                f" (из них {deleted_count} удал.)" if deleted_count > 0 else ""
            )
            lines.append(f"• {period}: <b>{total_count}</b> человек{deleted_note}")

    return "\n".join(lines)


def _build_year_df(cities, sorted_years, city_year_counts):
    data = []
    for city in cities:
        for year in sorted_years:
            active_count = city_year_counts[city].get(year, 0)
            if active_count > 0:
                data.append(
                    {"Город": city, "Год выпуска": year, "Количество": active_count}
                )
    return pd.DataFrame(data)


@commands_menu.add_command(
    "five_year_stats", "График по пятилеткам выпуска", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("five_year_stats"), AdminFilter())
async def show_five_year_stats(message: Message, app: App):
    """Показать график регистраций по пятилеткам выпуска и городам"""

    status_msg = await send_safe(
        message.chat.id, "⏳ Генерация графика по пятилеткам выпуска..."
    )

    cursor = app.collection.find(
        {"graduation_year": {"$exists": True, "$ne": 0}}
    )
    active_registrations = await cursor.to_list(length=None)

    deleted_cursor = app.deleted_users.find(
        {"graduation_year": {"$exists": True, "$ne": 0}}
    )
    deleted_registrations = await deleted_cursor.to_list(length=None)

    if not active_registrations:
        await status_msg.edit_text(
            "❌ Нет данных о регистрациях с указанным годом выпуска."
        )
        return

    all_events = await app.get_all_events()
    event_name_map = {str(e["_id"]): e.get("city", str(e["_id"])) for e in all_events}
    enabled_event_ids = {
        str(e["_id"])
        for e in all_events
        if e.get("enabled") and e.get("status") == "upcoming"
    }

    df = pd.DataFrame(active_registrations)
    df = df[df["event_id"].isin(enabled_event_ids)]

    df["graduation_year"] = pd.to_numeric(df["graduation_year"], errors="coerce")
    df = df.dropna(subset=["graduation_year"])
    df["Пятилетка"] = df["graduation_year"].apply(
        lambda y: f"{int(y) // 5 * 5}–{int(y) // 5 * 5 + 4}"
    )

    df["Город"] = df["event_id"].map(event_name_map).fillna("Другие")  # type: ignore[arg-type]

    pivot = (
        df.groupby(["Пятилетка", "Город"])["full_name"]
        .count()
        .unstack()
        .fillna(0)
        .sort_index()
    )

    known_cities = sorted(pivot.columns.difference(["Другие"]))
    available_cities = list(known_cities)
    if "Другие" in pivot.columns:
        available_cities.append("Другие")
    if available_cities:
        pivot = pivot[available_cities]

    plt.figure(figsize=(12, 7), dpi=100)
    pivot_df: pd.DataFrame = pivot  # type: ignore[assignment]
    ax = pivot_df.plot(kind="bar", stacked=True, figsize=(12, 7), colormap="Set2")

    plt.title("Зарегистрировавшиеся по пятилеткам выпуска (только активные)")
    plt.xlabel("Пятилетка")
    plt.ylabel("Количество участников")
    plt.xticks(rotation=45)
    plt.legend(title="Город", bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.tight_layout()
    plt.grid(axis="y")

    for bar_idx, (idx, row) in enumerate(pivot_df.iterrows()):
        cumulative = 0
        for city in pivot_df.columns:
            value = row[city]
            if value > 0:
                ax.text(
                    x=bar_idx,
                    y=cumulative + value / 2,
                    s=str(int(value)),  # type: ignore[arg-type]
                    ha="center",
                    va="center",
                    fontsize=9,
                )
                cumulative += value

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    active_count = len(active_registrations)
    deleted_count = len(deleted_registrations)

    await status_msg.delete()

    caption = "📊 Зарегистрировавшиеся по пятилеткам выпуска и городам\n"
    caption += f"График показывает только {active_count} активных участников\n"
    if deleted_count > 0:
        caption += f"(в статистике также есть {deleted_count} удаленных участников, не показанных на графике)"

    await message.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="five_year_stats.png"),
        caption=caption,
    )


@commands_menu.add_command(
    "payment_stats", "Круговая диаграмма оплат", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("payment_stats"), AdminFilter())
async def show_payment_stats(message: Message, app: App):
    """Показать круговую диаграмму оплат по пятилеткам выпуска"""

    status_msg = await send_safe(
        message.chat.id, "⏳ Генерация круговой диаграммы оплат..."
    )

    cursor = app.collection.find(
        {
            "graduation_year": {"$exists": True, "$ne": 0},
            "payment_status": "confirmed",
            "payment_amount": {"$gt": 0},
        }
    )
    active_registrations = await cursor.to_list(length=None)

    deleted_cursor = app.deleted_users.find(
        {
            "graduation_year": {"$exists": True, "$ne": 0},
            "payment_status": "confirmed",
            "payment_amount": {"$gt": 0},
        }
    )
    deleted_registrations = await deleted_cursor.to_list(length=None)

    if not active_registrations:
        await status_msg.edit_text(
            "❌ Нет данных об оплатах с указанным годом выпуска."
        )
        return

    df = pd.DataFrame(active_registrations)

    df["graduation_year"] = pd.to_numeric(df["graduation_year"], errors="coerce")
    df = df.dropna(subset=["graduation_year"])
    df["Пятилетка"] = df["graduation_year"].apply(
        lambda y: f"{int(y) // 5 * 5}–{int(y) // 5 * 5 + 4}"
    )

    donation_by_period = df.groupby("Пятилетка")["payment_amount"].sum()
    donation_by_period = donation_by_period[donation_by_period > 0].sort_index()  # type: ignore[assignment]

    status_stats = {
        "active": len(active_registrations),
        "deleted": len(deleted_registrations),
        "active_sum": sum(reg.get("payment_amount", 0) for reg in active_registrations),
        "deleted_sum": sum(
            reg.get("payment_amount", 0) for reg in deleted_registrations
        ),
    }

    plt.figure(figsize=(10, 10), dpi=100)

    colors = plt.colormaps["Set3"].colors[: len(donation_by_period)]  # type: ignore[attr-defined]

    total = donation_by_period.sum()
    labels = [
        f"{period}: {amount:,.0f} ₽ ({amount / total:.1%})"
        for period, amount in zip(donation_by_period.index, donation_by_period.values)
    ]

    plt.pie(
        donation_by_period.values,  # type: ignore[arg-type]
        labels=labels,
        autopct="",
        startangle=90,
        colors=colors,
        shadow=False,
        wedgeprops={"linewidth": 1, "edgecolor": "white"},
    )

    plt.title(
        "Суммарные оплаты по пятилеткам выпуска\n(только активные участники)",
        fontsize=16,
        pad=20,
    )
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    await status_msg.delete()

    caption = (
        "💰 Суммарные оплаты по пятилеткам выпуска (график: только активные участники)"
    )

    if status_stats["deleted"] > 0:
        caption += f"\n\nВсего: {status_stats['active_sum'] + status_stats['deleted_sum']:,.0f} ₽"
        caption += f"\n• Активные ({status_stats['active']} чел.): {status_stats['active_sum']:,.0f} ₽"
        caption += f"\n• Удаленные ({status_stats['deleted']} чел.): {status_stats['deleted_sum']:,.0f} ₽"

    await message.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="payment_stats.png"),
        caption=caption,
    )
