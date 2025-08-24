# --------------------------------------------------------------------------------
# Battery Management System (BMS) Script Documentation
# --------------------------------------------------------------------------------
#
# **Script Name:** bms.py
# **Version:** 1.3 (As of August 24, 2025) - Updated with robust RRD fetching to handle empty/malformed XML, removed spinner for display stability, ASCII charts in TUI, and web charts via Chart.js.
# **Author:** [Your Name or Original Developer] - Built for Raspberry Pi-based battery monitoring and balancing.
# **Purpose:** This script acts as a complete Battery Management System (BMS) for a 3s8p battery configuration (3 series banks, each with 8 parallel cells). It monitors temperatures and voltages, balances charge between banks, detects issues, logs events, sends alerts, and provides user interfaces via terminal (TUI) and web dashboard. Now includes time-series logging of voltages and median temperatures using RRDTool for persistent storage, ASCII line charts in the TUI for visual history, and interactive charts in the web dashboard using Chart.js.
#
# **Detailed Overview:**
# - **Temperature Monitoring:** Connects to 24 NTC thermistors (8 per bank) via a Lantronix EDS4100 device using Modbus TCP. Reads raw values, applies calibration offsets (calculated at startup or loaded from file), and checks for anomalies like high/low temperatures, deviations from bank medians, rapid rises, lags in group tracking, or disconnections.
# - Calibration: On first valid read (all sensors > valid_min), computes median temperature and offsets for each sensor to normalize readings. Saves to 'offsets.txt' for future runs.
# - Anomalies Checked:
# - Invalid/Disconnected: Reading <= valid_min (e.g., 0.0°C).
# - High: > high_threshold (e.g., 42.0°C).
# - Low: < low_threshold (e.g., 0.0°C).
# - Deviation: Absolute > abs_deviation_threshold (e.g., 2.0°C) or relative > deviation_threshold (e.g., 10%) from bank median.
# - Abnormal Rise: Increase > rise_threshold (e.g., 2.0°C) since last poll.
# - Group Lag: Change differs from bank median change by > disconnection_lag_threshold (e.g., 0.5°C).
# - Sudden Disconnection: Was valid, now invalid.
# - **Voltage Monitoring & Balancing:** Uses ADS1115 ADC over I2C to measure voltages of 3 banks. Balances if difference > VoltageDifferenceToBalance (e.g., 0.1V) by connecting high to low bank via relays and DC-DC converter.
# - Heating Mode: If any temperature < 10°C, balances regardless of voltage difference to generate heat.
# - Safety: Skips balancing if alerts active (e.g., anomalies). Rests for BalanceRestPeriodSeconds (e.g., 60s) after balancing.
# - Voltage Checks: Alerts if < LowVoltageThresholdPerBattery (e.g., 18.5V), > HighVoltageThresholdPerBattery (e.g., 21.0V), or zero.
# - **Alerts & Notifications:** Logs to 'battery_monitor.log'. Activates alarm relay on issues. Sends throttled emails (e.g., every 3600s) via SMTP.
# - **Watchdog:** If enabled, pets hardware watchdog during long operations to prevent resets. Uses /dev/watchdog with 30s timeout.
# - **User Interfaces:**
# - **TUI (Terminal UI):** Uses curses for real-time display: ASCII art batteries with voltages/temps, alerts, balancing progress bar/animation, last 20 events. Now includes ASCII line charts for voltage history per bank and median temperature, placed in the top-right section for visualization of trends over time.
# - **Web Dashboard:** HTTP server on port 8080 (configurable). Shows voltages, temps, alerts, balancing status. Supports API for status/balance/history. Optional auth/CORS. Now includes interactive time-series charts using Chart.js for voltages per bank and median temperature, placed at the top of the page after the header for easy viewing.
# - **Time-Series Logging:** Uses RRDTool for persistent storage of bank voltages and overall median temperature. Data is updated every poll interval (e.g., 10s), but RRD is configured with 1min steps for aggregation. History is limited to ~480 entries (e.g., 8 hours). Fetch functions retrieve data for TUI and web rendering.
# - **Startup Self-Test:** Validates config, hardware connections (I2C/Modbus), initial reads, balancer (tests all pairs for voltage changes).
# - Retries on failure after 2min. Alerts and activates alarm if fails.
# - **Error Handling:** Retries reads (exponential backoff), handles missing hardware (test mode), logs tracebacks, graceful shutdown on Ctrl+C.
# - **Configuration:** From 'battery_monitor.ini'. Defaults if missing keys. See INI documentation below.
# - **Logging:** Configurable level (e.g., INFO). Timestamps events.
# - **Shutdown:** Cleans GPIO, web server, watchdog on exit.
#
# **Key Features Explained for Non-Programmers:**
# - Imagine this script as a vigilant guardian for your battery pack. It constantly checks the "health" (temperature and voltage) of each part of the battery.
# - Temperatures: Like checking body temperature with 24 thermometers. If one is too hot/cold or acting weird, it raises an alarm.
# - Voltages: Measures "energy level" in each of 3 groups. If one has more energy than another, it transfers some to balance them, like pouring water between buckets.
# - Heating: In cold weather (<10°C), it deliberately transfers energy to create warmth inside the battery cabinet.
# - Alerts: If something's wrong, it logs it, turns on a buzzer/light (alarm relay), and emails you (but not too often to avoid spam).
# - Interfaces: Terminal shows a fancy text-based dashboard with ASCII charts for trends; web page lets you view from browser with interactive charts.
# - Startup Check: Like a self-diagnostic when your car starts – ensures everything's connected and working before running.
# - Time-Series: Tracks history of voltages and temps, shows trends in charts to spot patterns over time.
#
# **How It Works (Step-by-Step for Non-Programmers):**
# 1. **Start:** Loads settings from INI file (like a recipe book).
# 2. **Setup:** Connects to hardware (sensors, relays) – if missing, runs in "pretend" mode. Creates/loads RRD database for history.
# 3. **Self-Test:** Checks if config makes sense, hardware responds, sensors give good readings, balancing actually changes voltages. If fail, alerts and retries.
# 4. **Main Loop (Repeats Forever):**
# - Read temperatures from all 24 sensors.
# - Calibrate them (adjust based on startup values for accuracy).
# - Check for temperature problems (too hot, too cold, etc.).
# - Read voltages from 3 banks.
# - Check for voltage problems (too high, too low, zero).
# - Update RRD database with voltages and median temp.
# - If cold (<10°C anywhere), balance to heat up.
# - Else, if voltages differ too much, balance to equalize.
# - Fetch history from RRD for charts.
# - Update terminal (with ASCII charts)/web displays (with Chart.js).
# - Log events, send emails if issues.
# - Pet watchdog (tell hardware "I'm alive" to avoid auto-reset).
# - Wait a bit (e.g., 10s), repeat.
# 5. **Balancing Process:** Connects high to low bank with relays, turns on converter to transfer charge, shows progress, turns off after time.
# 6. **Shutdown:** If you press Ctrl+C, cleans up connections safely.
#
# **Updated Logic Flow Diagram (ASCII - More Detailed):**
#
# +-------------------------+
# | Load Config from INI |
# | (Read settings file) |
# +-------------------------+
# |
# v
# +-------------------------+
# | Setup Hardware |
# | (I2C bus, GPIO pins) |
# | Create/Load RRD DB |
# +-------------------------+
# |
# v
# +-------------------------+
# | Startup Self-Test |
# | (Config valid? |
# | Hardware connected? |
# | Initial reads OK? |
# | Balancer works?) |
# | If fail: Alert, Retry |
# +-------------------------+
# |
# v
# +-------------------------+ <---------------------+
# | Main Loop (Repeat) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Read Temps (Modbus) | |
# | (24 sensors via TCP) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Calibrate Temps | |
# | (Apply offsets if set) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Check Temp Issues | |
# | (High/Low/Deviation/ | |
# | Rise/Lag/Disconnect) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Read Voltages (ADC) | |
# | (3 banks via I2C) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Check Voltage Issues | |
# | (High/Low/Zero) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Update RRD with Data | |
# | (Voltages, Median Temp)| |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | If Any Temp < 10°C: | |
# | Balance for Heating | |
# | Else If Volt Diff > Th: | |
# | Balance Normally | |
# | (High to Low Bank) | |
# | Skip if Alerts Active | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Fetch RRD History | |
# | (For Charts) | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Update TUI (Terminal) | |
# | & Web Dashboard | |
# | (Show status, alerts, | |
# | ASCII/Chart.js Charts)| |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Log Events, Send Email | |
# | if Issues & Throttled | |
# +-------------------------+ |
# | |
# v |
# +-------------------------+ |
# | Sleep (Poll Interval) | |
# | Pet Watchdog if Enabled| |
# +-------------------------+ |
# | |
# +----------------------------------------+
#
# **Dependencies (What the Script Needs to Run):**
# - **Python Version:** 3.11 or higher (core language for running the code).
# - **Hardware Libraries:** smbus (for I2C communication with sensors/relays), RPi.GPIO (for controlling Raspberry Pi pins). Install: sudo apt install python3-smbus python3-rpi.gpio.
# - **External Library:** art (for ASCII art in TUI). Install: pip install art.
# - **Time-Series Storage:** rrdtool (for RRD database). Install: sudo apt install rrdtool.
# - **Standard Python Libraries:** socket (networking), statistics (math like medians), time (timing/delays), configparser (read INI), logging (save logs), signal (handle shutdown), gc (memory cleanup), os (files), sys (exit), smtplib/email (emails), curses (TUI), threading (web server), json/http.server/urllib/base64 (web), traceback (errors), fcntl/struct (watchdog), subprocess (for rrdtool commands), xml.etree.ElementTree (for parsing RRD XML output).
# - **Hardware Requirements:** Raspberry Pi (any model, detects for watchdog), ADS1115 ADC (voltage), TCA9548A multiplexer (I2C channels), Relays (balancing), Lantronix EDS4100 (Modbus for temps), GPIO pins (e.g., 5 for DC-DC, 6 for alarm, 4 for fan).
# - **No Internet for Installs:** All libraries must be pre-installed; script can't download. For web charts, Chart.js is loaded via CDN (requires internet for dashboard users).
#
# **Installation Guide (Step-by-Step for Non-Programmers):**
# 1. **Install Python:** On Raspberry Pi, run in terminal: sudo apt update; sudo apt install python3.
# 2. **Install Hardware Libraries:** sudo apt install python3-smbus python3-rpi.gpio.
# 3. **Install Art Library:** pip install art (or sudo pip install art if needed).
# 4. **Install RRDTool for Time-Series:** sudo apt install rrdtool.
# 5. **Enable I2C:** Run sudo raspi-config, go to Interface Options > I2C > Enable, then reboot.
# 6. **Create/Edit INI File:** Make 'battery_monitor.ini' in same folder as script. Copy template below and fill in values (e.g., emails, IPs).
# 7. **Run Script:** sudo python bms.py (needs root for hardware access).
# 8. **View Web Dashboard:** Open browser to http://<your-pi-ip>:8080. Charts will load via Chart.js CDN.
# 9. **Logs:** Check 'battery_monitor.log' for details. Set LoggingLevel=DEBUG in INI for more info.
# 10. **RRD Database:** Created automatically as 'bms.rrd' on first run. No manual setup needed.
#
# **Notes & Troubleshooting:**
# - **Hardware Matching:** Ensure INI addresses/pins match your setup. Wrong IP/port = no temps.
# - **Email Setup:** Use Gmail app password (not regular password) for SMTP_Password.
# - **TUI Size:** Terminal should be wide (>80 columns) for full display, including charts.
# - **Test Mode:** If no hardware, script runs without crashing but warns.
# - **Security:** For web, enable auth_required=True and set strong username/password.
# - **Offsets File:** 'offsets.txt' stores calibration – delete to recalibrate.
# - **RRD Issues:** If rrdtool commands fail, check installation and permissions. Database 'bms.rrd' stores aggregated data; use rrdtool info bms.rrd for details.
# - **Common Errors:** I2C errors = check wiring/connections. Modbus errors = check Lantronix IP/port. RRD errors = ensure rrdtool installed and path correct.
# - **Performance:** Poll interval ~10s; balancing ~5s. Adjust in INI. Charts fetch from RRD (~480 entries) won't impact performance.
# - **Customization:** Edit thresholds in INI for your battery specs (e.g., Li-ion safe ranges). For longer history, adjust RRA in RRD creation.
#
# **battery_monitor.ini Documentation (With Comments - Copy This to Your File):**
# ; battery_monitor.ini - Configuration File for BMS Script
# ; Comments start with ; - Explain each setting for non-programmers.
# ; Sections in [Brackets], keys = values.
# ; Defaults used if missing.
#
# [Temp] ; Settings for temperature sensors.
# ip = 192.168.15.240 ; IP address of Lantronix device (like a network address for sensor box).
# modbus_port = 10001 ; Network port for sensor communication (like a door number).
# poll_interval = 10.0 ; Seconds between temperature checks (how often to read sensors).
# rise_threshold = 2.0 ; Max allowed temp increase per poll (e.g., 2°C rise flags alert).
# deviation_threshold = 0.1 ; Max relative difference from bank average (10% deviation alerts).
# disconnection_lag_threshold = 0.5 ; How much a sensor can lag bank change before alerting.
# high_threshold = 42.0 ; Too hot if above this (°C) - alert!
# low_threshold = 0.0 ; Too cold if below this (°C) - alert!
# scaling_factor = 100.0 ; Divides raw sensor data to get °C (sensor specific).
# valid_min = 0.0 ; Below this = invalid/disconnected sensor.
# max_retries = 3 ; Retry failed reads this many times.
# retry_backoff_base = 1 ; Delay base for retries (1s, 2s, 4s...).
# query_delay = 0.25 ; Wait seconds after sending sensor query.
# num_channels = 24 ; Number of sensors (fixed for 3 banks x 8).
# abs_deviation_threshold = 2.0 ; Max absolute difference from bank average (°C).
# cabinet_over_temp_threshold = 35.0 ; Cabinet too hot if median > this (°C) - fan on!
#
# [General] ; Overall system settings.
# NumberOfBatteries = 3 ; Number of banks (fixed at 3 for 3s8p).
# VoltageDifferenceToBalance = 0.1 ; Balance if banks differ by more than this (V).
# BalanceDurationSeconds = 5 ; How long to balance each time (seconds).
# SleepTimeBetweenChecks = 0.1 ; Short wait in loop (seconds) - for responsiveness.
# BalanceRestPeriodSeconds = 60 ; Wait after balancing before next (seconds).
# LowVoltageThresholdPerBattery = 18.5 ; Alert if bank below this (V).
# HighVoltageThresholdPerBattery = 21.0 ; Alert if bank above this (V).
# EmailAlertIntervalSeconds = 3600 ; Min seconds between emails (1 hour).
# I2C_BusNumber = 1 ; I2C bus on Pi (usually 1).
# VoltageDividerRatio = 0.01592 ; Math factor to convert ADC reading to real voltage.
# LoggingLevel = INFO ; Log detail: DEBUG (lots), INFO (key events), ERROR (problems).
# WebInterfaceEnabled = True ; Turn on web dashboard (True/False).
# StartupSelfTestEnabled = True ; Run checks at start (True/False).
# WatchdogEnabled = True ; Use hardware watchdog to prevent freezes (True/False).
#
# [I2C] ; Addresses for I2C devices (in hex, like 0x70).
# MultiplexerAddress = 0x70 ; I2C switch address.
# VoltageMeterAddress = 0x49 ; ADC for voltages.
# RelayAddress = 0x26 ; Relay controller.
#
# [GPIO] ; Pi pin numbers for relays.
# DC_DC_RelayPin = 5 ; Pin to control DC-DC converter.
# AlarmRelayPin = 6 ; Pin for alarm (buzzer/light).
# FanRelayPin = 4 ; Pin for cabinet fan.
#
# [Email] ; Settings for alert emails.
# SMTP_Server = smtp.gmail.com ; Email server (Gmail).
# SMTP_Port = 587 ; Server port.
# SenderEmail = your_email@gmail.com ; From address.
# RecipientEmail = recipient@example.com ; To address.
# SMTP_Username = your_email@gmail.com ; Login user.
# SMTP_Password = your_app_password ; App-specific password (not regular).
#
# [ADC] ; Settings for voltage ADC (hex values).
# ConfigRegister = 0x01 ; ADC config location.
# ConversionRegister = 0x00 ; Where to read values.
# ContinuousModeConfig = 0x0100 ; Continuous reading mode.
# SampleRateConfig = 0x0080 ; How fast to sample.
# GainConfig = 0x0400 ; Sensitivity setting.
#
# [Calibration] ; Fine-tune voltage readings (close to 1.0).
# Sensor1_Calibration = 0.99856 ; For bank 1.
# Sensor2_Calibration = 0.99856 ; For bank 2.
# Sensor3_Calibration = 0.99809 ; For bank 3.
#
# [Startup] ; Self-test settings.
# test_balance_duration = 15 ; Balance time during test (seconds).
# min_voltage_delta = 0.01 ; Min voltage change to pass test (V).
# test_read_interval = 2.0 ; Read interval during test (seconds).
#
# [Web] ; Dashboard settings.
# host = 0.0.0.0 ; Listen on all IPs.
# web_port = 8080 ; Port for web access.
# auth_required = False ; Require login (True/False).
# username = admin ; Web login user.
# password = admin123 ; Web login pass.
# api_enabled = True ; Allow API calls.
# cors_enabled = False ; Allow cross-origin (for apps).
# cors_origins = * ; Allowed origins (* = all).
#
# --------------------------------------------------------------------------------
# Code Begins Below - With Line-by-Line Comments for Non-Programmers
# --------------------------------------------------------------------------------
# Import necessary Python libraries for various tasks - these are like toolboxes for different jobs.
import socket # Used to connect to the Lantronix EDS4100 device over the network - like making a phone call to the sensor box.
import statistics # Helps calculate averages and medians for temperature data - math helpers.
import time # Manages timing, delays, and timestamps for events - like a clock and stopwatch.
import configparser # Reads settings from the INI configuration file - loads the recipe.
import logging # Logs events and errors to a file for troubleshooting - like a diary.
import signal # Handles graceful shutdown when the user presses Ctrl+C - catches "stop" signal.
import gc # Manages memory cleanup during long-running operations - garbage collector.
import os # Handles file operations, like reading/writing offsets - file manager.
import sys # Used to exit the script cleanly - system commands.
import threading # Runs the web server in a separate thread - multitasking.
import json # Formats data for the web interface - data packer.
from urllib.parse import urlparse, parse_qs # Parses web requests - breaks down URLs.
import base64 # Decodes authentication credentials for the web interface - secret decoder.
import traceback # Logs detailed error information for debugging - error detective.
import subprocess # Runs external commands like rrdtool for time-series database operations - external tool caller.
import xml.etree.ElementTree as ET # Parses XML output from rrdtool for fetching history data - XML parser.
# Import libraries for hardware interaction, with fallback for testing - try to load hardware tools, if not, pretend mode.
try:
    import smbus # Communicates with I2C devices like the ADC and relays - hardware talker.
    import RPi.GPIO as GPIO # Controls Raspberry Pi GPIO pins for relays - pin controller.
