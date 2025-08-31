import gi
gi.require_version("Gtk", "3.0")
gi.require_version('Gst', '1.0')
gi.require_version('AppIndicator3', '0.1')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Gst, Pango, AppIndicator3, PangoCairo, Notify, Gio
import time
import sqlite3
import os
from datetime import datetime, timedelta
import requests
from collections import defaultdict
import cairo
import tempfile
import calendar
import shutil
import sys
import threading
import gettext
import locale

APP_NAME = "dailynote"
HOME = os.path.expanduser("~")

LOCALE_DIR = os.path.join(HOME, ".local", "share", "locale") 
locale.setlocale(locale.LC_ALL, '')
locale.bindtextdomain(APP_NAME, LOCALE_DIR)
gettext.bindtextdomain(APP_NAME, LOCALE_DIR)
gettext.textdomain(APP_NAME)
_ = gettext.gettext

installed_dir = os.path.join(HOME, '.local', 'share', APP_NAME)
is_installed = os.path.abspath(os.path.dirname(__file__)) == installed_dir

if is_installed:
    BASE_DIR = installed_dir
    LOCALE_DIR = os.path.join(HOME, '.local', 'share', 'locale')
    DB_NAME = os.path.join(BASE_DIR, "notes.db") # Keep DB with other app data
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOCALE_DIR = os.path.join(BASE_DIR, "locale")
    DB_NAME = os.path.join(BASE_DIR, "notes.db")

ICONS_DIR = os.path.join(BASE_DIR, "icons")
ALARMS_DIR = os.path.join(BASE_DIR, "alarms")
os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)



try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error:
    print("Locale could not be set. Using default C locale.")

locale.bindtextdomain(APP_NAME, LOCALE_DIR)
locale.bind_textdomain_codeset(APP_NAME, "UTF-8")
gettext.textdomain(APP_NAME)
_ = gettext.gettext

Gst.init(None)
Notify.init("DailyNote")

HEADERS = {'User-Agent': 'NoteApplication/1.0 (example@mail.com)'}

