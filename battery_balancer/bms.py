# Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)
# --------------------------------------------------------------------------------
# Overview:
# This script is a complete Battery Management System (BMS) designed for a battery setup with 3 series-connected banks, each containing 8 parallel cells (3s8p). It monitors temperatures using NTC sensors connected to a Lantronix EDS4100 device via Modbus over TCP, measures voltages using an ADS1115 ADC over I2C, and balances voltages between banks using relays and a DC-DC converter. The system runs continuously, checking for issues, logging events, sending email alerts, and displaying information through a Text User Interface (TUI) in the terminal and an optional web dashboard.
# Key Features:
# - **Temperature Monitoring**: Reads 24 NTC sensors (8 per bank), calibrates them at startup, and checks for issues like high/low temperatures, deviations, rapid rises, or sensor disconnections.
# - **Voltage Monitoring and Balancing**: Measures voltages of 3 banks, balances them if the difference exceeds a threshold (e.g., 0.1V), by transferring charge from the highest to the lowest bank.
# - **Alerts and Notifications**: Logs issues to a file, activates an alarm relay, and sends throttled email alerts (e.g., every hour).
# - **User Interfaces**:
# - **TUI**: Shows real-time battery status with ASCII art, voltages, temperatures, alerts, balancing progress (top-right), and the last 20 events (bottom-right).
# - **Startup Self-Test**: Checks configuration, hardware connections, sensor readings, and balancer functionality at startup.
# - **Error Handling**: Retries failed reads, handles missing hardware, and logs detailed errors.
# - **Configuration**: Uses 'battery_monitor.ini' for settings, with defaults if keys are missing.
# - **Shutdown Handling**: Cleans up gracefully on Ctrl+C.
# How It Works (High-Level Flow):
# 1. **Load Configuration**: Reads settings from 'battery_monitor.ini' (e.g., IP address, thresholds).
# 2. **Setup Hardware**: Initializes I2C bus for ADC/relays and GPIO pins for relays.
# 3. **Startup Self-Test**: Validates config, tests hardware connectivity, reads initial sensor data, and tests balancing.
# 4. **Main Loop**:
# - Reads temperatures and voltages.
# - Calibrates temperatures using startup offsets.
# - Checks for issues (e.g., high/low voltages, temperature anomalies).
# - Balances banks if needed (no issues, voltage difference > threshold).
# - Updates TUI and web dashboard.
# - Logs events and sends email alerts if issues are detected.
# - Sleeps briefly before the next cycle.
# 5. **Shutdown**: Cleans up GPIO and web server on exit.
# Logic Flow Diagram (ASCII):
# ```
# +-----------------+
# | Start Script |
# +-----------------+
# |
# v
# +-----------------+
# | Load Config |
# | (INI File) |
# +-----------------+
# |
# v
# +-----------------+
# | Setup Hardware |
# | (I2C, GPIO) |
# +-----------------+
# |
# v
# +-----------------+
# | Startup Check |
# | (Config, HW, |
# | Sensors, Balancer)|
# +-----------------+
# | Fail
# v
# (Alarm + Continue)
# |
# v
# +-----------------+
# | Infinite Loop |
# +-----------------+
# |
# v
# /------------------\ /------------------\
# | Read Temps | | Read Voltages |
# \------------------/ \------------------/
# | |
# v v
# +-----------------+ +-----------------+
# | Process Temps | | Check Issues |
# | & Alerts | | & Alerts |
# +-----------------+ +-----------------+
# | |
# \------------------/
# |
# v
# +-----------------+
# | Need Balance? |
# +-----------------+
# | Yes
# v
# +-----------------+
# | Balance Banks |
# | (Relays, DC-DC) |
# +-----------------+
# | No
# v
# +-----------------+
# | Update TUI |
# | (Left: Status, |
# | Right: Balance, |
# | Events) |
# +-----------------+
# |
# v
# +-----------------+
# | Update Web Data |
# +-----------------+
# |
# v
# +-----------------+
# | Sleep & Repeat |
# +-----------------+
# ^
# | (Loop Back)
# |
# ```
# Dependencies:
# - Python 3.11+: For running the script.
# - Hardware Libraries: `smbus` (I2C), `RPi.GPIO` (GPIO control).
# - External Library: `art` (for ASCII art, install via `pip install art`).
# - Standard Libraries: socket, statistics, time, configparser, logging, signal, gc, os, sys, smtplib, email.mime.text, curses, threading, json, http.server, urllib.parse, base64, traceback.
# - Hardware: Raspberry Pi, ADS1115 ADC, TCA9548A multiplexer, relays, Lantronix EDS4100, GPIO pins 17/27.
# - Configuration: 'battery_monitor.ini' file (template provided separately).
# Installation:
# 1. Install Python: `sudo apt install python3`
# 2. Install hardware libraries: `sudo apt install python3-smbus python3-rpi.gpio`
# 3. Install art library: `pip install art`
# 4. Enable I2C: `sudo raspi-config > Interfacing Options > I2C > Enable; reboot`
# 5. Create 'battery_monitor.ini' with correct settings (e.g., email, IP).
# 6. Run: `sudo python bms.py` (root required for GPIO/I2C).
# 7. Access web dashboard at http://<pi-ip>:8080.
# Notes:
# - Ensure hardware matches INI settings (I2C addresses, GPIO pins, Modbus IP/port).
# - Update email settings with valid credentials (use app-specific password for Gmail).
# - TUI requires a terminal with sufficient width (>80 columns) for optimal display.
# - Logs are saved to 'battery_monitor.log'; use DEBUG level for detailed logs.
# - Web interface security: Enable `auth_required` and set strong credentials.
# --------------------------------------------------------------------------------
# Code Begins Below
# --------------------------------------------------------------------------------
# Import necessary Python libraries for various tasks
import socket # Used to connect to the Lantronix EDS4100 device over the network
import statistics # Helps calculate averages and medians for temperature data
import time # Manages timing, delays, and timestamps for events
import configparser # Reads settings from the INI configuration file
import logging # Logs events and errors to a file for troubleshooting
import signal # Handles graceful shutdown when the user presses Ctrl+C
import gc # Manages memory cleanup during long-running operations
import os # Handles file operations, like reading/writing offsets
import sys # Used to exit the script cleanly
import threading # Runs the web server in a separate thread
import json # Formats data for the web interface
from urllib.parse import urlparse, parse_qs # Parses web requests
import base64 # Decodes authentication credentials for the web interface
import traceback # Logs detailed error information for debugging
# Import libraries for hardware interaction, with fallback for testing
try:
    import smbus # Communicates with I2C devices like the ADC and relays
    import RPi.GPIO as GPIO # Controls Raspberry Pi GPIO pins for relays
except ImportError:
    # If hardware libraries are missing, run in test mode without hardware
    print("Hardware libraries not available - running in test mode")
    smbus = None
    GPIO = None
# Import libraries for email alerts and web server
from email.mime.text import MIMEText # Builds email messages
import smtplib # Sends email alerts
from http.server import HTTPServer, BaseHTTPRequestHandler # Runs the web server
import curses # Creates the terminal-based Text User Interface (TUI)
from art import text2art # Generates ASCII art for the TUI display
# Add imports for watchdog
import fcntl  # For watchdog ioctl
import struct  # For watchdog struct
# Set up logging to save events and errors to 'battery_monitor.log'
logging.basicConfig(
    filename='battery_monitor.log', # Log file name
    level=logging.INFO, # Log level (INFO captures key events)
    format='%(asctime)s - %(message)s' # Log format with timestamp
)
# Global variables to store system state
config_parser = configparser.ConfigParser() # Object to read INI file
bus = None # I2C bus for communicating with hardware
last_email_time = 0 # Tracks when the last email alert was sent
balance_start_time = None # Tracks when balancing started
last_balance_time = 0 # Tracks when the last balancing ended
battery_voltages = [] # Stores current voltages for each bank
previous_temps = None # Stores previous temperature readings
previous_bank_medians = None # Stores previous median temperatures per bank
run_count = 0 # Counts how many times the main loop has run
startup_offsets = None # Temperature calibration offsets from startup
startup_median = None # Median temperature at startup
startup_set = False # Indicates if temperature calibration is set
alert_states = {} # Tracks alerts for each temperature channel
balancing_active = False # Indicates if balancing is currently happening
startup_failed = False # Indicates if startup tests failed
startup_alerts = [] # Stores startup test failure messages
web_server = None # Web server object
event_log = [] # Stores the last 20 events (e.g., alerts, balancing)
web_data = {
    'voltages': [0.0] * 3, # Current voltages for 3 banks
    'temperatures': [None] * 24, # Current temperatures for 24 sensors
    'alerts': [], # Current active alerts
    'balancing': False, # Balancing status
    'last_update': time.time(), # Last data update timestamp
    'system_status': 'Initializing' # System status (e.g., Running, Alert)
}
# Define which temperature sensors belong to each bank (3 banks, 8 sensors each)
BANK_RANGES = [(1, 8), (9, 16), (17, 24)] # Channels 1-8 (Bank 1), 9-16 (Bank 2), 17-24 (Bank 3)
NUM_BANKS = 3 # Fixed number of banks for 3s8p configuration
# Global for watchdog
WATCHDOG_DEV = '/dev/watchdog'
watchdog_fd = None
def get_bank_for_channel(ch):
    """
    Find which battery bank a temperature sensor belongs to.
    Args:
        ch (int): Sensor channel number (1 to 24)
    Returns:
        int: Bank number (1 to 3) or None if the channel is invalid
    """
    # Loop through each bank’s channel range
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):
        # Check if the channel number falls within this bank’s range
        if start <= ch <= end:
            return bank_id # Return the bank number
    return None # Return None if the channel doesn’t belong to any bank