except ImportError:
    # If hardware libraries are missing, run in test mode without hardware - safe mode for no hardware.
    print("Hardware libraries not available - running in test mode") # Warn user.
    smbus = None # Set to none if missing.
    GPIO = None # Set to none if missing.
# Import libraries for email alerts and web server - communication tools.
from email.mime.text import MIMEText # Builds email messages - email builder.
import smtplib # Sends email alerts - email sender.
from http.server import HTTPServer, BaseHTTPRequestHandler # Runs the web server - web host.
import curses # Creates the terminal-based Text User Interface (TUI) - terminal drawer.
from art import text2art # Generates ASCII art for the TUI display - art maker.
# Add imports for watchdog - system watchdog tools.
import fcntl # For watchdog ioctl - low-level control.
import struct # For watchdog struct - data packer.
# Set up logging to save events and errors to 'battery_monitor.log' - start the diary.
logging.basicConfig(
    filename='battery_monitor.log', # Log file name - where diary is saved.
    level=logging.INFO, # Log level (INFO captures key events) - how detailed.
    format='%(asctime)s - %(message)s' # Log format with timestamp - date + message.
)
# Global variables to store system state - shared info across the script.
config_parser = configparser.ConfigParser() # Object to read INI file - config reader.
bus = None # I2C bus for communicating with hardware - hardware connection.
last_email_time = 0 # Tracks when the last email alert was sent - email timer.
balance_start_time = None # Tracks when balancing started - balance clock start.
last_balance_time = 0 # Tracks when the last balancing ended - balance clock end.
battery_voltages = [] # Stores current voltages for each bank - voltage list.
previous_temps = None # Stores previous temperature readings - old temps.
previous_bank_medians = None # Stores previous median temperatures per bank - old medians.
run_count = 0 # Counts how many times the main loop has run - cycle counter.
startup_offsets = None # Temperature calibration offsets from startup - adjustment numbers.
startup_median = None # Median temperature at startup - average at start.
startup_set = False # Indicates if temperature calibration is set - calibration flag.
alert_states = {} # Tracks alerts for each temperature channel - alert memory.
balancing_active = False # Indicates if balancing is currently happening - balancing flag.
startup_failed = False # Indicates if startup tests failed - test fail flag.
startup_alerts = [] # Stores startup test failure messages - test error list.
web_server = None # Web server object - web host.
event_log = [] # Stores the last 20 events (e.g., alerts, balancing) - event history.
web_data = {
    'voltages': [0.0] * 3, # Current voltages for 3 banks - voltage array.
    'temperatures': [None] * 24, # Current temperatures for 24 sensors - temp array.
    'alerts': [], # Current active alerts - alert list.
    'balancing': False, # Balancing status - balance flag.
    'last_update': time.time(), # Last data update timestamp - update time.
    'system_status': 'Initializing' # System status (e.g., Running, Alert) - status string.
}
# Define which temperature sensors belong to each bank (3 banks, 8 sensors each) - bank groups.
BANK_RANGES = [(1, 8), (9, 16), (17, 24)] # Channels 1-8 (Bank 1), 9-16 (Bank 2), 17-24 (Bank 3) - fixed ranges.
NUM_BANKS = 3 # Fixed number of banks for 3s8p configuration - constant 3.
# Global for watchdog - watchdog file path.
WATCHDOG_DEV = '/dev/watchdog' # Device file for watchdog - hardware reset preventer.
watchdog_fd = None # File handle for watchdog - open connection.
# RRD globals for time-series - database file and history limit.
RRD_FILE = 'bms.rrd' # RRD database file for storing time-series data - persistent storage.
HISTORY_LIMIT = 480 # Number of historical entries to retain (e.g., ~8 hours at 1min steps) - limit for memory/efficiency.
def get_bank_for_channel(ch):
    """
    Find which battery bank a temperature sensor belongs to.
    This function takes a sensor number (1-24) and figures out which group (bank 1,2,3) it belongs to.
    Args:
        ch (int): Sensor channel number (1 to 24) - the sensor ID.
    Returns:
        int: Bank number (1 to 3) or None if the channel is invalid - the group ID.
    """
    # Loop through each bank’s channel range - check each group.
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1): # For bank 1: start=1 end=8, etc.
        # Check if the channel number falls within this bank’s range - is it in this group?
        if start <= ch <= end:
            return bank_id # Return the bank number - found it.
    return None # Return None if the channel doesn’t belong to any bank - not found.
def modbus_crc(data):
    """
    Calculate a checksum (CRC) to ensure data integrity for Modbus communication.
    This is like a safety check to make sure the data wasn't corrupted during transmission.
    Args:
        data (bytes): Data to calculate the CRC for - the message bytes.
    Returns:
        bytes: 2-byte CRC value in little-endian order - the check code.
    """
    crc = 0xFFFF # Start with a fixed initial value - magic starting number.
    # Process each byte in the data - go through each piece.
    for byte in data:
        crc ^= byte # Combine the byte with the CRC - mix it in.
        # Perform 8 iterations for each bit - check every tiny part.
        for _ in range(8):
            if crc & 0x0001: # Check if the least significant bit is 1 - look at the end bit.
                crc = (crc >> 1) ^ 0xA001 # Shift right and apply polynomial - math adjustment.
            else:
                crc >>= 1 # Shift right if bit is 0 - simple shift.
    return crc.to_bytes(2, 'little') # Return CRC as 2 bytes - pack it up.
def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """
    Read temperatures from NTC sensors via Modbus over TCP.
    This function connects to the sensor device over network, sends a request for data, receives it, checks it's good, and converts to temperatures.
    Args:
        ip (str): IP address of the Lantronix EDS4100 device - device address.
        modbus_port (int): Network port for Modbus communication - door number.
        query_delay (float): Seconds to wait after sending a query - pause for response.
        num_channels (int): Number of temperature sensors to read - how many.
        scaling_factor (float): Converts raw sensor data to degrees Celsius - math factor.
        max_retries (int): Maximum attempts to retry failed reads - try again count.
        retry_backoff_base (int): Base for retry delay (e.g., 1s, 2s, 4s) - wait multiplier.
    Returns:
        list: Temperature readings or an error message if the read fails - list of temps or error string.
    """
    logging.info("Starting temperature sensor read.") # Log the start of the read - note start.
    # Create the Modbus query to request data - build the request message.
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big') # Base query bytes.
    crc = modbus_crc(query_base) # Calculate checksum for the query - safety check.
    query = query_base + crc # Combine query and checksum - full message.
    # Try reading the sensors up to max_retries times - loop for attempts.
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{modbus_port}") # Log attempt.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Create a network socket - open connection.
            s.settimeout(3) # Set a 3-second timeout for the connection - don't wait forever.
            s.connect((ip, modbus_port)) # Connect to the device - dial the number.
            s.send(query) # Send the Modbus query - ask for data.
            pet_watchdog()
            time.sleep(query_delay) # Wait for the device to respond - pause.
            response = s.recv(1024) # Receive up to 1024 bytes of response - get answer.
            s.close() # Close the connection - hang up.
            # Check if the response is too short - too little data?
            if len(response) < 5:
                raise ValueError("Short response") # Error if short.
            # Check if the response length is correct - right size?
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length") # Error if wrong length.
            # Verify the checksum - is data intact?
            calc_crc = modbus_crc(response[:-2]) # Recalculate CRC.
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch") # Error if mismatch.
            # Parse the response header - read the top part.
            slave, func, byte_count = response[0:3] # Extract info.
            if slave != 1 or func != 3 or byte_count != num_channels * 2: # Check if header good.
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}" # Special error.
                return "Error: Invalid response header." # Bad header.
            # Extract temperature data from the response - get the meat.
            data = response[3:3 + byte_count] # Data bytes.
            raw_temperatures = [] # List for temps.
            for i in range(0, len(data), 2): # Process 2 bytes at a time - each temp is 2 bytes.
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor # Convert to number and scale.
                raw_temperatures.append(val) # Add to list.
            logging.info("Temperature read successful.") # Log success.
            return raw_temperatures # Return the list of temperatures.
        except socket.error as e:
            # Handle network errors - connection problems.
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.") # Log warning.
            if attempt < max_retries - 1:
                pet_watchdog()
                time.sleep(retry_backoff_base ** attempt) # Wait before retrying - longer each time.
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.") # Log final error.
                return f"Error: Failed after {max_retries} attempts - {str(e)}." # Return error message.
        except ValueError as e:
            # Handle data validation errors - bad data.
            logging.warning(f"Temp read attempt {attempt+1} failed (validation): {str(e)}. Retrying.") # Log warning.
            if attempt < max_retries - 1:
                pet_watchdog()
                time.sleep(retry_backoff_base ** attempt) # Wait.
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.") # Log error.
                return f"Error: Failed after {max_retries} attempts - {str(e)}." # Return error.
        except Exception as e:
            # Handle unexpected errors - catch-all.
            logging.error(f"Unexpected error in temp read attempt {attempt+1}: {str(e)}\n{traceback.format_exc()}") # Log with details.
            return f"Error: Unexpected failure - {str(e)}" # Return error.