def setup_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        date TEXT NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alarms (
        note_id INTEGER PRIMARY KEY,
        sound TEXT,
        volume INTEGER,
        duration INTEGER,
        time TEXT,
        FOREIGN KEY(note_id) REFERENCES notes(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fixed_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        event_time TEXT,
        alarm_enabled INTEGER NOT NULL DEFAULT 0,
        alarm_days TEXT,
        sound TEXT,
        volume INTEGER,
        repeat_type TEXT DEFAULT 'weekly', 
        repeat_day INTEGER,               
        repeat_month INTEGER              
    )
    """)
    
    conn.commit()
    conn.close()

class NoteApplication(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(title=_("DailyNote"), application=application)
        
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        
        self.set_border_width(0)
        self.notes = []
        self.fixed_notes = []
        self.active_alarms = set()
        self.sound_player = Gst.ElementFactory.make("playbin", "player")
        self.current_latitude = None
        self.current_longitude = None
        self.current_location_name = None
        self.current_font_description = "Sans Serif 10"
        self.startup_notification_enabled = True
        self.css_provider = Gtk.CssProvider()
        self.last_known_day = None
        self.open_popups = {}
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png", prefix="dailynote_")
        self.indicator_icon_path = temp_file.name
        temp_file.close()

        setup_database()
        
        self.load_settings_from_db()
        self._load_css()
        
        self._create_ui()
        
        self.load_notes()
        self.load_fixed_notes()
        self.refresh_notes_list()
        self.refresh_fixed_notes_list()
        
        self.start_weather_update_in_background()
        GLib.idle_add(self.show_startup_notification)
        self.setup_indicator()
        self.update_date_and_icon()
        self.update_indicator_icon()
        
        GLib.timeout_add_seconds(1, self.update_time)
        GLib.timeout_add_seconds(1, self.check_alarms)
        
        self.connect("delete-event", self.on_window_close)

    def is_dark_theme(self):
        try:
            style_context = self.get_style_context()
            bg_color = style_context.get_property('background-color', Gtk.StateFlags.NORMAL)
            luminance = (0.299 * bg_color.red + 0.587 * bg_color.green + 0.114 * bg_color.blue)
            return luminance < 0.5
        except Exception:
            return False

    def on_window_close(self, widget, event):
        self.hide()
        return True

    def _get_themed_icon_path(self, icon_name):
        theme_folder = "light" if self.is_dark_theme() else "dark"
        return os.path.join(ICONS_DIR, theme_folder, icon_name)

    def _load_css(self):
        screen = Gdk.Screen.get_default()
        parsed_font = Pango.FontDescription.from_string(self.current_font_description)
        font_size = parsed_font.get_size()
        font_family = parsed_font.get_family()
        css_font_size = int(font_size / Pango.SCALE)
        css_font_string = f"font-family: \"{font_family}\"; font-size: {css_font_size}pt;"
        css_string = f"""
        * {{ {css_font_string} }}
        .not-list-frame, .weather-frame {{
            border: 0.5px solid #616161;
            border-radius: 1px;
            padding: 5px;
        }}
        .weather-frame {{ padding: 10px; }}
        """
        self.css_provider.load_from_data(css_string.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_screen(screen, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _create_ui(self):
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        self.add(main_vbox)

        header_bar = Gtk.HeaderBar()
        header_bar.set_show_close_button(True)
        header_bar.set_title(_("DailyNote"))
        
        menu_button = Gtk.MenuButton()
        menu_icon_path = self._get_themed_icon_path("menu.svg")
        if os.path.exists(menu_icon_path):
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(menu_icon_path, 24, 24)
            menu_icon = Gtk.Image.new_from_pixbuf(pixbuf)
            menu_button.add(menu_icon)
        
        self.popover = Gtk.Popover()
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=10)
        btn_settings = Gtk.Button(label=_("Application Settings"))
        btn_settings.connect("clicked", self.settings_popup)
        menu_box.pack_start(btn_settings, False, False, 0)
        btn_font_select = Gtk.Button(label=_("Change Font"))
        btn_font_select.connect("clicked", self.on_font_select_clicked)
        menu_box.pack_start(btn_font_select, False, False, 0)
        self.btn_notifications = Gtk.Button()
        self.btn_notifications.connect("clicked", self.toggle_startup_notification)
        menu_box.pack_start(self.btn_notifications, False, False, 0)
        self.update_notification_button_label()
        
        menu_box.add(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=5, margin_bottom=5))
        btn_backup = Gtk.Button(label=_("Backup Database"))
        btn_backup.connect("clicked", self.backup_popup)
        menu_box.pack_start(btn_backup, False, False, 0)
        btn_restore = Gtk.Button(label=_("Restore from Backup"))
        btn_restore.connect("clicked", self.restore_popup)
        menu_box.pack_start(btn_restore, False, False, 0)

        menu_box.show_all()
        self.popover.add(menu_box)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        menu_button.set_popover(self.popover)
        header_bar.pack_start(menu_button)
        self.set_titlebar(header_bar)

        top_bar_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.calendar_widget = self._create_calendar_icon_widget()
        top_bar_hbox.pack_start(self.calendar_widget, False, False, 0)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_bar_hbox.pack_start(spacer, True, True, 0)
        self.lbl_clock = Gtk.Label()
        top_bar_hbox.pack_end(self.lbl_clock, False, False, 0)
        main_vbox.pack_start(top_bar_hbox, False, False, 0)

        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self.on_calendar_day_selected)
        main_vbox.pack_start(self.calendar, False, False, 0)
        
        notes_header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        
        self.note_stack = Gtk.Stack()
        self.note_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        
        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(self.note_stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        
        notes_header_box.pack_start(stack_switcher, False, False, 0)

        self.search_hbox = Gtk.Box(spacing=5, margin_start=5, margin_end=5)
        search_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE) 
        search_icon_path = self._get_themed_icon_path("search.svg")
        if os.path.exists(search_icon_path):
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(search_icon_path, 16, 16)
            img = Gtk.Image.new_from_pixbuf(pixbuf)
            search_button.set_image(img)
        search_button.connect("clicked", self.clear_search_entry)
        self.search_hbox.pack_start(search_button, False, False, 0)
        self.entry_search = Gtk.Entry(placeholder_text=_("Search in notes..."))
        self.entry_search.connect("changed", self.search_notes)
        self.search_hbox.pack_start(self.entry_search, True, True, 0)
        notes_header_box.pack_start(self.search_hbox, False, False, 0)
        
        main_vbox.pack_start(notes_header_box, False, False, 0)

        self.notes_listbox = Gtk.ListBox()
        daily_scroll = Gtk.ScrolledWindow()
        daily_scroll.add(self.notes_listbox)
        self.note_stack.add_titled(daily_scroll, "daily", _("Daily Notes"))
        
        self.fixed_notes_listbox = Gtk.ListBox()
        fixed_scroll = Gtk.ScrolledWindow()
        fixed_scroll.add(self.fixed_notes_listbox)
        
        fixed_notes_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        btn_add_fixed = self.create_button_with_icon("plus.svg", _("Add New Fixed Note/Reminder"), self.fixed_note_popup)
        fixed_notes_page.pack_start(btn_add_fixed, False, False, 5)
        fixed_notes_page.pack_start(fixed_scroll, True, True, 0)
        self.note_stack.add_titled(fixed_notes_page, "fixed", _("Fixed Notes"))
        
        notes_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.NONE)
        notes_frame.get_style_context().add_class("not-list-frame")
        notes_frame.add(self.note_stack)
        main_vbox.pack_start(notes_frame, True, True, 0)

        lbl_weather_title = Gtk.Label(label=_("Current Weather:"), xalign=0)
        main_vbox.pack_start(lbl_weather_title, False, False, 0)
        self.weather_frame = Gtk.Frame()
        self.weather_frame.get_style_context().add_class("weather-frame")
        self.weather_frame_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=10)
        self.weather_frame.add(self.weather_frame_vbox)
        self.lbl_current_weather = Gtk.Label(label=_("Loading weather information..."))
        self.weather_frame_vbox.pack_start(self.lbl_current_weather, True, True, 0)
        main_vbox.pack_start(self.weather_frame, False, False, 0)
        GLib.timeout_add_seconds(600, self.start_weather_update_in_background)

        btn_box1 = Gtk.Box(spacing=10, margin_top=10)
        main_vbox.pack_end(btn_box1, False, False, 0)
        btn_add_note = self.create_button_with_icon("plus.svg", _("Add Note"), self.add_note_popup)
        btn_alarm = self.create_button_with_icon("alarm.png", _("Alarm"), self.alarm_popup, icon_size=24)
        btn_location = self.create_button_with_icon("location.png", _("Location"), self.location_settings_popup)
        btn_box1.pack_start(btn_add_note, True, True, 0)
        btn_box1.pack_start(btn_alarm, True, True, 0)
        btn_box1.pack_start(btn_location, True, True, 0)

        btn_box2 = Gtk.Box(spacing=10)
        main_vbox.pack_end(btn_box2, False, False, 0)
        btn_weekly = self.create_button_with_icon("calendar-week.png", _("Weekly View"), self.weekly_view_popup)
        btn_monthly = self.create_button_with_icon("calendar-month.png", _("Monthly View"), self.monthly_view_popup)
        btn_advanced_weather = self.create_button_with_icon("weather.png", _("Advanced Weather"), self.advanced_weather_popup)
        btn_box2.pack_start(btn_weekly, True, True, 0)
        btn_box2.pack_start(btn_monthly, True, True, 0)
        btn_box2.pack_start(btn_advanced_weather, True, True, 0)

    def _create_calendar_icon_widget(self):
        self.calendar_overlay = Gtk.Overlay()
        self.calendar_icon_image = Gtk.Image()
        self.calendar_overlay.add(self.calendar_icon_image)
        self.calendar_day_label = Gtk.Label()
        self.calendar_day_label.set_valign(Gtk.Align.CENTER)
        self.calendar_day_label.set_halign(Gtk.Align.CENTER)
        self.calendar_day_label.set_margin_top(10) 
        self.calendar_day_label.set_margin_end(2)
        self.calendar_overlay.add_overlay(self.calendar_day_label)
        self.calendar_overlay.show_all()
        return self.calendar_overlay

    def update_date_and_icon(self):
        day_str = datetime.now().strftime("%d")
        self.last_known_day = datetime.now().day
        style_context = self.get_style_context()
        text_color_rgba = style_context.get_color(Gtk.StateFlags.NORMAL)
        r, g, b = [int(c * 255) for c in (text_color_rgba.red, text_color_rgba.green, text_color_rgba.blue)]
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self.calendar_day_label.set_markup(f"<span weight='heavy' size='x-large' foreground='{hex_color}'>{day_str}</span>")
        icon_path = self._get_themed_icon_path("calendar_icon.svg")
        if os.path.exists(icon_path):
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 42, 42)
            self.calendar_icon_image.set_from_pixbuf(pixbuf)

    def update_indicator_icon(self):
        size = 48
        day_str = datetime.now().strftime("%d")
        base_icon_path = self._get_themed_icon_path("calendar_icon.svg")
        if not os.path.exists(base_icon_path): return
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(base_icon_path, size, size)
        surface = cairo.ImageSurface(cairo.Format.ARGB32, size, size)
        context = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        layout = PangoCairo.create_layout(context)
        font_desc = Pango.FontDescription("Sans Heavy 20")
        layout.set_font_description(font_desc)
        layout.set_text(day_str, -1)
        style_context = self.get_style_context()
        text_color = style_context.get_color(Gtk.StateFlags.NORMAL)
        context.set_source_rgba(text_color.red, text_color.green, text_color.blue, text_color.alpha)
        text_width, text_height = layout.get_pixel_size()
        text_x, text_y = (size - text_width) / 2, (size - text_height) / 2 + 6
        context.move_to(text_x, text_y)
        PangoCairo.show_layout(context, layout)
        surface.write_to_png(self.indicator_icon_path)
        if hasattr(self, 'indicator'):
            self.indicator.set_icon_full(self.indicator_icon_path, _("DailyNote Calendar"))

    def clear_search_entry(self, widget):
        self.entry_search.set_text("")
        
    def setup_indicator(self):
        self.indicator = AppIndicator3.Indicator.new("dailynote-app", "goa-account-google", AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(self.create_indicator_menu())
        
    def create_indicator_menu(self):
        menu = Gtk.Menu()
        item_add_note = Gtk.MenuItem(label=_("Add New Note"))
        item_add_note.connect("activate", self.add_note_popup)
        menu.append(item_add_note)
        item_show = Gtk.MenuItem(label=_("Show Application"))
        item_show.connect("activate", self.on_show_application)
        menu.append(item_show)
        item_quit = Gtk.MenuItem(label=_("Quit"))
        item_quit.connect("activate", self.cleanup_and_quit)
        menu.append(item_quit)
        menu.show_all()
        return menu

    def on_show_application(self, widget, *args):
        self.show_all()
        self.present()

    def create_button_with_icon(self, icon_name, label_text, callback, icon_size=16):
        btn = Gtk.Button()
        hbox = Gtk.Box(spacing=5, margin_start=10, margin_end=10)
        hbox.set_halign(Gtk.Align.CENTER)
        if icon_name:
            icon_path = self._get_themed_icon_path(icon_name)
            if os.path.exists(icon_path):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, icon_size, icon_size)
                img = Gtk.Image.new_from_pixbuf(pixbuf)
                hbox.pack_start(img, False, False, 0)
        lbl = Gtk.Label(label=label_text)
        hbox.pack_start(lbl, False, False, 0)
        btn.add(hbox)
        btn.connect("clicked", callback)
        return btn

    def update_time(self):
        self.lbl_clock.set_markup("<span weight='bold' size='x-large'>" + time.strftime("%H:%M:%S") + "</span>")
        if datetime.now().day != self.last_known_day:
            self.update_date_and_icon()
            self.update_indicator_icon()
        return True
        
    def on_calendar_day_selected(self, calendar):
        self.entry_search.handler_block_by_func(self.search_notes)
        self.entry_search.set_text("")
        self.entry_search.handler_unblock_by_func(self.search_notes)
        self.refresh_notes_list()
        self.refresh_open_popups()

    def search_notes(self, entry):
        search_text = entry.get_text().lower()
        active_tab = self.note_stack.get_visible_child_name()

        if active_tab == "daily":
            if search_text:
                filtered_notes = [n for n in self.notes if search_text in n['title'].lower() or (n.get('content') and search_text in n.get('content', '').lower())]
                self.refresh_notes_list(filtered_notes=filtered_notes)
            else:
                self.refresh_notes_list()
        elif active_tab == "fixed":
            if search_text:
                filtered_notes = [n for n in self.fixed_notes if search_text in n['title'].lower() or (n.get('content') and search_text in n.get('content', '').lower())]
                self.refresh_fixed_notes_list(filtered_notes=filtered_notes)
            else:
                self.refresh_fixed_notes_list()

    def refresh_notes_list(self, *args, filtered_notes=None):
        for child in self.notes_listbox.get_children():
            self.notes_listbox.remove(child)

        if filtered_notes is not None:
            notes_to_display = filtered_notes
        else:
            year, month, day = self.calendar.get_date()
            date_str = f"{year}-{month+1:02d}-{day:02d}"
            notes_to_display = [n for n in self.notes if n['date'] == date_str]

        all_alarms = self.load_all_alarms()

        if not notes_to_display:
            if filtered_notes is not None:
                self.notes_listbox.add(Gtk.Label(label=_("No search results found.")))
            else:
                self.notes_listbox.add(Gtk.Label(label=_("No notes for that day")))
        else:
            for note in notes_to_display:
                btn_note = Gtk.Button(halign=Gtk.Align.FILL)
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_start=5, margin_end=5)
                
                edit_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
                edit_icon.set_valign(Gtk.Align.CENTER)
                hbox.pack_start(edit_icon, False, False, 0)
                
                display_text = note['title']
                alarm_info = all_alarms.get(note.get('id'))
                if alarm_info:
                    display_text += f" ({alarm_info.get('time')})"

                if filtered_notes is not None:
                    try:
                        date_obj = datetime.strptime(note['date'], '%Y-%m-%d')
                        formatted_date = date_obj.strftime('%d.%m.%Y')
                        base_text = note['title']
                        if alarm_info:
                            base_text += f" ({alarm_info.get('time')})"
                        display_text = f"{base_text} [{formatted_date}]"
                    except ValueError:
                        display_text = f"{note['title']} [{note['date']}]"
                
                lbl = Gtk.Label(label=display_text, xalign=0, margin_top=5, margin_bottom=5)
                lbl.set_line_wrap(True)
                hbox.pack_start(lbl, True, True, 0)
                
                if note.get('id') in all_alarms:
                    image_path = self._get_themed_icon_path("alarm_filled.png")
                    if os.path.exists(image_path):
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(image_path, 24, 24)
                        img = Gtk.Image.new_from_pixbuf(pixbuf)
                        img.set_valign(Gtk.Align.CENTER)
                        hbox.pack_end(img, False, False, 0)

                btn_note.add(hbox)
                btn_note.connect("clicked", lambda w, n=note: self.edit_note_popup(n))
                self.notes_listbox.add(btn_note)
                
        self.notes_listbox.show_all()

    def refresh_open_popups(self):
        if self.open_popups.get("weekly"):
            win = self.open_popups["weekly"]["window"]
            grid = self.open_popups["weekly"]["grid"]
            if win.is_visible():
                year, month, day = self.calendar.get_date()
                selected_date = datetime(year, month + 1, day)
                title = f"{selected_date.strftime('%Y %B')} - {_('Weekly View')}"
                win.get_titlebar().set_title(title)
                self.populate_weekly_grid(grid, selected_date, win)
        
        if self.open_popups.get("monthly"):
            win = self.open_popups["monthly"]["window"]
            grid = self.open_popups["monthly"]["grid"]
            if win.is_visible():
                year, month, day = self.calendar.get_date()
                selected_date = datetime(year, month + 1, day)
                title = f"{selected_date.strftime('%Y %B')} - {_('Monthly View')}"
                win.get_titlebar().set_title(title)
                self.populate_monthly_grid(grid, selected_date, win)

    def load_notes(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, content, date FROM notes")
        self.notes = [{'id': r[0], 'title': r[1], 'content': r[2], 'date': r[3]} for r in cursor.fetchall()]
        conn.close()
    
    def load_fixed_notes(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, content, alarm_enabled, event_time, alarm_days, repeat_type, repeat_day, repeat_month FROM fixed_notes ORDER BY id")
        self.fixed_notes = [
            {'id': r[0], 'title': r[1], 'content': r[2], 'alarm_enabled': r[3], 'event_time': r[4], 
             'alarm_days': r[5], 'repeat_type': r[6], 'repeat_day': r[7], 'repeat_month': r[8]} for r in cursor.fetchall()
        ]
        conn.close()

    def load_all_alarms(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT note_id, sound, volume, duration, time FROM alarms")
        alarms = {r[0]: {'sound': r[1], 'volume': r[2], 'duration': r[3], 'time': r[4]} for r in cursor.fetchall()}
        conn.close()
        return alarms
    
    def save_note_db(self, note):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if 'id' in note:
            cursor.execute("UPDATE notes SET title=?, content=?, date=? WHERE id=?", (note['title'], note['content'], note['date'], note['id']))
        else:
            cursor.execute("INSERT INTO notes (title, content, date) VALUES (?, ?, ?)", (note['title'], note['content'], note['date']))
            note['id'] = cursor.lastrowid
        conn.commit()
        conn.close()
        self.load_notes()

    def save_alarm_db(self, note_id, sound, volume, duration, time_str):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO alarms (note_id, sound, volume, duration, time) VALUES (?, ?, ?, ?, ?)", (note_id, sound, volume, duration, time_str))
        conn.commit()
        conn.close()

    def load_alarm_db(self, note_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT sound, volume, duration, time FROM alarms WHERE note_id=?", (note_id,))
        row = cursor.fetchone()
        conn.close()
        return {'sound': row[0], 'volume': row[1], 'duration': row[2], 'time': row[3]} if row else None

    def delete_note_db(self, note_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notes WHERE id=?", (note_id,))
        cursor.execute("DELETE FROM alarms WHERE note_id=?", (note_id,))
        conn.commit()
        conn.close()
        self.load_notes()

    def delete_alarm_db(self, note_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alarms WHERE note_id=?", (note_id,))
        conn.commit()
        conn.close()
    
    def save_setting_db(self, key, value):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def load_settings_from_db(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        settings = {row[0]: row[1] for row in cursor.execute("SELECT key, value FROM settings").fetchall()}
        width = int(settings.get('window_width', 600))
        height = int(settings.get('window_height', 800))
        opacity = float(settings.get('window_opacity', 1.0))
        self.set_default_size(width, height)
        self.props.opacity = opacity
        self.current_latitude = settings.get('latitude', None)
        self.current_longitude = settings.get('longitude', None)
        self.current_location_name = settings.get('location_name', None)
        self.current_font_description = settings.get('font_description', "Sans Serif 10")
        self.startup_notification_enabled = settings.get('startup_notification_enabled', 'True') == 'True'
        conn.close()

    def settings_popup(self, widget):
        self.popover.hide()
        win = Gtk.Window(title=_("Application Settings"), transient_for=self, modal=True, default_width=350)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15, margin=20)
        win.add(vbox)
        size_grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        size_grid.attach(Gtk.Label(label=_("Width:"), xalign=0), 0, 0, 1, 1)
        size_grid.attach(Gtk.Label(label=_("Height:"), xalign=0), 0, 1, 1, 1)
        width_adj = Gtk.Adjustment(value=self.get_size()[0], lower=400, upper=1920, step_increment=10)
        spin_width = Gtk.SpinButton(adjustment=width_adj)
        size_grid.attach(spin_width, 1, 0, 1, 1)
        height_adj = Gtk.Adjustment(value=self.get_size()[1], lower=500, upper=1080, step_increment=10)
        spin_height = Gtk.SpinButton(adjustment=height_adj)
        size_grid.attach(spin_height, 1, 1, 1, 1)
        vbox.pack_start(size_grid, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Window Opacity:"), xalign=0), False, False, 0)
        opacity_adj = Gtk.Adjustment(value=self.props.opacity, lower=0.2, upper=1.0, step_increment=0.05)
        scale_opacity = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=opacity_adj, digits=2)
        vbox.pack_start(scale_opacity, False, False, 0)
        btn_box = Gtk.Box(spacing=10, margin_top=10)
        btn_save = Gtk.Button(label=_("Save and Close"))
        btn_save.connect("clicked", self.save_app_settings, spin_width, spin_height, scale_opacity, win)
        btn_box.pack_end(btn_save, False, False, 0)
        vbox.pack_end(btn_box, False, False, 0)
        win.show_all()

    def save_app_settings(self, widget, spin_width, spin_height, scale_opacity, settings_window):
        width = spin_width.get_value_as_int()
        height = spin_height.get_value_as_int()
        opacity = scale_opacity.get_value()
        self.save_setting_db('window_width', str(width))
        self.save_setting_db('window_height', str(height))
        self.save_setting_db('window_opacity', str(opacity))
        self.resize(width, height)
        self.props.opacity = opacity
        settings_window.destroy()

    def add_note_popup(self, widget, *args):
        win = Gtk.Window(title=_("Add New Daily Note"), transient_for=self, modal=True, default_width=400, default_height=500)
        vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox_main)
        popup_calendar = Gtk.Calendar()
        vbox_main.pack_start(popup_calendar, False, False, 0)
        entry_title = Gtk.Entry(placeholder_text=_("Title..."))
        vbox_main.pack_start(entry_title, False, False, 0)
        lbl_content_title = Gtk.Label(label=_("Content:"), xalign=0, margin_top=5)
        vbox_main.pack_start(lbl_content_title, False, False, 0)
        textview_content = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        scroll = Gtk.ScrolledWindow(min_content_height=150)
        scroll.add(textview_content)
        content_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.NONE)
        content_frame.get_style_context().add_class("not-list-frame")
        content_frame.add(scroll)
        vbox_main.pack_start(content_frame, True, True, 0)
        btn_save = Gtk.Button(label=_("Save"))
        btn_save.connect("clicked", lambda w: self.save_new_note(win, entry_title, textview_content, popup_calendar))
        vbox_main.pack_end(btn_save, False, False, 0)
        win.show_all()

    def save_new_note(self, window, entry_title, textview_content, popup_calendar):
        title = entry_title.get_text()
        buffer = textview_content.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        year, month, day = popup_calendar.get_date()
        date_str = f"{year}-{month+1:02d}-{day:02d}"
        note = {'title': title, 'content': content, 'date': date_str}
        self.save_note_db(note)
        self.refresh_notes_list()
        self.refresh_open_popups()
        window.destroy()

    def edit_note_popup(self, note_item, parent_window=None):
        parent = parent_window if parent_window else self
        win = Gtk.Window(title=_("Edit Note"), transient_for=parent, modal=True, default_width=400, default_height=500)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox)
        entry_title = Gtk.Entry(text=note_item['title'])
        vbox.pack_start(entry_title, False, False, 0)
        lbl_content_title = Gtk.Label(label=_("Content:"), xalign=0, margin_top=5)
        vbox.pack_start(lbl_content_title, False, False, 0)
        textview_content = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        textview_content.get_buffer().set_text(note_item.get('content',''))
        scroll = Gtk.ScrolledWindow(min_content_height=150)
        scroll.add(textview_content)
        content_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.NONE)
        content_frame.get_style_context().add_class("not-list-frame")
        content_frame.add(scroll)
        vbox.pack_start(content_frame, True, True, 0)
        btn_box = Gtk.Box(spacing=10)
        btn_edit = Gtk.Button(label=_("Save"))
        btn_edit.connect("clicked", lambda w: self.save_existing_note(note_item, entry_title, textview_content, win))
        btn_delete = Gtk.Button(label=_("Delete"))
        btn_delete.connect("clicked", lambda w: self.delete_note(note_item, win))
        btn_box.pack_start(btn_edit, True, True, 0)
        btn_box.pack_start(btn_delete, True, True, 0)
        vbox.pack_end(btn_box, False, False, 0)
        win.show_all()

    def save_existing_note(self, note_item, entry_title, textview_content, window):
        note_item['title'] = entry_title.get_text()
        buffer = textview_content.get_buffer()
        note_item['content'] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        self.save_note_db(note_item)
        self.refresh_notes_list()
        self.refresh_open_popups()
        window.destroy()

    def delete_note(self, note_item, window):
        dialog = Gtk.MessageDialog(transient_for=window, modal=True, message_type=Gtk.MessageType.QUESTION, text=_("Are you sure you want to delete this note?"))
        dialog.add_button(_("No"), Gtk.ResponseType.NO)
        dialog.add_button(_("Yes"), Gtk.ResponseType.YES)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self.delete_note_db(note_item['id'])
            self.refresh_notes_list()
            self.refresh_open_popups()
            window.destroy()

    def check_alarms(self):
        now = datetime.now()
        now_str = now.strftime("%H:%M")
        now_date_str = now.strftime("%Y-%m-%d")
        for note in self.notes:
            alarm = self.load_alarm_db(note.get('id'))
            if alarm and note.get('date') == now_date_str and alarm.get('time') == now_str and note.get('id') not in self.active_alarms:
                self.active_alarms.add(note.get('id'))
                self.show_alarm_popup(note, alarm)

        today_weekday = str(now.weekday())
        today_monthday = now.day
        today_month = now.month

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, content, alarm_days, repeat_type, repeat_day, repeat_month FROM fixed_notes WHERE alarm_enabled=1 AND event_time=?", (now_str,))
        fixed_alarms_to_check = cursor.fetchall()
        conn.close()

        for alarm_data in fixed_alarms_to_check:
            alarm_id = f"fixed_{alarm_data[0]}"
            if alarm_id not in self.active_alarms:
                note_title, note_content = alarm_data[1], alarm_data[2]
                alarm_days, repeat_type, repeat_day, repeat_month = alarm_data[3], alarm_data[4], alarm_data[5], alarm_data[6]
                trigger_alarm = False
                if repeat_type == 'weekly' and today_weekday in alarm_days.split(','):
                    trigger_alarm = True
                elif repeat_type == 'monthly' and today_monthday == repeat_day:
                    trigger_alarm = True
                elif repeat_type == 'yearly' and today_monthday == repeat_day and today_month == repeat_month:
                    trigger_alarm = True
                if trigger_alarm:
                    self.active_alarms.add(alarm_id)
                    fake_note = {'id': alarm_id, 'title': note_title, 'content': note_content}
                    fake_alarm = {'sound': None, 'volume': 80, 'duration': 10} 
                    self.show_alarm_popup(fake_note, fake_alarm)
        return True

    def show_alarm_popup(self, note, alarm):
        win = Gtk.Window(title=_("Alarm"), default_width=400, default_height=300)
        win.set_keep_above(True)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=10)
        win.add(vbox)
        
        title_markup = GLib.markup_escape_text(note['title'])
        vbox.pack_start(Gtk.Label(label=f"<b>{_('Title')}: {title_markup}</b>", use_markup=True, xalign=0), False, False, 0)
        
        lbl_content_header = Gtk.Label(label=_("Content:"), xalign=0, margin_top=10, margin_bottom=2)
        vbox.pack_start(lbl_content_header, False, False, 0)
        content_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.NONE)
        content_frame.get_style_context().add_class("not-list-frame")
        textview_content = Gtk.TextView()
        textview_content.set_editable(False)
        textview_content.set_cursor_visible(False)
        textview_content.set_wrap_mode(Gtk.WrapMode.WORD)
        textview_content.get_buffer().set_text(note.get('content', ''))
        textview_content.set_left_margin(5)
        textview_content.set_right_margin(5)
        textview_content.set_top_margin(5)
        textview_content.set_bottom_margin(5)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(textview_content)
        content_frame.add(scroll)
        vbox.pack_start(content_frame, True, True, 0)
        vbox.pack_start(Gtk.Label(label=_("Snooze duration (min):"), xalign=0), False, False, 0)
        scale_snooze = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 180, 1)
        scale_snooze.set_value(5)
        vbox.pack_start(scale_snooze, False, False, 0)
        btn_box = Gtk.Box(spacing=10)
        btn_snooze = Gtk.Button(label=_("Snooze"))
        btn_dismiss = Gtk.Button(label=_("Dismiss"))
        btn_box.pack_start(btn_snooze, True, True, 0)
        btn_box.pack_start(btn_dismiss, True, True, 0)
        vbox.pack_end(btn_box, False, False, 0)
        
        if alarm.get('sound'):
            sound_path = os.path.join(ALARMS_DIR, alarm['sound'])
            if os.path.exists(sound_path):
                self.sound_player.set_property("uri", f"file://{sound_path}")
                self.sound_player.set_property("volume", alarm['volume'] / 100.0)
                self.sound_player.set_state(Gst.State.PLAYING)

        bus = self.sound_player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self.on_eos_message, None)
        timeout_source = GLib.timeout_add_seconds(alarm.get('duration', 10), self.stop_alarm_sound_and_window, win, note['id'], True)
        
        def on_action_close():
            self.stop_sound()
            GLib.source_remove(timeout_source)
            if note['id'] in self.active_alarms: self.active_alarms.remove(note['id'])
            win.destroy()

        def snooze_action(widget):
            snooze_minutes = int(scale_snooze.get_value())
            if snooze_minutes > 0 and isinstance(note.get('id'), int):
                new_time = (datetime.now() + timedelta(minutes=snooze_minutes)).strftime("%H:%M")
                snooze_text = _("Alarm snoozed until {time}.").format(time=new_time)
                dialog = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=snooze_text)
                dialog.run()
                dialog.destroy()
                self.save_alarm_db(note.get('id'), alarm.get('sound'), alarm.get('volume'), alarm.get('duration'), new_time)
            self.refresh_notes_list()
            self.refresh_open_popups()
            on_action_close()
        
        def dismiss_action(widget):
            if isinstance(note.get('id'), int):
                self.delete_alarm_db(note.get('id'))
                self.refresh_notes_list()
                self.refresh_open_popups()
            on_action_close()

        def on_window_close(widget, event):
            on_action_close()
            return False

        btn_snooze.connect("clicked", snooze_action)
        btn_dismiss.connect("clicked", dismiss_action)
        win.connect("delete-event", on_window_close)
        win.show_all()
        
    def stop_alarm_sound_and_window(self, window, note_id, delete_from_db=False):
        self.stop_sound()
        if note_id in self.active_alarms: self.active_alarms.remove(note_id)
        if delete_from_db and isinstance(note_id, int):
            self.delete_alarm_db(note_id)
            self.refresh_notes_list()
            self.refresh_open_popups()
        window.destroy()
        return False

    def alarm_popup(self, widget):
        win = Gtk.Window(title=_("Select Note for Alarm"), transient_for=self, modal=True, default_width=400, default_height=420)
        vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox_main)
        popup_calendar = Gtk.Calendar()
        vbox_main.pack_start(popup_calendar, False, False, 0)
        listbox_titles = Gtk.ListBox()
        scroll = Gtk.ScrolledWindow(min_content_height=260)
        scroll.add(listbox_titles)
        note_list_frame_alarm = Gtk.Frame(shadow_type=Gtk.ShadowType.NONE)
        note_list_frame_alarm.get_style_context().add_class("not-list-frame")
        note_list_frame_alarm.add(scroll)
        vbox_main.pack_start(note_list_frame_alarm, True, True, 0)
        def update_titles(calendar):
            for child in listbox_titles.get_children(): listbox_titles.remove(child)
            year, month, day = calendar.get_date()
            date_str = f"{year}-{month+1:02d}-{day:02d}"
            day_notes = [n for n in self.notes if n['date'] == date_str]
            all_alarms = self.load_all_alarms()
            if not day_notes:
                listbox_titles.add(Gtk.Label(label=_("No notes for that day")))
            else:
                for note in day_notes:
                    btn = Gtk.Button(halign=Gtk.Align.FILL)
                    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_start=5, margin_end=5)
                    edit_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
                    edit_icon.set_valign(Gtk.Align.CENTER)
                    hbox.pack_start(edit_icon, False, False, 0)
                    lbl = Gtk.Label(label=note['title'], xalign=0, margin_top=5, margin_bottom=5)
                    lbl.set_line_wrap(True)
                    hbox.pack_start(lbl, True, True, 0)
                    if note.get('id') in all_alarms:
                        image_path = self._get_themed_icon_path("alarm_filled.png")
                        if os.path.exists(image_path):
                            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(image_path, 24, 24)
                            img = Gtk.Image.new_from_pixbuf(pixbuf)
                            img.set_valign(Gtk.Align.CENTER)
                            hbox.pack_end(img, False, False, 0)
                    btn.add(hbox)
                    btn.connect("clicked", lambda w, n=note: self.alarm_settings_popup(n, win))
                    listbox_titles.add(btn)
            listbox_titles.show_all()
        popup_calendar.connect("day-selected", lambda w: update_titles(w))
        update_titles(popup_calendar)
        win.show_all()

    def alarm_settings_popup(self, note_item, parent_win=None):
        if parent_win: parent_win.destroy()
        title = _("Alarm Settings: {title}").format(title=note_item['title'])
        win = Gtk.Window(title=title, transient_for=self, modal=True, default_width=400)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox)
        vbox.pack_start(Gtk.Label(label=_("Set Time (HH:MM):"), xalign=0), False, False, 0)
        entry_time = Gtk.Entry(text="08:00")
        vbox.pack_start(entry_time, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Select Alarm Sound:"), xalign=0), False, False, 0)
        combo_sound = Gtk.ComboBoxText()
        if os.path.exists(ALARMS_DIR):
            for f in sorted(os.listdir(ALARMS_DIR)):
                if f.lower().endswith((".wav", ".m3u", ".mp3")): combo_sound.append_text(f)
        if len(combo_sound.get_model()) > 0: combo_sound.set_active(0)
        vbox.pack_start(combo_sound, False, False, 0)
        btn_test_sound = Gtk.Button(label=_("Play Sound"))
        btn_test_sound.connect("clicked", lambda w: (self.stop_sound(btn_test_sound) if btn_test_sound.get_label() == _("Stop Sound") else self.play_selected_alarm_sound(combo_sound.get_active_text(), scale_volume.get_value(), btn_test_sound)))
        vbox.pack_start(btn_test_sound, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Volume:"), xalign=0), False, False, 0)
        scale_volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale_volume.set_value(50)
        scale_volume.connect("value-changed", self.on_volume_changed)
        vbox.pack_start(scale_volume, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Alarm Duration (s):"), xalign=0), False, False, 0)
        scale_duration = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 180, 1)
        scale_duration.set_value(10)
        vbox.pack_start(scale_duration, False, False, 0)
        alarm_data = self.load_alarm_db(note_item.get('id'))
        if alarm_data:
            entry_time.set_text(alarm_data['time'])
            scale_volume.set_value(alarm_data['volume'])
            scale_duration.set_value(alarm_data['duration'])
            for i, row in enumerate(combo_sound.get_model()):
                if row[0] == alarm_data['sound']: combo_sound.set_active(i)
        btn_box = Gtk.Box(spacing=10)
        btn_save = Gtk.Button(label=_("Save"))
        btn_delete = Gtk.Button(label=_("Delete (Remove Alarm)"))
        btn_box.pack_start(btn_save, True, True, 0)
        btn_box.pack_start(btn_delete, True, True, 0)
        vbox.pack_end(btn_box, False, False, 0)
        def on_save_clicked(widget):
            self.stop_sound(btn_test_sound)
            self.save_alarm_db(note_item.get('id'), combo_sound.get_active_text() or "", int(scale_volume.get_value()), int(scale_duration.get_value()), entry_time.get_text())
            win.destroy()
            self.refresh_notes_list()
        def on_delete_clicked(widget):
            self.stop_sound(btn_test_sound)
            self.delete_alarm_db(note_item.get('id'))
            win.destroy()
            self.refresh_notes_list()
        btn_save.connect("clicked", on_save_clicked)
        btn_delete.connect("clicked", on_delete_clicked)
        win.connect("delete-event", lambda w, e: self.stop_sound(btn_test_sound))
        win.show_all()
        
    def play_selected_alarm_sound(self, sound_file, volume, test_button):
        if not sound_file: return
        sound_path = os.path.join(ALARMS_DIR, sound_file)
        if not os.path.exists(sound_path): return
        self.stop_sound(test_button)
        self.sound_player.set_property("uri", f"file://{sound_path}")
        self.sound_player.set_property("volume", volume / 100.0)
        self.sound_player.set_state(Gst.State.PLAYING)
        test_button.set_label(_("Stop Sound"))
        bus = self.sound_player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self.on_eos_message, test_button)

    def on_eos_message(self, bus, message, test_button):
        if test_button: 
            self.stop_sound(test_button)
        else:
            self.sound_player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.sound_player.set_state(Gst.State.PLAYING)
        
    def on_volume_changed(self, scale):
        if self.sound_player.get_state(0)[1] == Gst.State.PLAYING:
            self.sound_player.set_property("volume", scale.get_value() / 100.0)

    def stop_sound(self, test_button=None):
        self.sound_player.set_state(Gst.State.NULL)
        if test_button:
            test_button.set_label(_("Play Sound"))

    def show_startup_notification(self):
        if not self.startup_notification_enabled: return False
        today = datetime.now().strftime("%Y-%m-%d")
        todays_notes = [n for n in self.notes if n['date'] == today]
        if todays_notes:
            title = _("Today's Notes")
            message_parts = []
            for n in todays_notes:
                escaped_title = GLib.markup_escape_text(n['title'])
                escaped_content = GLib.markup_escape_text(n.get('content', '') or '')
                message_parts.append(f"<b>{escaped_title}</b>: {escaped_content}")
            message = "\n\n".join(message_parts)
            notification = Notify.Notification.new(title, message, "dialog-information")
            notification.set_urgency(Notify.Urgency.NORMAL)
            notification.show()
        return False
    
    def toggle_startup_notification(self, widget):
        self.startup_notification_enabled = not self.startup_notification_enabled
        self.save_setting_db('startup_notification_enabled', str(self.startup_notification_enabled))
        self.update_notification_button_label()
        self.popover.hide()

    def update_notification_button_label(self):
        label = _("Disable Notifications") if self.startup_notification_enabled else _("Enable Notifications")
        self.btn_notifications.set_label(label)
        
    def start_weather_update_in_background(self, *args):
        for child in self.weather_frame_vbox.get_children():
            self.weather_frame_vbox.remove(child)
        loading_label = Gtk.Label(label=_("Loading weather information..."))
        self.weather_frame_vbox.pack_start(loading_label, True, True, 0)
        self.weather_frame_vbox.show_all()
        
        thread = threading.Thread(target=self._fetch_weather_data)
        thread.daemon = True
        thread.start()
        return True

    def _fetch_weather_data(self):
        if not self.current_latitude or not self.current_longitude:
            GLib.idle_add(self._update_weather_ui, {"error": _("Please set a location.")})
            return

        try:
            url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={self.current_latitude}&lon={self.current_longitude}"
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            data = response.json()
            GLib.idle_add(self._update_weather_ui, data)
        except Exception as e:
            print(f"Error fetching weather: {e}")
            GLib.idle_add(self._update_weather_ui, {"error": _("Could not retrieve weather.")})

    def _update_weather_ui(self, data):
        for child in self.weather_frame_vbox.get_children():
            self.weather_frame_vbox.remove(child)

        if not data or data.get("error"):
            error_text = data.get("error", _("Failed to get weather data."))
            error_label = Gtk.Label(label=error_text, justify=Gtk.Justification.CENTER)
            self.weather_frame_vbox.pack_start(error_label, True, True, 0)
            self.weather_frame_vbox.show_all()
            return
        
        try:
            timeseries = data.get('properties', {}).get('timeseries', [])
            if not timeseries: raise ValueError("Could not get time series data from API.")
            
            current_data = timeseries[0]['data']
            details = current_data['instant']['details']
            summary = current_data['next_1_hours']['summary']
            temperature = details.get('air_temperature')
            weather_symbol_code = summary.get('symbol_code')
            wind_speed_ms = details.get('wind_speed')
            relative_humidity = details.get('relative_humidity')
            precipitation_amount = current_data['next_1_hours']['details'].get('precipitation_amount', 0)
            wind_speed_kmh = round(wind_speed_ms * 3.6, 1) if wind_speed_ms is not None else 0
            
            weather_symbols = {
                'fair_day': _('Fair'), 'clearsky_day': _('Clear'), 
                'clearsky_night': _('Clear (Night)'), 'partlycloudy_day': _('Partly Cloudy'), 
                'partlycloudy_night': _('Partly Cloudy (Night)'), 'cloudy': _('Cloudy'), 
                'rain': _('Rain'), 'heavyrain': _('Heavy Rain'), 'fog': _('Fog')
            }
            
            main_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            weather_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            icon_filename = "partly-cloudy-day.svg"
            if weather_symbol_code:
                specific_icon_filename = f"{weather_symbol_code}.svg"
                if os.path.exists(os.path.join(ICONS_DIR, specific_icon_filename)):
                    icon_filename = specific_icon_filename
                else:
                    generic_code = weather_symbol_code.split('_')[0]
                    if os.path.exists(os.path.join(ICONS_DIR, f"{generic_code}.svg")):
                        icon_filename = f"{generic_code}.svg"

            final_icon_path = os.path.join(ICONS_DIR, icon_filename)
            img = Gtk.Image()
            if os.path.exists(final_icon_path):
                 pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(final_icon_path, 48, 48)
                 img.set_from_pixbuf(pixbuf)
            else: img.set_from_icon_name('image-missing-symbolic', Gtk.IconSize.DIALOG)

            weather_vbox.pack_start(img, False, False, 0)
            weather_desc = weather_symbols.get(weather_symbol_code, _("Unknown"))
            weather_vbox.pack_start(Gtk.Label(label=weather_desc), False, False, 0)
            main_hbox.pack_start(weather_vbox, False, False, 0)
            
            info_grid = Gtk.Grid(column_spacing=40, row_spacing=5)
            def create_info_column(icon_filename, text, col_idx):
                icon_path = self._get_themed_icon_path(icon_filename)
                img = Gtk.Image()
                if os.path.exists(icon_path):
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 24, 24)
                    img.set_from_pixbuf(pixbuf)
                info_grid.attach(img, col_idx, 0, 1, 1)
                info_grid.attach(Gtk.Label(label=text), col_idx, 1, 1, 1)

            create_info_column("thermometer.png", f"{temperature}°C", 0)
            create_info_column("wind.png", f"{wind_speed_kmh} km/h", 1)
            create_info_column("water.png", f"%{relative_humidity}", 2)
            create_info_column("rain.png", f"{precipitation_amount} mm", 3)
            main_hbox.pack_end(info_grid, False, False, 0)
            
            self.weather_frame_vbox.pack_start(main_hbox, True, True, 0)
            self.weather_frame_vbox.show_all()
        except Exception as e:
            print(f"Error updating weather UI: {e}")
            error_label = Gtk.Label(label=_("Error displaying weather data."), justify=Gtk.Justification.CENTER)
            self.weather_frame_vbox.pack_start(error_label, True, True, 0)
            self.weather_frame_vbox.show_all()

    def location_settings_popup(self, widget):
        win = Gtk.Window(title=_("Location Settings"), transient_for=self, modal=True, default_width=400)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox)
        vbox.pack_start(Gtk.Label(label=_("Location Name:"), xalign=0), False, False, 0)
        entry_location = Gtk.Entry(text=self.current_location_name or "", placeholder_text=_("City, Country Code"))
        vbox.pack_start(entry_location, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Latitude:"), xalign=0), False, False, 0)
        entry_lat = Gtk.Entry(text=self.current_latitude or "", placeholder_text=_("00.0000 (North +, South -)"))
        vbox.pack_start(entry_lat, False, False, 0)
        vbox.pack_start(Gtk.Label(label=_("Longitude:"), xalign=0), False, False, 0)
        entry_lon = Gtk.Entry(text=self.current_longitude or "", placeholder_text=_("00.0000 (East +, West -)"))
        vbox.pack_start(entry_lon, False, False, 0)
        btn_box = Gtk.Box(spacing=10)
        btn_save = Gtk.Button(label=_("Save"))
        btn_delete = Gtk.Button(label=_("Reset to Default"))
        btn_box.pack_start(btn_save, True, True, 0)
        btn_box.pack_start(btn_delete, True, True, 0)
        vbox.pack_end(btn_box, False, False, 0)
        btn_save.connect("clicked", lambda w: self.save_location(win, entry_location, entry_lat, entry_lon))
        btn_delete.connect("clicked", lambda w: self.reset_location_to_default(entry_location, entry_lat, entry_lon))
        win.show_all()

    def save_location(self, window, entry_location, entry_lat, entry_lon):
        try:
            new_lat, new_lon = float(entry_lat.get_text()), float(entry_lon.get_text())
            new_location_name = entry_location.get_text()
            self.save_setting_db('latitude', str(new_lat))
            self.save_setting_db('longitude', str(new_lon))
            self.save_setting_db('location_name', new_location_name)
            self.current_latitude, self.current_longitude, self.current_location_name = str(new_lat), str(new_lon), new_location_name
            self.start_weather_update_in_background()
            window.destroy()
        except ValueError:
            dialog = Gtk.MessageDialog(transient_for=window, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=_("Latitude and longitude must be numeric values."))
            dialog.run()
            dialog.destroy()

    def reset_location_to_default(self, entry_location, entry_lat, entry_lon):
        entry_location.set_text("")
        entry_lat.set_text("")
        entry_lon.set_text("")
        
    def advanced_weather_popup(self, widget):
        title = _("5-Day Weather Forecast for {location}").format(location=self.current_location_name or '...')
        win = Gtk.Window(title=title, transient_for=self, modal=True, default_width=700, default_height=500)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(vbox)
        if not self.current_latitude or not self.current_longitude:
            lbl_warning = Gtk.Label(label=_("To see the advanced weather forecast,\nplease set a location first."))
            lbl_warning.set_justify(Gtk.Justification.CENTER)
            vbox.pack_start(lbl_warning, True, True, 0)
            win.show_all()
            return
        try:
            url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={self.current_latitude}&lon={self.current_longitude}"
            response = requests.get(url, headers=HEADERS)
            response.raise_for_status()
            data = response.json()
            if not data or 'properties' not in data or 'timeseries' not in data['properties']: raise ValueError("Invalid or incomplete data from API.")
            forecast_by_day_and_time = self.group_forecast_data(data['properties']['timeseries'])
            grid = Gtk.Grid(column_spacing=15, row_spacing=10)
            grid.get_style_context().add_class("forecast-grid")
            vbox.pack_start(grid, True, True, 0)
            day_names = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
            time_periods = [_("Morning"), _("Noon"), _("Evening"), _("Night")]
            grid.attach(Gtk.Label(label=""), 0, 0, 1, 1)
            for i, period in enumerate(time_periods):
                lbl = Gtk.Label()
                lbl.set_markup(f"<span weight='bold'>{period}</span>")
                grid.attach(lbl, i + 1, 0, 1, 1)
            days_to_display = sorted(forecast_by_day_and_time.keys())[:5]
            for i, day in enumerate(days_to_display):
                day_of_week = datetime.strptime(day, "%Y-%m-%d").weekday()
                lbl_day = Gtk.Label(justify=Gtk.Justification.LEFT)
                lbl_day.set_markup(f"<span weight='bold'>{day_names[day_of_week]}</span>")
                grid.attach(lbl_day, 0, i + 1, 1, 1)
                
                translated_periods = [_("Morning"), _("Noon"), _("Evening"), _("Night")]
                original_periods = ["Morning", "Noon", "Evening", "Night"]
                period_map = dict(zip(original_periods, translated_periods))

                for j, period_key in enumerate(original_periods):
                    period_display = period_map[period_key]
                    if period_key in forecast_by_day_and_time[day] and forecast_by_day_and_time[day][period_key]:
                        data_item = forecast_by_day_and_time[day][period_key]
                        temp = data_item.get('temperature', '-')
                        icon_code = data_item.get('icon', None)
                        wind_speed = data_item.get('wind_speed', '-')
                        humidity = data_item.get('humidity', '-')
                        cell_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=5)
                        if icon_code:
                            icon_path = os.path.join(ICONS_DIR, f"{icon_code}.svg")
                            if os.path.exists(icon_path):
                                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 32, 32)
                                cell_vbox.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
                        lbl_temp = Gtk.Label(use_markup=True, label=f"<b>{temp}°C</b>")
                        cell_vbox.pack_start(lbl_temp, False, False, 0)
                        wind_label_text = _("Wind: {speed} m/s").format(speed=wind_speed)
                        humidity_label_text = _("Humidity: %{humidity}").format(humidity=humidity)
                        cell_vbox.pack_start(Gtk.Label(label=wind_label_text), False, False, 0)
                        cell_vbox.pack_start(Gtk.Label(label=humidity_label_text), False, False, 0)
                        grid.attach(cell_vbox, j + 1, i + 1, 1, 1)
                    else:
                        grid.attach(Gtk.Label(label="-"), j + 1, i + 1, 1, 1)
            btn_close = Gtk.Button(label=_("Close"))
            btn_close.connect("clicked", lambda w: win.destroy())
            vbox.pack_end(btn_close, False, False, 0)
            win.show_all()
        except (requests.exceptions.RequestException, ValueError, IndexError, KeyError) as e:
            dialog = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=_("Could not retrieve weather data."))
            dialog.run()
            dialog.destroy()
            print(f"Error detail: {e}")

    def find_closest_data(self, day_data, target_hour):
        if not day_data: return None
        return min(day_data, key=lambda item: abs(datetime.fromisoformat(item['time'].replace('Z', '+00:00')).hour - target_hour))

    def group_forecast_data(self, timeseries_data):
        forecast_by_day = defaultdict(list)
        for item in timeseries_data:
            day_str = datetime.fromisoformat(item['time'].replace('Z', '+00:00')).strftime("%Y-%m-%d")
            forecast_by_day[day_str].append(item)
        grouped_data = defaultdict(dict)
        for day, day_data in forecast_by_day.items():
            grouped_data[day]['Morning'] = self.extract_weather_info(self.find_closest_data(day_data, 6))
            grouped_data[day]['Noon'] = self.extract_weather_info(self.find_closest_data(day_data, 12))
            grouped_data[day]['Evening'] = self.extract_weather_info(self.find_closest_data(day_data, 18))
            grouped_data[day]['Night'] = self.extract_weather_info(self.find_closest_data(day_data, 0))
        return grouped_data
        
    def extract_weather_info(self, item):
        if not item: return None
        try:
            summary_key_order = ['next_1_hours', 'next_6_hours', 'next_12_hours']
            summary = next((item['data'][key]['summary'] for key in summary_key_order if key in item['data']), None)
            if not summary: return None
            details = item['data']['instant']['details']
            return {'temperature': details['air_temperature'], 'icon': summary['symbol_code'], 'wind_speed': details['wind_speed'], 'humidity': details['relative_humidity']}
        except KeyError:
            return None
    
    def on_font_select_clicked(self, widget):
        self.popover.hide()
        dialog = Gtk.FontChooserDialog(title=_("Select Font"), transient_for=self, modal=True)
        dialog.set_font(self.current_font_description)
        if dialog.run() == Gtk.ResponseType.OK:
            new_font_description = dialog.get_font()
            if new_font_description:
                self.current_font_description = new_font_description
                self.save_setting_db('font_description', new_font_description)
                self._load_css()
        dialog.destroy()
        
    def weekly_view_popup(self, widget):
        win = Gtk.Window(default_width=900, default_height=600)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        win.set_titlebar(header)
        win.set_resizable(True)
        year, month, day = self.calendar.get_date()
        selected_date = datetime(year, month + 1, day)
        title = f"{selected_date.strftime('%Y %B')} - {_('Weekly View')}"
        header.set_title(title)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(main_box)
        weekly_grid = Gtk.Grid(column_spacing=10, row_spacing=10, vexpand=True)
        weekly_grid.set_column_homogeneous(True)
        
        self.open_popups["weekly"] = {"window": win, "grid": weekly_grid}
        win.connect("destroy", lambda w: self.open_popups.pop("weekly", None))

        self.populate_weekly_grid(weekly_grid, selected_date, win)
        main_box.pack_start(weekly_grid, True, True, 0)
        win.show_all()

    def populate_weekly_grid(self, grid, selected_date, parent_win):
        for child in grid.get_children():
            grid.remove(child)
        start_of_week = selected_date - timedelta(days=selected_date.weekday())
        day_names = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
        for i, name in enumerate(day_names):
            lbl = Gtk.Label(xalign=0.5, margin_bottom=5)
            lbl.set_markup(f"<b>{name}</b>")
            grid.attach(lbl, i, 0, 1, 1)
        for i in range(7):
            current_day = start_of_week + timedelta(days=i)
            date_str = current_day.strftime("%Y-%m-%d")
            day_cell_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin=2)
            day_cell_container.get_style_context().add_class("not-list-frame")
            day_cell_container.set_vexpand(True)
            lbl_day = Gtk.Label(xalign=0, margin=4)
            lbl_day.set_markup(f"<b>{current_day.day}</b>")
            day_cell_container.pack_start(lbl_day, False, False, 0)
            day_scroll = Gtk.ScrolledWindow()
            day_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_left=4, margin_right=4, margin_bottom=4)
            day_scroll.add(notes_box)
            day_cell_container.pack_start(day_scroll, True, True, 0)
            day_notes = [n for n in self.notes if n['date'] == date_str]
            all_alarms = self.load_all_alarms()
            for note in day_notes:
                btn_note = Gtk.Button()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                btn_note.add(hbox)
                lbl_note = Gtk.Label(label=note['title'], xalign=0)
                lbl_note.set_line_wrap(True)
                hbox.pack_start(lbl_note, True, True, 0)
                if note.get('id') in all_alarms:
                    image_path = self._get_themed_icon_path("alarm_filled.png")
                    if os.path.exists(image_path):
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(image_path, 16, 16)
                        img = Gtk.Image.new_from_pixbuf(pixbuf)
                        img.set_valign(Gtk.Align.CENTER)
                        hbox.pack_end(img, False, False, 0)
                btn_note.connect("clicked", lambda w, n=note, p=parent_win: self.edit_note_popup(n, parent_window=p))
                notes_box.pack_start(btn_note, False, False, 0)
            grid.attach(day_cell_container, i, 1, 1, 1)
        grid.show_all()

    def monthly_view_popup(self, widget):
        win = Gtk.Window(default_width=1000, default_height=800)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        win.set_titlebar(header)
        win.set_resizable(True)
        year, month, day = self.calendar.get_date()
        selected_date = datetime(year, month + 1, day)
        title = f"{selected_date.strftime('%Y %B')} - {_('Monthly View')}"
        header.set_title(title)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        win.add(main_box)
        monthly_grid = Gtk.Grid(column_spacing=5, row_spacing=5, vexpand=True, hexpand=True)
        monthly_grid.set_column_homogeneous(True)
        
        self.open_popups["monthly"] = {"window": win, "grid": monthly_grid}
        win.connect("destroy", lambda w: self.open_popups.pop("monthly", None))

        self.populate_monthly_grid(monthly_grid, selected_date, win)
        main_box.pack_start(monthly_grid, True, True, 0)
        win.show_all()

    def populate_monthly_grid(self, grid, selected_date, parent_win):
        all_alarms = self.load_all_alarms()
        for child in grid.get_children():
            grid.remove(child)
        first_day_of_month = selected_date.replace(day=1)
        start_weekday = first_day_of_month.weekday() 
        num_days_in_month = calendar.monthrange(selected_date.year, selected_date.month)[1]
        day_names = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
        for i, name in enumerate(day_names):
            lbl = Gtk.Label(xalign=0.5, margin_bottom=5)
            lbl.set_markup(f"<b>{name}</b>")
            grid.attach(lbl, i, 0, 1, 1)
        current_day_number = 1
        for row in range(1, 7):
            for col in range(7):
                if row == 1 and col < start_weekday:
                    continue
                if current_day_number > num_days_in_month:
                    break
                current_day_date = first_day_of_month.replace(day=current_day_number)
                date_str = current_day_date.strftime("%Y-%m-%d")
                day_cell_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin=2)
                day_cell_container.get_style_context().add_class("not-list-frame")
                day_cell_container.set_vexpand(True)
                lbl_day = Gtk.Label(xalign=0, margin=4)
                lbl_day.set_markup(f"<b>{current_day_number}</b>")
                day_cell_container.pack_start(lbl_day, False, False, 0)
                day_scroll = Gtk.ScrolledWindow()
                day_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_left=4, margin_right=4, margin_bottom=4)
                day_scroll.add(notes_box)
                day_cell_container.pack_start(day_scroll, True, True, 0)
                day_notes = [n for n in self.notes if n['date'] == date_str]
                for note in day_notes:
                    btn_note = Gtk.Button()
                    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                    btn_note.add(hbox)
                    lbl_note = Gtk.Label(label=note['title'], xalign=0)
                    lbl_note.set_line_wrap(True)
                    hbox.pack_start(lbl_note, True, True, 0)
                    if note.get('id') in all_alarms:
                        image_path = self._get_themed_icon_path("alarm_filled.png")
                        if os.path.exists(image_path):
                            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(image_path, 16, 16)
                            img = Gtk.Image.new_from_pixbuf(pixbuf)
                            img.set_valign(Gtk.Align.CENTER)
                            hbox.pack_end(img, False, False, 0)
                    btn_note.connect("clicked", lambda w, n=note, p=parent_win: self.edit_note_popup(n, parent_window=p))
                    notes_box.pack_start(btn_note, False, False, 0)
                grid.attach(day_cell_container, col, row, 1, 1)
                current_day_number += 1
            if current_day_number > num_days_in_month:
                break
        grid.show_all()

    def fixed_note_popup(self, widget, note_data=None):
        win_title = _("Edit Fixed Note/Reminder") if note_data else _("Add New Fixed Note/Reminder")
        win = Gtk.Window(title=win_title, transient_for=self, modal=True)
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=15)
        win.add(main_vbox)
        main_vbox.pack_start(Gtk.Label(label=_("Title:"), xalign=0), False, False, 0)
        entry_title = Gtk.Entry()
        main_vbox.pack_start(entry_title, False, False, 0)
        main_vbox.pack_start(Gtk.Label(label=_("Description (Optional):"), xalign=0), False, False, 0)
        textview_content = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        scroll = Gtk.ScrolledWindow(min_content_height=80)
        scroll.add(textview_content)
        main_vbox.pack_start(scroll, True, True, 0)
        
        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_vbox.pack_start(time_box, False, False, 0)

        entry_time_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        entry_time_vbox.pack_start(Gtk.Label(label=_("Event Time (e.g., 09:00):"), xalign=0), False, False, 0)
        entry_time = Gtk.Entry(placeholder_text="HH:MM")
        entry_time_vbox.pack_start(entry_time, False, False, 0)
        time_box.pack_start(entry_time_vbox, True, True, 0)
        
        check_alarm = Gtk.CheckButton(label=_("Enable Alarm"))
        check_alarm.set_valign(Gtk.Align.END)
        time_box.pack_start(check_alarm, False, False, 0)

        main_vbox.pack_start(Gtk.Label(label=_("Repeat Type:"), xalign=0, margin_top=10), False, False, 0)
        combo_repeat_type = Gtk.ComboBoxText()
        combo_repeat_type.append("weekly", _("Weekly (On selected days)"))
        combo_repeat_type.append("monthly", _("Monthly (On a specific day of the month)"))
        combo_repeat_type.append("yearly", _("Yearly (On a specific date)"))
        main_vbox.pack_start(combo_repeat_type, False, False, 0)

        repeat_stack = Gtk.Stack()
        repeat_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        
        days_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, margin_top=5)
        day_names = [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]
        weekly_check_buttons = [Gtk.CheckButton(label=name) for name in day_names]
        for check in weekly_check_buttons:
            days_box.pack_start(check, True, True, 0)
        repeat_stack.add_titled(days_box, "weekly", _("Weekly"))

        monthly_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, margin_top=5)
        monthly_box.pack_start(Gtk.Label(label=_("Day of the Month:")), False, False, 0)
        adj_day = Gtk.Adjustment(value=1, lower=1, upper=31, step_increment=1)
        monthly_spin_day = Gtk.SpinButton(adjustment=adj_day)
        monthly_box.pack_start(monthly_spin_day, False, False, 0)
        repeat_stack.add_titled(monthly_box, "monthly", _("Monthly"))

        yearly_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, margin_top=5)
        yearly_box.pack_start(Gtk.Label(label=_("Date:")), False, False, 0)
        adj_year_day = Gtk.Adjustment(value=1, lower=1, upper=31, step_increment=1)
        yearly_spin_day = Gtk.SpinButton(adjustment=adj_year_day)
        yearly_box.pack_start(yearly_spin_day, False, False, 0)
        yearly_combo_month = Gtk.ComboBoxText()
        months = [
            _("January"), _("February"), _("March"), _("April"), _("May"), _("June"), 
            _("July"), _("August"), _("September"), _("October"), _("November"), _("December")
        ]
        for i, month_name in enumerate(months):
            yearly_combo_month.append(str(i + 1), month_name)
        yearly_box.pack_start(yearly_combo_month, True, True, 0)
        repeat_stack.add_titled(yearly_box, "yearly", _("Yearly"))
        main_vbox.pack_start(repeat_stack, False, False, 0)

        def on_repeat_type_changed(combo):
            active_id = combo.get_active_id()
            if active_id:
                repeat_stack.set_visible_child_name(active_id)
        
        combo_repeat_type.connect("changed", on_repeat_type_changed)
        
        def on_time_entry_changed(entry):
            is_sensitive = bool(entry.get_text())
            combo_repeat_type.set_sensitive(is_sensitive)
            repeat_stack.set_sensitive(is_sensitive)
            check_alarm.set_sensitive(is_sensitive)
            if not is_sensitive:
                check_alarm.set_active(False)

        entry_time.connect("changed", on_time_entry_changed)

        if note_data:
            entry_title.set_text(note_data.get('title', ''))
            textview_content.get_buffer().set_text(note_data.get('content', ''))
            entry_time.set_text(note_data.get('event_time', ''))
            check_alarm.set_active(note_data.get('alarm_enabled', 0) == 1)
            on_time_entry_changed(entry_time)
            repeat_type = note_data.get('repeat_type', 'weekly')
            combo_repeat_type.set_active_id(repeat_type)
            if repeat_type == 'weekly':
                selected_days = note_data.get('alarm_days', '').split(',')
                for i, check in enumerate(weekly_check_buttons):
                    if str(i) in selected_days:
                        check.set_active(True)
            elif repeat_type == 'monthly':
                monthly_spin_day.set_value(note_data.get('repeat_day', 1))
            elif repeat_type == 'yearly':
                yearly_spin_day.set_value(note_data.get('repeat_day', 1))
                yearly_combo_month.set_active_id(str(note_data.get('repeat_month', 1)))
        else:
            combo_repeat_type.set_active_id('weekly')
            on_time_entry_changed(entry_time)

        btn_box = Gtk.Box(spacing=10, margin_top=15)
        main_vbox.pack_end(btn_box, False, False, 0)
        
        btn_save = Gtk.Button(label=_("Save"))
        
        all_controls = {
            'title': entry_title, 'content': textview_content, 'time': entry_time,
            'alarm_enabled': check_alarm, 'repeat_type': combo_repeat_type, 
            'weekly_checks': weekly_check_buttons, 'monthly_day': monthly_spin_day, 
            'yearly_day': yearly_spin_day, 'yearly_month': yearly_combo_month
        }
        btn_save.connect("clicked", self.on_fixed_note_save, all_controls, note_data, win)

        if note_data:
            btn_delete = Gtk.Button(label=_("Delete"))
            btn_box.pack_start(btn_delete, True, True, 0)
            btn_delete.connect("clicked", self.on_fixed_note_delete, note_data['id'], win)

        btn_box.pack_start(btn_save, True, True, 0)
        win.show_all()

    def on_fixed_note_save(self, widget, controls, existing_note, window):
        title = controls['title'].get_text()
        if not title: return
        buffer = controls['content'].get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        event_time = controls['time'].get_text()
        alarm_enabled = 1 if controls['alarm_enabled'].get_active() else 0
        repeat_type = controls['repeat_type'].get_active_id()
        
        note_dict = {'title': title, 'content': content, 'event_time': event_time, 
                     'alarm_enabled': alarm_enabled, 'repeat_type': repeat_type, 
                     'alarm_days': '', 'repeat_day': None, 'repeat_month': None}

        if repeat_type == 'weekly':
            selected_days = [str(i) for i, check in enumerate(controls['weekly_checks']) if check.get_active()]
            note_dict['alarm_days'] = ",".join(selected_days)
        elif repeat_type == 'monthly':
            note_dict['repeat_day'] = int(controls['monthly_day'].get_value())
        elif repeat_type == 'yearly':
            note_dict['repeat_day'] = int(controls['yearly_day'].get_value())
            note_dict['repeat_month'] = int(controls['yearly_month'].get_active_id())
            
        if existing_note:
            note_dict['id'] = existing_note['id']
            
        self.save_fixed_note_db(note_dict)
        self.refresh_fixed_notes_list()
        window.destroy()
    
    def on_fixed_note_delete(self, widget, note_id, window):
        dialog = Gtk.MessageDialog(transient_for=window, modal=True, message_type=Gtk.MessageType.QUESTION, text=_("Are you sure you want to delete this fixed note?"))
        dialog.add_button(_("No"), Gtk.ResponseType.NO)
        dialog.add_button(_("Yes"), Gtk.ResponseType.YES)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fixed_notes WHERE id=?", (note_id,))
            conn.commit()
            conn.close()
            self.load_fixed_notes()
            self.refresh_fixed_notes_list()
            window.destroy()

    def save_fixed_note_db(self, note_dict):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if 'id' in note_dict:
            cursor.execute("""UPDATE fixed_notes SET title=?, content=?, event_time=?, alarm_enabled=?, 
                              alarm_days=?, repeat_type=?, repeat_day=?, repeat_month=? WHERE id=?""", 
                           (note_dict['title'], note_dict['content'], note_dict['event_time'], 
                            note_dict['alarm_enabled'], note_dict['alarm_days'], note_dict['repeat_type'], 
                            note_dict['repeat_day'], note_dict['repeat_month'], note_dict['id']))
        else:
            cursor.execute("""INSERT INTO fixed_notes (title, content, event_time, alarm_enabled, 
                                                     alarm_days, repeat_type, repeat_day, repeat_month) 
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", 
                           (note_dict['title'], note_dict['content'], note_dict['event_time'],
                            note_dict['alarm_enabled'], note_dict['alarm_days'], note_dict['repeat_type'],
                            note_dict['repeat_day'], note_dict['repeat_month']))
        conn.commit()
        conn.close()
        self.load_fixed_notes()

    def refresh_fixed_notes_list(self, filtered_notes=None):
        for child in self.fixed_notes_listbox.get_children():
            self.fixed_notes_listbox.remove(child)

        notes_to_display = self.fixed_notes if filtered_notes is None else filtered_notes

        day_map = {"0": _("Mon"), "1": _("Tue"), "2": _("Wed"), "3": _("Thu"), "4": _("Fri"), "5": _("Sat"), "6": _("Sun")}
        month_map = {1: _("Jan"), 2: _("Feb"), 3: _("Mar"), 4: _("Apr"), 5: _("May"), 6: _("Jun"), 7: _("Jul"), 8: _("Aug"), 9: _("Sep"), 10: _("Oct"), 11: _("Nov"), 12: _("Dec")}
        
        if not notes_to_display:
            if filtered_notes is not None:
                self.fixed_notes_listbox.add(Gtk.Label(label=_("No search results found.")))
        else:
            for note_data in notes_to_display:
                row = Gtk.ListBoxRow()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin=5)
                row.add(hbox)
                main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                hbox.pack_start(main_content_box, True, True, 0)
                
                title_label = Gtk.Label(xalign=0)
                escaped_title = GLib.markup_escape_text(note_data.get('title', ''))
                title_label.set_markup(f"<b>{escaped_title}</b>")
                main_content_box.pack_start(title_label, False, False, 0)

                details_text = ""
                rule_part = ""
                event_time = note_data.get('event_time')
                alarm_enabled = note_data.get('alarm_enabled') == 1
                repeat_type = note_data.get('repeat_type') or 'weekly'
                
                if repeat_type == 'weekly':
                    days_str = note_data.get('alarm_days', '')
                    if days_str:
                        rule_part = _("Every {days}").format(days=", ".join([day_map[d] for d in days_str.split(',') if d]))
                elif repeat_type == 'monthly':
                    if note_data.get('repeat_day'):
                        rule_part = _("On the {day}. of every month").format(day=note_data.get('repeat_day'))
                elif repeat_type == 'yearly':
                    if note_data.get('repeat_day') and note_data.get('repeat_month'):
                        month_name = month_map.get(note_data.get('repeat_month'), '?')
                        rule_part = _("Every year on {month} {day}").format(month=month_name, day=note_data.get('repeat_day'))

                prefix = _("Alarm:") if alarm_enabled and event_time else _("Event:")
                
                if event_time and rule_part:
                    details_text = f"{prefix} {event_time} ({rule_part})"
                elif event_time:
                    details_text = _("{prefix} {time} (Does not repeat)").format(prefix=prefix, time=event_time)
                elif rule_part:
                    details_text = _("Reminder: ({rule})").format(rule=rule_part)
                else:
                    details_text = _("Undated, non-repeating note")
                
                details_label = Gtk.Label(label=details_text, xalign=0)
                main_content_box.pack_start(details_label, False, False, 0)

                switch = Gtk.Switch()
                switch.set_valign(Gtk.Align.CENTER)
                switch.set_active(alarm_enabled)
                switch.set_sensitive(bool(event_time))
                switch.connect("notify::active", self.on_fixed_note_switch_toggled, note_data.get('id'))
                hbox.pack_end(switch, False, False, 0)
                
                edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
                edit_button.set_valign(Gtk.Align.CENTER)
                edit_button.connect("clicked", self.fixed_note_popup, note_data)
                hbox.pack_end(edit_button, False, False, 0)

                self.fixed_notes_listbox.add(row)
        
        self.fixed_notes_listbox.show_all()

    def on_fixed_note_switch_toggled(self, switch, gparam, note_id):
        is_alarm_enabled = 1 if switch.get_active() else 0
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE fixed_notes SET alarm_enabled=? WHERE id=?", (is_alarm_enabled, note_id))
        conn.commit()
        conn.close()
        self.load_fixed_notes()
        self.refresh_fixed_notes_list()

    def backup_popup(self, widget):
        self.popover.hide()
        dialog = Gtk.FileChooserDialog(title=_("Backup Database"), parent=self, action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        current_date = datetime.now().strftime("%Y-%m-%d")
        dialog.set_current_name(f"dailynote_backup_{current_date}.db")
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            destination_path = dialog.get_filename()
            try:
                shutil.copy(DB_NAME, destination_path)
                success_text = _("Backup Successful!\nFile saved to:\n{path}").format(path=destination_path)
                success_dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=success_text)
                success_dialog.run()
                success_dialog.destroy()
            except Exception as e:
                error_text = _("An error occurred during backup:\n{error}").format(error=e)
                error_dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=error_text)
                error_dialog.run()
                error_dialog.destroy()
        dialog.destroy()

    def restore_popup(self, widget):
        self.popover.hide()
        warning_dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING, text=_("WARNING!"))
        warning_dialog.add_button(_("No"), Gtk.ResponseType.NO)
        warning_dialog.add_button(_("Yes"), Gtk.ResponseType.YES)
        warning_dialog.format_secondary_text(_("This action will delete all your current notes and replace them with the selected backup file. Are you sure you want to continue?"))
        response = warning_dialog.run()
        warning_dialog.destroy()
        if response == Gtk.ResponseType.YES:
            dialog = Gtk.FileChooserDialog(title=_("Select Backup File"), parent=self, action=Gtk.FileChooserAction.OPEN)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            file_filter = Gtk.FileFilter()
            file_filter.set_name(_("Database Files"))
            file_filter.add_pattern("*.db")
            dialog.add_filter(file_filter)
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                backup_path = dialog.get_filename()
                try:
                    shutil.copy(backup_path, DB_NAME)
                    success_dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=_("Restore Successful!"), secondary_text=_("Please restart the application for the changes to take effect."))
                    success_dialog.run()
                    success_dialog.destroy()
                    self.cleanup_and_quit()
                except Exception as e:
                    error_text = _("An error occurred during restore:\n{error}").format(error=e)
                    error_dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=error_text)
                    error_dialog.run()
                    error_dialog.destroy()
            dialog.destroy()
    
    def on_font_select_clicked(self, widget):
        self.popover.hide()
        dialog = Gtk.FontChooserDialog(title=_("Select Font"), transient_for=self, modal=True)
        dialog.set_font(self.current_font_description)
        if dialog.run() == Gtk.ResponseType.OK:
            new_font_description = dialog.get_font()
            if new_font_description:
                self.current_font_description = new_font_description
                self.save_setting_db('font_description', new_font_description)
                self._load_css()
        dialog.destroy()

    def cleanup_and_quit(self, *args):
        if hasattr(self, 'indicator_icon_path') and os.path.exists(self.indicator_icon_path):
            try:
                os.remove(self.indicator_icon_path)
            except OSError as e:
                print(f"Error while deleting temporary file: {e}")
        Notify.uninit()
        
        app = self.get_application()
        if app:
            app.quit()

class Application(Gtk.Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, application_id="com.github.kullaniciadi.dailynote",
                         flags=Gio.ApplicationFlags.FLAGS_NONE, **kwargs)
        self.window = None
        self.is_startup_launch = '--startup' in sys.argv
        
        self.add_main_option(
            "startup",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Start the application hidden in the background.",
            None
        )

    def do_activate(self):
        if not self.window:
            self.window = NoteApplication(self)

        if self.is_startup_launch:
            self.is_startup_launch = False
            return

        self.window.show_all()
        self.window.present()

    def do_shutdown(self):
        Gtk.Application.do_shutdown(self)

if __name__ == '__main__':
    app = Application()
    exit_status = app.run(sys.argv)
    sys.exit(exit_status)