def modbus_crc(data):
    """
    Calculate a checksum (CRC) to ensure data integrity for Modbus communication.
    Args:
        data (bytes): Data to calculate the CRC for
    Returns:
        bytes: 2-byte CRC value in little-endian order
    """
    crc = 0xFFFF # Start with a fixed initial value
    # Process each byte in the data
    for byte in data:
        crc ^= byte # Combine the byte with the CRC
        # Perform 8 iterations for each bit
        for _ in range(8):
            if crc & 0x0001: # Check if the least significant bit is 1
                crc = (crc >> 1) ^ 0xA001 # Shift right and apply polynomial
            else:
                crc >>= 1 # Shift right if bit is 0
    return crc.to_bytes(2, 'little') # Return CRC as 2 bytes
def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """
    Read temperatures from NTC sensors via Modbus over TCP.
    Args:
        ip (str): IP address of the Lantronix EDS4100 device
        modbus_port (int): Network port for Modbus communication
        query_delay (float): Seconds to wait after sending a query
        num_channels (int): Number of temperature sensors to read
        scaling_factor (float): Converts raw sensor data to degrees Celsius
        max_retries (int): Maximum attempts to retry failed reads
        retry_backoff_base (int): Base for retry delay (e.g., 1s, 2s, 4s)
    Returns:
        list: Temperature readings or an error message if the read fails
    """
    logging.info("Starting temperature sensor read.") # Log the start of the read
    # Create the Modbus query to request data
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base) # Calculate checksum for the query
    query = query_base + crc # Combine query and checksum
    # Try reading the sensors up to max_retries times
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{modbus_port}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Create a network socket
            s.settimeout(3) # Set a 3-second timeout for the connection
            s.connect((ip, modbus_port)) # Connect to the device
            s.send(query) # Send the Modbus query
            time.sleep(query_delay) # Wait for the device to respond
            response = s.recv(1024) # Receive up to 1024 bytes of response
            s.close() # Close the connection
            # Check if the response is too short
            if len(response) < 5:
                raise ValueError("Short response")
            # Check if the response length is correct
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")
            # Verify the checksum
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")
            # Parse the response header
            slave, func, byte_count = response[0:3]
            if slave != 1 or func != 3 or byte_count != num_channels * 2:
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}"
                return "Error: Invalid response header."
            # Extract temperature data from the response
            data = response[3:3 + byte_count]
            raw_temperatures = []
            for i in range(0, len(data), 2): # Process 2 bytes at a time
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val) # Convert raw data to temperature
            logging.info("Temperature read successful.") # Log successful read
            return raw_temperatures # Return the list of temperatures
        except socket.error as e:
            # Handle network errors
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt) # Wait before retrying
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."
        except ValueError as e:
            # Handle data validation errors
            logging.warning(f"Temp read attempt {attempt+1} failed (validation): {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."
        except Exception as e:
            # Handle unexpected errors
            logging.error(f"Unexpected error in temp read attempt {attempt+1}: {str(e)}\n{traceback.format_exc()}")
            return f"Error: Unexpected failure - {str(e)}"
def load_config():
    """
    Load settings from 'battery_monitor.ini' file, using defaults if settings are missing.
    Returns:
        dict: All configuration settings in a single dictionary
    Raises:
        FileNotFoundError: If the INI file is missing
    """
    logging.info("Loading configuration from 'battery_monitor.ini'.") # Log config load attempt
    global alert_states # Access the global alert states dictionary
    # Try to read the INI file
    if not config_parser.read('battery_monitor.ini'):
        logging.error("Config file 'battery_monitor.ini' not found.") # Log error if file is missing
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    # Temperature monitoring settings
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'), # IP address of the EDS4100 device
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001), # Modbus port
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0), # Seconds between temperature reads
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0), # Max allowed temperature rise
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1), # Max relative deviation
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5), # Lag threshold
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0), # Max safe temperature
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0), # Min safe temperature
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0), # Converts raw data to °C
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0), # Min valid temperature
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3), # Max retries for failed reads
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1), # Retry delay base
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25), # Delay after Modbus query
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24), # Number of sensors
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0) # Max absolute deviation
    }
    # Voltage and balancing settings
    voltage_settings = {
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3), # Number of banks
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1), # Min voltage difference to balance
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5), # Balancing duration
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1), # Loop sleep time
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60), # Rest after balancing
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5), # Min safe voltage
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0), # Max safe voltage
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600), # Email throttling interval
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1), # I2C bus number
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592), # Voltage divider ratio
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO') # Logging level (INFO, DEBUG, etc.)
    }
    # General flags for enabling features
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True), # Enable web interface
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True) # Enable startup tests
    }
    # I2C device addresses
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16), # Multiplexer address
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16), # ADC address
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x26'), 16) # Relay address
    }
    # GPIO pin settings
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17), # Pin for DC-DC converter relay
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27) # Pin for alarm relay
    }
    # Email alert settings
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'), # Email server
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587), # Email port
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'), # Sender email
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'), # Recipient email
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'), # Email username
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password') # Email password
    }
    # ADC configuration settings
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16), # ADC config register
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16), # ADC conversion register
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16), # Continuous mode setting
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16), # Sample rate setting
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16) # Gain setting
    }
    # Voltage calibration settings
    calibration_settings = {
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=0.99856), # Bank 1 calibration
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=0.99856), # Bank 2 calibration
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=0.99809) # Bank 3 calibration
    }
    # Startup self-test settings
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15), # Test balancing duration
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01), # Min voltage change for test
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0) # Interval between test reads
    }
    # Web interface settings
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'), # Web server host
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080), # Web server port
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False), # Require web authentication
        'username': config_parser.get('Web', 'username', fallback='admin'), # Web username
        'password': config_parser.get('Web', 'password', fallback='admin123'), # Web password
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True), # Enable API endpoints
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=True), # Enable CORS
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*') # Allowed CORS origins
    }
    # Set logging level based on configuration
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level) # Apply the logging level
    # Initialize alert states for each temperature sensor
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}
    logging.info("Configuration loaded successfully.") # Log successful config load
    # Combine all settings into one dictionary
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings}
def setup_hardware(settings):
    """
    Set up the I2C bus and GPIO pins for hardware communication.
    Args:
        settings (dict): Configuration settings from the INI file
    """
    global bus # Access the global I2C bus variable
    logging.info("Setting up hardware.") # Log hardware setup start
    # Initialize I2C bus if the library is available
    if smbus:
        bus = smbus.SMBus(settings['I2C_BusNumber']) # Set up I2C bus (usually bus 1)
    else:
        logging.warning("smbus not available - running in test mode") # Warn if I2C library is missing
        bus = None
    # Initialize GPIO pins if the library is available
    if GPIO:
        GPIO.setmode(GPIO.BCM) # Use BCM numbering for GPIO pins
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW) # Set up DC-DC converter relay pin
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Set up alarm relay pin
    else:
        logging.warning("RPi.GPIO not available - running in test mode") # Warn if GPIO library is missing
    logging.info("Hardware setup complete.") # Log successful setup
def signal_handler(sig, frame):
    """
    Handle Ctrl+C (SIGINT) to shut down the script cleanly.
    Args:
        sig: Signal number (e.g., SIGINT for Ctrl+C)
        frame: Current stack frame (technical detail)
    """
    logging.info("Script stopped by user or signal.") # Log shutdown request
    global web_server # Access the global web server object
    # Shut down the web server if it’s running
    if web_server:
        web_server.shutdown() # Stop the web server
    # Clean up GPIO pins
    if GPIO:
        GPIO.cleanup() # Reset GPIO pins to default state
    close_watchdog()
    sys.exit(0) # Exit the script
def load_offsets(num_channels):
    """
    Load temperature calibration offsets from 'offsets.txt' if it exists.
    Args:
        num_channels (int): Number of temperature sensors
    Returns:
        tuple: (startup_median, offsets) or (None, None) if the file is missing or invalid
    """
    logging.info("Loading startup offsets from 'offsets.txt'.") # Log offset load attempt
    # Check if the offsets file exists
    if os.path.exists('offsets.txt'):
        try:
            with open('offsets.txt', 'r') as f:
                lines = f.readlines() # Read all lines from the file
            # Check if the file is empty
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.") # Warn if file is empty
                return None, None
            startup_median = float(lines[0].strip()) # Read the median temperature
            offsets = [float(line.strip()) for line in lines[1:]] # Read offsets for each sensor
            # Verify the number of offsets matches the number of sensors
            if len(offsets) != num_channels:
                logging.warning(f"Invalid offsets count; expected {num_channels}, got {len(offsets)}. Using none.")
                return None, None
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.") # Log successful load
            return startup_median, offsets
        except (ValueError, IndexError):
            logging.warning("Corrupt offsets.txt; using none.") # Warn if file is corrupt
            return None, None
    logging.warning("No 'offsets.txt' found; using none.") # Warn if file is missing
    return None, None
def save_offsets(startup_median, startup_offsets):
    """
    Save temperature median and offsets to 'offsets.txt'.
    Args:
        startup_median (float): Median temperature at startup
        startup_offsets (list): List of temperature offsets for each sensor
    """
    logging.info("Saving startup offsets to 'offsets.txt'.") # Log save attempt
    try:
        with open('offsets.txt', 'w') as f:
            f.write(f"{startup_median}\n") # Write the median temperature
            for offset in startup_offsets:
                f.write(f"{offset}\n") # Write each offset
        logging.debug("Offsets saved.") # Log successful save
    except IOError as e:
        logging.error(f"Failed to save offsets: {e}") # Log error if save fails
