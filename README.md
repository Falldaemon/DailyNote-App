# DailyNote - Daily Note and Reminder Application

DailyNote is a GTK-based desktop application that allows users to take daily notes, create fixed reminders, and track real-time weather information.

## Features

* Add, edit, and delete notes by date.
* Recurring (weekly, monthly, yearly) reminders and alarms.
* Real-time and 5-day detailed weather forecast.
* Database backup and restore.
* Change the application-wide font and size.
* Window resizing and transparency adjustment.

## Installation

To run the application, you need to install the necessary dependencies for your Linux distribution from the official repositories.

### For Debian / Ubuntu based systems:

```bash
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gir1.2-appindicator3-0.1 gir1.2-notify-0.7 python3-requests

### For  Arch Linux based systems:

```bash
sudo pacman -S python-gobject gtk3 gst-plugins-base-libs libappindicator-gtk3 libnotify python-requests


### For  Arch Linux based systems:

```bash
sudo dnf install python3-gobject gtk3 gstreamer1-plugins-base libappindicator-gtk3 libnotify python3-requests



