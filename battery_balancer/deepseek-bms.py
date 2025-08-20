I'll update the script with your recommendations and add a web interface. Here's the comprehensive update:

## Updated Files

### 1. Updated Configuration File (`battery_monitor.ini`)
```ini
[file name]: battery_monitor.ini
[file content begin]
; Battery Monitor Configuration File (battery_monitor.ini)
; Updated with web interface and improved configuration
; Sections added: [Web] for web interface configuration

[Temp]
ip = 192.168.15.240
port = 10001
poll_interval = 10.0
rise_threshold = 2.0
deviation_threshold = 0.1
disconnection_lag_threshold = 0.5
high_threshold = 60.0
low_threshold = 0.0
scaling_factor = 100.0
valid_min = 0.0
max_retries = 3
retry_backoff_base = 1
query_delay = 0.5
num_channels = 24
abs_deviation_threshold = 2.0

[General]
NumberOfBatteries = 3
VoltageDifferenceToBalance = 0.1
BalanceDurationSeconds = 5
SleepTimeBetweenChecks = 0.1
BalanceRestPeriodSeconds = 60
LowVoltageThresholdPerBattery = 18.5
HighVoltageThresholdPerBattery = 21.0
EmailAlertIntervalSeconds = 3600
I2C_BusNumber = 1
VoltageDividerRatio = 0.01592
LoggingLevel = INFO
; Enable or disable web interface
WebInterfaceEnabled = true
; Enable or disable startup self-test
StartupSelfTestEnabled = true

[I2C]
MultiplexerAddress = 0x70
VoltageMeterAddress = 0x49
RelayAddress = 0x26

[GPIO]
DC_DC_RelayPin = 17
AlarmRelayPin = 27

[Email]
SMTP_Server = smtp.gmail.com
SMTP_Port = 587
SenderEmail = your_email@gmail.com
RecipientEmail = recipient@example.com
; Add SMTP authentication fields
SMTP_Username = your_email@gmail.com
SMTP_Password = your_app_password

[ADC]
ConfigRegister = 0x01
ConversionRegister = 0x00
ContinuousModeConfig = 0x0100
SampleRateConfig = 0x0080
GainConfig = 0x0400

[Calibration]
Sensor1_Calibration = 0.99856
Sensor2_Calibration = 0.99856
Sensor3_Calibration = 0.99809

[Startup]
test_balance_duration = 15
min_voltage_delta = 0.01
test_read_interval = 2

[Web]
; Web interface configuration
host = 0.0.0.0
port = 8080
; Authentication for web interface (optional)
auth_required = false
username = admin
password = admin123
; Enable/disable API endpoints
api_enabled = true
; Cross-Origin Resource Sharing (CORS) settings
cors_enabled = true
cors_origins = *
[file content end]
```

