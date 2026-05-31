import os
import uuid
import pandas as pd
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters, ContextTypes


# ---------- CONFIG ----------
load_dotenv("botX.env")
TOKEN     = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

# ---------- SUPABASE ----------
import json as _json
import urllib.request as _req

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def _sb_get():
    url = f"{SUPABASE_URL}/rest/v1/botdata?id=eq.1&select=value"
    r = _req.urlopen(_req.Request(url, headers=_HEADERS))
    rows = _json.loads(r.read())
    return rows

def _sb_upsert(value_str):
    payload = _json.dumps({"ID": 1, "value": value_str}).encode()
    r = _req.Request(
        f"{SUPABASE_URL}/rest/v1/botdata",
        data=payload,
        headers={**_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        method="POST"
    )
    _req.urlopen(r)

def _load_data():
    try:
        rows = _sb_get()
        if rows:
            return _json.loads(rows[0]["value"])
    except:
        pass
    return {"received": [], "given": [], "custom_events": [], "custom_relations": []}

def save():
    _sb_upsert(_json.dumps(data, ensure_ascii=False))

data = _load_data()

# ---------- STATE ----------
states = {}  # user_id -> {"mode": ..., "ctx": ...}

def get_state(user_id):
    if user_id not in states:
        states[user_id] = {"mode": "idle", "ctx": {}}
    return states[user_id]

def reset(user_id):
    states[user_id] = {"mode": "idle", "ctx": {}}

def today():
    return datetime.now().strftime("%Y-%m-%d")

def parse_date(text):
    """Try multiple date formats and return YYYY-MM-DD"""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text  # return as-is if nothing matched

# ---------- DYNAMIC LISTS ----------
BASE_EVENTS    = ["חתונה", "ברית", "יום הולדת"]
BASE_AMOUNTS   = [450, 500, 600, 700, 800, 1000]
BASE_RELATIONS = ["משפחה", "חברים מהשכונה", "מהצבא", "מהצד השני"]

def get_events():
    return list(dict.fromkeys(BASE_EVENTS + data.get("custom_events", [])))

def get_relations():
    return list(dict.fromkeys(BASE_RELATIONS + data.get("custom_relations", [])))

def add_custom_event(ev):
    if ev not in BASE_EVENTS and ev not in data.get("custom_events", []):
        data.setdefault("custom_events", []).append(ev)
        save()

def add_custom_relation(rel):
    if rel not in BASE_RELATIONS and rel not in data.get("custom_relations", []):
        data.setdefault("custom_relations", []).append(rel)
        save()

# ---------- RECORD HELPERS ----------
def new_record(name, amount, event, relation, date):
    return {"id": str(uuid.uuid4()), "name": name, "amount": amount,
            "event": event, "relation": relation, "date": date}

def get_record_by_id(record_id, table):
    for r in data[table]:
        if r["id"] == record_id or r["id"].startswith(record_id):
            return r
    return None

def format_record(r):
    return f"{r['name']} - {r['amount']}₪ ({r['event']}, {r['relation']}, {r['date']})"

def _apply_edit(rec_id, table, field, value):
    for r in data[table]:
        if r["id"] == rec_id:
            r[field] = value
            save()
            return

# ---------- KEYBOARDS ----------
def menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 חיפוש",               callback_data="search")],
        [InlineKeyboardButton("💰 שמור כמה קיבלת",      callback_data="rec")],
        [InlineKeyboardButton("💸 שמור כמה הבאת",       callback_data="giv")],
        [InlineKeyboardButton("📊 טעינת אקסל",          callback_data="excel")],
        [InlineKeyboardButton("🔥 מחק נתונים",          callback_data="delete")],
    ])

def nav_kb(back_cb):
    return [InlineKeyboardButton("⬅️ חזרה", callback_data=back_cb),
            InlineKeyboardButton("🏠 תפריט", callback_data="main")]

def amounts_kb(back_cb):
    rows = []
    for i in range(0, len(BASE_AMOUNTS), 2):
        rows.append([InlineKeyboardButton(str(n), callback_data=f"amt_{n}")
                     for n in BASE_AMOUNTS[i:i+2]])
    rows.append([InlineKeyboardButton("אחר", callback_data="amt_other")])
    rows.append(nav_kb(back_cb))
    return InlineKeyboardMarkup(rows)

