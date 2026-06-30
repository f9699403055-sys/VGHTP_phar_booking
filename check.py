from __future__ import annotations

import argparse
import os
import smtplib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Iterable

import requests

DEFAULT_WORKSCHEDULES_URL = (
    "https://booking.appointy.com/api/v1/workschedules"
    "?startDate=2026-08-10T00:00:00&endDate=2026-09-22T00:00:00"
    "&business=jr7Tk6bL94BbuwxfbsyWUg%253D%253D"
)
DEFAULT_BOOKEDSLOTS_URL = (
    "https://booking.appointy.com/api/v1/bookedslots"
    "?startDate=2026-06-29T00:00:00&endDate=2026-08-11T00:00:00"
    "&staffId=0&business=jr7Tk6bL94BbuwxfbsyWUg%253D%253D"
)
DEFAULT_BLOCKTIMES_URL = ""
DEFAULT_EXCEPTIONS_URL = ""

WEEKDAY_MAP = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    label: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("interval end must not be earlier than start")


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_csv_ints(value: str | None) -> set[int] | None:
    if value is None:
        return None
    cleaned = [part.strip() for part in value.split(",") if part.strip()]
    if not cleaned:
        return None
    return {int(part) for part in cleaned}


def fetch_json(url: str) -> list[dict]:
    if not url:
        return []
    response = requests.get(
        url,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; VGHTP-phar-checker/1.0)",
            "Accept": "application/json, text/plain, */*",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"Unexpected JSON payload type for {url!r}: {type(payload)!r}")


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    if not ordered:
        return []

    merged: list[Interval] = [ordered[0]]
    for cur in ordered[1:]:
        prev = merged[-1]
        if cur.start <= prev.end:
            merged[-1] = Interval(prev.start, max(prev.end, cur.end), prev.label or cur.label)
        else:
            merged.append(cur)
    return merged


def subtract_many(base: list[Interval], blocks: list[Interval]) -> list[Interval]:
    if not base:
        return []
    if not blocks:
        return base

    blocks = merge_intervals(blocks)
    result: list[Interval] = []

    for slot in base:
        cursor = slot.start
        for block in blocks:
            if block.end <= cursor:
                continue
            if block.start >= slot.end:
                break
            if block.start > cursor:
                result.append(Interval(cursor, min(block.start, slot.end), slot.label))
            cursor = max(cursor, block.end)
            if cursor >= slot.end:
                break
        if cursor < slot.end:
            result.append(Interval(cursor, slot.end, slot.label))

    return result


def infer_service_filter(value: str | None) -> set[int] | None:
    parsed = parse_csv_ints(value)
    return parsed


def filter_records(records: list[dict], service_ids: set[int] | None) -> list[dict]:
    if not service_ids:
        return records
    filtered: list[dict] = []
    for record in records:
        sid = record.get("serviceId")
        if sid in service_ids:
            filtered.append(record)
    return filtered


def expand_workschedules(
    workschedules: list[dict],
    range_start: datetime,
    range_end: datetime,
    service_ids: set[int] | None,
) -> list[Interval]:
    results: list[Interval] = []

    for item in filter_records(workschedules, service_ids):
        rule = item.get("recurringRule") or {}
        if rule.get("freq") != "WEEKLY":
            continue

        byday = rule.get("byday") or []
        if not byday:
            continue

        start_template = parse_dt(item["startDateTime"])
        end_template = parse_dt(item["endDateTime"])
        start_time = start_template.time()
        end_time = end_template.time()
        weekdays = {WEEKDAY_MAP[day] for day in byday if day in WEEKDAY_MAP}
        if not weekdays:
            continue

        until_raw = rule.get("until")
        until_date = parse_dt(until_raw).date() if until_raw else range_end.date()
        scan_start = range_start.date()
        scan_end = min(range_end.date(), until_date)

        cur = scan_start
        while cur <= scan_end:
            if cur.weekday() in weekdays:
                start_dt = datetime.combine(cur, start_time)
                end_dt = datetime.combine(cur, end_time)
                if end_dt > range_start and start_dt < range_end:
                    results.append(
                        Interval(
                            start=max(start_dt, range_start),
                            end=min(end_dt, range_end),
                            label=f"service {item.get('serviceId')} staff {item.get('staffId')}",
                        )
                    )
            cur += timedelta(days=1)

    return merge_intervals(results)