### 2. Updated Main Script (`bms.py`)
```python
[file name]: bms.py
[file content begin]
"""
Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)
Now includes web interface and configuration improvements.
"""

import socket
import statistics
import time
import configparser
import logging
import signal
import gc
import os
import smbus
import RPi.GPIO as GPIO
from email.mime.text import MIMEText
import smtplib
import curses
import sys
from art import text2art
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import base64

# Global variables
config_parser = configparser.ConfigParser()
bus = None
last_email_time = 0
balance_start_time = None
last_balance_time = 0
battery_voltages = []
previous_temps = None
previous_bank_medians = None
run_count = 0
startup_offsets = None
startup_median = None
startup_set = False
alert_states = {}
balancing_active = False
startup_failed = False
startup_alerts = []
web_server = None
web_data = {
    'voltages': [0, 0, 0],
    'temperatures': [None] * 24,
    'alerts': [],
    'balancing': False,
    'last_update': time.time(),
    'system_status': 'Initializing'
}

# Bank definitions
BANK_RANGES = [(1, 8), (9, 16), (17, 24)]
NUM_BANKS = 3  # Will be overridden by config if needed

def get_bank_for_channel(ch):
    """Get bank ID for a given channel."""
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):
        if start <= ch <= end:
            return bank_id
    return None

def modbus_crc(data):
    """Calculate Modbus CRC for data integrity."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

def read_ntc_sensors(ip, port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """Read NTC sensor temperatures via Modbus over TCP with retries."""
    logging.info("Starting temperature sensor read.")
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{port}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect((ip, port))
                s.send(query)
                time.sleep(query_delay)
                response = s.recv(1024)
            
            if len(response) < 5:
                raise ValueError("Short response")
            
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")
            
            slave, func, byte_count = response[0:3]
            if slave != 1 or func != 3 or byte_count != num_channels * 2:
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}"
                return "Error: Invalid response header."
            
            data = response[3:3 + byte_count]
            raw_temperatures = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val)
            
            logging.info("Temperature read successful.")
            return raw_temperatures
        
        except Exception as e:
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."

def load_config():
    """Load settings from 'battery_monitor.ini' with fallbacks."""
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states, NUM_BANKS
    
    if not config_parser.read('battery_monitor.ini'):
        logging.error("Config file 'battery_monitor.ini' not found.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    
    # Temp settings
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),
        'port': config_parser.getint('Temp', 'port', fallback=10001),
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0),
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24),
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0)
    }
    
    # General settings
    voltage_settings = {
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3),
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=10),
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=5.0),
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=30),
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=3.0),
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=4.2),
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=300),
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO'),
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True),
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True)
    }
    
    # Update NUM_BANKS based on config
    NUM_BANKS = voltage_settings['NumberOfBatteries']
    
    # I2C settings
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x48'), 16),
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x10'), 16)
    }
    
    # GPIO settings
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17),
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27)
    }
    
    # Email settings with authentication
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.example.com'),
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='alert@example.com'),
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='admin@example.com'),
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback=''),
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='')
    }
    
    # ADC settings
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x4000'), 16),
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x1400'), 16),
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x2000'), 16)
    }
    
    # Calibration settings
    calibration_settings = {
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=1.0),
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=1.0),
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=1.0)
    }
    
    # Startup settings
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)
    }
    
    # Web settings
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'),
        'port': config_parser.getint('Web', 'port', fallback=8080),
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False),
        'username': config_parser.get('Web', 'username', fallback='admin'),
        'password': config_parser.get('Web', 'password', fallback='admin123'),
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True),
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=True),
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*')
    }
    
    # Set logging level
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}
    
    logging.info("Configuration loaded successfully.")
    return {**temp_settings, **voltage_settings, **i2c_settings, **gpio_settings, 
            **email_settings, **adc_settings, **calibration_settings, 
            **startup_settings, **web_settings}

def setup_hardware(settings):
    """Initialize I2C bus and GPIO pins."""
    global bus
    logging.info("Setting up hardware.")
    bus = smbus.SMBus(settings['I2C_BusNumber'])
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
    logging.info("Hardware setup complete.")

def signal_handler(sig, frame):
    """Handle SIGINT for graceful shutdown."""
    logging.info("Script stopped by user or signal.")
    global web_server
    if web_server:
        web_server.shutdown()
    GPIO.cleanup()
    sys.exit(0)

def load_offsets():
    """Load temp offsets from file if exists."""
    logging.info("Loading startup offsets from 'offsets.txt'.")
    if os.path.exists('offsets.txt'):
        with open('offsets.txt', 'r') as f:
            lines = f.readlines()
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.")
                return None, None
            startup_median = float(lines[0].strip())
            offsets = [float(line.strip()) for line in lines[1:]]
            if len(offsets) != 24:  # Assume num_channels=24
                logging.warning("Invalid offsets count; using none.")
                return None, None
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")
            return startup_median, offsets
    logging.warning("No 'offsets.txt' found; using none.")
    return None, None

def save_offsets(startup_median, offsets):
    """Save temp median and offsets to file."""
    logging.info("Saving startup offsets to 'offsets.txt'.")
    with open('offsets.txt', 'w') as f:
        f.write(f"{startup_median}\n")
        for offset in offsets:
            f.write(f"{offset}\n")
    logging.debug("Offsets saved.")

def check_invalid_reading(raw, ch, alerts, valid_min):
    """Check if raw temp is invalid."""
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min}).")
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.")
        return True
    return False

def check_high_temp(calibrated, ch, alerts, high_threshold):
    """Check for high temperature."""
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C).")
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.")

def check_low_temp(calibrated, ch, alerts, low_threshold):
    """Check for low temperature."""
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C).")
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.")

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """Check deviation from bank median."""
    abs_dev = abs(calibrated - bank_median)
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%}).")
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """Check for abnormal temp rise since last poll."""
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s).")
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.")

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """Check if channel rise lags bank median rise."""
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C).")
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")

def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """Check for sudden sensor disconnection."""
    previous = previous_temps[ch-1]
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Sudden disconnection.")
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.")

def choose_channel(channel, multiplexer_address):
    """Select I2C multiplexer channel."""
    logging.debug(f"Switching to I2C channel {channel}.")
    bus.write_byte(multiplexer_address, 1 << channel)

def setup_voltage_meter(settings):
    """Configure ADC for voltage measurement."""
    logging.debug("Configuring voltage meter ADC.")
    config_value = (settings['ContinuousModeConfig'] | 
                    settings['SampleRateConfig'] | 
                    settings['GainConfig'])
    bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)

def read_voltage_with_retry(bank_id, settings):
    """Read bank voltage with retries and averaging."""
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    for attempt in range(2):
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        readings = []
        raw_values = []
        for _ in range(2):
            meter_channel = (bank_id - 1) % 3
            choose_channel(meter_channel, settings['MultiplexerAddress'])
            setup_voltage_meter(settings)
            bus.write_byte(settings['VoltageMeterAddress'], 0x01)
            time.sleep(0.05)
            raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister'])
            raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")
            if raw_adc != 0:
                measured_voltage = raw_adc * (6.144 / 32767)
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor
                readings.append(actual_voltage)
                raw_values.append(raw_adc)
            else:
                readings.append(0.0)
                raw_values.append(0)
        if readings:
            average = sum(readings) / len(readings)
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            if valid_readings:
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")
    return None, [], []

def set_relay_connection(high, low, settings):
    """Set relays for balancing between banks."""
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])
        relay_state = 0
        if high == 1 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)
            logging.debug("Relays 1, 2, and 4 activated for high to low.")
        elif high == 1 and low == 3:
            relay_state |= (1 << 1) | (1 << 2) | (1 << 3)
            logging.debug("Relays 2, 3, and 4 activated for high to low.")
        elif high == 2 and low == 1:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
            logging.debug("Relays 1, 3, and 4 activated for high to low.")
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)
            logging.debug("Relays 1, 2, and 4 activated for high to low.")

        logging.debug(f"Final relay state: {bin(relay_state)}")
        logging.info(f"Sending relay state command to hardware.")
        bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state)
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}")
    except IOError as e:
        logging.error(f"I/O error while setting up relay: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}")

def control_dcdc_converter(turn_on, settings):
    """Turn DC-DC converter on/off via GPIO."""
    try:
        GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(message, settings):
    """Send email alert with throttling and authentication."""
    global last_email_time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")
        return
    try:
        msg = MIMEText(message)
        msg['Subject'] = "Battery Monitor Alert"
        msg['From'] = settings['SenderEmail']
        msg['To'] = settings['RecipientEmail']
        
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            server.starttls()
            if settings['SMTP_Username'] and settings['SMTP_Password']:
                server.login(settings['SMTP_Username'], settings['SMTP_Password'])
            server.send_message(msg)
        
        last_email_time = time.time()
        logging.info(f"Alert email sent: {message}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")

def check_for_issues(voltages, temps_alerts, settings):
    """Check voltage/temp issues, trigger alerts/relay."""
    global startup_failed, startup_alerts
    logging.info("Checking for voltage and temp issues.")
    alert_needed = startup_failed
    alerts = []
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            alerts.append(f"Bank {i}: Zero voltage.")
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: High voltage ({v:.2f}V).")
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: Low voltage ({v:.2f}V).")
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
    if temps_alerts:
        alerts.extend(temps_alerts)
        alert_needed = True
    if alert_needed:
        GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
        logging.info("Alarm relay activated.")
        send_alert_email("\n".join(alerts), settings)
    else:
        GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
        logging.info("No issues; alarm relay deactivated.")
    return alert_needed, alerts

def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    """Balance voltages between high and low banks with progress in TUI."""
    global balance_start_time, last_balance_time, balancing_active, web_data
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    logging.info(f"Starting balance from Bank {high} to {low}.")
    balancing_active = True
    web_data['balancing'] = True
    voltage_high, _, _ = read_voltage_with_retry(high, settings)
    voltage_low, _, _ = read_voltage_with_retry(low, settings)
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    set_relay_connection(high, low, settings)
    control_dcdc_converter(True, settings)
    balance_start_time = time.time()
    animation_frames = ['|', '/', '-', '\\']
    frame_index = 0
    progress_y = 17 + 6 + 2
    height, _ = stdscr.getmaxyx()
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        if progress_y < height and progress_y + 1 < height:
            try:
                stdscr.addstr(progress_y, 0, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing status.")
            try:
                stdscr.addstr(progress_y + 1, 0, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing progress bar.")
        else:
            logging.warning("Skipping balancing progress display - out of bounds.")
        stdscr.refresh()
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")
        frame_index += 1
        time.sleep(0.01)
    logging.info("Balancing process completed.")
    control_dcdc_converter(False, settings)
    logging.info("Turning off DC-DC converter.")
    set_relay_connection(0, 0, settings)
    logging.info("Resetting relay connections to default state.")
    balancing_active = False
    web_data['balancing'] = False
    last_balance_time = time.time()

def compute_bank_medians(calibrated_temps, valid_min):
    """Compute median temps per bank."""
    bank_medians = []
    for start, end in BANK_RANGES:
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None]
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0
        bank_medians.append(bank_median)
    return bank_medians

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    """Draw the TUI with battery art, temps inside, ADC, alerts."""
    logging.debug("Refreshing TUI.")
    stdscr.clear()
    # Colors
    curses.start_color()
    curses.use_default_colors()
    TITLE_COLOR = curses.color_pair(1)
    HIGH_V = curses.color_pair(2)
    LOW_V = curses.color_pair(3)
    OK_V = curses.color_pair(4)
    ADC_C = curses.color_pair(5)
    BAL_C = curses.color_pair(6)
    INFO_C = curses.color_pair(7)
    ERR_C = curses.color_pair(8)
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    
    # Get screen dimensions for bounds checks
    height, width = stdscr.getmaxyx()
    
    # Total voltage
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = HIGH_V if total_v > total_high else LOW_V if total_v < total_low else OK_V
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    roman_lines = roman_v.splitlines()
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < width:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    
    y_offset = len(roman_lines) + 2
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return
    
    # Tall battery art with temps inside
    battery_art_base = [
        "   ___________   ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |    +++    |  ",
        "  |    +++    |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |    ---    |  ",
        "  |    ---    |  ",
        "  |    ---    |  ",
        "  |           |  ",
        "  |           |  ",
        "  |___________|  "
    ]
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    
    # Draw base art for all banks side-by-side
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS
        if y_offset + row < height and len(full_line) < width:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, OK_V)
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    
    # Overlay content inside each bank
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width
        # Voltage on line 1, centered
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        v_color = ERR_C if voltages[bank_id] == 0.0 else HIGH_V if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else LOW_V if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else OK_V
        v_center = start_pos + (art_width - len(v_str)) // 2
        v_y = y_offset + 1
        if v_y < height and v_center + len(v_str) < width:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        
        # Temps on lines 2-9 (C1-C8)
        start, end = BANK_RANGES[bank_id]
        for local_ch, ch in enumerate(range(start, end + 1), 0):
            idx = ch - 1
            raw = raw_temps[idx] if idx < len(raw_temps) else 0
            calib = calibrated_temps[idx]
            calib_str = f"{calib:.1f}" if calib is not None else "Inv"
            if is_startup:
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv"
                offset_str = f"{offsets[idx]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A"
                detail = f" ({raw_str}/{offset_str})"
            else:
                detail = ""
            t_str = f"C{local_ch+1}: {calib_str}{detail}"
            t_color = ERR_C if "Inv" in calib_str else HIGH_V if calib > settings['high_threshold'] else LOW_V if calib < settings['low_threshold'] else OK_V
            t_center = start_pos + (art_width - len(t_str)) // 2
            t_y = y_offset + 2 + local_ch
            if t_y < height and t_center + len(t_str) < width:
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color)
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.")
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.")
        
        # Median on line 15
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C"
        med_center = start_pos + (art_width - len(med_str)) // 2
        med_y = y_offset + 15
        if med_y < height and med_center + len(med_str) < width:
            try:
                stdscr.addstr(med_y, med_center, med_str, INFO_C)
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.")
    
    y_offset += art_height + 2
    if y_offset >= height:
        logging.warning("Skipping ADC/readings - out of bounds.")
    else:
        # ADC/readings
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)
            logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")
            if voltage is None:
                voltage = 0.0
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_C)
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.")
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.")
            y_offset += 1
            if y_offset < height:
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_C)
                    else:
                        stdscr.addstr(y_offset, 0, "  [Readings: No data]", ADC_C)
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.")
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.")
            y_offset += 1
    
    y_offset += 1
    
    # Startup median, fixed formatting
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", INFO_C)
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2
    
    # Alerts
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", INFO_C)
        except curses.error:
            logging.warning("addstr error for alerts header.")
    y_offset += 1
    if alerts:
        for alert in alerts:
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, alert, ERR_C)
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.")
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.")
            y_offset += 1
    else:
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", OK_V)
            except curses.error:
                logging.warning("addstr error for no alerts message.")
        else:
            logging.warning("Skipping no alerts message - out of bounds.")
    
    stdscr.refresh()

def startup_self_test(settings, stdscr):
    """Perform startup self-test with configurable enable/disable."""
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets
    
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        if stdscr:
            stdscr.clear()
            try:
                stdscr.addstr(0, 0, "Startup Self-Test Disabled", curses.color_pair(4))
                stdscr.refresh()
                time.sleep(2)
            except:
                pass
        return []
    
    logging.info("Starting self-test: Validating config, connectivity, sensors, and balancer.")
    alerts = []
    if stdscr:
        stdscr.clear()
        y = 0
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))
            except curses.error:
                logging.warning("addstr error for title.")
        y += 2
        stdscr.refresh()
    
    # Step 1: Config validation
    if stdscr and y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 1.")
    if stdscr:
        stdscr.refresh()
        time.sleep(0.5)
    if settings['NumberOfBatteries'] != NUM_BANKS:
        alerts.append("Config mismatch: NumberOfBatteries != 3.")
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for config mismatch.")
    else:
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for config OK.")
    if stdscr:
        y += 2
        stdscr.refresh()
    
    # Step 2: Hardware connectivity
    if stdscr and y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 2.")
    if stdscr:
        stdscr.refresh()
        time.sleep(0.5)
    try:
        choose_channel(0, settings['MultiplexerAddress'])
        bus.read_byte(settings['VoltageMeterAddress'])
        bus.read_byte(settings['RelayAddress'])
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for I2C OK.")
    except IOError as e:
        alerts.append(f"I2C connectivity failure: {str(e)}")
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for I2C failure.")
    try:
        test_query = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1)
        if isinstance(test_query, str) and "Error" in test_query:
            raise ValueError(test_query)
        if stdscr and y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Modbus OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for Modbus OK.")
    except Exception as e:
        alerts.append(f"Modbus test failure: {str(e)}")
        if stdscr and y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, f"Modbus failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for Modbus failure.")
    if stdscr:
        y += 3
        stdscr.refresh()
    
    # Step 3: Initial sensor reads
    if stdscr and y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 3.")
    if stdscr:
        stdscr.refresh()
        time.sleep(0.5)
    initial_temps = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
    if isinstance(initial_temps, str):
        alerts.append(f"Initial temp read failure: {initial_temps}")
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temp read failure.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for temp failure.")
    else:
        if stdscr and y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for temps OK.")
    initial_voltages = [read_voltage_with_retry(i, settings)[0] or 0.0 for i in range(1, NUM_BANKS + 1)]
    if any(v == 0.0 for v in initial_voltages):
        alerts.append("Initial voltage read failure: Zero voltage on one or more banks.")
        if stdscr and y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for voltage failure.")
    else:
        if stdscr and y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for voltages OK.")
    # Set calibration if all temps valid
    if isinstance(initial_temps, list):
        valid_count = sum(1 for t in initial_temps if t > settings['valid_min'])
        if valid_count == settings['num_channels']:
            startup_median = statistics.median(initial_temps)
            startup_offsets = [startup_median - t for t in initial_temps]
            save_offsets(startup_median, startup_offsets)
            startup_set = True
            logging.info(f"Temp calibration set during startup. Median: {startup_median:.1f}°C")
    if stdscr:
        y += 3
        stdscr.refresh()
    
    # Step 4: Balancer test (only if no previous failures)
    if not alerts:
        if stdscr and y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 4.")
        if stdscr:
            y += 1
            stdscr.refresh()
            time.sleep(0.5)
        pairs = [(1,2), (1,3), (2,1), (2,3), (3,1), (3,2)]
        test_duration = settings['test_balance_duration']
        read_interval = settings['test_read_interval']
        min_delta = settings['min_voltage_delta']
        
        for high, low in pairs:
            if stdscr and y < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y, 0, f"Testing balance: Bank {high} -> {low} for {test_duration}s.", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for testing balance.")
            if stdscr:
                stdscr.refresh()
            logging.info(f"Testing balance: Bank {high} -> {low} for {test_duration}s.")
            
            # Pre-check temps (skip if anomalous)
            temp_anomaly = False
            if initial_temps and isinstance(initial_temps, list):
                for t in initial_temps:
                    if t > settings['high_threshold'] or t < settings['low_threshold']:
                        temp_anomaly = True
                        break
            if temp_anomaly:
                alerts.append(f"Skipping balance test {high}->{low}: Temp anomalies.")
                if stdscr and y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for skipped temp.")
                if stdscr:
                    y += 2
                    stdscr.refresh()
                continue
            
            # Start balancing
            set_relay_connection(high, low, settings)
            control_dcdc_converter(True, settings)
            start_time = time.time()
            
            high_trend = []
            low_trend = []
            progress_y = y + 1
            while time.time() - start_time < test_duration:
                time.sleep(read_interval)
                high_v = read_voltage_with_retry(high, settings)[0] or 0.0
                low_v = read_voltage_with_retry(low, settings)[0] or 0.0
                high_trend.append(high_v)
                low_trend.append(low_v)
                elapsed = time.time() - start_time
                if stdscr and progress_y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                        stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1
                        stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, High {high_v:.2f}V, Low {low_v:.2f}V", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error in startup balance progress.")
                if stdscr:
                    stdscr.refresh()
                logging.debug(f"Trend read: High {high_v:.2f}V, Low {low_v:.2f}V")
            
            # Stop balancing
            control_dcdc_converter(False, settings)
            set_relay_connection(0, 0, settings)
            
            # Analyze trends
            if stdscr and progress_y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for analyzing.")
            if stdscr:
                stdscr.refresh()
            if len(high_trend) >= 3:
                high_delta = high_trend[0] - high_trend[-1]
                low_delta = low_trend[-1] - low_trend[0]
                if high_delta < min_delta or low_delta < min_delta:
                    alerts.append(f"Balance test {high}->{low} failed: Insufficient change (High Δ={high_delta:.3f}V, Low Δ={low_delta:.3f}V).")
                    if stdscr and progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient voltage change.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient change.")
                else:
                    if stdscr and progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test passed.", curses.color_pair(4))
                        except curses.error:
                            logging.warning("addstr error for test passed.")
            else:
                alerts.append(f"Balance test {high}->{low} failed: Insufficient readings.")
                if stdscr and progress_y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for test failed insufficient readings.")
            if stdscr:
                stdscr.refresh()
                y = progress_y + 2
            time.sleep(2)
    
    # Handle test results
    startup_alerts = alerts
    if alerts:
        startup_failed = True
        logging.error("Startup self-test failures: " + "; ".join(alerts))
        send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings)
        GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
        if stdscr and y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Self-Test Complete with Failures. Continuing with warnings.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for self-test failures.")
    else:
        if stdscr and y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Self-Test Complete. All OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for self-test OK.")
        logging.info("Startup self-test passed.")
    if stdscr:
        stdscr.refresh()
        time.sleep(3)
    return alerts

class BMSRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for web interface."""
    
    def __init__(self, settings, *args, **kwargs):
        self.settings = settings
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS Interface"')
            self.end_headers()
            return
        
        # Handle CORS if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        
        # Handle different endpoints
        if path == '/':
            self.serve_index()
        elif path == '/api/status':
            self.serve_api_status()
        elif path == '/api/balance':
            self.serve_api_balance()
        elif path == '/api/config':
            self.serve_api_config()
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS."""
        self.send_response(200)
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
    
    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS Interface"')
            self.end_headers()
            return
        
        # Handle CORS if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        
        # Handle different endpoints
        if path == '/api/balance':
            self.handle_balance_request()
        else:
            self.send_response(404)
            self.end_headers()
    
    def authenticate(self):
        """Check HTTP Basic Authentication."""
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            return (username == self.settings['username'] and 
                    password == self.settings['password'])
        return False
    
    def serve_index(self):
        """Serve the main HTML page."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Battery Management System</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
                            
                            // Update battery banks
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
                            
                            // Update alerts
                            const alertsContainer = document.getElementById('alerts-container');
                            if (data.alerts.length > 0) {
                                alertsContainer.innerHTML = data.alerts.map(alert => 
                                    `<p class="alert">${alert}</p>`
                                ).join('');
                            } else {
                                alertsContainer.innerHTML = '<p class="normal">No alerts</p>';
                            }
                            
                            // Update balance button
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
                
                // Initial load and periodic updates
                updateStatus();
                setInterval(updateStatus, 5000);
            </script>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))
    
    def serve_api_status(self):
        """Serve system status as JSON."""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
        self.end_headers()
        
        response = {
            'voltages': web_data['voltages'],
            'temperatures': web_data['temperatures'],
            'alerts': web_data['alerts'],
            'balancing': web_data['balancing'],
            'last_update': web_data['last_update'],
            'system_status': web_data['system_status'],
            'total_voltage': sum(web_data['voltages'])
        }
        
        self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def serve_api_balance(self):
        """Serve balance information as JSON."""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
        self.end_headers()
        
        response = {
            'balancing': web_data['balancing'],
            'can_balance': not web_data['balancing'] and len(web_data['alerts']) == 0
        }
        
        self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def serve_api_config(self):
        """Serve configuration information as JSON."""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
        self.end_headers()
        
        # Return a subset of safe configuration values
        response = {
            'number_of_batteries': self.settings['NumberOfBatteries'],
            'poll_interval': self.settings['poll_interval'],
            'voltage_thresholds': {
                'low': self.settings['LowVoltageThresholdPerBattery'],
                'high': self.settings['HighVoltageThresholdPerBattery']
            },
            'temperature_thresholds': {
                'low': self.settings['low_threshold'],
                'high': self.settings['high_threshold']
            }
        }
        
        self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def handle_balance_request(self):
        """Handle balance initiation request."""
        global balancing_active
        
        if balancing_active:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            if self.settings['cors_enabled']:
                self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.end_headers()
            
            response = {'success': False, 'message': 'Balancing already in progress'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return
        
        if len(web_data['alerts']) > 0:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            if self.settings['cors_enabled']:
                self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.end_headers()
            
            response = {'success': False, 'message': 'Cannot balance with active alerts'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return
        
        # Find banks to balance
        voltages = web_data['voltages']
        if len(voltages) < 2:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            if self.settings['cors_enabled']:
                self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.end_headers()
            
            response = {'success': False, 'message': 'Not enough battery banks'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return
        
        max_v = max(voltages)
        min_v = min(voltages)
        high_bank = voltages.index(max_v) + 1
        low_bank = voltages.index(min_v) + 1
        
        if max_v - min_v < self.settings['VoltageDifferenceToBalance']:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            if self.settings['cors_enabled']:
                self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.end_headers()
            
            response = {'success': False, 'message': 'Voltage difference too small for balancing'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return
        
        # Set flag to indicate manual balance request
        # The main loop will handle the actual balancing
        balancing_active = True
        web_data['balancing'] = True
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
        self.end_headers()
        
        response = {
            'success': True, 
            'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))

def start_web_server(settings):
    """Start the web server in a separate thread."""
    global web_server
    
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    
    host = settings['host']
    port = settings['port']
    
    def handler(*args):
        BMSRequestHandler(settings, *args)
    
    try:
        web_server = HTTPServer((host, port), handler)
        logging.info(f"Web server started on {host}:{port}")
        
        # Start server in a separate thread
        server_thread = threading.Thread(target=web_server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
    except Exception as e:
        logging.error(f"Failed to start web server: {e}")

def main(stdscr):
    """Main loop for polling, processing, balancing, and TUI."""
    global web_data, balancing_active
    
    stdscr.keypad(True)
    # Initialize colors early, before any TUI drawing
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    
    settings = load_config()
    setup_hardware(settings)
    
    # Start web server
    start_web_server(settings)
    
    startup_self_test(settings, stdscr)
    signal.signal(signal.SIGINT, signal_handler)
    
    startup_median, startup_offsets = load_offsets()
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True
        logging.info(f"Loaded startup median: {startup_median:.1f}°C")
    
    previous_temps = None
    previous_bank_medians = [None] * NUM_BANKS
    run_count = 0
    
    web_data['system_status'] = 'Running'
    
    while True:
        logging.info("Starting poll cycle.")
        web_data['last_update'] = time.time()
        
        # Read temps
        temp_result = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
        temps_alerts = []
        if isinstance(temp_result, str):
            temps_alerts.append(temp_result)
            calibrated_temps = [None] * settings['num_channels']
            raw_temps = [settings['valid_min']] * settings['num_channels']
            bank_medians = [0.0] * NUM_BANKS
            web_data['system_status'] = 'Temp Read Error'
        else:
            valid_count = sum(1 for t in temp_result if t > settings['valid_min'])
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result)
                startup_offsets = [startup_median - raw for raw in temp_result]
                save_offsets(startup_median, startup_offsets)
                startup_set = True
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
            
            if startup_set and startup_offsets is None:
                startup_set = False
            
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])]
            raw_temps = temp_result
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min'])
            
            # Update web data
            web_data['temperatures'] = calibrated_temps
            web_data['system_status'] = 'Running'
            
            for ch, raw in enumerate(raw_temps, 1):
                if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']):
                    continue
                calib = calibrated_temps[ch-1]
                bank_id = get_bank_for_channel(ch)
                bank_median = bank_medians[bank_id - 1]
                check_high_temp(calib, ch, temps_alerts, settings['high_threshold'])
                check_low_temp(calib, ch, temps_alerts, settings['low_threshold'])
                check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'])
            
            if run_count > 0 and previous_temps and previous_bank_medians is not None:
                for bank_id in range(1, NUM_BANKS + 1):
                    bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]
                    start, end = BANK_RANGES[bank_id - 1]
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1]
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'])
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'])
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts)
            
            previous_temps = calibrated_temps[:]
            previous_bank_medians = bank_medians[:]
        
        # Read voltages (per bank)
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)
            battery_voltages.append(v if v is not None else 0.0)
        
        # Update web data
        web_data['voltages'] = battery_voltages
        
        # Check issues (combined)
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        
        # Update web data
        web_data['alerts'] = all_alerts
        if alert_needed:
            web_data['system_status'] = 'Alert'
        
        # Balance if needed, but skip if any alerts
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)
            min_v = min(battery_voltages)
            high_b = battery_voltages.index(max_v) + 1
            low_b = battery_voltages.index(min_v) + 1
            current_time = time.time()
            
            if balancing_active:
                # Manual balance request is being processed
                if not alert_needed and min_v > 0:
                    balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)
                    balancing_active = False
            elif alert_needed:
                logging.warning("Skipping balancing due to active alerts.")
            elif max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']:
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)
        
        # Draw TUI with is_startup
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0))
        
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks']))

if __name__ == '__main__':
    curses.wrapper(main)