def load_config():
    """
    Load settings from 'battery_monitor.ini' file, using defaults if settings are missing.
    This function reads the INI file and collects all settings into a dictionary.
    Returns:
        dict: All configuration settings in a single dictionary - big settings collection.
    Raises:
        FileNotFoundError: If the INI file is missing - error if no file.
    """
    logging.info("Loading configuration from 'battery_monitor.ini'.") # Log config load attempt - start loading.
    global alert_states # Access the global alert states dictionary - shared alerts.
    # Try to read the INI file - open the recipe.
    if not config_parser.read('battery_monitor.ini'): # If read fails.
        logging.error("Config file 'battery_monitor.ini' not found.") # Log error if file is missing.
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.") # Throw error.
    # Temperature monitoring settings - temp section.
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'), # IP address of the EDS4100 device.
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001), # Modbus port.
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0), # Seconds between temperature reads.
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0), # Max allowed temperature rise.
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1), # Max relative deviation.
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5), # Lag threshold.
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0), # Max safe temperature.
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0), # Min safe temperature.
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0), # Converts raw data to °C.
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0), # Min valid temperature.
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3), # Max retries for failed reads.
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1), # Retry delay base.
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25), # Delay after Modbus query.
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24), # Number of sensors.
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0), # Max absolute deviation.
        'cabinet_over_temp_threshold': config_parser.getfloat('Temp', 'cabinet_over_temp_threshold', fallback=35.0) # Cabinet over-temp for fan.
    }
    # Voltage and balancing settings - general voltage.
    voltage_settings = {
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3), # Number of banks.
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1), # Min voltage difference to balance.
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5), # Balancing duration.
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1), # Loop sleep time.
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60), # Rest after balancing.
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5), # Min safe voltage.
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0), # Max safe voltage.
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600), # Email throttling interval.
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1), # I2C bus number.
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592), # Voltage divider ratio.
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO') # Logging level (INFO, DEBUG, etc.).
    }
    # General flags for enabling features - on/off switches.
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True), # Enable web interface.
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True), # Enable startup tests.
        'WatchdogEnabled': config_parser.getboolean('General', 'WatchdogEnabled', fallback=True) # Enable watchdog.
    }
    # I2C device addresses - hardware IDs.
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16), # Multiplexer address.
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16), # ADC address.
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x26'), 16) # Relay address.
    }
    # GPIO pin settings - pin numbers.
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17), # Pin for DC-DC converter relay.
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27), # Pin for alarm relay.
        'FanRelayPin': config_parser.getint('GPIO', 'FanRelayPin', fallback=4) # Pin for fan relay.
    }
    # Email alert settings - email info.
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'), # Email server.
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587), # Email port.
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'), # Sender email.
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'), # Recipient email.
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'), # Email username.
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password') # Email password.
    }
    # ADC configuration settings - ADC setup numbers.
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16), # ADC config register.
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16), # ADC conversion register.
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16), # Continuous mode setting.
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16), # Sample rate setting.
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16) # Gain setting.
    }
    # Voltage calibration settings - adjustment factors.
    calibration_settings = {
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=0.99856), # Bank 1 calibration.
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=0.99856), # Bank 2 calibration.
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=0.99809) # Bank 3 calibration.
    }
    # Startup self-test settings - test params.
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15), # Test balancing duration.
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01), # Min voltage change for test.
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0) # Interval between test reads.
    }
    # Web interface settings - web config.
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'), # Web server host.
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080), # Web server port.
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False), # Require web authentication.
        'username': config_parser.get('Web', 'username', fallback='admin'), # Web username.
        'password': config_parser.get('Web', 'password', fallback='admin123'), # Web password.
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True), # Enable API endpoints.
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=True), # Enable CORS.
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*') # Allowed CORS origins.
    }
    # Set logging level based on configuration - adjust diary detail.
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO) # Get level from string.
    logging.getLogger().setLevel(log_level) # Apply the logging level - set detail.
    # Initialize alert states for each temperature sensor - setup alert trackers.
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)} # Dict for each sensor.
    logging.info("Configuration loaded successfully.") # Log successful config load - done.
    # Combine all settings into one dictionary - big collection.
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings} # Merge all.
def setup_hardware(settings):
    """
    Set up the I2C bus and GPIO pins for hardware communication.
    This function prepares the connections to sensors and pins, and initializes the RRD database for time-series logging.
    Args:
        settings (dict): Configuration settings from the INI file - settings dict.
    """
    global bus # Access the global I2C bus variable - shared connection.
    logging.info("Setting up hardware.") # Log hardware setup start - begin.
    # Initialize I2C bus if the library is available - setup hardware talk.
    if smbus:
        bus = smbus.SMBus(settings['I2C_BusNumber']) # Set up I2C bus (usually bus 1).
    else:
        logging.warning("smbus not available - running in test mode") # Warn if I2C library is missing.
        bus = None # No bus.
    # Initialize GPIO pins if the library is available - setup pins.
    if GPIO:
        GPIO.setmode(GPIO.BCM) # Use BCM numbering for GPIO pins - pin naming style.
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW) # Set up DC-DC converter relay pin - off at start.
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Set up alarm relay pin - off at start.
        GPIO.setup(settings['FanRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Set up fan relay pin - off at start.
    else:
        logging.warning("RPi.GPIO not available - running in test mode") # Warn if GPIO library is missing.
    # Create RRD database if it doesn't exist - time-series storage setup.
    try:
        if os.path.exists(RRD_FILE):
            logging.info("Recreating RRD database for updated configuration.")
            os.remove(RRD_FILE)
        subprocess.check_call(['rrdtool', 'create', RRD_FILE,
                               '--step', '60', # 1min step for aggregation.
                               'DS:volt1:GAUGE:120:0:25', # Bank 1 voltage (heartbeat 2min, range 0-25V).
                               'DS:volt2:GAUGE:120:0:25', # Bank 2.
                               'DS:volt3:GAUGE:120:0:25', # Bank 3.
                               'DS:medtemp:GAUGE:120:-20:100', # Median temp (-20 to 100°C).
                               'RRA:LAST:0.0:1:480', # Last value, retain 480 steps (~8 hours at 1min).
                               'RRA:LAST:0.0:5:100']) # Last value, 5min consolidation for longer trends.
        logging.info("Created RRD database for time-series logging.") # Log creation.
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD creation failed: {e}") # Log error if creation fails.
    except FileNotFoundError:
        logging.error("rrdtool not found. Please install rrdtool (sudo apt install rrdtool).") # Log if rrdtool missing.
    logging.info("Hardware setup complete, including RRD initialization.") # Log successful setup - done.
def signal_handler(sig, frame):
    """
    Handle Ctrl+C (SIGINT) to shut down the script cleanly.
    This function runs when you press Ctrl+C to stop safely.
    Args:
        sig: Signal number (e.g., SIGINT for Ctrl+C) - stop code.
        frame: Current stack frame (technical detail) - where we are.
    """
    logging.info("Script stopped by user or signal.") # Log shutdown request - stopping.
    global web_server # Access the global web server object - shared web.
    # Shut down the web server if it’s running - stop web.
    if web_server:
        web_server.shutdown() # Stop the web server.
    # Clean up GPIO pins - reset pins.
    if GPIO:
        GPIO.cleanup() # Reset GPIO pins to default state.
    close_watchdog() # Close watchdog if open.
    sys.exit(0) # Exit the script - bye.
def load_offsets(num_channels):
    """
    Load temperature calibration offsets from 'offsets.txt' if it exists.
    This function reads saved adjustments from file.
    Args:
        num_channels (int): Number of temperature sensors - how many.
    Returns:
        tuple: (startup_median, offsets) or (None, None) if the file is missing or invalid - median and list or none.
    """
    logging.info("Loading startup offsets from 'offsets.txt'.") # Log offset load attempt - start.
    # Check if the offsets file exists - is there a file?
    if os.path.exists('offsets.txt'):
        try:
            with open('offsets.txt', 'r') as f: # Open file for reading.
                lines = f.readlines() # Read all lines from the file - get text.
            # Check if the file is empty - no data?
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.") # Warn if file is empty.
                return None, None # No data.
            startup_median = float(lines[0].strip()) # Read the median temperature - first line.
            offsets = [float(line.strip()) for line in lines[1:]] # Read offsets for each sensor - rest lines.
            # Verify the number of offsets matches the number of sensors - right count?
            if len(offsets) != num_channels:
                logging.warning(f"Invalid offsets count; expected {num_channels}, got {len(offsets)}. Using none.") # Warn wrong count.
                return None, None # Bad.
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.") # Log successful load.
            return startup_median, offsets # Return them.
        except (ValueError, IndexError):
            logging.warning("Corrupt offsets.txt; using none.") # Warn if file is corrupt.
            return None, None # Bad file.
    logging.warning("No 'offsets.txt' found; using none.") # Warn if file is missing.
    return None, None # No file.
def save_offsets(startup_median, startup_offsets):
    """
    Save temperature median and offsets to 'offsets.txt'.
    This function writes adjustments to file for next time.
    Args:
        startup_median (float): Median temperature at startup - average.
        startup_offsets (list): List of temperature offsets for each sensor - adjustments.
    """
    logging.info("Saving startup offsets to 'offsets.txt'.") # Log save attempt - start.
    try:
        with open('offsets.txt', 'w') as f: # Open file for writing.
            f.write(f"{startup_median}\n") # Write the median temperature - first line.
            for offset in startup_offsets: # Loop through offsets.
                f.write(f"{offset}\n") # Write each offset - one per line.
        logging.debug("Offsets saved.") # Log successful save - done.
    except IOError as e:
        logging.error(f"Failed to save offsets: {e}") # Log error if save fails - problem.
def check_invalid_reading(raw, ch, alerts, valid_min):
    """
    Check if a temperature reading is invalid (too low or disconnected).
    This checks if sensor is broken or disconnected.
    Args:
        raw (float): Raw temperature reading - unadjusted temp.
        ch (int): Sensor channel number - sensor ID.
        alerts (list): List to store alert messages - add here.
        valid_min (float): Minimum valid temperature - below = bad.
    Returns:
        bool: True if the reading is invalid, False otherwise - bad or good.
    """
    if raw <= valid_min: # Check if the reading is below the minimum valid value - too low?
        bank = get_bank_for_channel(ch) # Find which bank the sensor belongs to - group.
        alert = f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min})." # Create alert message - make string.
        alerts.append(alert) # Add alert to the list - store it.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log with timestamp - history.
        if len(event_log) > 20:
            event_log.pop(0) # Keep only the last 20 events - remove oldest.
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.") # Log the issue - note.
        return True # Yes, invalid.
    return False # No, good.
def check_high_temp(calibrated, ch, alerts, high_threshold):
    """
    Check if a temperature is too high.
    Alerts if above safe max.
    Args:
        calibrated (float): Calibrated temperature - adjusted temp.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
        high_threshold (float): Maximum safe temperature - max ok.
    """
    if calibrated > high_threshold: # Check if temperature exceeds the high threshold - too hot?
        bank = get_bank_for_channel(ch) # Find the bank - group.
        alert = f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)." # Create alert - string.
        alerts.append(alert) # Add to alerts - store.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log - history.
        if len(event_log) > 20:
            event_log.pop(0) # Trim.
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.") # Log the issue - note.
def check_low_temp(calibrated, ch, alerts, low_threshold):
    """
    Check if a temperature is too low.
    Alerts if below safe min.
    Args:
        calibrated (float): Calibrated temperature - adjusted temp.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
        low_threshold (float): Minimum safe temperature - min ok.
    """
    if calibrated < low_threshold: # Check if temperature is below the low threshold - too cold?
        bank = get_bank_for_channel(ch) # Find the bank - group.
        alert = f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C)." # Create alert - string.
        alerts.append(alert) # Add to alerts - store.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log - history.
        if len(event_log) > 20:
            event_log.pop(0) # Trim.
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.") # Log the issue - note.
def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """
    Check if a temperature deviates too much from the bank’s average.
    Alerts if too different from group average.
    Args:
        calibrated (float): Calibrated temperature - adjusted temp.
        bank_median (float): Median temperature of the bank - group average.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
        abs_deviation_threshold (float): Maximum allowed absolute deviation - max diff.
        deviation_threshold (float): Maximum allowed relative deviation - max % diff.
    """
    abs_dev = abs(calibrated - bank_median) # Calculate absolute difference from bank median - how far.
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0 # Calculate relative difference - % far.
    # Check if deviation is too high - too different?
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch) # Find the bank - group.
        alert = f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%})." # Create alert - string.
        alerts.append(alert) # Add to alerts - store.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log - history.
        if len(event_log) > 20:
            event_log.pop(0) # Trim.
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.") # Log issue.
def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """
    Check if a temperature has risen too quickly since the last check.
    Alerts if jump too big.
    Args:
        current (float): Current temperature - now temp.
        previous_temps (list): Previous temperature readings - old list.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
        poll_interval (float): Time between checks - wait time.
        rise_threshold (float): Maximum allowed temperature rise - max jump.
    """
    previous = previous_temps[ch-1] # Get the previous temperature for this sensor - old value.
    if previous is not None: # Check if previous reading exists - have old?
        rise = current - previous # Calculate temperature increase - how much up.
        if rise > rise_threshold: # Check if increase is too large - too fast?
            bank = get_bank_for_channel(ch) # Find the bank - group.
            alert = f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s)." # Create alert - string.
            alerts.append(alert) # Add to alerts - store.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.") # Log issue.