def check_invalid_reading(raw, ch, alerts, valid_min):
    """
    Check if a temperature reading is invalid (too low or disconnected).
    Args:
        raw (float): Raw temperature reading
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        valid_min (float): Minimum valid temperature
    Returns:
        bool: True if the reading is invalid, False otherwise
    """
    if raw <= valid_min: # Check if the reading is below the minimum valid value
        bank = get_bank_for_channel(ch) # Find which bank the sensor belongs to
        alert = f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min})." # Create alert message
        alerts.append(alert) # Add alert to the list
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log with timestamp
        if len(event_log) > 20:
            event_log.pop(0) # Keep only the last 20 events
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.") # Log the issue
        return True
    return False
def check_high_temp(calibrated, ch, alerts, high_threshold):
    """
    Check if a temperature is too high.
    Args:
        calibrated (float): Calibrated temperature
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        high_threshold (float): Maximum safe temperature
    """
    if calibrated > high_threshold: # Check if temperature exceeds the high threshold
        bank = get_bank_for_channel(ch) # Find the bank
        alert = f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)." # Create alert
        alerts.append(alert) # Add to alerts
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
        if len(event_log) > 20:
            event_log.pop(0) # Keep last 20 events
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.") # Log the issue
def check_low_temp(calibrated, ch, alerts, low_threshold):
    """
    Check if a temperature is too low.
    Args:
        calibrated (float): Calibrated temperature
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        low_threshold (float): Minimum safe temperature
    """
    if calibrated < low_threshold: # Check if temperature is below the low threshold
        bank = get_bank_for_channel(ch) # Find the bank
        alert = f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C)." # Create alert
        alerts.append(alert) # Add to alerts
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
        if len(event_log) > 20:
            event_log.pop(0) # Keep last 20 events
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.") # Log the issue
def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """
    Check if a temperature deviates too much from the bank’s average.
    Args:
        calibrated (float): Calibrated temperature
        bank_median (float): Median temperature of the bank
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        abs_deviation_threshold (float): Maximum allowed absolute deviation
        deviation_threshold (float): Maximum allowed relative deviation
    """
    abs_dev = abs(calibrated - bank_median) # Calculate absolute difference from bank median
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0 # Calculate relative difference
    # Check if deviation is too high
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch) # Find the bank
        alert = f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%})." # Create alert
        alerts.append(alert) # Add to alerts
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
        if len(event_log) > 20:
            event_log.pop(0) # Keep last 20 events
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.") # Log the issue
def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """
    Check if a temperature has risen too quickly since the last check.
    Args:
        current (float): Current temperature
        previous_temps (list): Previous temperature readings
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        poll_interval (float): Time between checks
        rise_threshold (float): Maximum allowed temperature rise
    """
    previous = previous_temps[ch-1] # Get the previous temperature for this sensor
    if previous is not None: # Check if previous reading exists
        rise = current - previous # Calculate temperature increase
        if rise > rise_threshold: # Check if increase is too large
            bank = get_bank_for_channel(ch) # Find the bank
            alert = f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s)." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.") # Log the issue
def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """
    Check if a sensor’s temperature change lags behind the bank’s average change.
    Args:
        current (float): Current temperature
        previous_temps (list): Previous temperature readings
        bank_median_rise (float): Average temperature rise for the bank
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
        disconnection_lag_threshold (float): Maximum allowed lag
    """
    previous = previous_temps[ch-1] # Get the previous temperature
    if previous is not None: # Check if previous reading exists
        rise = current - previous # Calculate temperature increase
        if abs(rise - bank_median_rise) > disconnection_lag_threshold: # Check if lag is too large
            bank = get_bank_for_channel(ch) # Find the bank
            alert = f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C)." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.") # Log the issue
def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """
    Check if a sensor has suddenly stopped working.
    Args:
        current: Current temperature reading (None if disconnected)
        previous_temps (list): Previous temperature readings
        ch (int): Sensor channel number
        alerts (list): List to store alert messages
    """
    previous = previous_temps[ch-1] # Get the previous temperature
    if previous is not None and current is None: # Check if sensor was working but now isn’t
        bank = get_bank_for_channel(ch) # Find the bank
        alert = f"Bank {bank} Ch {ch}: Sudden disconnection." # Create alert
        alerts.append(alert) # Add to alerts
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
        if len(event_log) > 20:
            event_log.pop(0) # Keep last 20 events
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.") # Log the issue
def choose_channel(channel, multiplexer_address):
    """
    Select an I2C channel on the multiplexer.
    Args:
        channel (int): Channel number to select (0 to 3)
        multiplexer_address (int): I2C address of the multiplexer
    """
    logging.debug(f"Switching to I2C channel {channel}.") # Log channel switch
    if bus: # Check if I2C bus is available
        try:
            bus.write_byte(multiplexer_address, 1 << channel) # Select the channel
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}") # Log error if switch fails
def setup_voltage_meter(settings):
    """
    Configure the ADS1115 ADC for voltage measurements.
    Args:
        settings (dict): Configuration settings
    """
    logging.debug("Configuring voltage meter ADC.") # Log ADC setup
    if bus: # Check if I2C bus is available
        try:
            # Combine ADC settings for continuous mode, sample rate, and gain
            config_value = (settings['ContinuousModeConfig'] |
                            settings['SampleRateConfig'] |
                            settings['GainConfig'])
            bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value) # Send config to ADC
        except IOError as e:
            logging.error(f"I2C error configuring voltage meter: {str(e)}") # Log error if setup fails
def read_voltage_with_retry(bank_id, settings):
    """
    Read the voltage of a battery bank with retries for accuracy.
    Args:
        bank_id (int): Bank number (1 to 3)
        settings (dict): Configuration settings
    Returns:
        tuple: (average voltage, list of readings, list of raw ADC values) or (None, [], []) if failed
    """
    logging.info(f"Starting voltage read for Bank {bank_id}.") # Log voltage read attempt
    voltage_divider_ratio = settings['VoltageDividerRatio'] # Get voltage divider ratio
    sensor_id = bank_id # Sensor ID matches bank ID
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration'] # Get calibration factor
    # Try reading twice for reliability
    for attempt in range(2):
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.") # Log attempt number
        readings = [] # Store voltage readings
        raw_values = [] # Store raw ADC values
        # Take two readings for consistency
        for _ in range(2):
            meter_channel = (bank_id - 1) % 3 # Map bank to ADC channel
            choose_channel(meter_channel, settings['MultiplexerAddress']) # Select the channel
            setup_voltage_meter(settings) # Configure the ADC
            if bus: # If I2C bus is available
                try:
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01) # Start ADC conversion
                    time.sleep(0.05) # Wait for conversion to complete
                    raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister']) # Read ADC value
                    raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8) # Adjust byte order
                except IOError as e:
                    logging.error(f"I2C error in voltage read for Bank {bank_id}: {str(e)}") # Log error
                    raw_adc = 0
            else:
                raw_adc = 16000 + bank_id * 100 # Mock value for testing
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}") # Log raw ADC value
            if raw_adc != 0: # If reading is valid
                measured_voltage = raw_adc * (6.144 / 32767) # Convert ADC to voltage
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor # Apply calibration
                readings.append(actual_voltage) # Store the voltage
                raw_values.append(raw_adc) # Store the raw ADC value
            else:
                readings.append(0.0) # Store zero if reading failed
                raw_values.append(0)
        # Check if readings are consistent
        if readings:
            average = sum(readings) / len(readings) # Calculate average voltage
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05] # Filter consistent readings
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05] # Filter corresponding ADC values
            if valid_readings: # If we have valid readings
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.") # Log success
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc # Return average and details
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.") # Log retry
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.") # Log failure
    return None, [], [] # Return failure result