def expand_blocklike(
    records: list[dict],
    range_start: datetime,
    range_end: datetime,
    service_ids: set[int] | None,
    start_key: str,
    end_key: str,
) -> list[Interval]:
    results: list[Interval] = []
    for item in filter_records(records, service_ids):
        start_raw = item.get(start_key)
        end_raw = item.get(end_key)
        if not start_raw or not end_raw:
            continue
        start_dt = parse_dt(start_raw)
        end_dt = parse_dt(end_raw)
        if end_dt <= range_start or start_dt >= range_end:
            continue
        results.append(
            Interval(
                start=max(start_dt, range_start),
                end=min(end_dt, range_end),
                label=f"service {item.get('serviceId')} staff {item.get('staffId')}",
            )
        )
    return merge_intervals(results)


def format_report(available: list[Interval]) -> str:
    if not available:
        return "目前沒有找到可預約時段。"

    grouped: dict[date, list[Interval]] = defaultdict(list)
    for slot in available:
        grouped[slot.start.date()].append(slot)

    lines: list[str] = []
    for day in sorted(grouped):
        lines.append(day.isoformat())
        for slot in grouped[day]:
            lines.append(f"  {slot.start:%H:%M} - {slot.end:%H:%M}")
        lines.append("")
    return "\n".join(lines).rstrip()


def send_email(subject: str, body: str) -> None:
    host = os.getenv("EMAIL_SMTP_HOST", "").strip()
    port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    username = os.getenv("EMAIL_SMTP_USERNAME", "").strip()
    password = os.getenv("EMAIL_SMTP_PASSWORD", "").strip()
    sender = os.getenv("EMAIL_FROM", username).strip()
    recipient = os.getenv("EMAIL_TO", "").strip()

    if not (host and username and password and sender and recipient):
        print("EMAIL config missing; skipping send. Set EMAIL_SMTP_HOST/USERNAME/PASSWORD/FROM/TO.")
        return

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Appointy availability for VGHTP MV booking.")
    parser.add_argument("--start", default=os.getenv("CHECK_START_DATE", ""), help="Start date in YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=int(os.getenv("CHECK_DAYS", "21")), help="How many days to inspect")
    parser.add_argument("--workschedules-url", default=os.getenv("APPOINTY_WORKSCHEDULES_URL", DEFAULT_WORKSCHEDULES_URL))
    parser.add_argument("--bookedslots-url", default=os.getenv("APPOINTY_BOOKEDSLOTS_URL", DEFAULT_BOOKEDSLOTS_URL))
    parser.add_argument("--blocktimes-url", default=os.getenv("APPOINTY_BLOCKTIMES_URL", DEFAULT_BLOCKTIMES_URL))
    parser.add_argument("--exceptions-url", default=os.getenv("APPOINTY_EXCEPTIONS_URL", DEFAULT_EXCEPTIONS_URL))
    parser.add_argument("--service-ids", default=os.getenv("APPOINTY_SERVICE_IDS", "1247122"))
    parser.add_argument("--send-email", action="store_true", default=os.getenv("SEND_EMAIL", "0") == "1")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    service_ids = infer_service_filter(args.service_ids)

    start_date = parse_date(args.start) if args.start else date.today()
    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = range_start + timedelta(days=args.days)

    workschedules = fetch_json(args.workschedules_url)
    bookedslots = fetch_json(args.bookedslots_url)
    blocktimes = fetch_json(args.blocktimes_url)
    exceptions = fetch_json(args.exceptions_url)

    work_slots = expand_workschedules(workschedules, range_start, range_end, service_ids)
    booked = expand_blocklike(bookedslots, range_start, range_end, service_ids, "appointmentStartTime", "appointmentEndTime")
    booked = [Interval(slot.start, slot.end + timedelta(minutes=1), slot.label) for slot in booked]
    blocks = expand_blocklike(blocktimes, range_start, range_end, service_ids, "startDateTime", "endDateTime")
    ex = expand_blocklike(exceptions, range_start, range_end, service_ids, "startDateTime", "endDateTime")

    unavailable = merge_intervals(booked + blocks + ex)
    available = subtract_many(work_slots, unavailable)

    report = format_report(available)
    print(report)

    if args.send_email:
        subject = f"MV 訪視可預約時段摘要 ({start_date.isoformat()} 起)"
        send_email(subject, report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