def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """
    Check if a sensor’s temperature change lags behind the bank’s average change.
    Alerts if not keeping up with group.
    Args:
        current (float): Current temperature - now.
        previous_temps (list): Previous temperature readings - old.
        bank_median_rise (float): Average temperature rise for the bank - group up.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
        disconnection_lag_threshold (float): Maximum allowed lag - max behind.
    """
    previous = previous_temps[ch-1] # Get previous.
    if previous is not None: # Have old?
        rise = current - previous # Calculate temperature increase - up.
        if abs(rise - bank_median_rise) > disconnection_lag_threshold: # Check if lag is too large - too different?
            bank = get_bank_for_channel(ch) # Find bank.
            alert = f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C)." # Alert string.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.") # Log.
def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """
    Check if a sensor has suddenly stopped working.
    Alerts if was good, now bad.
    Args:
        current: Current temperature reading (None if disconnected) - now.
        previous_temps (list): Previous temperature readings - old.
        ch (int): Sensor channel number - ID.
        alerts (list): List to store alert messages - add here.
    """
    previous = previous_temps[ch-1] # Get previous.
    if previous is not None and current is None: # Was good, now bad?
        bank = get_bank_for_channel(ch) # Bank.
        alert = f"Bank {bank} Ch {ch}: Sudden disconnection." # Alert.
        alerts.append(alert) # Add.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
        if len(event_log) > 20:
            event_log.pop(0) # Trim.
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.") # Log.
def ascii_line_chart(data, width=40, height=5, symbols=' ▁▂▃▄▅▆▇█'):
    """
    Generate an ASCII line chart from data series.
    This function creates a simple text-based line graph using Unicode block characters for visualization in the TUI.
    Args:
        data (list): List of numerical values to plot - data points.
        width (int): Width of the chart in characters - horizontal size.
        height (int): Height of the chart in lines - vertical size.
        symbols (str): String of characters representing increasing heights - block symbols.
    Returns:
        str: Multi-line string representing the ASCII chart - chart text.
    """
    if not data: # No data?
        return '\n'.join([' ' * width] * height) # Empty chart.
    data = [d for d in data if d is not None] # Filter None values to avoid errors.
    if not data:
        return '\n'.join([' ' * width] * height) # Empty if all None.
    min_val, max_val = min(data), max(data) # Min and max values.
    range_val = max_val - min_val or 1 # Range, avoid divide by zero.
    # Scale data to symbol indices - normalize.
    scaled = [(val - min_val) / range_val * (len(symbols) - 1) for val in data]
    chart = [] # List for lines.
    # Build each row from top to bottom - high to low.
    for y in range(height - 1, -1, -1):
        # For each x, choose symbol if data exists - build line.
        line = ''.join(symbols[int(scaled[x])] if len(data) > x else ' ' for x in range(width))
        chart.append(line) # Add line.
    return '\n'.join(chart) # Return as string.
def choose_channel(channel, multiplexer_address):
    """
    Select an I2C channel on the multiplexer.
    Switches to a specific hardware line.
    Args:
        channel (int): Channel number to select (0 to 3) - line number.
        multiplexer_address (int): I2C address of the multiplexer - switch ID.
    """
    logging.debug(f"Switching to I2C channel {channel}.") # Log switch.
    if bus: # If hardware available.
        try:
            bus.write_byte(multiplexer_address, 1 << channel) # Select the channel - send command.
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}") # Log error.
def setup_voltage_meter(settings):
    """
    Configure the ADS1115 ADC for voltage measurements.
    Sets up the voltage reader.
    Args:
        settings (dict): Configuration settings - settings.
    """
    logging.debug("Configuring voltage meter ADC.") # Log setup.
    if bus: # Hardware?
        try:
            # Combine ADC settings for continuous mode, sample rate, and gain - math setup.
            config_value = (settings['ContinuousModeConfig'] |
                            settings['SampleRateConfig'] |
                            settings['GainConfig'])
            bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value) # Send config to ADC - write.
        except IOError as e:
            logging.error(f"I2C error configuring voltage meter: {str(e)}") # Log error.
def read_voltage_with_retry(bank_id, settings):
    """
    Read the voltage of a battery bank with retries for accuracy.
    Tries reading twice for good data.
    Args:
        bank_id (int): Bank number (1 to 3) - group ID.
        settings (dict): Configuration settings - settings.
    Returns:
        tuple: (average voltage, list of readings, list of raw ADC values) or (None, [], []) if failed - voltage info or none.
    """
    logging.info(f"Starting voltage read for Bank {bank_id}.") # Log start.
    voltage_divider_ratio = settings['VoltageDividerRatio'] # Get ratio.
    sensor_id = bank_id # Sensor ID matches bank ID - same.
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration'] # Get calibration.
    # Try reading twice for reliability - loop attempts.
    for attempt in range(2):
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.") # Log attempt.
        readings = [] # Store voltage readings - list.
        raw_values = [] # Store raw ADC values - list.
        # Take two readings for consistency - sub loop.
        for _ in range(2):
            meter_channel = (bank_id - 1) % 3 # Map bank to ADC channel - calculate line.
            choose_channel(meter_channel, settings['MultiplexerAddress']) # Select the channel - switch.
            setup_voltage_meter(settings) # Configure the ADC - setup.
            if bus: # If hardware.
                try:
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01) # Start ADC conversion - trigger.
                    pet_watchdog()
                    time.sleep(0.05) # Wait for conversion to complete - pause.
                    raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister']) # Read value.
                    raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8) # Adjust byte order - fix format.
                except IOError as e:
                    logging.error(f"I2C error in voltage read for Bank {bank_id}: {str(e)}") # Log error.
                    raw_adc = 0 # Bad read.
            else:
                raw_adc = 16000 + bank_id * 100 # Mock value for testing - fake.
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}") # Log raw.
            if raw_adc != 0: # If valid.
                measured_voltage = raw_adc * (6.144 / 32767) # Convert ADC to voltage - math.
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor # Apply calibration - adjust.
                readings.append(actual_voltage) # Store voltage.
                raw_values.append(raw_adc) # Store raw.
            else:
                readings.append(0.0) # Zero if failed.
                raw_values.append(0) # Zero.
        # Check if readings are consistent - good data?
        if readings:
            average = sum(readings) / len(readings) # Calculate average voltage - mean.
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05] # Filter close ones - consistent.
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05] # Filter ADC.
            if valid_readings: # Have good?
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.") # Log success.
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc # Return average and details.
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.") # Log retry.
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.") # Log failure.
    return None, [], [] # Failure.
def set_relay_connection(high, low, settings):
    """
    Set up relays to connect a high-voltage bank to a low-voltage bank for balancing.
    Turns on specific switches to connect groups.
    Args:
        high (int): High-voltage bank number - from.
        low (int): Low-voltage bank number - to.
        settings (dict): Configuration settings - settings.
    """
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}") # Log setup.
        logging.debug("Switching to relay control channel.") # Log switch.
        choose_channel(3, settings['MultiplexerAddress']) # Select relay channel - switch.
        relay_state = 0 # Start with all relays off - zero.
        # Set relay patterns based on bank combination - which switches.
        if high == 1 and low == 2:
            relay_state |= (1 << 3) # Activate relay 4 - turn on.
            logging.debug("Relays 4 activated for high to low.") # Log.
        elif high == 1 and low == 3:
            relay_state |= (1 << 2) | (1 << 3) # Relays 3 and 4.
            logging.debug("Relays 3, and 4 activated for high to low.") # Log.
        elif high == 2 and low == 1:
            relay_state |= (1 << 0) # Relay 1.
            logging.debug("Relays 1 activated for high to low.") # Log.
        elif high == 2 and low == 3:
            relay_state |=(1 << 0) | (1 << 2) | (1 << 3) # Relays 1, 3, 4.
            logging.debug("Relays 1, 3, and 4 activated for high to low.") # Log.
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1) # Relays 1, 2.
            logging.debug("Relays 1, 2 activated for high to low.") # Log.
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3) # Relays 1, 2, 4.
            logging.debug("Relays 1, 2, and 4 activated for high to low.") # Log.
        logging.debug(f"Final relay state: {bin(relay_state)}") # Log state.
        if bus: # Hardware?
            logging.info(f"Sending relay state command to hardware.") # Log send.
            bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state) # Send state - write.
        logging.info(f"Relay setup completed for balancing from Bank {high} to Bank {low}") # Log success.
    except (IOError, AttributeError) as e:
        logging.error(f"I/O error while setting up relay: {e}") # Log I/O error.
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}") # Log unexpected.
def control_dcdc_converter(turn_on, settings):
    """
    Turn the DC-DC converter on or off using a GPIO pin.
    Controls the charge transfer device.
    Args:
        turn_on (bool): True to turn on, False to turn off - on/off.
        settings (dict): Configuration settings - settings.
    """
    try:
        if GPIO: # Pins available?
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW) # Set pin high or low - on/off.
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}") # Log state.
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}") # Log error.
def send_alert_email(message, settings):
    """
    Send an email alert with throttling to avoid spam.
    Sends email if time passed.
    Args:
        message (str): The email message content - what to say.
        settings (dict): Configuration settings - email info.
    """
    global last_email_time # Access last time - shared.
    # Check if enough time has passed since the last email - no spam.
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.") # Log skip.
        return # Skip.
    try:
        # Create the email message - build email.
        msg = MIMEText(message) # Text email.
        msg['Subject'] = "Battery Monitor Alert" # Title.
        msg['From'] = settings['SenderEmail'] # From.
        msg['To'] = settings['RecipientEmail'] # To.
        # Connect to the email server and send the message - send it.
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server: # Open server.
            server.starttls() # Secure.
            if settings['SMTP_Username'] and settings['SMTP_Password']: # Credentials?
                server.login(settings['SMTP_Username'], settings['SMTP_Password']) # Login.
            server.send_message(msg) # Send.
        last_email_time = time.time() # Update time.
        logging.info(f"Alert email sent: {message}") # Log sent.
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}") # Log fail.
def check_for_issues(voltages, temps_alerts, settings):
    """
    Check for voltage and temperature issues and trigger alerts.
    Looks for problems and alerts.
    Args:
        voltages (list): List of bank voltages - voltages.
        temps_alerts (list): List of temperature-related alerts - temp issues.
        settings (dict): Configuration settings - settings.
    Returns:
        tuple: (alert_needed, alerts_list) indicating if an alert is needed and the list of alerts - need alert? and list.
    """
    global startup_failed, startup_alerts # Access startup flags - shared.
    logging.info("Checking for voltage and temp issues.") # Log check.
    alert_needed = startup_failed # Start with startup status - from test.
    alerts = [] # List for messages.
    # Add startup failures to alerts if any - test issues.
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts)) # Add.
    # Check each bank’s voltage for issues - loop banks.
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0: # Zero or bad?
            alert = f"Bank {i}: Zero voltage." # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"Zero voltage alert on Bank {i}.") # Log.
            alert_needed = True # Yes.
        elif v > settings['HighVoltageThresholdPerBattery']: # Too high?
            alert = f"Bank {i}: High voltage ({v:.2f}V)." # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.") # Log.
            alert_needed = True # Yes.
        elif v < settings['LowVoltageThresholdPerBattery']: # Too low?
            alert = f"Bank {i}: Low voltage ({v:.2f}V)." # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.") # Log.
            alert_needed = True # Yes.
    # Add temperature alerts if any - combine.
    if temps_alerts:
        alerts.extend(temps_alerts) # Add temps.
        alert_needed = True # Yes.
    # Activate or deactivate the alarm relay - turn on/off alarm.
    if alert_needed:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH) # On.
        logging.info("Alarm relay activated.") # Log on.
        send_alert_email("\n".join(alerts), settings) # Email.
    else:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.LOW) # Off.
        logging.info("No issues; alarm relay deactivated.") # Log off.
    return alert_needed, alerts # Return need and list.
def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    """
    Balance voltage between two banks by transferring charge.
    Transfers from high to low.
    Args:
        stdscr: Curses screen object for TUI display - terminal.
        high (int): High-voltage bank number - from.
        low (int): Low-voltage bank number - to.
        settings (dict): Configuration settings - settings.
        temps_alerts (list): List of temperature alerts - temp issues.
    """
    global balance_start_time, last_balance_time, balancing_active, web_data # Shared.
    # Skip balancing if there are temperature issues - safe.
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.") # Log skip.
        return # Skip.
    logging.info(f"Starting balance from Bank {high} to {low}.") # Log start.
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing started from Bank {high} to Bank {low}") # Log event.
    if len(event_log) > 20:
        event_log.pop(0) # Trim.
    balancing_active = True # On.
    web_data['balancing'] = True # Update web.
    # Read current voltages - now values.
    voltage_high, _, _ = read_voltage_with_retry(high, settings) # High.
    voltage_low, _, _ = read_voltage_with_retry(low, settings) # Low.
    # Safety check: don’t balance if low bank voltage is zero - bad.
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.") # Log skip.
        balancing_active = False # Off.
        web_data['balancing'] = False # Update.
        return # Skip.
    # Set up relays and start the DC-DC converter - connect and on.
    set_relay_connection(high, low, settings) # Connect.
    control_dcdc_converter(True, settings) # On.
    balance_start_time = time.time() # Start time.
    # Animation frames for balancing progress display - spinny thing.
    animation_frames = ['|', '/', '-', '\\'] # Symbols.
    frame_index = 0 # Start at 0.
    height, width = stdscr.getmaxyx() # Get terminal size - dimensions.
    right_half_x = width // 2 # Right side start.
    progress_y = 1 # Top-right start.
    # Run balancing for the configured duration - loop time.
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time # Time passed.
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds']) # % done.
        # Read current voltages during balancing - update.
        voltage_high, _, _ = read_voltage_with_retry(high, settings) # High.
        voltage_low, _, _ = read_voltage_with_retry(low, settings) # Low.
        # Create a progress bar - visual.
        bar_length = 20 # Length.
        filled = int(bar_length * progress) # Filled part.
        bar = '=' * filled + ' ' * (bar_length - filled) # Build bar.
        # Display balancing status in top-right half - show.
        if progress_y < height and right_half_x + 50 < width: # Fits?
            try:
                stdscr.addstr(progress_y, right_half_x, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6)) # Status.
            except curses.error:
                logging.warning("addstr error for balancing status.") # Error.
            try:
                stdscr.addstr(progress_y + 1, right_half_x, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6)) # Bar.
            except curses.error:
                logging.warning("addstr error for balancing progress bar.") # Error.
        else:
            logging.warning("Skipping balancing progress display - out of bounds.") # No fit.
        stdscr.refresh() # Update screen.
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V") # Log progress.
        frame_index += 1 # Next frame.
        # Pet the watchdog every loop - keep alive.
        if settings.get('WatchdogEnabled', False):
            pet_watchdog() # Pet.
        time.sleep(0.01) # Short delay for animation.
    # Finish balancing - done.
    logging.info("Balancing process completed.") # Log done.
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing completed from Bank {high} to Bank {low}") # Log event.
    if len(event_log) > 20:
        event_log.pop(0) # Trim.
    control_dcdc_converter(False, settings) # Off.
    logging.info("Turning off DC-DC converter.") # Log off.
    set_relay_connection(0, 0, settings) # Reset relays - off.
    logging.info("Resetting relay connections to default state.") # Log reset.
    balancing_active = False # Off.
    web_data['balancing'] = False # Update.
    last_balance_time = time.time() # End time.