def set_relay_connection(high, low, settings):
    """
    Set up relays to connect a high-voltage bank to a low-voltage bank for balancing.
    Args:
        high (int): High-voltage bank number
        low (int): Low-voltage bank number
        settings (dict): Configuration settings
    """
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}") # Log relay setup
        logging.debug("Switching to relay control channel.") # Log channel switch
        choose_channel(3, settings['MultiplexerAddress']) # Select relay channel
        relay_state = 0 # Start with all relays off
        # Set relay patterns based on bank combination
        if high == 1 and low == 2:
            relay_state |= (1 << 3) # Activate relay 4
            logging.debug("Relays 4 activated for high to low.")
        elif high == 1 and low == 3:
            relay_state |= (1 << 2) | (1 << 3) # Activate relays 3 and 4
            logging.debug("Relays 3, and 4 activated for high to low.")
        elif high == 2 and low == 1:
            relay_state |= (1 << 0) # Activate relay 1
            logging.debug("Relays 1 activated for high to low.")
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3) # Activate relays 1, 3, and 4
            logging.debug("Relays 1, 3, and 4 activated for high to low.")
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1) # Activate relays 1 and 2
            logging.debug("Relays 1, 2 activated for high to low.")
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3) # Activate relays 1, 2, and 4
            logging.debug("Relays 1, 2, and 4 activated for high to low.")
        logging.debug(f"Final relay state: {bin(relay_state)}") # Log the relay state
        if bus: # If I2C bus is available
            logging.info(f"Sending relay state command to hardware.") # Log command send
            bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state) # Send relay state
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}") # Log success
    except (IOError, AttributeError) as e:
        logging.error(f"I/O error while setting up relay: {e}") # Log I/O error
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}") # Log unexpected error
def control_dcdc_converter(turn_on, settings):
    """
    Turn the DC-DC converter on or off using a GPIO pin.
    Args:
        turn_on (bool): True to turn on, False to turn off
        settings (dict): Configuration settings
    """
    try:
        if GPIO: # If GPIO library is available
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW) # Set pin high or low
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}") # Log state change
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}") # Log error
def send_alert_email(message, settings):
    """
    Send an email alert with throttling to avoid spam.
    Args:
        message (str): The email message content
        settings (dict): Configuration settings
    """
    global last_email_time # Access the last email time
    # Check if enough time has passed since the last email
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.") # Log skipped email
        return
    try:
        # Create the email message
        msg = MIMEText(message) # Create a text email
        msg['Subject'] = "Battery Monitor Alert" # Set email subject
        msg['From'] = settings['SenderEmail'] # Set sender
        msg['To'] = settings['RecipientEmail'] # Set recipient
        # Connect to the email server and send the message
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            server.starttls() # Enable secure connection
            if settings['SMTP_Username'] and settings['SMTP_Password']: # If credentials are provided
                server.login(settings['SMTP_Username'], settings['SMTP_Password']) # Log in to the server
            server.send_message(msg) # Send the email
        last_email_time = time.time() # Update the last email time
        logging.info(f"Alert email sent: {message}") # Log successful send
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}") # Log error
def check_for_issues(voltages, temps_alerts, settings):
    """
    Check for voltage and temperature issues and trigger alerts.
    Args:
        voltages (list): List of bank voltages
        temps_alerts (list): List of temperature-related alerts
        settings (dict): Configuration settings
    Returns:
        tuple: (alert_needed, alerts_list) indicating if an alert is needed and the list of alerts
    """
    global startup_failed, startup_alerts # Access startup failure flags
    logging.info("Checking for voltage and temp issues.") # Log issue check
    alert_needed = startup_failed # Start with startup failure status
    alerts = [] # List to store alert messages
    # Add startup failures to alerts if any
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts)) # Add startup issues
    # Check each bank’s voltage for issues
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0: # Check for zero or invalid voltage
            alert = f"Bank {i}: Zero voltage." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"Zero voltage alert on Bank {i}.") # Log the issue
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']: # Check for high voltage
            alert = f"Bank {i}: High voltage ({v:.2f}V)." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.") # Log the issue
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']: # Check for low voltage
            alert = f"Bank {i}: Low voltage ({v:.2f}V)." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.") # Log the issue
            alert_needed = True
    # Add temperature alerts if any
    if temps_alerts:
        alerts.extend(temps_alerts) # Combine temperature alerts
        alert_needed = True
    # Activate or deactivate the alarm relay
    if alert_needed:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH) # Turn on alarm relay
        logging.info("Alarm relay activated.") # Log relay activation
        send_alert_email("\n".join(alerts), settings) # Send email alert
    else:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.LOW) # Turn off alarm relay
        logging.info("No issues; alarm relay deactivated.") # Log no issues
    return alert_needed, alerts # Return whether an alert is needed and the alert list
def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    """
    Balance voltage between two banks by transferring charge.
    Args:
        stdscr: Curses screen object for TUI display
        high (int): High-voltage bank number
        low (int): Low-voltage bank number
        settings (dict): Configuration settings
        temps_alerts (list): List of temperature alerts
    """
    global balance_start_time, last_balance_time, balancing_active, web_data # Access global variables
    # Skip balancing if there are temperature issues
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.") # Log skip reason
        return
    logging.info(f"Starting balance from Bank {high} to {low}.") # Log balancing start
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing started from Bank {high} to Bank {low}") # Add to event log
    if len(event_log) > 20:
        event_log.pop(0) # Keep last 20 events
    balancing_active = True # Mark balancing as active
    web_data['balancing'] = True # Update web interface
    # Read current voltages
    voltage_high, _, _ = read_voltage_with_retry(high, settings) # Read high bank voltage
    voltage_low, _, _ = read_voltage_with_retry(low, settings) # Read low bank voltage
    # Safety check: don’t balance if low bank voltage is zero
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.") # Log skip reason
        balancing_active = False # Reset balancing flag
        web_data['balancing'] = False # Update web interface
        return
    # Set up relays and start the DC-DC converter
    set_relay_connection(high, low, settings) # Connect high to low bank
    control_dcdc_converter(True, settings) # Turn on DC-DC converter
    balance_start_time = time.time() # Record start time
    # Animation frames for balancing progress display
    animation_frames = ['|', '/', '-', '\\'] # Symbols for animation
    frame_index = 0 # Current animation frame
    height, width = stdscr.getmaxyx() # Get terminal size
    right_half_x = width // 2 # Start position for right half of screen
    progress_y = 1 # Start balancing display at top-right
    # Run balancing for the configured duration
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time # Calculate elapsed time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds']) # Calculate progress (0 to 1)
        # Read current voltages during balancing
        voltage_high, _, _ = read_voltage_with_retry(high, settings) # Update high bank voltage
        voltage_low, _, _ = read_voltage_with_retry(low, settings) # Update low bank voltage
        # Create a progress bar
        bar_length = 20 # Length of the progress bar
        filled = int(bar_length * progress) # Number of filled segments
        bar = '=' * filled + ' ' * (bar_length - filled) # Build the bar
        # Display balancing status in top-right half
        if progress_y < height and right_half_x + 50 < width: # Check if display fits
            try:
                stdscr.addstr(progress_y, right_half_x, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6)) # Show balancing status
            except curses.error:
                logging.warning("addstr error for balancing status.") # Log display error
            try:
                stdscr.addstr(progress_y + 1, right_half_x, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6)) # Show progress bar
            except curses.error:
                logging.warning("addstr error for balancing progress bar.") # Log display error
        else:
            logging.warning("Skipping balancing progress display - out of bounds.") # Log if out of bounds
        stdscr.refresh() # Update the terminal display
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V") # Log progress
        frame_index += 1 # Move to next animation frame
        time.sleep(0.01) # Short delay for smooth animation
    # Finish balancing
    logging.info("Balancing process completed.") # Log completion
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing completed from Bank {high} to Bank {low}") # Add to event log
    if len(event_log) > 20:
        event_log.pop(0) # Keep last 20 events
    control_dcdc_converter(False, settings) # Turn off DC-DC converter
    logging.info("Turning off DC-DC converter.") # Log converter off
    set_relay_connection(0, 0, settings) # Reset relays
    logging.info("Resetting relay connections to default state.") # Log relay reset
    balancing_active = False # Reset balancing flag
    web_data['balancing'] = False # Update web interface
    last_balance_time = time.time() # Record end time
def compute_bank_medians(calibrated_temps, valid_min):
    """
    Calculate the median temperature for each bank.
    Args:
        calibrated_temps (list): List of calibrated temperatures
        valid_min (float): Minimum valid temperature
    Returns:
        list: Median temperatures for each bank
    """
    bank_medians = [] # List to store median temperatures
    for start, end in BANK_RANGES: # Loop through each bank’s channel range
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None] # Get valid temperatures
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0 # Calculate median or use 0.0
        bank_medians.append(bank_median) # Add to list
    return bank_medians # Return medians