def events_kb(back_cb):
    rows = [[InlineKeyboardButton(e, callback_data=f"ev_{e}")] for e in get_events()]
    rows.append([InlineKeyboardButton("אחר", callback_data="ev_other")])
    rows.append(nav_kb(back_cb))
    return InlineKeyboardMarkup(rows)

def relations_single_kb(back_cb):
    """Single-select relations keyboard — for save flow"""
    rows = [[InlineKeyboardButton(r, callback_data=f"rel_{r}")] for r in get_relations()]
    rows.append([InlineKeyboardButton("אחר", callback_data="rel_other")])
    rows.append(nav_kb(back_cb))
    return InlineKeyboardMarkup(rows)

def relations_multi_kb(back_cb, selected):
    """Multi-select relations keyboard — for fallback search filter"""
    rows = []
    for r in get_relations():
        mark = "✔️ " if r in selected else "⬜ "
        rows.append([InlineKeyboardButton(mark + r, callback_data=f"frel_{r}")])
    rows.append([InlineKeyboardButton("✅ אישור", callback_data="frel_confirm")])
    rows.append(nav_kb(back_cb))
    return InlineKeyboardMarkup(rows)

def dates_multi_kb(back_cb, selected, filtered_dates=None):
    """Multi-select dates keyboard — filtered by prior relation/event selections"""
    if filtered_dates is None:
        filtered_dates = []
    rows = []
    for d in filtered_dates:
        mark = "✔️ " if d in selected else "⬜ "
        rows.append([InlineKeyboardButton(mark + d, callback_data=f"fdate_{d}")])
    rows.append([InlineKeyboardButton("✅ אישור", callback_data="fdate_confirm")])
    rows.append(nav_kb(back_cb))
    return InlineKeyboardMarkup(rows)