def compute_bank_medians(calibrated_temps, valid_min):
    """
    Calculate the median temperature for each bank.
    Gets average per group.
    Args:
        calibrated_temps (list): List of calibrated temperatures - adjusted list.
        valid_min (float): Minimum valid temperature - min ok.
    Returns:
        list: Median temperatures for each bank - averages.
    """
    bank_medians = [] # List for medians.
    for start, end in BANK_RANGES: # Loop banks.
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None] # Valid temps.
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0 # Median or 0.
        bank_medians.append(bank_median) # Add.
    return bank_medians # Return list.
def fetch_rrd_history():
    """
    Fetch historical data from RRD database.
    This function uses rrdtool xport to export data as XML, parses it, and returns a list of dicts with time, voltages, and median temp.
    Returns:
        list: List of dicts with historical data - [{'time': ts, 'volt1': v1, 'volt2': v2, 'volt3': v3, 'medtemp': mt}, ...] recent first.
    """
    start = int(time.time()) - (HISTORY_LIMIT * 60) # Last 480 steps (1min each, ~8 hours) - calculate start time.
    try:
        # Run rrdtool xport to get XML data - export command.
        output = subprocess.check_output(['rrdtool', 'xport',
                                          '--start', str(start),
                                          '--end', 'now',
                                          '--step', '60',
                                          'DEF:v1=bms.rrd:volt1:LAST',
                                          'DEF:v2=bms.rrd:volt2:LAST',
                                          'DEF:v3=bms.rrd:volt3:LAST',
                                          'DEF:mt=bms.rrd:medtemp:LAST',
                                          'XPORT:v1:Bank1',
                                          'XPORT:v2:Bank2',
                                          'XPORT:v3:Bank3',
                                          'XPORT:mt:MedianTemp'])
        logging.debug(f"Raw RRD xport output: {output.decode()}") # Log raw XML for debug.
        root = ET.fromstring(output.decode()) # Parse XML.
        data = [] # List for rows.
        for row in root.findall('.//row'): # Loop rows.
            t_elem = row.find('t') # Find timestamp element.
            if t_elem is None or t_elem.text is None: # Skip if missing or empty.
                logging.warning("Skipping RRD row with missing timestamp.") # Log warning.
                continue # Skip row.
            try:
                t = int(t_elem.text) # Timestamp.
            except ValueError:
                logging.warning("Skipping RRD row with invalid timestamp.") # Log warning.
                continue # Skip if not int.
            vs = [] # List for values.
            for v in row.findall('v'): # Loop values.
                if v.text is None: # Skip if missing.
                    vs.append(None) # None for missing.
                    continue
                try:
                    vs.append(float(v.text) if v.text != 'NaN' else None) # Float or None.
                except ValueError:
                    vs.append(None) # None if invalid.
            if len(vs) != 4: # Skip if not 4 values.
                logging.warning(f"Skipping RRD row with incomplete values (got {len(vs)}).") # Log warning.
                continue # Skip.
            data.append({'time': t, 'volt1': vs[0], 'volt2': vs[1], 'volt3': vs[2], 'medtemp': vs[3]}) # Dict.
        logging.debug(f"Fetched {len(data)} history entries from RRD.") # Log count.
        return data[::-1] # Reverse to recent first - order.
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD xport failed: {e}") # Log fail.
        return [] # Empty on error.
    except ET.ParseError as e:
        logging.error(f"RRD XML parse error: {e}. Output was: {output.decode()}") # Log parse error with output.
        return [] # Empty on parse error.
    except FileNotFoundError:
        logging.error("rrdtool not found for fetch. Install rrdtool.") # Log missing.
        return [] # Empty.
    except Exception as e:
        logging.error(f"Unexpected error in fetch_rrd_history: {e}\n{traceback.format_exc()}") # Log unexpected.
        return [] # Empty on unexpected.
