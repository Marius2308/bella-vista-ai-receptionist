from datetime import datetime

import pytest

import db
import tools

# "Now" in the tests: Friday 2 Oct 2026, 10:00 (restaurant time)
NOW = datetime(2026, 10, 2, 10, 0)
FRIDAY, SATURDAY, MONDAY = "2026-10-09", "2026-10-10", "2026-10-05"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(tools, "now_local", lambda: NOW)


def book(time, party=2, date=FRIDAY, name="Test Guest"):
    return tools.create_reservation(name=name, phone="+38344000000", date=date, time=time, party_size=party)


# ── table maths ───────────────────────────────────────────────────

@pytest.mark.parametrize("party,tables", [(1, 1), (4, 1), (5, 2), (8, 2), (9, 3), (20, 5)])
def test_tables_needed(party, tables):
    assert tools.calculate_tables_needed(party) == tables


# ── validation ────────────────────────────────────────────────────

def test_valid_slot_passes():
    assert tools.validate_slot(FRIDAY, "19:00", 4) is None


def test_closed_on_monday():
    assert "closed" in tools.validate_slot(MONDAY, "19:00", 2).lower()


def test_before_opening_is_rejected():
    assert tools.validate_slot(FRIDAY, "09:00", 2) is not None   # Friday opens 12:00


def test_last_slot_must_finish_before_closing():
    assert tools.validate_slot(FRIDAY, "22:00", 2) is None       # 22:00-23:00, closes 23:00
    assert tools.validate_slot(FRIDAY, "23:00", 2) is not None


def test_past_time_is_rejected():
    assert "past" in tools.validate_slot("2026-10-01", "19:00", 2).lower()
    assert "past" in tools.validate_slot("2026-10-02", "09:00", 2).lower()


@pytest.mark.parametrize("party", [0, -1, 21])
def test_bad_party_size_rejected(party):
    assert tools.validate_slot(FRIDAY, "19:00", party) is not None


@pytest.mark.parametrize("date,time", [("not-a-date", "19:00"), (FRIDAY, "7pm"), ("", "")])
def test_bad_format_rejected(date, time):
    assert "format" in tools.validate_slot(date, time, 2).lower()


# ── availability ──────────────────────────────────────────────────

def test_empty_restaurant_is_available():
    result = tools.check_availability(FRIDAY, "19:00", 4)
    assert result["available"] is True
    assert result["tables_free"] == 5


def test_availability_rejects_closed_day():
    result = tools.check_availability(MONDAY, "19:00", 2)
    assert result["available"] is False


def test_full_slot_suggests_alternatives():
    for _ in range(5):
        assert book("19:00", party=4)["success"]
    result = tools.check_availability(FRIDAY, "19:00", 2)
    assert result["available"] is False
    assert result["alternative_times"]
    assert "19:00" not in result["alternative_times"]


def test_overlapping_reservation_blocks_tables():
    assert book("19:00", party=8)["success"]                    # 2 tables, 19:00-20:00
    # 3 tables left: a party needing 3 fits, a party needing 4 does not
    assert tools.check_availability(FRIDAY, "19:00", 12)["available"] is True
    assert tools.check_availability(FRIDAY, "19:00", 16)["available"] is False
    # 19:30 overlaps the 19:00 booking, so it only has 3 tables free too
    assert tools.check_availability(FRIDAY, "19:30", 12)["tables_free"] == 3
    # 20:00 starts exactly when the first booking ends: no overlap
    assert tools.check_availability(FRIDAY, "20:00", 12)["tables_free"] == 5


# ── creating reservations ─────────────────────────────────────────

def test_successful_booking_is_saved():
    result = book("19:00", party=5)
    assert result["success"] is True
    assert result["tables_reserved"] == 2
    saved = db.get_all_reservations()
    assert len(saved) == 1 and saved[0]["name"] == "Test Guest"


def test_booking_on_closed_day_is_refused_and_not_saved():
    assert book("19:00", date=MONDAY)["success"] is False
    assert db.get_all_reservations() == []


def test_booking_in_the_past_is_refused():
    assert book("19:00", date="2026-09-01")["success"] is False


def test_cannot_book_when_full():
    for _ in range(5):
        assert book("19:00", party=4)["success"]
    assert book("19:00", party=1)["success"] is False
    assert len(db.get_all_reservations()) == 5


def test_name_and_phone_required():
    assert tools.create_reservation("", "123", FRIDAY, "19:00", 2)["success"] is False
    assert tools.create_reservation("Ann", "  ", FRIDAY, "19:00", 2)["success"] is False


def test_long_names_are_capped():
    book("19:00", name="x" * 500)
    assert len(db.get_all_reservations()[0]["name"]) == 100


def test_callback_request_is_saved():
    assert tools.create_callback_request("Ann", "123")["success"] is True
    assert db.get_all_callbacks()[0]["status"] == "pending"


def test_run_tool_handles_bad_input_without_crashing():
    out = tools.run_tool("check_availability", {"date": FRIDAY, "time": "19:00", "party_size": "abc"})
    assert "error" in out.lower()
    assert "Unknown tool" in tools.run_tool("nope", {})


# ── weekdays: the AI must not have to work these out ──────────────

def test_calendar_labels_are_correct_and_start_today():
    from timeutil import calendar_text
    text = calendar_text(datetime(2026, 10, 10, 9, 0))
    assert text.startswith("Saturday 2026-10-10 (today), Sunday 2026-10-11 (tomorrow), Monday 2026-10-12,")
    assert "Saturday 2026-10-17" in text and "Thursday 2026-10-15" in text
    assert "Saturday 2026-10-15" not in text           # the mistake the model made


def test_system_prompt_and_reminder_contain_the_calendar(monkeypatch):
    import prompts, assistant
    monkeypatch.setattr(prompts, "now_local", lambda: datetime(2026, 10, 10, 9, 0))
    monkeypatch.setattr(assistant, "now_local", lambda: datetime(2026, 10, 10, 9, 0))
    assert "Saturday 2026-10-17" in prompts.get_system_prompt()
    assert "Saturday 2026-10-17" in assistant.inject_date_reminder("book saturday")


def test_tool_messages_include_the_weekday():
    assert "Friday 2026-10-09" in tools.check_availability(FRIDAY, "19:00", 2)["message"]
    assert "Friday 2026-10-09" in book("19:00")["message"]