def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    """
    Draw the Text User Interface (TUI) to show battery status, alerts, balancing, and event history.
    Args:
        stdscr: Curses screen object for terminal display
        voltages (list): List of bank voltages
        calibrated_temps (list): List of calibrated temperatures
        raw_temps (list): List of raw temperature readings
        offsets (list): List of temperature offsets
        bank_medians (list): List of median temperatures per bank
        startup_median (float): Median temperature at startup
        alerts (list): List of active alerts
        settings (dict): Configuration settings
        startup_set (bool): Whether temperature calibration is set
        is_startup (bool): Whether this is the startup display
    """
    logging.debug("Refreshing TUI.") # Log TUI update
    stdscr.clear() # Clear the terminal screen
    # Set up colors for the TUI
    curses.start_color() # Enable color support
    curses.use_default_colors() # Use terminal’s default colors
    curses.init_pair(1, curses.COLOR_RED, -1) # Red for critical errors
    curses.init_pair(2, curses.COLOR_RED, -1) # Red for high/low alerts
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Yellow for warnings
    curses.init_pair(4, curses.COLOR_GREEN, -1) # Green for normal values
    curses.init_pair(5, curses.COLOR_WHITE, -1) # White for general text
    curses.init_pair(6, curses.COLOR_YELLOW, -1) # Yellow for balancing
    curses.init_pair(7, curses.COLOR_CYAN, -1) # Cyan for headers
    curses.init_pair(8, curses.COLOR_MAGENTA, -1) # Magenta for invalid readings
    height, width = stdscr.getmaxyx() # Get terminal dimensions
    right_half_x = width // 2 # Divide screen into left and right halves
    # Display total voltage as ASCII art on the left
    total_v = sum(voltages) # Calculate total voltage
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS # Max safe total voltage
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS # Min safe total voltage
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4) # Choose color based on voltage
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True) # Create ASCII art for total voltage
    roman_lines = roman_v.splitlines() # Split into lines
    # Display each line of the ASCII art
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < right_half_x: # Check if it fits on the left
            try:
                stdscr.addstr(i + 1, 0, line, v_color) # Display the line
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.") # Log display error
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.") # Log if out of bounds
    y_offset = len(roman_lines) + 2 # Move down after ASCII art
    if y_offset >= height: # Check if there’s space left
        logging.warning("TUI y_offset exceeds height; skipping art.") # Log if no space
        return
    # Battery art template (ASCII representation of a battery)
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
    art_height = len(battery_art_base) # Height of the battery art
    art_width = len(battery_art_base[0]) # Width of one battery
    # Draw battery art for each bank on the left
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS # Repeat for all banks
        if y_offset + row < height and len(full_line) < right_half_x: # Check if it fits
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4)) # Display green battery art
            except curses.error:
                logging.warning(f"addstr error for art row {row}.") # Log display error
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.") # Log if out of bounds
    # Add voltage and temperature data to each battery
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width # Position for this bank’s art
        # Display voltage
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V" # Format voltage
        v_color = curses.color_pair(8) if voltages[bank_id] == 0.0 else \
                 curses.color_pair(2) if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else \
                 curses.color_pair(3) if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else \
                 curses.color_pair(4) # Choose color based on voltage
        v_center = start_pos + (art_width - len(v_str)) // 2 # Center the voltage text
        v_y = y_offset + 1 # Position near the top of the battery
        if v_y < height and v_center + len(v_str) < right_half_x: # Check if it fits
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color) # Display voltage
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.") # Log display error
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.") # Log if out of bounds
        # Display temperatures for each sensor in the bank
        start, end = BANK_RANGES[bank_id] # Get channel range for this bank
        for local_ch, ch in enumerate(range(start, end + 1), 0):
            idx = ch - 1 # Index for temperature arrays
            raw = raw_temps[idx] if idx < len(raw_temps) else 0 # Get raw temperature
            calib = calibrated_temps[idx] # Get calibrated temperature
            calib_str = f"{calib:.1f}" if calib is not None else "Inv" # Format temperature or show invalid
            # During startup, show raw and offset values
            if is_startup:
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv" # Format raw temperature
                offset_str = f"{offsets[idx]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A" # Format offset
                detail = f" ({raw_str}/{offset_str})" # Combine raw and offset
            else:
                detail = "" # No extra details after startup
            t_str = f"C{local_ch+1}: {calib_str}{detail}" # Format temperature string
            t_color = curses.color_pair(8) if "Inv" in calib_str else \
                     curses.color_pair(2) if calib > settings['high_threshold'] else \
                     curses.color_pair(3) if calib < settings['low_threshold'] else \
                     curses.color_pair(4) # Choose color based on temperature
            t_center = start_pos + (art_width - len(t_str)) // 2 # Center the temperature text
            t_y = y_offset + 2 + local_ch # Position below voltage
            if t_y < height and t_center + len(t_str) < right_half_x: # Check if it fits
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color) # Display temperature
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.") # Log display error
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.") # Log if out of bounds
        # Display bank median temperature
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C" # Format median temperature
        med_center = start_pos + (art_width - len(med_str)) // 2 # Center the median text
        med_y = y_offset + 15 # Position at bottom of battery
        if med_y < height and med_center + len(med_str) < right_half_x: # Check if it fits
            try:
                stdscr.addstr(med_y, med_center, med_str, curses.color_pair(7)) # Display median in cyan
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.") # Log display error
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.") # Log if out of bounds
    y_offset += art_height + 2 # Move down after battery art
    # Display ADC readings if there’s space
    if y_offset >= height:
        logging.warning("Skipping ADC/readings - out of bounds.") # Log if no space
    else:
        for i in range(1, NUM_BANKS + 1): # Loop through each bank
            voltage, readings, adc_values = read_voltage_with_retry(i, settings) # Read voltage and ADC data
            logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}") # Log details
            if voltage is None:
                voltage = 0.0 # Use zero if reading failed
            if y_offset < height: # Check if ADC display fits
                try:
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", curses.color_pair(5)) # Display ADC value
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.") # Log display error
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.") # Log if out of bounds
            y_offset += 1 # Move down
            if y_offset < height: # Check if readings display fits
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", curses.color_pair(5)) # Display readings
                    else:
                        stdscr.addstr(y_offset, 0, " [Readings: No data]", curses.color_pair(5)) # Display no data
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.") # Log display error
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.") # Log if out of bounds
            y_offset += 1 # Move down
    y_offset += 1 # Add space
    # Display startup median temperature
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A" # Format median
    if y_offset < height: # Check if it fits
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7)) # Display in cyan
        except curses.error:
            logging.warning("addstr error for startup median.") # Log display error
    else:
        logging.warning("Skipping startup median - out of bounds.") # Log if out of bounds
    y_offset += 2 # Add space
    # Display alerts section on the left
    if y_offset < height: # Check if header fits
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", curses.color_pair(7)) # Display alerts header in cyan
        except curses.error:
            logging.warning("addstr error for alerts header.") # Log display error
    y_offset += 1 # Move down
    # Display individual alerts
    if alerts:
        for alert in alerts: # Loop through alerts
            if y_offset < height and len(alert) < right_half_x: # Check if alert fits on left
                try:
                    stdscr.addstr(y_offset, 0, alert, curses.color_pair(8)) # Display alert in magenta
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.") # Log display error
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.") # Log if out of bounds
            y_offset += 1 # Move down
    else:
        if y_offset < height: # Check if message fits
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", curses.color_pair(4)) # Display no alerts in green
            except curses.error:
                logging.warning("addstr error for no alerts message.") # Log display error
        else:
            logging.warning("Skipping no alerts message - out of bounds.") # Log if out of bounds
    # Display event history on bottom-right half
    y_offset = height // 2 # Start at middle for bottom half
    if y_offset < height: # Check if header fits
        try:
            stdscr.addstr(y_offset, right_half_x, "Event History:", curses.color_pair(7)) # Display header in cyan
        except curses.error:
            logging.warning("addstr error for event history header.") # Log display error
    y_offset += 1 # Move down
    for event in event_log[-20:]: # Loop through last 20 events
        if y_offset < height and len(event) < width - right_half_x: # Check if event fits on right
            try:
                stdscr.addstr(y_offset, right_half_x, event, curses.color_pair(5)) # Display event in white
            except curses.error:
                logging.warning(f"addstr error for event '{event}'.") # Log display error
            y_offset += 1 # Move down
        else:
            logging.warning(f"Skipping event '{event}' - out of bounds.") # Log if out of bounds
    stdscr.refresh() # Update the terminal display
def setup_watchdog(timeout=60):
    """
    Set up the hardware watchdog timer for the Raspberry Pi.
    Detects Pi model and loads appropriate watchdog module (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 and newer).
    Falls back to opening /dev/watchdog for unknown drivers.
    Args:
        timeout (int): Watchdog timeout in seconds (default: 60)
    """
    global watchdog_fd
    try:
        # Detect Pi model
        model = "Unknown"
        if os.path.exists('/proc/device-tree/model'):
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip().lower()
        logging.info(f"Detected Raspberry Pi model: {model}")

        # Select watchdog module based on model
        if 'raspberry pi' in model and not 'raspberry pi 5' in model:  # Pi 1-4, including 2B
            module = 'bcm2835_wdt'
        else:  # Pi 5 and newer models
            module = 'rp1-wdt'
            logging.info("Assuming rp1-wdt for Pi 5 or newer model")

        # Load watchdog module
        os.system(f'sudo modprobe {module}')
        logging.info(f"Loaded watchdog module: {module}")
        time.sleep(1)  # Allow module to initialize

        # Verify /dev/watchdog exists
        if not os.path.exists(WATCHDOG_DEV):
            logging.error(f"Watchdog device {WATCHDOG_DEV} not found. Attempting to open anyway.")
            watchdog_fd = None
            try:
                watchdog_fd = open(WATCHDOG_DEV, 'w')
                logging.info(f"Opened {WATCHDOG_DEV} despite initial check failure")
            except IOError as e:
                logging.error(f"Failed to open watchdog: {e}. Ensure appropriate module loaded (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 or newer).")
                return

        # Open watchdog device
        watchdog_fd = open(WATCHDOG_DEV, 'w')
        logging.debug(f"Opened watchdog device: {WATCHDOG_DEV}")

        # Set timeout
        try:
            magic = ord('W') << 8 | 0x06  # WDIOC_SETTIMEOUT
            fcntl.ioctl(watchdog_fd, magic, struct.pack("I", timeout))
            logging.info(f"Watchdog set with timeout {timeout}s")
        except IOError as e:
            logging.warning(f"Failed to set watchdog timeout: {e}. Using default timeout.")
        logging.debug("Watchdog successfully initialized")
    except Exception as e:
        logging.error(f"Failed to setup watchdog: {e}. Ensure appropriate module loaded (bcm2835_wdt for Pi 1-4, rp1-wdt for Pi 5 or newer).")
        watchdog_fd = None
def close_watchdog():
    if watchdog_fd:
        try:
            watchdog_fd.write('V')  # Disable on clean exit
            watchdog_fd.close()
        except IOError:
            pass