def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    """
    Draw the Text User Interface (TUI) to show battery status, alerts, balancing, and event history.
    Updates the terminal display, including ASCII line charts for voltage and median temp history in top-right. Draws chart labels even if no data, showing empty charts.
    Args:
        stdscr: Curses screen object for terminal display - screen.
        voltages (list): List of bank voltages - voltages.
        calibrated_temps (list): List of calibrated temperatures - adjusted temps.
        raw_temps (list): List of raw temperature readings - unadjusted.
        offsets (list): List of temperature offsets - adjustments.
        bank_medians (list): List of median temperatures per bank - averages.
        startup_median (float): Median temperature at startup - start average.
        alerts (list): List of active alerts - issues.
        settings (dict): Configuration settings - settings.
        startup_set (bool): Whether temperature calibration is set - flag.
        is_startup (bool): Whether this is the startup display - first?
    """
    logging.debug("Refreshing TUI.") # Log update.
    stdscr.clear() # Clear screen.
    # Set up colors for the TUI - color setup.
    curses.start_color() # Enable color.
    curses.use_default_colors() # Default colors.
    curses.init_pair(1, curses.COLOR_RED, -1) # Red for errors.
    curses.init_pair(2, curses.COLOR_RED, -1) # Red for high/low.
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Yellow for warnings.
    curses.init_pair(4, curses.COLOR_GREEN, -1) # Green for normal.
    curses.init_pair(5, curses.COLOR_WHITE, -1) # White for text.
    curses.init_pair(6, curses.COLOR_YELLOW, -1) # Yellow for balancing.
    curses.init_pair(7, curses.COLOR_CYAN, -1) # Cyan for headers.
    curses.init_pair(8, curses.COLOR_MAGENTA, -1) # Magenta for invalid.
    height, width = stdscr.getmaxyx() # Size.
    right_half_x = width // 2 # Right start.
    # Display total voltage as ASCII art on the left - big number.
    total_v = sum(voltages) # Total.
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS # Max total.
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS # Min total.
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4) # Color based on value.
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True) # Art.
    roman_lines = roman_v.splitlines() # Lines.
    # Display each line of the ASCII art - draw.
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < right_half_x: # Fits?
            try:
                stdscr.addstr(i + 1, 0, line, v_color) # Draw line.
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.") # Error.
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.") # No fit.
    y_offset = len(roman_lines) + 2 # Down.
    if y_offset >= height: # No space?
        logging.warning("TUI y_offset exceeds height; skipping art.") # Log.
        return # Skip.
    # Battery art template (ASCII representation of a battery) - battery picture.
    battery_art_base = [
        " ___________ ",
        " | | ",
        " | | ",
        " | | ",
        " | | ",
        " | +++ | ",
        " | +++ | ",
        " | | ",
        " | | ",
        " | | ",
        " | | ",
        " | --- | ",
        " | --- | ",
        " | --- | ",
        " | | ",
        " | | ",
        " |_________| "
    ]
    art_height = len(battery_art_base) # Height.
    art_width = len(battery_art_base[0]) # Width.
    # Draw battery art for each bank on the left - draw batteries.
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS # Repeat for banks.
        if y_offset + row < height and len(full_line) < right_half_x: # Fits?
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4)) # Green art.
            except curses.error:
                logging.warning(f"addstr error for art row {row}.") # Error.
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.") # No fit.
    # Add voltage and temperature data to each battery - overlay info.
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width # Position.
        # Display voltage - show V.
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V" # Format.
        v_color = curses.color_pair(8) if voltages[bank_id] == 0.0 else \
                 curses.color_pair(2) if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else \
                 curses.color_pair(3) if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else \
                 curses.color_pair(4) # Color.
        v_center = start_pos + (art_width - len(v_str)) // 2 # Center.
        v_y = y_offset + 1 # Position.
        if v_y < height and v_center + len(v_str) < right_half_x: # Fits?
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color) # Draw.
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.") # Error.
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.") # No.
        # Display temperatures for each sensor in the bank - show temps.
        start, end = BANK_RANGES[bank_id] # Range.
        for local_ch, ch in enumerate(range(start, end + 1), 0):
            idx = ch - 1 # Index.
            raw = raw_temps[idx] if idx < len(raw_temps) else 0 # Raw.
            calib = calibrated_temps[idx] # Calib.
            calib_str = f"{calib:.1f}" if calib is not None else "Inv" # Format or invalid.
            # During startup, show raw and offset values - extra info.
            if is_startup:
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv" # Raw format.
                offset_str = f"{offsets[idx]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A" # Offset.
                detail = f" ({raw_str}/{offset_str})" # Combine.
            else:
                detail = "" # No extra.
            t_str = f"C{local_ch+1}: {calib_str}{detail}" # Temp string.
            t_color = curses.color_pair(8) if "Inv" in calib_str else \
                     curses.color_pair(2) if calib > settings['high_threshold'] else \
                     curses.color_pair(3) if calib < settings['low_threshold'] else \
                     curses.color_pair(4) # Color.
            t_center = start_pos + (art_width - len(t_str)) // 2 # Center.
            t_y = y_offset + 2 + local_ch # Position.
            if t_y < height and t_center + len(t_str) < right_half_x: # Fits?
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color) # Draw.
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.") # Error.
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.") # No.
        # Display bank median temperature - average.
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C" # Format.
        med_center = start_pos + (art_width - len(med_str)) // 2 # Center.
        med_y = y_offset + 15 # Bottom.
        if med_y < height and med_center + len(med_str) < right_half_x: # Fits?
            try:
                stdscr.addstr(med_y, med_center, med_str, curses.color_pair(7)) # Cyan.
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.") # Error.
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.") # No.
    y_offset += art_height + 2 # Down.
    # Display ADC readings if there’s space - raw data.
    if y_offset >= height:
        logging.warning("Skipping ADC/readings - out of bounds.") # No space.
    else:
        for i in range(1, NUM_BANKS + 1): # Loop banks.
            voltage, readings, adc_values = read_voltage_with_retry(i, settings) # Read.
            logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}") # Log.
            if voltage is None:
                voltage = 0.0 # Zero.
            if y_offset < height: # Fits?
                try:
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", curses.color_pair(5)) # Draw ADC.
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.") # Error.
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.") # No.
            y_offset += 1 # Down.
            if y_offset < height: # Fits?
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", curses.color_pair(5)) # Draw readings.
                    else:
                        stdscr.addstr(y_offset, 0, " [Readings: No data]", curses.color_pair(5)) # No data.
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.") # Error.
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.") # No.
            y_offset += 1 # Down.
    y_offset += 1 # Space.
    # Display startup median temperature - start average.
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A" # Format.
    if y_offset < height: # Fits?
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7)) # Cyan.
        except curses.error:
            logging.warning("addstr error for startup median.") # Error.
    else:
        logging.warning("Skipping startup median - out of bounds.") # No.
    y_offset += 2 # Space.
    # Display alerts section on the left - issues.
    if y_offset < height: # Fits?
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", curses.color_pair(7)) # Header cyan.
        except curses.error:
            logging.warning("addstr error for alerts header.") # Error.
    y_offset += 1 # Down.
    # Display individual alerts - list them.
    if alerts:
        for alert in alerts: # Loop.
            if y_offset < height and len(alert) < right_half_x: # Fits left?
                try:
                    stdscr.addstr(y_offset, 0, alert, curses.color_pair(8)) # Magenta.
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.") # Error.
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.") # No.
            y_offset += 1 # Down.
    else:
        if y_offset < height: # Fits?
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", curses.color_pair(4)) # Green.
            except curses.error:
                logging.warning("addstr error for no alerts message.") # Error.
        else:
            logging.warning("Skipping no alerts message - out of bounds.") # No.
    # Fetch history and draw ASCII charts in top-right - trends.
    history = fetch_rrd_history() # Get data.
    y_chart = 1 # Top-right y start.
    chart_width = 30 # Chart width for ASCII.
    chart_height = 5 # Chart height.
    # Draw per-bank voltage charts - voltages. Draw labels and empty if no data.
    for b in range(3):
        volt_hist = [d[f'volt{b+1}'] for d in history if d[f'volt{b+1}'] is not None] if history else [] # Per bank or empty.
        chart = ascii_line_chart(volt_hist, width=chart_width, height=chart_height) # Generate or empty.
        label = f"Bank {b+1} V: "
        for i, line in enumerate(chart.splitlines()): # Lines.
            full_line = label + line if i == 0 else ' ' * len(label) + line # Label on first line.
            if y_chart + i < height and right_half_x + len(full_line) < width: # Fits?
                try:
                    stdscr.addstr(y_chart + i, right_half_x, full_line, curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning(f"addstr error for Bank {b+1} voltage chart line {i}.") # Error.
            else:
                logging.warning(f"Skipping Bank {b+1} voltage chart line {i} - out of bounds.") # No fit.
        y_chart += chart_height + 1 # Space down.
    # Draw median temp chart - temp.
    temp_hist = [d['medtemp'] for d in history if d['medtemp'] is not None] if history else [] # Median or empty.
    temp_chart = ascii_line_chart(temp_hist, width=chart_width, height=chart_height) # Generate or empty.
    label = "Med Temp: "
    for i, line in enumerate(temp_chart.splitlines()): # Lines.
        full_line = label + line if i == 0 else ' ' * len(label) + line # Label on first line.
        if y_chart + i < height and right_half_x + len(full_line) < width: # Fits?
            try:
                stdscr.addstr(y_chart + i, right_half_x, full_line, curses.color_pair(7)) # Cyan.
            except curses.error:
                logging.warning(f"addstr error for median temp chart line {i}.") # Error.
        else:
            logging.warning(f"Skipping median temp chart line {i} - out of bounds.") # No fit.
    # Display event history on bottom-right half - history.
    y_offset = height // 2 # Middle for bottom.
    if y_offset < height: # Fits?
        try:
            stdscr.addstr(y_offset, right_half_x, "Event History:", curses.color_pair(7)) # Cyan header.
        except curses.error:
            logging.warning("addstr error for event history header.") # Error.
    y_offset += 1 # Down.
    for event in event_log[-20:]: # Last 20.
        if y_offset < height and len(event) < width - right_half_x: # Fits right?
            try:
                stdscr.addstr(y_offset, right_half_x, event, curses.color_pair(5)) # White.
            except curses.error:
                logging.warning(f"addstr error for event '{event}'.") # Error.
            y_offset += 1 # Down.
        else:
            logging.warning(f"Skipping event '{event}' - out of bounds.") # No.
    pet_watchdog()
    stdscr.refresh() # Update screen.
def setup_watchdog(timeout=30):
    """
    Set up the hardware watchdog timer for the Raspberry Pi.
    Detects Pi model and loads appropriate watchdog module (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 and newer).
    Falls back to opening /dev/watchdog for unknown models.
    Args:
        timeout (int): Watchdog timeout in seconds (default: 30) - reset time.
    """
    global watchdog_fd # Shared handle.
    try:
        # Detect Pi model - what Pi?
        model = "Unknown" # Default.
        if os.path.exists('/proc/device-tree/model'):
            with open('/proc/device-tree/model', 'r') as f: # Open model file.
                model = f.read().strip().lower() # Read and clean.
        logging.info(f"Detected Raspberry Pi model: {model}") # Log model.
        # Select watchdog module based on model - choose driver.
        if 'raspberry pi' in model and not 'raspberry pi 5' in model: # Pi 1-4.
            module = 'bcm2835_wdt' # Old.
        else: # Pi 5+.
            module = 'rp1-wdt' # New.
            logging.info("Assuming rp1-wdt for Pi 5 or newer model") # Log.
        # Load watchdog module - activate.
        os.system(f'sudo modprobe {module}') # System command.
        logging.info(f"Loaded watchdog module: {module}") # Log loaded.
        time.sleep(1) # Wait init.
        # Verify /dev/watchdog exists - check device.
        if not os.path.exists(WATCHDOG_DEV):
            logging.error(f"Watchdog device {WATCHDOG_DEV} not found. Attempting to open anyway.") # Log error.
            watchdog_fd = None # None.
            try:
                watchdog_fd = open(WATCHDOG_DEV, 'wb') # Try open.
                logging.info(f"Opened {WATCHDOG_DEV} despite initial check failure") # Log success.
            except IOError as e:
                logging.error(f"Failed to open watchdog: {e}. Ensure appropriate module loaded (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 or newer).") # Log fail.
                return # Stop.
        # Open watchdog device - connect.
        watchdog_fd = open(WATCHDOG_DEV, 'wb') # Open write.
        logging.debug(f"Opened watchdog device: {WATCHDOG_DEV}") # Log open.
        # Set timeout - set time.
        try:
            magic = ord('W') << 8 | 0x06 # Code for set.
            fcntl.ioctl(watchdog_fd, magic, struct.pack("I", timeout)) # Set.
            logging.info(f"Watchdog set with timeout {timeout}s") # Log set.
        except IOError as e:
            logging.warning(f"Failed to set watchdog timeout: {e}. Using default timeout.") # Log default.
        logging.debug("Watchdog successfully initialized") # Log done.
    except Exception as e:
        logging.error(f"Failed to setup watchdog: {e}. Ensure appropriate module loaded (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 or newer).") # Log fail.
        watchdog_fd = None # None.
def pet_watchdog():
    """
    Pet the watchdog to prevent system reset.
    Tells hardware "still running".
    """
    global watchdog_fd # Shared.
    if watchdog_fd:
        try:
            watchdog_fd.write(b'w') # Write 'w' to reset the watchdog timer - pet.
            logging.debug("Watchdog petted successfully") # Log pet.
        except IOError as e:
            logging.error(f"Failed to pet watchdog: {e}") # Log fail.
def close_watchdog():
    """
    Close the watchdog on shutdown.
    """
    global watchdog_fd # Shared.
    if watchdog_fd:
        try:
            watchdog_fd.write(b'V') # Disable on clean exit - off.
            watchdog_fd.close() # Close.
        except IOError:
            pass # Ignore error.
def startup_self_test(settings, stdscr):
    """
    Run startup tests to check configuration, hardware, sensors, and balancing.
    Checks everything at start.
    Args:
        settings (dict): Configuration settings - settings.
        stdscr: Curses screen object for display - screen.
    Returns:
        list: List of failure alerts - issues or empty.
    """
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets # Shared.
    # Skip if startup test is disabled - check flag.
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.") # Log skip.
        return [] # Empty.
    retries = 0 # Start count.
    while True: # Loop for retries.
        logging.info(f"Starting self-test attempt {retries + 1}") # Log attempt.
        alerts = [] # Failure list.
        stdscr.clear() # Clear screen.
        y = 0 # Position.
        # Display test title - show starting.
        if y < stdscr.getmaxyx()[0]: # Fits?
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1)) # Red.
            except curses.error:
                logging.warning("addstr error for title.") # Error.
        y += 2 # Down.
        stdscr.refresh() # Update.
        # Step 1: Validate configuration - check settings.
        logging.info("Step 1: Validating configuration parameters.") # Log step.
        logging.debug(f"Configuration details: NumberOfBatteries={settings['NumberOfBatteries']}, "
                      f"I2C_BusNumber={settings['I2C_BusNumber']}, "
                      f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
                      f"RelayAddress=0x{settings['RelayAddress']:02x}, "
                      f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
                      f"NumChannels={settings['num_channels']}, ScalingFactor={settings['scaling_factor']}") # Details.
        if y < stdscr.getmaxyx()[0]: # Fits?
            try:
                stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4)) # Green.
            except curses.error:
                logging.warning("addstr error for step 1.") # Error.
        stdscr.refresh() # Update.
        pet_watchdog()
        time.sleep(0.5) # Pause.
        # Check if number of banks matches expected - right number?
        if settings['NumberOfBatteries'] != NUM_BANKS:
            alert = f"Config mismatch: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}." # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.warning(f"Config mismatch detected: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}.") # Log.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for config mismatch.") # Error.
        else:
            logging.debug("Configuration validation passed: NumberOfBatteries matches NUM_BANKS.") # Good.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for config OK.") # Error.
        y += 2 # Down.
        stdscr.refresh() # Update.
        # Step 2: Test hardware connectivity - check connections.
        logging.info("Step 2: Testing hardware connectivity (I2C and Modbus).") # Log step.
        if y < stdscr.getmaxyx()[0]: # Fits?
            try:
                stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4)) # Green.
            except curses.error:
                logging.warning("addstr error for step 2.") # Error.
        stdscr.refresh() # Update.
        pet_watchdog()
        time.sleep(0.5) # Pause.
        # Test I2C connectivity - hardware talk.
        logging.debug(f"Testing I2C connectivity on bus {settings['I2C_BusNumber']}: "
                      f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                      f"Relay=0x{settings['RelayAddress']:02x}") # Details.
        try:
            if bus: # Hardware?
                logging.debug(f"Selecting I2C channel 0 on multiplexer 0x{settings['MultiplexerAddress']:02x}") # Log.
                choose_channel(0, settings['MultiplexerAddress']) # Select.
                logging.debug(f"Reading byte from VoltageMeter at 0x{settings['VoltageMeterAddress']:02x}") # Log.
                bus.read_byte(settings['VoltageMeterAddress']) # Test read.
                logging.debug("I2C connectivity test passed for all devices.") # Good.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for I2C OK.") # Error.
        except (IOError, AttributeError) as e:
            alert = f"I2C connectivity failure: {str(e)}" # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.error(f"I2C connectivity failure: {str(e)}. Bus={settings['I2C_BusNumber']}, "
                          f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                          f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                          f"Relay=0x{settings['RelayAddress']:02x}") # Details.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for I2C failure.") # Error.
        # Test Modbus connectivity - sensor network.
        logging.debug(f"Testing Modbus connectivity to {settings['ip']}:{settings['modbus_port']} with "
                      f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}") # Details.
        try:
            test_query = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1) # Test one.
            if isinstance(test_query, str) and "Error" in test_query:
                raise ValueError(test_query) # Error.
            logging.debug(f"Modbus test successful: Received {len(test_query)} values: {test_query}") # Good.
            if y + 2 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 2, 0, "Modbus OK.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for Modbus OK.") # Error.
        except Exception as e:
            alert = f"Modbus test failure: {str(e)}" # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.error(f"Modbus test failure: {str(e)}. Connection={settings['ip']}:{settings['modbus_port']}, "
                          f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}") # Details.
            if y + 2 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 2, 0, f"Modbus failure: {str(e)}", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for Modbus failure.") # Error.
        y += 3 # Down.
        stdscr.refresh() # Update.
        # Step 3: Initial sensor reads - first data.
        logging.info("Step 3: Performing initial sensor reads (temperature and voltage).") # Log step.
        if y < stdscr.getmaxyx()[0]: # Fits?
            try:
                stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4)) # Green.
            except curses.error:
                logging.warning("addstr error for step 3.") # Error.
        stdscr.refresh() # Update.
        pet_watchdog()
        time.sleep(0.5) # Pause.
        # Test temperature sensor reading - temps.
        logging.debug(f"Reading {settings['num_channels']} temperature channels from {settings['ip']}:{settings['modbus_port']} "
                      f"with query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}, "
                      f"max_retries={settings['max_retries']}, retry_backoff_base={settings['retry_backoff_base']}") # Details.
        initial_temps = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'],
                                         settings['num_channels'], settings['scaling_factor'],
                                         settings['max_retries'], settings['retry_backoff_base']) # Read.
        if isinstance(initial_temps, str): # Error?
            alert = f"Initial temp read failure: {initial_temps}" # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.error(f"Initial temperature read failure: {initial_temps}") # Log.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, "Temp read failure.", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for temp failure.") # Error.
        else:
            logging.debug(f"Initial temperature read successful: {len(initial_temps)} values, {initial_temps}") # Good.
            valid_count = sum(1 for t in initial_temps if t > settings['valid_min']) # Count good.
            logging.debug(f"Valid temperature readings: {valid_count}/{settings['num_channels']}, valid_min={settings['valid_min']}") # Log count.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for temps OK.") # Error.
        # Test voltage reading - voltages.
        logging.debug(f"Reading voltages for {NUM_BANKS} banks with VoltageDividerRatio={settings['VoltageDividerRatio']}") # Details.
        initial_voltages = [] # List.
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings) # Read.
            logging.debug(f"Bank {i} voltage read: Voltage={voltage}, Readings={readings}, ADC={adc_values}, "
                          f"CalibrationFactor={settings[f'Sensor{i}_Calibration']}") # Details.
            initial_voltages.append(voltage if voltage is not None else 0.0) # Add or 0.
        if any(v == 0.0 for v in initial_voltages): # Any zero?
            alert = "Initial voltage read failure: Zero voltage on one or more banks." # Alert.
            alerts.append(alert) # Add.
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
            if len(event_log) > 20:
                event_log.pop(0) # Trim.
            logging.error(f"Initial voltage read failure: Voltages={initial_voltages}") # Log.
            if y + 2 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for voltage failure.") # Error.
        else:
            logging.debug(f"Initial voltage read successful: Voltages={initial_voltages}") # Good.
            if y + 2 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for voltages OK.") # Error.
        # Set up temperature calibration if all readings are valid - adjust.
        if isinstance(initial_temps, list):
            valid_count = sum(1 for t in initial_temps if t > settings['valid_min']) # Count.
            if valid_count == settings['num_channels']: # All good?
                startup_median = statistics.median(initial_temps) # Median.
                logging.debug(f"Calculated startup median: {startup_median:.1f}°C") # Log.
                # Load existing offsets or calculate new ones if offsets.txt missing - load or new.
                _, startup_offsets = load_offsets(settings['num_channels']) # Load.
                if startup_offsets is None:
                    startup_offsets = [startup_median - t for t in initial_temps] # Calculate.
                    save_offsets(startup_median, startup_offsets) # Save.
                    logging.info(f"Calculated and saved new offsets on first run: {startup_offsets}") # Log new.
                else:
                    logging.info(f"Using existing offsets from offsets.txt: {startup_offsets}") # Log existing.
                startup_set = True # Set.
            else:
                logging.warning(f"Temperature calibration skipped: Only {valid_count}/{settings['num_channels']} valid readings.") # Skip.
                startup_median = None # Reset.
                startup_offsets = None # Reset.
                startup_set = False # Not set.
        y += 3 # Down.
        stdscr.refresh() # Update.
        # Step 4: Balancer verification (only if no previous failures and valid voltages) - test balance.
        if not alerts and all(v > 0 for v in initial_voltages): # Good so far?
            logging.info("Step 4: Verifying balancer functionality.") # Log step.
            if y < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for step 4.") # Error.
            y += 1 # Down.
            stdscr.refresh() # Update.
            pet_watchdog()
            time.sleep(0.5) # Pause.
            # Read initial voltages for all banks - start values.
            initial_bank_voltages = []
            for bank in range(1, NUM_BANKS + 1):
                voltage, _, _ = read_voltage_with_retry(bank, settings) # Read.
                initial_bank_voltages.append(voltage if voltage is not None else 0.0) # Add.
            if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                try:
                    stdscr.addstr(y + 1, 0, f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for initial bank voltages.") # Error.
            logging.debug(f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V") # Log.
            y += 2 # Down.
            stdscr.refresh() # Update.
            # Test all possible balancing pairs, ordered by highest to lowest initial voltage - all combos.
            bank_voltages_dict = {b: initial_bank_voltages[b-1] for b in range(1, NUM_BANKS + 1)} # Dict.
            sorted_banks = sorted(bank_voltages_dict, key=bank_voltages_dict.get, reverse=True) # Sort high to low.
            pairs = [] # List.
            for source in sorted_banks:
                for dest in [b for b in range(1, NUM_BANKS + 1) if b != source]:
                    pairs.append((source, dest) ) # All pairs.
            test_duration = settings['test_balance_duration'] # Time.
            read_interval = settings['test_read_interval'] # Interval.
            min_delta = settings['min_voltage_delta'] # Min change.
            logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                          f"read_interval={read_interval}s, min_voltage_delta={min_delta}V") # Log params.
            for source, dest in pairs: # Loop pairs.
                logging.debug(f"Testing balance from Bank {source} to Bank {dest}") # Log pair.
                if y < stdscr.getmaxyx()[0]: # Fits?
                    try:
                        stdscr.addstr(y, 0, f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.", curses.color_pair(6)) # Yellow.
                    except curses.error:
                        logging.warning("addstr error for testing balance.") # Error.
                stdscr.refresh() # Update.
                logging.info(f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.") # Log.
                # Skip if temperature anomalies exist - safe.
                temp_anomaly = False # Flag.
                if initial_temps and isinstance(initial_temps, list): # Have temps?
                    for t in initial_temps:
                        if t > settings['high_threshold'] or t < settings['low_threshold']: # Bad temp?
                            temp_anomaly = True # Flag.
                            break # Stop.
                if temp_anomaly:
                    alert = f"Skipping balance test from Bank {source} to Bank {dest}: Temp anomalies." # Alert.
                    alerts.append(alert) # Add.
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
                    if len(event_log) > 20:
                        event_log.pop(0) # Trim.
                    logging.warning(f"Skipping balance test from Bank {source} to Bank {dest}: Temperature anomalies detected.") # Log skip.
                    if y + 1 < stdscr.getmaxyx()[0]: # Fits?
                        try:
                            stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2)) # Red.
                        except curses.error:
                            logging.warning("addstr error for skipped temp.") # Error.
                    y += 2 # Down.
                    stdscr.refresh() # Update.
                    continue # Next pair.
                # Read initial voltages - start.
                initial_source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Source.
                initial_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Dest.
                pet_watchdog()
                time.sleep(0.5) # Pause.
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Initial - Bank {source}={initial_source_v:.2f}V, Bank {dest}={initial_dest_v:.2f}V") # Log.
                # Start test balancing - go.
                set_relay_connection(source, dest, settings) # Connect.
                control_dcdc_converter(True, settings) # On.
                start_time = time.time() # Start.
                # Track voltage changes during test - trends.
                source_trend = [initial_source_v] # List source.
                dest_trend = [initial_dest_v] # List dest.
                progress_y = y + 1 # Progress position.
                # Run test for duration - loop.
                while time.time() - start_time < test_duration:
                    pet_watchdog()
                    time.sleep(read_interval) # Wait.
                    source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Read source.
                    dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Read dest.
                    source_trend.append(source_v) # Add.
                    dest_trend.append(dest_v) # Add.
                    logging.debug(f"Balance test from Bank {source} to Bank {dest}: Bank {source}={source_v:.2f}V, Bank {dest}={dest_v:.2f}V") # Log.
                    elapsed = time.time() - start_time # Time.
                    if progress_y < stdscr.getmaxyx()[0]: # Fits?
                        try:
                            stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6)) # Clear line.
                            stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, Bank {source} {source_v:.2f}V, Bank {dest} {dest_v:.2f}V", curses.color_pair(6)) # Show progress.
                        except curses.error:
                            logging.warning("addstr error in startup balance progress.") # Error.
                    stdscr.refresh() # Update.
                # Read final voltages - end.
                final_source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Source.
                final_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Dest.
                pet_watchdog()
                time.sleep(0.5) # Pause.
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Final - Bank {source}={final_source_v:.2f}V, Bank {dest}={final_dest_v:.2f}V") # Log.
                control_dcdc_converter(False, settings) # Off.
                set_relay_connection(0, 0, settings) # Reset.
                if progress_y + 1 < stdscr.getmaxyx()[0]: # Fits?
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6)) # Yellow.
                    except curses.error:
                        logging.warning("addstr error for analyzing.") # Error.
                stdscr.refresh() # Update.
                # Analyze voltage changes - check if worked.
                if len(source_trend) >= 3: # Enough data?
                    source_change = final_source_v - initial_source_v # Source change.
                    dest_change = final_dest_v - initial_dest_v # Dest change.
                    logging.debug(f"Balance test from Bank {source} to Bank {dest} analysis: Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V, Min change={min_delta}V") # Log analysis.
                    # Check if changes are as expected (source decreases, destination increases) - correct direction?
                    if source_change >= 0 or dest_change <= 0 or abs(source_change) < min_delta or dest_change < min_delta:
                        alert = f"Balance test from Bank {source} to Bank {dest} failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V)." # Alert.
                        alerts.append(alert) # Add.
                        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
                        if len(event_log) > 20:
                            event_log.pop(0) # Trim.
                        logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Source did not decrease or destination did not increase sufficiently.") # Log fail.
                        if progress_y + 1 < stdscr.getmaxyx()[0]: # Fits?
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(2)) # Red.
                            except curses.error:
                                logging.warning("addstr error for test failed insufficient change.") # Error.
                    else:
                        logging.debug(f"Balance test from Bank {source} to Bank {dest} passed: Correct trend and sufficient voltage change.") # Good.
                        if progress_y + 1 < stdscr.getmaxyx()[0]: # Fits?
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test passed (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(4)) # Green.
                            except curses.error:
                                logging.warning("addstr error for test passed.") # Error.
                else:
                    alert = f"Balance test from Bank {source} to Bank {dest} failed: Insufficient readings." # Alert.
                    alerts.append(alert) # Add.
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Log.
                    if len(event_log) > 20:
                        event_log.pop(0) # Trim.
                    logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Only {len(source_trend)} readings collected.") # Log fail.
                    if progress_y + 1 < stdscr.getmaxyx()[0]: # Fits?
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2)) # Red.
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient readings.") # Error.
                stdscr.refresh() # Update.
                y = progress_y + 2 # Down.
                pet_watchdog()
                time.sleep(2) # Pause.
        # Store test results - save.
        startup_alerts = alerts # Save.
        if alerts:
            startup_failed = True # Fail.
            startup_alerts = alerts # Save.
            logging.error("Startup self-test failures: " + "; ".join(alerts)) # Log failures.
            send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings) # Email.
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH) # Alarm on.
            stdscr.clear() # Clear.
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Startup failures: " + "; ".join(alerts), curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for self-test failures.") # Error.
            if stdscr.getmaxyx()[0] > 2:
                try:
                    stdscr.addstr(2, 0, "Alarm activated. Retrying in 2 minutes...", curses.color_pair(2)) # Red.
                except curses.error:
                    logging.warning("addstr error for retry message.") # Error.
            stdscr.refresh() # Update.
            # Pet the watchdog before and after long sleep - keep alive.
            if settings.get('WatchdogEnabled', False):
                pet_watchdog() # Pet.
            for _ in range(12): # 120s / 10s
                pet_watchdog()
                time.sleep(10)
            if settings.get('WatchdogEnabled', False):
                pet_watchdog() # Pet.
            retries += 1 # Next try.
            continue # Retry.
        else:
            startup_failed = False # Good.
            startup_alerts = [] # Empty.
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.LOW) # Off.
            stdscr.clear() # Clear.
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Self-Test Passed. Proceeding to main loop.", curses.color_pair(4)) # Green.
                except curses.error:
                    logging.warning("addstr error for self-test OK.") # Error.
            stdscr.refresh() # Update.
            pet_watchdog()
            time.sleep(2) # Pause.
            logging.info("Startup self-test passed.") # Log good.
            return [] # Proceed.
