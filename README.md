DailyNote - Daily Note and Reminder Application
DailyNote is a GTK-based desktop application that allows users to take daily notes, create fixed reminders, and track real-time weather information.

Features
Add, edit, and delete notes by date.

Recurring (weekly, monthly, yearly) reminders and alarms.

Real-time and 5-day detailed weather forecast.

Database backup and restore.

Change the application-wide font and size.

Window resizing and transparency adjustment.

1. Install Dependencies
Next, install the necessary dependencies for your Linux distribution. The installation script will check for these, but it's best to install them beforehand.

For Debian / Ubuntu based systems:

```bash
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-requests
```

For Arch Linux based systems:

```bash
sudo pacman -S python-gobject gtk3 gst-plugins-base-libs libappindicator-gtk3 libnotify python-requests
```

For Fedora:

```bash
sudo dnf install python3-gobject gtk3 gstreamer1-plugins-base libappindicator-gtk3 libnotify python3-requests
```

2. Clone the Repository
First, download the project files to your computer using Git:

```bash
git clone https://github.com/Falldaemon/DailyNote-App.git
```

```bash
cd DailyNote-App
```

3. Run the Installation Script
Finally, run the make install command from within the project directory. This command does not require sudo.

```bash
make install
```

This will:

Check if all dependencies are met.

Compile translation files.

Copy the application files to ~/.local/share/dailynote.

Create an executable in ~/.local/bin.

Add an application shortcut to your desktop menu and enable autostart on login.

Usage
After installation, you can run the application in two ways:

From your desktop's application menu (search for "DailyNote").

By typing dailynote in your terminal.

Uninstallation
To remove the application from your system, navigate back to the project directory where you cloned it and run:

```bash
make uninstall
```

This will remove all files, shortcuts, and autostart entries created during installation.

---

### Troubleshooting

**"command not found" Error**

After installation, if you type `dailynote` in the terminal and get a "command not found" error, this is a common issue on some Linux systems.

* **Solution 1 (The Easy Way):** First, try logging out and logging back into your session, or simply restart your computer. On most modern systems, this will solve the problem automatically.

* **Solution 2 (The Manual Way):** If the first solution does not work, run the following command in your terminal to permanently add the installation path to your shell:
    ```bash
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    ```
    After running this command, you must **close the terminal and open a new one** for the change to take effect.