def startup_self_test(settings, stdscr):
    """
    Run startup tests to check configuration, hardware, sensors, and balancing.
    Args:
        settings (dict): Configuration settings
        stdscr: Curses screen object for display
    Returns:
        list: List of failure alerts
    """
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets # Access global startup variables
    # Skip if startup test is disabled
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.") # Log skip
        return []
    retries = 0
    while True:
        logging.info(f"Starting self-test attempt {retries + 1}") # Log test start
        alerts = [] # List to store failure messages
        stdscr.clear() # Clear the screen
        y = 0 # Start display position
        # Display test title
        if y < stdscr.getmaxyx()[0]: # Check if it fits
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1)) # Display in red
            except curses.error:
                logging.warning("addstr error for title.") # Log display error
        y += 2 # Move down
        stdscr.refresh() # Update display
        # Step 1: Validate configuration
        logging.info("Step 1: Validating configuration parameters.") # Log step
        logging.debug(f"Configuration details: NumberOfBatteries={settings['NumberOfBatteries']}, "
                      f"I2C_BusNumber={settings['I2C_BusNumber']}, "
                      f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
                      f"RelayAddress=0x{settings['RelayAddress']:02x}, "
                      f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
                      f"NumChannels={settings['num_channels']}, ScalingFactor={settings['scaling_factor']}") # Log config details
        if y < stdscr.getmaxyx()[0]: # Check if step display fits
            try:
                stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4)) # Display in green
            except curses.error:
                logging.warning("addstr error for step 1.") # Log display error
        stdscr.refresh() # Update display
        time.sleep(0.5) # Short delay
        # Check if number of banks matches expected
        if settings['NumberOfBatteries'] != NUM_BANKS:
            alert = f"Config mismatch: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.warning(f"Config mismatch detected: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}.") # Log warning
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2)) # Display in red
                except curses.error:
                    logging.warning("addstr error for config mismatch.") # Log display error
        else:
            logging.debug("Configuration validation passed: NumberOfBatteries matches NUM_BANKS.") # Log success
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for config OK.") # Log display error
        y += 2 # Move down
        stdscr.refresh() # Update display
        # Step 2: Test hardware connectivity
        logging.info("Step 2: Testing hardware connectivity (I2C and Modbus).") # Log step
        if y < stdscr.getmaxyx()[0]: # Check if step display fits
            try:
                stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4)) # Display in green
            except curses.error:
                logging.warning("addstr error for step 2.") # Log display error
        stdscr.refresh() # Update display
        time.sleep(0.5) # Short delay
        # Test I2C connectivity
        logging.debug(f"Testing I2C connectivity on bus {settings['I2C_BusNumber']}: "
                      f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                      f"Relay=0x{settings['RelayAddress']:02x}") # Log I2C test details
        try:
            if bus: # If I2C bus is available
                logging.debug(f"Selecting I2C channel 0 on multiplexer 0x{settings['MultiplexerAddress']:02x}") # Log channel select
                choose_channel(0, settings['MultiplexerAddress']) # Select channel 0
                logging.debug(f"Reading byte from VoltageMeter at 0x{settings['VoltageMeterAddress']:02x}") # Log read attempt
                bus.read_byte(settings['VoltageMeterAddress']) # Read a byte to test connection
                logging.debug("I2C connectivity test passed for all devices.") # Log success
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for I2C OK.") # Log display error
        except (IOError, AttributeError) as e:
            alert = f"I2C connectivity failure: {str(e)}" # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.error(f"I2C connectivity failure: {str(e)}. Bus={settings['I2C_BusNumber']}, "
                          f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                          f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                          f"Relay=0x{settings['RelayAddress']:02x}") # Log error details
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2)) # Display in red
                except curses.error:
                    logging.warning("addstr error for I2C failure.") # Log display error
        # Test Modbus connectivity
        logging.debug(f"Testing Modbus connectivity to {settings['ip']}:{settings['modbus_port']} with "
                      f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}") # Log Modbus test details
        try:
            test_query = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1) # Test read
            if isinstance(test_query, str) and "Error" in test_query:
                raise ValueError(test_query) # Raise error if test fails
            logging.debug(f"Modbus test successful: Received {len(test_query)} values: {test_query}") # Log success
            if y + 2 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 2, 0, "Modbus OK.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for Modbus OK.") # Log display error
        except Exception as e:
            alert = f"Modbus test failure: {str(e)}" # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.error(f"Modbus test failure: {str(e)}. Connection={settings['ip']}:{settings['modbus_port']}, "
                          f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}") # Log error details
            if y + 2 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 2, 0, f"Modbus failure: {str(e)}", curses.color_pair(2)) # Display in red
                except curses.error:
                    logging.warning("addstr error for Modbus failure.") # Log display error
        y += 3 # Move down
        stdscr.refresh() # Update display
        # Step 3: Initial sensor reads
        logging.info("Step 3: Performing initial sensor reads (temperature and voltage).") # Log step
        if y < stdscr.getmaxyx()[0]: # Check if step display fits
            try:
                stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4)) # Display in green
            except curses.error:
                logging.warning("addstr error for step 3.") # Log display error
        stdscr.refresh() # Update display
        time.sleep(0.5) # Short delay
        # Test temperature sensor reading
        logging.debug(f"Reading {settings['num_channels']} temperature channels from {settings['ip']}:{settings['modbus_port']} "
                      f"with query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}, "
                      f"max_retries={settings['max_retries']}, retry_backoff_base={settings['retry_backoff_base']}") # Log temperature read details
        initial_temps = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'],
                                         settings['num_channels'], settings['scaling_factor'],
                                         settings['max_retries'], settings['retry_backoff_base']) # Read initial temperatures
        if isinstance(initial_temps, str):
            alert = f"Initial temp read failure: {initial_temps}" # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.error(f"Initial temperature read failure: {initial_temps}") # Log failure
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, "Temp read failure.", curses.color_pair(2)) # Display in red
                except curses.error:
                    logging.warning("addstr error for temp failure.") # Log display error
        else:
            logging.debug(f"Initial temperature read successful: {len(initial_temps)} values, {initial_temps}") # Log success
            valid_count = sum(1 for t in initial_temps if t > settings['valid_min']) # Count valid readings
            logging.debug(f"Valid temperature readings: {valid_count}/{settings['num_channels']}, valid_min={settings['valid_min']}") # Log valid count
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for temps OK.") # Log display error
        # Test voltage reading
        logging.debug(f"Reading voltages for {NUM_BANKS} banks with VoltageDividerRatio={settings['VoltageDividerRatio']}") # Log voltage read details
        initial_voltages = [] # List to store initial voltages
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings) # Read voltage
            logging.debug(f"Bank {i} voltage read: Voltage={voltage}, Readings={readings}, ADC={adc_values}, "
                          f"CalibrationFactor={settings[f'Sensor{i}_Calibration']}") # Log details
            initial_voltages.append(voltage if voltage is not None else 0.0) # Add voltage or 0.0
        if any(v == 0.0 for v in initial_voltages): # Check for zero voltages
            alert = "Initial voltage read failure: Zero voltage on one or more banks." # Create alert
            alerts.append(alert) # Add to alerts
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
            if len(event_log) > 20:
                event_log.pop(0) # Keep last 20 events
            logging.error(f"Initial voltage read failure: Voltages={initial_voltages}") # Log failure
            if y + 2 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2)) # Display in red
                except curses.error:
                    logging.warning("addstr error for voltage failure.") # Log display error
        else:
            logging.debug(f"Initial voltage read successful: Voltages={initial_voltages}") # Log success
            if y + 2 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for voltages OK.") # Log display error
        # Set up temperature calibration if all readings are valid
        if isinstance(initial_temps, list):
            valid_count = sum(1 for t in initial_temps if t > settings['valid_min']) # Count valid temperatures
            if valid_count == settings['num_channels']: # If all are valid
                startup_median = statistics.median(initial_temps) # Calculate median
                logging.debug(f"Calculated startup median: {startup_median:.1f}°C") # Log median
                # Load existing offsets or calculate new ones if offsets.txt missing
                _, startup_offsets = load_offsets(settings['num_channels'])
                if startup_offsets is None:
                    startup_offsets = [startup_median - t for t in initial_temps] # Calculate offsets
                    save_offsets(startup_median, startup_offsets) # Save new offsets
                    logging.info(f"Calculated and saved new offsets on first run: {startup_offsets}") # Log new offsets
                else:
                    logging.info(f"Using existing offsets from offsets.txt: {startup_offsets}") # Log existing offsets
                startup_set = True # Mark calibration as set
            else:
                logging.warning(f"Temperature calibration skipped: Only {valid_count}/{settings['num_channels']} valid readings.") # Log skip
                startup_median = None # Reset median
                startup_offsets = None # Reset offsets
                startup_set = False # Mark calibration not set
        y += 3 # Move down
        stdscr.refresh() # Update display
        # Step 4: Balancer verification (only if no previous failures and valid voltages)
        if not alerts and all(v > 0 for v in initial_voltages):
            logging.info("Step 4: Verifying balancer functionality.") # Log step
            if y < stdscr.getmaxyx()[0]: # Check if step display fits
                try:
                    stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for step 4.") # Log display error
            y += 1 # Move down
            stdscr.refresh() # Update display
            time.sleep(0.5) # Short delay
            # Read initial voltages for all banks
            initial_bank_voltages = []
            for bank in range(1, NUM_BANKS + 1):
                voltage, _, _ = read_voltage_with_retry(bank, settings) # Read voltage
                initial_bank_voltages.append(voltage if voltage is not None else 0.0) # Add voltage
            if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                try:
                    stdscr.addstr(y + 1, 0, f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for initial bank voltages.") # Log display error
            logging.debug(f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V") # Log voltages
            y += 2 # Move down
            stdscr.refresh() # Update display
            # Test all possible balancing pairs, ordered by highest to lowest initial voltage
            bank_voltages_dict = {b: initial_bank_voltages[b-1] for b in range(1, NUM_BANKS + 1)}
            sorted_banks = sorted(bank_voltages_dict, key=bank_voltages_dict.get, reverse=True)
            pairs = []
            for source in sorted_banks:
                for dest in [b for b in range(1, NUM_BANKS + 1) if b != source]:
                    pairs.append((source, dest))
            test_duration = settings['test_balance_duration'] # Get test duration
            read_interval = settings['test_read_interval'] # Get read interval
            min_delta = settings['min_voltage_delta'] # Get min voltage change
            logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                          f"read_interval={read_interval}s, min_voltage_delta={min_delta}V") # Log test parameters
            for source, dest in pairs: # Loop through pairs
                logging.debug(f"Testing balance from Bank {source} to Bank {dest}") # Log pair test
                if y < stdscr.getmaxyx()[0]: # Check if message fits
                    try:
                        stdscr.addstr(y, 0, f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.", curses.color_pair(6)) # Display in yellow
                    except curses.error:
                        logging.warning("addstr error for testing balance.") # Log display error
                stdscr.refresh() # Update display
                logging.info(f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.") # Log test
                # Skip if temperature anomalies exist
                temp_anomaly = False # Flag for anomaly
                if initial_temps and isinstance(initial_temps, list): # Check initial temperatures
                    for t in initial_temps:
                        if t > settings['high_threshold'] or t < settings['low_threshold']: # Check for out-of-range temps
                            temp_anomaly = True # Set flag
                            break
                if temp_anomaly:
                    alert = f"Skipping balance test from Bank {source} to Bank {dest}: Temp anomalies." # Create alert
                    alerts.append(alert) # Add to alerts
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
                    if len(event_log) > 20:
                        event_log.pop(0) # Keep last 20 events
                    logging.warning(f"Skipping balance test from Bank {source} to Bank {dest}: Temperature anomalies detected.") # Log skip
                    if y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                        try:
                            stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2)) # Display in red
                        except curses.error:
                            logging.warning("addstr error for skipped temp.") # Log display error
                    y += 2 # Move down
                    stdscr.refresh() # Update display
                    continue # Skip this pair
                # Read initial voltages
                initial_source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Read source voltage
                initial_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Read destination voltage
                time.sleep(0.5) # Short delay
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Initial - Bank {source}={initial_source_v:.2f}V, Bank {dest}={initial_dest_v:.2f}V") # Log initial voltages
                # Start test balancing
                set_relay_connection(source, dest, settings) # Set relays
                control_dcdc_converter(True, settings) # Turn on converter
                start_time = time.time() # Record start time
                # Track voltage changes during test
                source_trend = [initial_source_v] # List for source voltage trend
                dest_trend = [initial_dest_v] # List for destination voltage trend
                progress_y = y + 1 # Position for progress display
                # Run test for duration
                while time.time() - start_time < test_duration:
                    time.sleep(read_interval) # Wait between reads
                    source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Read source
                    dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Read destination
                    source_trend.append(source_v) # Add to trend
                    dest_trend.append(dest_v) # Add to trend
                    logging.debug(f"Balance test from Bank {source} to Bank {dest}: Bank {source}={source_v:.2f}V, Bank {dest}={dest_v:.2f}V") # Log current voltages
                    elapsed = time.time() - start_time # Calculate elapsed time
                    if progress_y < stdscr.getmaxyx()[0]: # Check if progress fits
                        try:
                            stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6)) # Clear line
                            stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, Bank {source} {source_v:.2f}V, Bank {dest} {dest_v:.2f}V", curses.color_pair(6)) # Display progress
                        except curses.error:
                            logging.warning("addstr error in startup balance progress.") # Log display error
                    stdscr.refresh() # Update display
                # Read final voltages
                final_source_v = read_voltage_with_retry(source, settings)[0] or 0.0 # Read final source
                final_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0 # Read final destination
                time.sleep(0.5) # Short delay
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Final - Bank {source}={final_source_v:.2f}V, Bank {dest}={final_dest_v:.2f}V") # Log final voltages
                control_dcdc_converter(False, settings) # Turn off converter
                set_relay_connection(0, 0, settings) # Reset relays
                if progress_y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6)) # Display in yellow
                    except curses.error:
                        logging.warning("addstr error for analyzing.") # Log display error
                stdscr.refresh() # Update display
                # Analyze voltage changes
                if len(source_trend) >= 3: # Check if enough readings
                    source_change = final_source_v - initial_source_v # Calculate source change
                    dest_change = final_dest_v - initial_dest_v # Calculate destination change
                    logging.debug(f"Balance test from Bank {source} to Bank {dest} analysis: Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V, Min change={min_delta}V") # Log analysis
                    # Check if changes are as expected (source decreases, destination increases)
                    if source_change >= 0 or dest_change <= 0 or abs(source_change) < min_delta or dest_change < min_delta:
                        alert = f"Balance test from Bank {source} to Bank {dest} failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V)." # Create alert
                        alerts.append(alert) # Add to alerts
                        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
                        if len(event_log) > 20:
                            event_log.pop(0) # Keep last 20 events
                        logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Source did not decrease or destination did not increase sufficiently.") # Log failure
                        if progress_y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(2)) # Display in red
                            except curses.error:
                                logging.warning("addstr error for test failed insufficient change.") # Log display error
                    else:
                        logging.debug(f"Balance test from Bank {source} to Bank {dest} passed: Correct trend and sufficient voltage change.") # Log success
                        if progress_y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test passed (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(4)) # Display in green
                            except curses.error:
                                logging.warning("addstr error for test passed.") # Log display error
                else:
                    alert = f"Balance test from Bank {source} to Bank {dest} failed: Insufficient readings." # Create alert
                    alerts.append(alert) # Add to alerts
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}") # Add to event log
                    if len(event_log) > 20:
                        event_log.pop(0) # Keep last 20 events
                    logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Only {len(source_trend)} readings collected.") # Log failure
                    if progress_y + 1 < stdscr.getmaxyx()[0]: # Check if message fits
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2)) # Display in red
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient readings.") # Log display error
                stdscr.refresh() # Update display
                y = progress_y + 2 # Move down
                time.sleep(2) # Short delay
        # Store test results
        startup_alerts = alerts # Save alerts
        if alerts:
            startup_failed = True
            startup_alerts = alerts
            logging.error("Startup self-test failures: " + "; ".join(alerts)) # Log failures
            send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings) # Send email
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Turn on alarm relay
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Startup failures: " + "; ".join(alerts), curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for self-test failures.") # Log display error
            if stdscr.getmaxyx()[0] > 2:
                try:
                    stdscr.addstr(2, 0, "Alarm activated. Retrying in 2 minutes...", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for retry message.")
            stdscr.refresh()
            time.sleep(120)  # Pause 2 minutes
            retries += 1
            continue  # Retry
        else:
            startup_failed = False
            startup_alerts = []
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)  # Alarm off
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Self-Test Passed. Proceeding to main loop.", curses.color_pair(4)) # Display in green
                except curses.error:
                    logging.warning("addstr error for self-test OK.") # Log display error
            stdscr.refresh() # Update display
            time.sleep(2)
            logging.info("Startup self-test passed.") # Log success
            return []  # Proceed