class BMSRequestHandler(BaseHTTPRequestHandler):
    """
    Handles HTTP requests for the web interface and API.
    Web request handler, now with /api/history for time-series data.
    """
    def __init__(self, request, client_address, server):
        """
        Initialize the handler with settings.
        Args:
            request: HTTP request - ask.
            client_address: Client's address - who.
            server: Web server instance - host.
        """
        self.settings = server.settings # Store settings.
        super().__init__(request, client_address, server) # Parent init.
    def log_message(self, format, *args):
        pass  # Suppress console output; optionally use logging.info instead.
    def do_GET(self):
        """
        Handle GET requests (e.g., load dashboard or API data).
        Get stuff.
        """
        parsed_path = urlparse(self.path) # Parse path.
        path = parsed_path.path # Path.
        # Check authentication if required - login?
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401) # No.
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"') # Ask login.
            self.end_headers() # End.
            return # Stop.
        # Set CORS headers if enabled - allow others.
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Origins.
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Methods.
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Headers.
        # Serve the dashboard page - main page.
        if path == '/':
            self.send_response(200) # OK.
            self.send_header('Content-type', 'text/html') # HTML.
            self.end_headers() # End.
            # HTML content for the web dashboard - page code, updated with Chart.js canvas and JS, blocks reversed.
            html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background-color: #2c3e50; color: white; padding: 15px; border-radius: 5px; }
        .status-card { background-color: white; border-radius: 5px; padding: 15px; margin: 10px 0; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .battery { display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 5px; background-color: #f9f9f9; }
        .voltage { font-size: 1.2em; font-weight: bold; }
        .temperature { font-size: 0.9em; }
        .alert { color: #e74c3c; font-weight: bold; }
        .normal { color: #27ae60; }
        .warning { color: #f39c12; }
        .button { background-color: #3498db; color: white; border: none; padding: 10px 15px; border-radius: 3px; cursor: pointer; }
        .button:hover { background-color: #2980b9; }
        .button:disabled { background-color: #95a5a6; cursor: not-allowed; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Battery Management System</h1>
            <p>Status: <span id="system-status">Loading...</span></p>
            <p>Last Update: <span id="last-update">-</span></p>
        </div>
        <div class="status-card">
            <h2>System Information</h2>
            <p>Total Voltage: <span id="total-voltage">-</span></p>
            <p>Balancing: <span id="balancing-status">No</span></p>
        </div>
        <div class="status-card">
            <h2>Actions</h2>
            <button id="refresh-btn" class="button">Refresh</button>
            <button id="balance-btn" class="button" disabled>Balance Now</button>
        </div>
        <div class="status-card">
            <h2>Alerts</h2>
            <div id="alerts-container"></div>
        </div>
        <div class="status-card">
            <h2>Battery Banks</h2>
            <div id="battery-container" class="grid"></div>
        </div>
        <div class="status-card">
            <h2>Time-Series Charts</h2>
            <canvas id="bmsChart" width="800" height="400"></canvas>
        </div>
    </div>
    <script>
        function updateStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('system-status').textContent = data.system_status;
                    document.getElementById('last-update').textContent = new Date(data.last_update * 1000).toLocaleString();
                    document.getElementById('total-voltage').textContent = data.total_voltage.toFixed(2) + 'V';
                    document.getElementById('balancing-status').textContent = data.balancing ? 'Yes' : 'No';
                    const batteryContainer = document.getElementById('battery-container');
                    batteryContainer.innerHTML = '';
                    data.voltages.forEach((voltage, index) => {
                        const bankDiv = document.createElement('div');
                        bankDiv.className = 'battery';
                        bankDiv.innerHTML = `
                            <h3>Bank ${index + 1}</h3>
                            <p class="voltage ${voltage === 0 ? 'alert' : (voltage > 21 || voltage < 18.5) ? 'warning' : 'normal'}">
                                ${voltage.toFixed(2)}V
                            </p>
                            <div class="temperatures">
                                ${data.temperatures.slice(index * 8, (index + 1) * 8).map((temp, tempIndex) => `
                                    <p class="temperature ${temp === null ? 'alert' : (temp > 60 || temp < 0) ? 'warning' : 'normal'}">
                                        C${tempIndex + 1}: ${temp !== null ? temp.toFixed(1) + '°C' : 'N/A'}
                                    </p>
                                `).join('')}
                            </div>
                        `;
                        batteryContainer.appendChild(bankDiv);
                    });
                    const alertsContainer = document.getElementById('alerts-container');
                    if (data.alerts.length > 0) {
                        alertsContainer.innerHTML = data.alerts.map(alert => `<p class="alert">${alert}</p>`).join('');
                    } else {
                        alertsContainer.innerHTML = '<p class="normal">No alerts</p>';
                    }
                    const balanceBtn = document.getElementById('balance-btn');
                    balanceBtn.disabled = data.balancing || data.alerts.length > 0;
                })
                .catch(error => {
                    console.error('Error fetching status:', error);
                    document.getElementById('system-status').textContent = 'Error';
                });
        }
        function updateChart() {
            fetch('/api/history')
                .then(response => response.json())
                .then(data => {
                    const hist = data.history;
                    const labels = hist.map(h => new Date(h.time * 1000).toLocaleTimeString());
                    const datasets = [
                        { label: 'Bank 1 V', data: hist.map(h => h.volt1), borderColor: 'green' },
                        { label: 'Bank 2 V', data: hist.map(h => h.volt2), borderColor: 'blue' },
                        { label: 'Bank 3 V', data: hist.map(h => h.volt3), borderColor: 'red' },
                        { label: 'Median Temp °C', data: hist.map(h => h.medtemp), borderColor: 'cyan', yAxisID: 'temp' }
                    ];
                    const ctx = document.getElementById('bmsChart').getContext('2d');
                    new Chart(ctx, {
                        type: 'line',
                        data: { labels, datasets },
                        options: {
                            scales: {
                                y: { type: 'linear', position: 'left', title: { display: true, text: 'Voltage (V)' } },
                                temp: { type: 'linear', position: 'right', title: { display: true, text: 'Temp (°C)' }, grid: { drawOnChartArea: false } }
                            }
                        }
                    });
                });
        }
        function initiateBalance() {
            fetch('/api/balance', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Balancing initiated');
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Error initiating balance:', error);
                    alert('Error initiating balance');
                });
        }
        document.getElementById('refresh-btn').addEventListener('click', updateStatus);
        document.getElementById('balance-btn').addEventListener('click', initiateBalance);
        updateStatus();
        updateChart(); // Initial chart load
        setInterval(updateStatus, 5000);
        setInterval(updateChart, 60000); // 1min chart refresh for faster testing
    </script>
</body>
</html>"""
            self.wfile.write(html.encode('utf-8')) # Send page.
        # Serve API status data - data for apps.
        elif path == '/api/status':
            self.send_response(200) # OK.
            self.send_header('Content-type', 'application/json') # JSON.
            self.end_headers() # End.
            # Prepare JSON response - data pack.
            response = {
                'voltages': web_data['voltages'],
                'temperatures': web_data['temperatures'],
                'alerts': web_data['alerts'],
                'balancing': web_data['balancing'],
                'last_update': web_data['last_update'],
                'system_status': web_data['system_status'],
                'total_voltage': sum(web_data['voltages'])
            }
            self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
        # Serve API history data - time-series for charts.
        elif path == '/api/history':
            self.send_response(200) # OK.
            self.send_header('Content-type', 'application/json') # JSON.
            self.end_headers() # End.
            history = fetch_rrd_history() # Fetch.
            response = {'history': history} # Pack.
            self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
        else:
            self.send_response(404) # Not found.
            self.end_headers() # End.
    def do_POST(self):
        """
        Handle POST requests (e.g., initiate balancing).
        Post stuff.
        """
        parsed_path = urlparse(self.path) # Parse.
        path = parsed_path.path # Path.
        # Check authentication if required - login.
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401) # No.
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"') # Ask.
            self.end_headers() # End.
            return # Stop.
        # Set CORS headers if enabled - allow.
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Origins.
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Methods.
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Headers.
        # Handle balance request - start balance.
        if path == '/api/balance':
            global balancing_active # Shared.
            # Check if already balancing - busy?
            if balancing_active:
                self.send_response(400) # Bad.
                self.send_header('Content-type', 'application/json') # JSON.
                self.end_headers() # End.
                response = {'success': False, 'message': 'Balancing already in progress'} # Msg.
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
                return # Stop.
            # Check for active alerts - issues?
            if len(web_data['alerts']) > 0:
                self.send_response(400) # Bad.
                self.send_header('Content-type', 'application/json') # JSON.
                self.end_headers() # End.
                response = {'success': False, 'message': 'Cannot balance with active alerts'} # Msg.
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
                return # Stop.
            voltages = web_data['voltages'] # Get voltages.
            # Check if there are enough banks - enough?
            if len(voltages) < 2:
                self.send_response(400) # Bad.
                self.send_header('Content-type', 'application/json') # JSON.
                self.end_headers() # End.
                response = {'success': False, 'message': 'Not enough battery banks'} # Msg.
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
                return # Stop.
            # Find high and low banks - max min.
            max_v = max(voltages) # Max.
            min_v = min(voltages) # Min.
            high_bank = voltages.index(max_v) + 1 # High ID.
            low_bank = voltages.index(min_v) + 1 # Low ID.
            # Check voltage difference - enough diff?
            if max_v - min_v < self.settings['VoltageDifferenceToBalance']:
                self.send_response(400) # Bad.
                self.send_header('Content-type', 'application/json') # JSON.
                self.end_headers() # End.
                response = {'success': False, 'message': 'Voltage difference too small for balancing'} # Msg.
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
                return # Stop.
            # Start balancing - go.
            balancing_active = True # On.
            self.send_response(200) # OK.
            self.send_header('Content-type', 'application/json') # JSON.
            self.end_headers() # End.
            response = {'success': True, 'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'} # Msg.
            self.wfile.write(json.dumps(response).encode('utf-8')) # Send.
        else:
            self.send_response(404) # Not.
            self.end_headers() # End.
    def do_OPTIONS(self):
        """
        Handle OPTIONS requests for CORS preflight.
        Pre check.
        """
        self.send_response(200) # OK.
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Allow.
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Methods.
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Headers.
        self.end_headers() # End.
    def authenticate(self):
        """
        Check if the request is authenticated using Basic Auth.
        Login check.
        Returns:
            bool: True if authenticated, False otherwise - good or bad.
        """
        auth_header = self.headers.get('Authorization') # Get header.
        if auth_header and auth_header.startswith('Basic '): # Basic?
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8') # Decode.
            username, password = auth_decoded.split(':', 1) # Split.
            return username == self.settings['username'] and password == self.settings['password'] # Match?
        return False # No.
def start_web_server(settings):
    """
    Start the web server for the dashboard and API.
    Runs web.
    Args:
        settings (dict): Configuration settings - settings.
    """
    global web_server # Shared.
    # Skip if web interface is disabled - check flag.
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.") # Log skip.
        return # Skip.
    # Custom HTTP server class to share settings - custom server.
    class CustomHTTPServer(HTTPServer):
        def __init__(self, *args, **kwargs):
            self.settings = settings # Store.
            super().__init__(*args, **kwargs) # Parent.
    try:
        # Create and start the web server in a thread - run background.
        web_server = CustomHTTPServer((settings['host'], settings['web_port']), BMSRequestHandler) # Create.
        logging.info(f"Web server started on {settings['host']}:{settings['web_port']}") # Log start.
        server_thread = threading.Thread(target=web_server.serve_forever) # Thread.
        server_thread.daemon = True # Daemon.
        server_thread.start() # Start.
    except Exception as e:
        logging.error(f"Failed to start web server: {e}") # Log fail.
def main(stdscr):
    """
    Main function to run the BMS loop.
    The big loop, now with RRD updates and history fetching for charts.
    Args:
        stdscr: Curses screen object - terminal.
    """
    # Initialize TUI colors (repeated for main loop) - colors again.
    stdscr.keypad(True) # Keypad on.
    curses.start_color() # Color on.
    curses.use_default_colors() # Defaults.
    curses.init_pair(1, curses.COLOR_RED, -1) # Red.
    curses.init_pair(2, curses.COLOR_RED, -1) # Red.
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Yellow.
    curses.init_pair(4, curses.COLOR_GREEN, -1) # Green.
    curses.init_pair(5, curses.COLOR_WHITE, -1) # White.
    curses.init_pair(6, curses.COLOR_YELLOW, -1) # Yellow.
    curses.init_pair(7, curses.COLOR_CYAN, -1) # Cyan.
    curses.init_pair(8, curses.COLOR_MAGENTA, -1) # Magenta.
    stdscr.nodelay(True) # Non block.
    # Global variables for state - shared.
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active
    # Load config and setup hardware - start.
    settings = load_config() # Load.
    setup_hardware(settings) # Setup.
    # Start web server - web.
    start_web_server(settings) # Start if enabled.
    # Run startup test - check.
    startup_self_test(settings, stdscr) # Run.
    # Set up shutdown handler - catch stop.
    signal.signal(signal.SIGINT, signal_handler) # Handle.
    # Set up watchdog - if on.
    if settings['WatchdogEnabled']:
        setup_watchdog(30) # 30s.
    while True: # Forever loop.
        # Read temperatures - get temps.
        temp_result = read_ntc_sensors(
            settings['ip'], settings['modbus_port'], settings['query_delay'],
            settings['num_channels'], settings['scaling_factor'],
            settings['max_retries'], settings['retry_backoff_base']
        ) # Read.
        temps_alerts = [] # Initialize here.
        if isinstance(temp_result, str):
            # Handle error (e.g., log it, set all temps to None, etc.) - bad.
            logging.error(f"Error reading temperatures: {temp_result}") # Log.
            temps_alerts = [] # Reset.
            calibrated_temps = [None] * settings['num_channels'] # None.
            raw_temps = [settings['valid_min']] * settings['num_channels'] # Min.
            bank_medians = [0.0] * NUM_BANKS # 0.
        else:
            # Process valid readings - good.
            valid_count = sum(1 for t in temp_result if t > settings['valid_min']) # Count good.
            # Set calibration if not set - adjust.
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result) # Median.
                startup_offsets = [startup_median - t for t in temp_result] # Offsets.
                save_offsets(startup_median, startup_offsets) # Save.
                startup_set = True # Set.
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C") # Log.
            # Reset if offsets missing - no offsets.
            if startup_set and startup_offsets is None:
                startup_set = False # Reset.
            # Apply calibration - adjust list.
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])] # Calib.
            raw_temps = temp_result # Raw.
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min']) # Medians.
            # Check for temperature issues - look for problems.
            for ch, raw in enumerate(raw_temps, 1):
                if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']) : # Invalid?
                    continue # Skip.
                calib = calibrated_temps[ch-1] # Calib.
                bank_id = get_bank_for_channel(ch) # Bank.
                bank_median = bank_medians[bank_id - 1] # Median.
                check_high_temp(calib, ch, temps_alerts, settings['high_threshold']) # High.
                check_low_temp(calib, ch, temps_alerts, settings['low_threshold']) # Low.
                check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold']) # Dev.
            # Check time-based issues if not first run - advanced checks.
            if run_count > 0 and previous_temps and previous_bank_medians is not None:
                for bank_id in range(1, NUM_BANKS + 1):
                    bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1] # Rise.
                    start, end = BANK_RANGES[bank_id - 1] # Range.
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1] # Temp.
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold']) # Rise.
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold']) # Lag.
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts) # Disconnect.
            # Update previous values - remember.
            previous_temps = calibrated_temps[:] # Copy.
            previous_bank_medians = bank_medians[:] # Copy.
        # Calculate overall median temperature for cabinet over-temp check and logging - cabinet temp.
        valid_calib_temps = [t for t in calibrated_temps if t is not None] # Valid only.
        overall_median = statistics.median(valid_calib_temps) if valid_calib_temps else 0.0 # Median or 0.
        # Check for cabinet over-temp and control fan - fan on/off.
        if overall_median > settings['cabinet_over_temp_threshold']:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.HIGH) # Fan on.
            logging.info(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan activated.") # Log on.
            if not any("Cabinet over temp" in a for a in temps_alerts): # Avoid duplicates.
                temps_alerts.append(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.") # Add alert.
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.") # Log event.
                if len(event_log) > 20:
                    event_log.pop(0) # Trim.
        else:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.LOW) # Fan off.
            logging.info("Cabinet temp normal. Fan deactivated.") # Log off.
        # Read voltages - get V.
        battery_voltages = [] # List.
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings) # Read.
            battery_voltages.append(v if v is not None else 0.0) # Add or 0.
        # Check for issues - problems?
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings) # Check.
        # Update RRD with current data - log to database.
        timestamp = int(time.time()) # Current time.
        values = f"{timestamp}:{battery_voltages[0]}:{battery_voltages[1]}:{battery_voltages[2]}:{overall_median}" # Format N:volt1:volt2:volt3:medtemp.
        subprocess.call(['rrdtool', 'update', RRD_FILE, values]) # Update command.
        logging.debug(f"RRD updated with: {values}") # Log update.
        # Check if balancing is needed - balance?
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages) # Max.
            min_v = min(battery_voltages) # Min.
            high_b = battery_voltages.index(max_v) + 1 # High.
            low_b = battery_voltages.index(min_v) + 1 # Low.
            current_time = time.time() # Now.
            # Start balancing if conditions met - go.
            if balancing_active or (alert_needed is False and max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts) # Balance.
                balancing_active = False # Reset.
        # Update web data - web update.
        web_data['voltages'] = battery_voltages # Voltages.
        web_data['temperatures'] = calibrated_temps # Temps.
        web_data['alerts'] = all_alerts # Alerts.
        web_data['balancing'] = balancing_active # Balance.
        web_data['last_update'] = time.time() # Time.
        web_data['system_status'] = 'Alert' if alert_needed else 'Running' # Status.
        # Update TUI - draw screen.
        draw_tui(
            stdscr, battery_voltages, calibrated_temps, raw_temps,
            startup_offsets or [0]*settings['num_channels'], bank_medians,
            startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0)
        ) # Draw.
        # Increment run count and clean up - count up.
        run_count += 1 # +1.
        gc.collect() # Clean memory.
        logging.info("Poll cycle complete.") # Log end.
        if settings['WatchdogEnabled']:
            pet_watchdog() # Pet.
        # Sleep before next cycle - wait.
        time.sleep(settings['poll_interval']) # Sleep.
    if settings['WatchdogEnabled']:
        close_watchdog() # Close.
# Run the main function with curses wrapper - start.
if __name__ == '__main__':
    curses.wrapper(main) # Start TUI and loop.