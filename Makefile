# ==============================================================================
# Makefile for the DailyNote Application
# ==============================================================================

# Variables
APP_NAME = dailynote
PREFIX ?= $(HOME)/.local
BIN_DIR = $(PREFIX)/bin
SHARE_DIR = $(PREFIX)/share/$(APP_NAME)
APPLICATIONS_DIR = $(PREFIX)/share/applications
AUTOSTART_DIR = $(HOME)/.config/autostart
LOCALE_INSTALL_DIR = $(PREFIX)/share/locale

# Use shell to get the python3 command path
PYTHON := $(shell command -v python3)

# Targets

all: mo

# ==============================================================================
# Primary User Targets: check, install, uninstall
# ==============================================================================

# Checks for all required system and Python dependencies directly.
# This logic is self-contained and does not require an external script.
check:
	@echo "--- Checking for required dependencies..."
	@# Check for essential system commands
	@command -v $(PYTHON) >/dev/null || \
		(echo "❌ HATA: python3 komutu bulunamadı. Lütfen Python 3'ü yükleyin." && exit 1)
	@command -v msgfmt >/dev/null || \
		(echo "❌ HATA: msgfmt komutu (gettext paketinden) bulunamadı. Lütfen gettext paketini yükleyin." && exit 1)

	@# Check for required Python modules
	@$(PYTHON) -c "import requests" >/dev/null 2>&1 || \
		(echo "❌ HATA: Python 'requests' modülü yüklü değil." && \
		 echo "   Lütfen dağıtımınızın paket yöneticisi ile yükleyin:" && \
		 echo "   - Debian/Ubuntu: sudo apt-get install python3-requests" && \
		 echo "   - Arch Linux:    sudo pacman -S python-requests" && \
		 echo "   - Fedora:        sudo dnf install python3-requests" && exit 1)

	@$(PYTHON) -c "import gi; gi.require_version('Gtk', '3.0')" >/dev/null 2>&1 || \
		(echo "❌ HATA: Python GObject/GTK3 kütüphaneleri yüklü değil veya yanlış yapılandırılmış." && \
		 echo "   Lütfen dağıtımınız için gerekli paketleri yükleyin (örn: python3-gi, gir1.2-gtk-3.0)." && exit 1)

	@echo "✅ Tüm bağımlılıklar kurulu."

# Installs the application. Depends on 'check' to run dependency validation first.
install: check mo
	@echo "--- DailyNote kuruluyor..."
	# Create necessary directories
	mkdir -p $(BIN_DIR)
	mkdir -p $(SHARE_DIR)/icons/dark
	mkdir -p $(SHARE_DIR)/icons/light
	mkdir -p $(SHARE_DIR)/alarms
	mkdir -p $(APPLICATIONS_DIR)
	mkdir -p $(AUTOSTART_DIR)

	# Copy application files
	cp -r icons/dark/* $(SHARE_DIR)/icons/dark/
	cp -r icons/light/* $(SHARE_DIR)/icons/light/
	cp -r alarms/* $(SHARE_DIR)/alarms/
	cp DailyNote.py $(SHARE_DIR)/

	# Copy compiled translation files
	@for lang in locale/*/; do \
		if [ -f "$$lang/LC_MESSAGES/$(APP_NAME).mo" ]; then \
			lang_code=$$(basename $$lang); \
			mkdir -p $(LOCALE_INSTALL_DIR)/$$lang_code/LC_MESSAGES; \
			cp $$lang/LC_MESSAGES/$(APP_NAME).mo $(LOCALE_INSTALL_DIR)/$$lang_code/LC_MESSAGES/$(APP_NAME).mo; \
		fi \
	done

	# Create the executable launcher script
	@echo '#!/bin/sh' > $(BIN_DIR)/$(APP_NAME)
	@echo '$(PYTHON) $(SHARE_DIR)/DailyNote.py "$$@"' >> $(BIN_DIR)/$(APP_NAME)
	chmod +x $(BIN_DIR)/$(APP_NAME)

	# Generate and install .desktop files
	sed 's|@@EXEC@@|$(BIN_DIR)/$(APP_NAME)|g; s|@@ICON@@|$(SHARE_DIR)/icons/icon.png|g' dailynote.desktop.in > $(APPLICATIONS_DIR)/$(APP_NAME).desktop
	sed 's|@@EXEC@@|$(BIN_DIR)/$(APP_NAME) --startup|g; s|@@ICON@@|$(SHARE_DIR)/icons/icon.png|g' dailynote-autostart.desktop.in > $(AUTOSTART_DIR)/$(APP_NAME).desktop

	@echo "🎉 Kurulum tamamlandı!"
	@echo "   Artık 'dailynote' komutunu terminalden çalıştırabilir veya uygulama menünüzde bulabilirsiniz."

# Uninstalls the application
uninstall:
	@echo "--- DailyNote kaldırılıyor..."
	rm -f $(BIN_DIR)/$(APP_NAME)
	rm -rf $(SHARE_DIR)
	rm -f $(APPLICATIONS_DIR)/$(APP_NAME).desktop
	rm -f $(AUTOSTART_DIR)/$(APP_NAME).desktop
	# TODO: Also remove installed translation files
	@echo "✅ Kaldırma işlemi tamamlandı."


# ==============================================================================
# Translation Management Targets
# ==============================================================================

# Compiles .po files into binary .mo files
mo:
	@echo "--- Çeviri dosyaları derleniyor..."
	@for lang in locale/*/; do \
		if [ -f "$$lang/LC_MESSAGES/$(APP_NAME).po" ]; then \
			msgfmt $$lang/LC_MESSAGES/$(APP_NAME).po -o $$lang/LC_MESSAGES/$(APP_NAME).mo; \
		fi \
	done

# Updates the translation template (.pot file) from the source code
pot:
	xgettext --from-code=UTF-8 -o locale/$(APP_NAME).pot DailyNote.py

# Updates existing .po files with new strings from the .pot template
po: pot
	@for lang in locale/*/; do \
		if [ -f "$$lang/LC_MESSAGES/$(APP_NAME).po" ]; then \
			msgmerge --update --backup=none $$lang/LC_MESSAGES/$(APP_NAME).po locale/$(APP_NAME).pot; \
		fi \
	done

.PHONY: all check mo install uninstall pot po