class BMSRequestHandler(BaseHTTPRequestHandler):
    """
    Handles HTTP requests for the web interface and API.
    """
    def __init__(self, request, client_address, server):
        """
        Initialize the handler with settings.
        Args:
            request: HTTP request
            client_address: Client's address
            server: Web server instance
        """
        self.settings = server.settings # Store settings
        super().__init__(request, client_address, server) # Call parent initializer
    def do_GET(self):
        """
        Handle GET requests (e.g., load dashboard or API data).
        """
        parsed_path = urlparse(self.path) # Parse the request path
        path = parsed_path.path # Get the path
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401) # Send unauthorized response
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"') # Send auth request
            self.end_headers() # End headers
            return
        # Set CORS headers if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Allow origins
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Allow methods
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Allow headers
        # Serve the dashboard page
        if path == '/':
            self.send_response(200) # OK response
            self.send_header('Content-type', 'text/html') # HTML content
            self.end_headers() # End headers
            # HTML content for the web dashboard
            html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
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
            <h2>Battery Banks</h2>
            <div id="battery-container" class="grid"></div>
        </div>
      
        <div class="status-card">
            <h2>Alerts</h2>
            <div id="alerts-container"></div>
        </div>
      
        <div class="status-card">
            <h2>Actions</h2>
            <button id="refresh-btn" class="button">Refresh</button>
            <button id="balance-btn" class="button" disabled>Balance Now</button>
        </div>
      
        <div class="status-card">
            <h2>System Information</h2>
            <p>Total Voltage: <span id="total-voltage">-</span></p>
            <p>Balancing: <span id="balancing-status">No</span></p>
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
                    } else:
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
      
        function initiateBalance() {
            fetch('/api/balance', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Balancing initiated');
                    } else:
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
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>"""
          
            self.wfile.write(html.encode('utf-8')) # Send HTML
        # Serve API status data
        elif path == '/api/status':
            self.send_response(200) # OK response
            self.send_header('Content-type', 'application/json') # JSON content
            self.end_headers() # End headers
            # Prepare JSON response
            response = {
                'voltages': web_data['voltages'],
                'temperatures': web_data['temperatures'],
                'alerts': web_data['alerts'],
                'balancing': web_data['balancing'],
                'last_update': web_data['last_update'],
                'system_status': web_data['system_status'],
                'total_voltage': sum(web_data['voltages'])
            }
            self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
        else:
            self.send_response(404) # Not found response
            self.end_headers() # End headers
    def do_POST(self):
        """
        Handle POST requests (e.g., initiate balancing).
        """
        parsed_path = urlparse(self.path) # Parse path
        path = parsed_path.path # Get path
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401) # Unauthorized
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"') # Request auth
            self.end_headers() # End headers
            return
        # Set CORS headers if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Allow origins
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Allow methods
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Allow headers
        # Handle balance request
        if path == '/api/balance':
            global balancing_active # Access global balancing flag
            # Check if already balancing
            if balancing_active:
                self.send_response(400) # Bad request
                self.send_header('Content-type', 'application/json') # JSON content
                self.end_headers() # End headers
                response = {'success': False, 'message': 'Balancing already in progress'} # Response message
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
                return
            # Check for active alerts
            if len(web_data['alerts']) > 0:
                self.send_response(400) # Bad request
                self.send_header('Content-type', 'application/json') # JSON content
                self.end_headers() # End headers
                response = {'success': False, 'message': 'Cannot balance with active alerts'} # Response message
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
                return
            voltages = web_data['voltages'] # Get current voltages
            # Check if there are enough banks
            if len(voltages) < 2:
                self.send_response(400) # Bad request
                self.send_header('Content-type', 'application/json') # JSON content
                self.end_headers() # End headers
                response = {'success': False, 'message': 'Not enough battery banks'} # Response message
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
                return
            # Find high and low banks
            max_v = max(voltages) # Max voltage
            min_v = min(voltages) # Min voltage
            high_bank = voltages.index(max_v) + 1 # High bank number
            low_bank = voltages.index(min_v) + 1 # Low bank number
            # Check voltage difference
            if max_v - min_v < self.settings['VoltageDifferenceToBalance']:
                self.send_response(400) # Bad request
                self.send_header('Content-type', 'application/json') # JSON content
                self.end_headers() # End headers
                response = {'success': False, 'message': 'Voltage difference too small for balancing'} # Response message
                self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
                return
            # Start balancing
            balancing_active = True # Set flag
            self.send_response(200) # OK response
            self.send_header('Content-type', 'application/json') # JSON content
            self.end_headers() # End headers
            response = {'success': True, 'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'} # Response message
            self.wfile.write(json.dumps(response).encode('utf-8')) # Send JSON
        else:
            self.send_response(404) # Not found
            self.end_headers() # End headers
    def do_OPTIONS(self):
        """
        Handle OPTIONS requests for CORS preflight.
        """
        self.send_response(200) # OK response
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins']) # Allow origins
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS') # Allow methods
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization') # Allow headers
        self.end_headers() # End headers
    def authenticate(self):
        """
        Check if the request is authenticated using Basic Auth.
        Returns:
            bool: True if authenticated, False otherwise
        """
        auth_header = self.headers.get('Authorization') # Get auth header
        if auth_header and auth_header.startswith('Basic '): # Check for Basic auth
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8') # Decode credentials
            username, password = auth_decoded.split(':', 1) # Split username and password
            return username == self.settings['username'] and password == self.settings['password'] # Check credentials
        return False # Authentication failed
def start_web_server(settings):
    """
    Start the web server for the dashboard and API.
    Args:
        settings (dict): Configuration settings
    """
    global web_server # Access global web server object
    # Skip if web interface is disabled
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.") # Log skip
        return
    # Custom HTTP server class to share settings
    class CustomHTTPServer(HTTPServer):
        def __init__(self, *args, **kwargs):
            self.settings = settings # Store settings
            super().__init__(*args, **kwargs) # Call parent initializer
    try:
        # Create and start the web server in a thread
        web_server = CustomHTTPServer((settings['host'], settings['web_port']), BMSRequestHandler) # Create server
        logging.info(f"Web server started on {settings['host']}:{settings['web_port']}") # Log start
        server_thread = threading.Thread(target=web_server.serve_forever) # Create thread
        server_thread.daemon = True # Make thread daemon (exits with main script)
        server_thread.start() # Start the thread
    except Exception as e:
        logging.error(f"Failed to start web server: {e}") # Log error
def main(stdscr):
    """
    Main function to run the BMS loop.
    Args:
        stdscr: Curses screen object
    """
    # Initialize TUI colors (repeated for main loop)
    stdscr.keypad(True) # Enable keypad
    curses.start_color() # Enable color
    curses.use_default_colors() # Use default colors
    curses.init_pair(1, curses.COLOR_RED, -1) # Red
    curses.init_pair(2, curses.COLOR_RED, -1) # Red
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Yellow
    curses.init_pair(4, curses.COLOR_GREEN, -1) # Green
    curses.init_pair(5, curses.COLOR_WHITE, -1) # White
    curses.init_pair(6, curses.COLOR_YELLOW, -1) # Yellow
    curses.init_pair(7, curses.COLOR_CYAN, -1) # Cyan
    curses.init_pair(8, curses.COLOR_MAGENTA, -1) # Magenta
    stdscr.nodelay(True)  # Non-blocking input
    # Global variables for state
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active
    # Load config and setup hardware
    settings = load_config() # Load settings
    setup_hardware(settings) # Setup hardware
    # Start web server
    start_web_server(settings) # Start web if enabled
    # Run startup test
    startup_self_test(settings, stdscr) # Run tests
    # Set up shutdown handler
    signal.signal(signal.SIGINT, signal_handler) # Handle Ctrl+C
    # Set up watchdog
    setup_watchdog(30)  # 30s timeout
    # Load temperature offsets
    startup_median, startup_offsets = load_offsets(settings['num_channels']) # Load offsets
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True # Set calibration flag
        logging.info(f"Loaded startup median: {startup_median:.1f}°C") # Log load
    # Initialize state
    previous_temps = None # Reset previous temps
    previous_bank_medians = [None] * NUM_BANKS # Reset previous medians
    run_count = 0 # Reset run count
    web_data['system_status'] = 'Running' # Set status
    # Main loop
    while True:
        # Discard pending inputs
        while stdscr.getch() != -1:
            pass
        logging.info("Starting poll cycle.") # Log cycle start
        web_data['last_update'] = time.time() # Update timestamp
        # Read temperatures
        temp_result = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base']) # Read temps
        temps_alerts = [] # List for temp alerts
        # Process temperature readings
        if isinstance(temp_result, str): # If read failed
            temps_alerts.append(temp_result) # Add error as alert
            calibrated_temps = [None] * settings['num_channels'] # Set to None
            raw_temps = [settings['valid_min']] * settings['num_channels'] # Set to min
            bank_medians = [0.0] * NUM_BANKS # Set medians to 0.0
        else:
            # Process valid readings
            valid_count = sum(1 for t in temp_result if t > settings['valid_min']) # Count valid
            # Set calibration if not set
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result) # Calculate median
                startup_offsets = [startup_median - raw for raw in temp_result] # Calculate offsets
                save_offsets(startup_median, startup_offsets) # Save offsets
                startup_set = True # Set flag
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C") # Log set
            # Reset if offsets missing
            if startup_set and startup_offsets is None:
                startup_set = False # Reset flag
            # Apply calibration
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])] # Calibrate temps
            raw_temps = temp_result # Store raw temps
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min']) # Calculate medians
            # Check for temperature issues
            for ch, raw in enumerate(raw_temps, 1):
                if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']): # Check invalid
                    continue
                calib = calibrated_temps[ch-1] # Get calibrated temp
                bank_id = get_bank_for_channel(ch) # Get bank
                bank_median = bank_medians[bank_id - 1] # Get median
                check_high_temp(calib, ch, temps_alerts, settings['high_threshold']) # Check high
                check_low_temp(calib, ch, temps_alerts, settings['low_threshold']) # Check low
                check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold']) # Check deviation
            # Check time-based issues if not first run
            if run_count > 0 and previous_temps and previous_bank_medians is not None:
                for bank_id in range(1, NUM_BANKS + 1):
                    bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1] # Calculate rise
                    start, end = BANK_RANGES[bank_id - 1] # Get range
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1] # Get temp
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold']) # Check rise
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold']) # Check lag
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts) # Check disconnection
            # Update previous values
            previous_temps = calibrated_temps[:] # Store current temps
            previous_bank_medians = bank_medians[:] # Store current medians
        # Read voltages
        battery_voltages = [] # List for voltages
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings) # Read voltage
            battery_voltages.append(v if v is not None else 0.0) # Add or 0.0
        # Check for issues
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings) # Check issues
        # Check if balancing is needed
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages) # Max voltage
            min_v = min(battery_voltages) # Min voltage
            high_b = battery_voltages.index(max_v) + 1 # High bank
            low_b = battery_voltages.index(min_v) + 1 # Low bank
            current_time = time.time() # Current time
            # Start balancing if conditions met
            if balancing_active or (alert_needed is False and max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts) # Balance
                balancing_active = False # Reset flag
        # Update web data
        web_data['voltages'] = battery_voltages # Update voltages
        web_data['temperatures'] = calibrated_temps # Update temperatures
        web_data['alerts'] = all_alerts # Update alerts
        web_data['balancing'] = balancing_active # Update balancing
        web_data['last_update'] = time.time() # Update timestamp
        web_data['system_status'] = 'Alert' if alert_needed else 'Running' # Update status
        # Update TUI
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0)) # Draw TUI
        # Increment run count and clean up
        run_count += 1 # Increment count
        gc.collect() # Clean memory
        logging.info("Poll cycle complete.") # Log cycle end
        pet_watchdog()  # Pet every cycle (~10s)
        # Sleep before next cycle
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks'])) # Sleep
    close_watchdog()
# Run the main function with curses wrapper
if __name__ == '__main__':
    curses.wrapper(main) # Start the TUI and main loop