def date_input_kb(back_cb):
    """Keyboard for date input — type or press today"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 היום הנוכחי", callback_data="date_today")],
        nav_kb(back_cb),
    ])

# ---------- EXCEL ----------
async def handle_excel(update):
    file = await update.message.document.get_file()
    import os as _os
    if _os.path.exists("temp.xlsx"):
        _os.remove("temp.xlsx")
    await file.download_to_drive("temp.xlsx")
    df = pd.read_excel("temp.xlsx")
    count = 0
    for i, row in df.iterrows():
        try:
            name   = str(row.iloc[0]).strip()
            if not name or name.lower() == "nan":
                continue
            amount = int(float(str(row.iloc[1]).replace(",", "").strip()))
        except Exception as e:
            print(f"שורה {i}: שגיאה — {e}")
            continue
        event    = str(row.iloc[2]).strip() if len(row) > 2 and not pd.isna(row.iloc[2]) else "חתונה"
        relation = str(row.iloc[3]).strip() if len(row) > 3 and not pd.isna(row.iloc[3]) else "לא הוזן ערך"
        raw_date = row.iloc[4] if len(row) > 4 else None
        try:
            if raw_date is None or pd.isna(raw_date):
                date = today()
            elif hasattr(raw_date, "strftime"):
                date = raw_date.strftime("%Y-%m-%d")
            else:
                date = parse_date(str(raw_date).split(" ")[0])
        except:
            date = today()
        add_custom_event(event)
        add_custom_relation(relation)
        data["received"].append(new_record(name, amount, event, relation, date))
        count += 1
    save()
    await update.message.reply_text(f"✅ נטענו {count} רשומות בהצלחה", reply_markup=menu_kb())

def get_filtered_dates(table, relations_filter=None, event_filter=None):
    """Return sorted unique dates from records matching the given filters"""
    results = data[table]
    if relations_filter:
        results = [r for r in results if r["relation"] in relations_filter]
    if event_filter:
        results = [r for r in results if r["event"] == event_filter]
    return sorted(set(r["date"] for r in results), reverse=True)

# ---------- SHOW RECORD (for view/edit/delete) ----------
async def show_record_actions(send_fn, r):
    text = (f"📋 רשומה מלאה:\n"
            f"שם: {r['name']}\n"
            f"סכום: {r['amount']}₪\n"
            f"אירוע: {r['event']}\n"
            f"קרבה: {r['relation']}\n"
            f"תאריך: {r['date']}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ עדכון",  callback_data="edit_rec"),
         InlineKeyboardButton("🗑️ מחיקה", callback_data="del_single_confirm")],
        [InlineKeyboardButton("⬅️ חזרה לרשימה", callback_data="back_to_results"),
         InlineKeyboardButton("🏠 תפריט",        callback_data="main")],
    ])
    await send_fn(text, reply_markup=kb)

# ---------- CALLBACK ----------
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    user_id = update.effective_user.id
    state = get_state(user_id)

    # ── MAIN ──
    if d == "main":
        reset(user_id)
        await q.message.reply_text("בחר פעולה:", reply_markup=menu_kb())
        return

    # ════════════════════════════════════════
    # SEARCH
    # ════════════════════════════════════════
    if d == "search":
        state["mode"] = "search_type"
        await q.message.reply_text("בחר סוג חיפוש:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("כמה קיבלתי", callback_data="s_rec")],
            [InlineKeyboardButton("כמה הבאתי",  callback_data="s_giv")],
            [InlineKeyboardButton("🏠 תפריט",   callback_data="main")],
        ]))
        return

    if d in ["s_rec", "s_giv"]:
        state["ctx"]["table"] = "received" if d == "s_rec" else "given"
        state["mode"] = "search_name"
        await q.message.reply_text("הזן שם לחיפוש:",
            reply_markup=InlineKeyboardMarkup([nav_kb("search")]))
        return

    # view a record from search results
    if d.startswith("view_rec_"):
        short_id = d[len("view_rec_"):]
        table = state["ctx"].get("table", "received")
        r = get_record_by_id(short_id, table)
        if r:
            state["ctx"]["viewing_id"] = r["id"]
            await show_record_actions(q.message.reply_text, r)
        return

    if d == "back_to_results":
        results = state["ctx"].get("last_results", [])
        if not results:
            await q.message.reply_text("בחר פעולה:", reply_markup=menu_kb())
            return
        out  = "\n\n".join([format_record(r) for r in results])
        rows = [[InlineKeyboardButton(r["name"], callback_data=f"view_rec_{r['id'][:8]}")] for r in results]
        rows.append([InlineKeyboardButton("🏠 תפריט", callback_data="main")])
        await q.message.reply_text(f"תוצאות:\n\n{out}", reply_markup=InlineKeyboardMarkup(rows))
        return

    # single-record delete
    if d == "del_single_confirm":
        await q.message.reply_text("⚠️ האם אתה בטוח שברצונך למחוק את הרשומה?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ כן, מחק", callback_data="del_single_yes"),
                 InlineKeyboardButton("❌ ביטול",   callback_data="back_to_results")],
            ]))
        return

    if d == "del_single_yes":
        rec_id = state["ctx"].get("viewing_id")
        table  = state["ctx"].get("table", "received")
        before = len(data[table])
        data[table] = [r for r in data[table] if r["id"] != rec_id]
        save()
        deleted = before - len(data[table])
        await q.message.reply_text(f"✅ נמחקה {deleted} רשומה", reply_markup=menu_kb())
        reset(user_id)
        return

    # edit field chooser
    if d == "edit_rec":
        state["mode"] = "edit_field"
        rec_id = state["ctx"].get("viewing_id", "")
        await q.message.reply_text("מה ברצונך לעדכן?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("שם",    callback_data="edit_f_name")],
            [InlineKeyboardButton("סכום",  callback_data="edit_f_amount")],
            [InlineKeyboardButton("אירוע", callback_data="edit_f_event")],
            [InlineKeyboardButton("קרבה",  callback_data="edit_f_relation")],
            [InlineKeyboardButton("תאריך", callback_data="edit_f_date")],
            [InlineKeyboardButton("⬅️ חזרה", callback_data=f"view_rec_{rec_id[:8]}"),
             InlineKeyboardButton("🏠 תפריט", callback_data="main")],
        ]))
        return

    if d == "edit_f_name":
        state["mode"] = "edit_name_input"
        await q.message.reply_text("הזן שם חדש:",
            reply_markup=InlineKeyboardMarkup([nav_kb("edit_rec")]))
        return

    if d == "edit_f_amount":
        state["mode"] = "edit_amount_pick"
        await q.message.reply_text("בחר סכום חדש:", reply_markup=amounts_kb("edit_rec"))
        return

    if d == "edit_f_event":
        state["mode"] = "edit_event_pick"
        await q.message.reply_text("בחר אירוע חדש:", reply_markup=events_kb("edit_rec"))
        return

    if d == "edit_f_relation":
        state["mode"] = "edit_relation_pick"
        await q.message.reply_text("בחר קרבה חדשה:", reply_markup=relations_single_kb("edit_rec"))
        return

    if d == "edit_f_date":
        state["mode"] = "edit_date_input"
        await q.message.reply_text("הזן תאריך חדש בפורמט DD-MM-YYYY או לחץ על הכפתור:",
            reply_markup=date_input_kb("edit_rec"))
        return

    # edit — amount pick
    if d.startswith("amt_") and state["mode"] == "edit_amount_pick":
        if d == "amt_other":
            state["mode"] = "edit_amount_custom"
            await q.message.reply_text("הזן סכום:",
                reply_markup=InlineKeyboardMarkup([nav_kb("edit_f_amount")]))
            return
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "amount", int(d.split("_")[1]))
        await q.message.reply_text("✅ סכום עודכן", reply_markup=menu_kb())
        reset(user_id)
        return

    # edit — event pick
    if d.startswith("ev_") and state["mode"] == "edit_event_pick":
        if d == "ev_other":
            state["mode"] = "edit_event_custom"
            await q.message.reply_text("הזן סוג אירוע:",
                reply_markup=InlineKeyboardMarkup([nav_kb("edit_f_event")]))
            return
        ev = d.split("_", 1)[1]
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "event", ev)
        await q.message.reply_text("✅ אירוע עודכן", reply_markup=menu_kb())
        reset(user_id)
        return

    # edit — relation pick (single)
    if d.startswith("rel_") and state["mode"] == "edit_relation_pick":
        if d == "rel_other":
            state["mode"] = "edit_relation_custom"
            await q.message.reply_text("הזן סוג קרבה:",
                reply_markup=InlineKeyboardMarkup([nav_kb("edit_f_relation")]))
            return
        rel = d.split("_", 1)[1]
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "relation", rel)
        await q.message.reply_text("✅ קרבה עודכנה", reply_markup=menu_kb())
        reset(user_id)
        return

    # ════════════════════════════════════════
    # SAVE FLOW  (rec / giv)
    # ════════════════════════════════════════
    if d in ["rec", "giv"]:
        state["ctx"]["table"] = "received" if d == "rec" else "given"
        state["mode"] = "save_name"
        await q.message.reply_text("הזן שם מלא:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]))
        return

    if d == "save_back_name":
        state["mode"] = "save_name"
        await q.message.reply_text("הזן שם מלא:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]))
        return

    if d == "save_back_amount":
        state["mode"] = "save_name"
        await q.message.reply_text("הזן שם מלא:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]))
        return

    if d == "save_back_event":
        state["mode"] = "amount"
        await q.message.reply_text("בחר סכום:", reply_markup=amounts_kb("save_back_amount"))
        return

    if d == "save_back_relation":
        state["mode"] = "event"
        await q.message.reply_text("בחר אירוע:", reply_markup=events_kb("save_back_event"))
        return

    if d == "save_back_date":
        state["mode"] = "relation"
        await q.message.reply_text("בחר קרבה:", reply_markup=relations_single_kb("save_back_relation"))
        return

    # save — amount pick
    if d.startswith("amt_") and state["mode"] == "amount":
        if d == "amt_other":
            state["mode"] = "amount_custom"
            await q.message.reply_text("הזן סכום:",
                reply_markup=InlineKeyboardMarkup([nav_kb("save_back_amount")]))
            return
        state["ctx"]["amount"] = int(d.split("_")[1])
        state["mode"] = "event"
        await q.message.reply_text("בחר אירוע:", reply_markup=events_kb("save_back_event"))
        return

    # save — event pick
    if d.startswith("ev_") and state["mode"] == "event":
        if d == "ev_other":
            state["mode"] = "event_custom"
            await q.message.reply_text("הזן סוג אירוע:",
                reply_markup=InlineKeyboardMarkup([nav_kb("save_back_event")]))
            return
        state["ctx"]["event"] = d.split("_", 1)[1]
        state["mode"] = "relation"
        await q.message.reply_text("בחר קרבה:", reply_markup=relations_single_kb("save_back_relation"))
        return

    # save — relation pick (single)
    if d.startswith("rel_") and state["mode"] == "relation":
        if d == "rel_other":
            state["mode"] = "relation_custom"
            await q.message.reply_text("הזן סוג קרבה:",
                reply_markup=InlineKeyboardMarkup([nav_kb("save_back_relation")]))
            return
        state["ctx"]["relation"] = d.split("_", 1)[1]
        state["mode"] = "date"
        await q.message.reply_text("הזן תאריך בפורמט DD-MM-YYYY או לחץ על הכפתור:",
            reply_markup=date_input_kb("save_back_date"))
        return

    # ── DATE TODAY BUTTON ──
    if d == "date_today":
        date = today()
        if state["mode"] == "date":
            rec = new_record(state["ctx"]["name"], state["ctx"]["amount"],
                             state["ctx"]["event"], state["ctx"]["relation"], date)
            data[state["ctx"]["table"]].append(rec)
            save()
            await q.message.reply_text("✅ נשמר בהצלחה", reply_markup=menu_kb())
            reset(user_id)
        elif state["mode"] == "edit_date_input":
            _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "date", date)
            await q.message.reply_text("✅ תאריך עודכן", reply_markup=menu_kb())
            reset(user_id)
        return

    # ════════════════════════════════════════
    # EXCEL
    # ════════════════════════════════════════
    if d == "excel":
        await q.message.reply_text(
            "📊 שלח קובץ Excel עם העמודות הבאות:\n\n"
            "*A* — שם מלא (חובה)\n"
            "*B* — סכום בשח (חובה, מספר בלבד)\n"
            "*C* — סוג אירוע (לא חובה, ברירת מחדל: חתונה)\n"
            "*D* — סוג קרבה (לא חובה, ברירת מחדל: לא הוזן ערך)\n"
            "*E* — תאריך (לא חובה, פורמטים: DD/MM/YYYY או DD-MM-YYYY או YYYY-MM-DD, ריק = היום הנוכחי)\n\n"
            "⚠️ שורת הכותרות (שם, סכום וכו') תדולג אוטומטית\n"
            "⚠️ רשומה ללא שם או סכום תדולג",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]])
        )
        return

    # ════════════════════════════════════════
    # DELETE (bulk)
    # ════════════════════════════════════════
    if d == "delete":
        await q.message.reply_text("מה ברצונך למחוק?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ כמה קיבלת", callback_data="bulk_del_rec")],
            [InlineKeyboardButton("🗑️ כמה הבאת",  callback_data="bulk_del_giv")],
            [InlineKeyboardButton("🗑️ הכל",        callback_data="bulk_del_all")],
            [InlineKeyboardButton("🏠 תפריט",      callback_data="main")],
        ]))
        return

    if d in ["bulk_del_rec", "bulk_del_giv", "bulk_del_all"]:
        label = {"bulk_del_rec": "כמה קיבלת", "bulk_del_giv": "כמה הבאת", "bulk_del_all": "הכל"}[d]
        state["mode"] = d
        await q.message.reply_text(f"⚠️ האם אתה בטוח שברצונך למחוק '{label}'?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ כן, מחק", callback_data=f"bulk_confirm_{d}"),
                 InlineKeyboardButton("❌ ביטול",    callback_data="main")],
            ]))
        return

    if d.startswith("bulk_confirm_bulk_del_"):
        key = d.replace("bulk_confirm_bulk_del_", "")
        if key == "rec":
            count = len(data["received"]); data["received"] = []
        elif key == "giv":
            count = len(data["given"]); data["given"] = []
        else:
            count = len(data["received"]) + len(data["given"])
            data["received"] = []; data["given"] = []
        save()
        await q.message.reply_text(f"✅ נמחקו {count} רשומות", reply_markup=menu_kb())
        reset(user_id)
        return

    # ════════════════════════════════════════
    # FALLBACK FILTER (after name not found)
    # ════════════════════════════════════════
    if d == "fallback_search":
        state["ctx"]["frel_selected"]  = []
        state["ctx"]["fdate_selected"] = []
        state["mode"] = "fall_relation"
        await q.message.reply_text("בחר קרבה (ניתן לבחור כמה):",
            reply_markup=relations_multi_kb("search", []))
        return

    # fallback — relation multi-select  (prefix frel_ is unique)
    if d.startswith("frel_") and d != "frel_confirm":
        rel = d[len("frel_"):]
        sel = state["ctx"].setdefault("frel_selected", [])
        if rel in sel: sel.remove(rel)
        else:          sel.append(rel)
        await q.message.edit_reply_markup(
            reply_markup=relations_multi_kb("search", sel))
        return

    if d == "frel_confirm":
        state["ctx"]["fall_relations"] = state["ctx"].get("frel_selected", [])
        state["mode"] = "fall_event"
        await q.message.reply_text("בחר אירוע:", reply_markup=events_kb("fallback_search"))
        return

    # fallback — event pick (reuses ev_ prefix, guarded by mode)
    if d.startswith("ev_") and state["mode"] == "fall_event":
        if d == "ev_other":
            state["mode"] = "fall_event_custom"
            await q.message.reply_text("הזן סוג אירוע:",
                reply_markup=InlineKeyboardMarkup([nav_kb("fallback_search")]))
            return
        state["ctx"]["fall_event"] = d.split("_", 1)[1]
        state["mode"] = "fall_date"
        table = state["ctx"].get("table", "received")
        fdates = get_filtered_dates(table,
            relations_filter=state["ctx"].get("fall_relations") or None,
            event_filter=state["ctx"]["fall_event"])
        state["ctx"]["fall_available_dates"] = fdates
        if not fdates:
            await q.message.reply_text("❌ אין רשומות התואמות לסינון הנוכחי",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]))
            reset(user_id)
            return
        await q.message.reply_text("בחר תאריכים (ניתן לבחור כמה):",
            reply_markup=dates_multi_kb("fallback_search", [], fdates))
        return

    # fallback — date multi-select  (prefix fdate_ is unique)
    if d.startswith("fdate_") and d != "fdate_confirm":
        date_val = d[len("fdate_"):]
        sel = state["ctx"].setdefault("fdate_selected", [])
        if date_val in sel: sel.remove(date_val)
        else:               sel.append(date_val)
        fdates = state["ctx"].get("fall_available_dates", [])
        await q.message.edit_reply_markup(
            reply_markup=dates_multi_kb("fallback_search", sel, fdates))
        return

    if d == "fdate_confirm":
        table   = state["ctx"].get("table", "received")
        rel_f   = state["ctx"].get("fall_relations") or None
        ev_f    = [state["ctx"]["fall_event"]] if state["ctx"].get("fall_event") else None
        date_f  = state["ctx"].get("fdate_selected") or None

        results = data[table]
        if rel_f:  results = [r for r in results if r["relation"] in rel_f]
        if ev_f:   results = [r for r in results if r["event"]    in ev_f]
        if date_f: results = [r for r in results if r["date"]     in date_f]

        if results:
            state["ctx"]["last_results"] = results
            out = "\n\n".join([format_record(r) for r in results])
            await q.message.reply_text(
                f"תוצאות מסוננות:\n\n{out}\n\nלחץ על שם לצפייה ועריכה:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(r["name"], callback_data=f"view_rec_{r['id'][:8]}")] for r in results]
                    + [[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]
                )
            )
            state["mode"] = "idle"
        else:
            await q.message.reply_text("❌ לא נמצאו נתונים",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט", callback_data="main")]]))
            reset(user_id)
        return


# ---------- MESSAGE ----------
async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.document:
        await handle_excel(update)
        return

    user_id = update.effective_user.id
    state = get_state(user_id)
    text = update.message.text.strip() if update.message.text else ""

    # ── SAVE FLOW ──
    if state["mode"] == "save_name":
        state["ctx"]["name"] = text
        state["mode"] = "amount"
        await update.message.reply_text("בחר סכום:", reply_markup=amounts_kb("save_back_amount"))
        return

    if state["mode"] == "amount_custom":
        try:
            state["ctx"]["amount"] = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ סכום לא תקין, נסה שוב:")
            return
        state["mode"] = "event"
        await update.message.reply_text("בחר אירוע:", reply_markup=events_kb("save_back_event"))
        return

    if state["mode"] == "event_custom":
        add_custom_event(text)
        state["ctx"]["event"] = text
        state["mode"] = "relation"
        await update.message.reply_text("בחר קרבה:", reply_markup=relations_single_kb("save_back_relation"))
        return

    if state["mode"] == "relation_custom":
        add_custom_relation(text)
        state["ctx"]["relation"] = text
        state["mode"] = "date"
        await update.message.reply_text("הזן תאריך בפורמט DD-MM-YYYY או לחץ על הכפתור:",
            reply_markup=date_input_kb("save_back_date"))
        return

    if state["mode"] == "date":
        if text:
            try:
                date = parse_date(text)
            except ValueError:
                await update.message.reply_text("⚠️ פורמט שגוי. נסה שוב DD-MM-YYYY:")
                return
        else:
            date = today()
        rec = new_record(state["ctx"]["name"], state["ctx"]["amount"],
                         state["ctx"]["event"], state["ctx"]["relation"], date)
        data[state["ctx"]["table"]].append(rec)
        save()
        await update.message.reply_text("✅ נשמר בהצלחה", reply_markup=menu_kb())
        reset(user_id)
        return

    # ── SEARCH BY NAME ──
    if state["mode"] == "search_name":
        table   = state["ctx"].get("table", "received")
        results = [r for r in data[table] if text.lower() in r["name"].lower()]
        if results:
            state["ctx"]["last_results"] = results
            out  = "\n\n".join([format_record(r) for r in results])
            rows = [[InlineKeyboardButton(r["name"], callback_data=f"view_rec_{r['id'][:8]}")] for r in results]
            rows.append([InlineKeyboardButton("⬅️ חזרה", callback_data="search"),
                         InlineKeyboardButton("🏠 תפריט", callback_data="main")])
            await update.message.reply_text(f"תוצאות:\n\n{out}", reply_markup=InlineKeyboardMarkup(rows))
        else:
            await update.message.reply_text("❌ לא נמצאו נתונים\nרוצה לחפש לפי פילטרים?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 חיפוש מתקדם", callback_data="fallback_search")],
                    [InlineKeyboardButton("🏠 תפריט",        callback_data="main")],
                ]))
        return

    # ── EDIT INPUTS ──
    if state["mode"] == "edit_name_input":
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "name", text)
        await update.message.reply_text("✅ שם עודכן", reply_markup=menu_kb())
        reset(user_id)
        return

    if state["mode"] == "edit_amount_custom":
        try:
            _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "amount", int(text))
            await update.message.reply_text("✅ סכום עודכן", reply_markup=menu_kb())
            reset(user_id)
        except ValueError:
            await update.message.reply_text("⚠️ סכום לא תקין:")
        return

    if state["mode"] == "edit_event_custom":
        add_custom_event(text)
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "event", text)
        await update.message.reply_text("✅ אירוע עודכן", reply_markup=menu_kb())
        reset(user_id)
        return

    if state["mode"] == "edit_relation_custom":
        add_custom_relation(text)
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "relation", text)
        await update.message.reply_text("✅ קרבה עודכנה", reply_markup=menu_kb())
        reset(user_id)
        return

    if state["mode"] == "edit_date_input":
        if text:
            try:
                date = parse_date(text)
            except ValueError:
                await update.message.reply_text("⚠️ פורמט שגוי, נסה שוב DD-MM-YYYY:")
                return
        else:
            date = today()
        _apply_edit(state["ctx"]["viewing_id"], state["ctx"].get("table","received"), "date", date)
        await update.message.reply_text("✅ תאריך עודכן", reply_markup=menu_kb())
        reset(user_id)
        return

    if state["mode"] == "fall_event_custom":
        add_custom_event(text)
        state["ctx"]["fall_event"] = text
        state["mode"] = "fall_date"
        await update.message.reply_text("בחר תאריכים:",
            reply_markup=dates_multi_kb("fallback_search", []))
        return

    # ── DEFAULT ──
    await update.message.reply_text("בחר פעולה:", reply_markup=menu_kb())


# ---------- KEEP ALIVE ----------
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def keep_alive():
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

# Start keep-alive server and wait for it to bind
import time
t = Thread(target=keep_alive, daemon=True)
t.start()
time.sleep(2)  # give server time to bind to port

# ---------- RUN ----------
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CallbackQueryHandler(cb))
app.add_handler(MessageHandler(filters.ALL, msg))

print("BOT RUNNING")
app.run_polling()
