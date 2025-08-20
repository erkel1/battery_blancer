"""
Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)

Updates based on revised balancer script:
- Removed NumberOfCellsInSeries, NumberOfSamples, MaxRetries from config; hardcoded samples=2, attempts=2 in read_voltage_with_retry.
- Used tall battery art from revised script, integrated temps into empty lines "inside" the art (C1-C8 on lines 2-9, med on line 10, volt on line 1).
- Wider art (25 chars) for natural look, centered text.
- ADC/readings shown below art per bank, as in revised.
- Balancing progress below, as before.
- Other logic adjusted to match revised balancer (e.g., no extra keys in config).
- Enhanced logging throughout for readability, matching original style (debug for steps, info for actions, error for exceptions).
- Fixed voltage color check bug: low threshold now uses 'LowVoltageThresholdPerBattery' correctly.
- Fixed startup median formatting: split f-string to avoid error when None.
- Fixed calibration None error: guard if startup_offsets is None, reset startup_set.
- Fixed unfinished balancing logic: completed if block.
- Fixed y_offset scope in balancing: hardcoded progress_y based on art height (17) + adc lines (6) + margins.
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

# Bank definitions
BANK_RANGES = [(1, 8), (9, 16), (17, 24)]  # Channels per bank
NUM_BANKS = 3

def get_bank_for_channel(ch):
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):
        if start <= ch <= end:
            return bank_id
    return None

def modbus_crc(data):
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
    logging.info("Starting temperature sensor read.")
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{port}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((ip, port))
            s.send(query)
            time.sleep(query_delay)
            response = s.recv(1024)
            s.close()
            
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
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states
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
    
    # Voltage/Balance settings (updated from revised)
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
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x48'), 16),
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x10'), 16),
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17),
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27),
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.example.com'),
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='alert@example.com'),
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='admin@example.com'),
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x4000'), 16),
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x1400'), 16),
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x2000'), 16),
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=1.0),
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=1.0),
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=1.0)
    }
    
    if voltage_settings['NumberOfBatteries'] != NUM_BANKS:
        logging.warning(f"NumberOfBatteries ({voltage_settings['NumberOfBatteries']}) does not match NUM_BANKS ({NUM_BANKS}); using {NUM_BANKS} for banks.")
    
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}
    
    logging.info("Configuration loaded successfully.")
    return {**temp_settings, **voltage_settings}

def setup_hardware(settings):
    global bus
    logging.info("Setting up hardware.")
    bus = smbus.SMBus(settings['I2C_BusNumber'])
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
    logging.info("Hardware setup complete.")

def signal_handler(sig, frame):
    logging.info("Script stopped by user or signal.")
    GPIO.cleanup()
    sys.exit(0)

def load_offsets():
    logging.info("Loading startup offsets from 'offsets.txt'.")
    if os.path.exists('offsets.txt'):
        with open('offsets.txt', 'r') as f:
            offsets = [float(line.strip()) for line in f]
            logging.debug(f"Loaded {len(offsets)} offsets.")
            return offsets
    logging.warning("No 'offsets.txt' found; using none.")
    return None

def save_offsets(offsets):
    logging.info("Saving startup offsets to 'offsets.txt'.")
    with open('offsets.txt', 'w') as f:
        for offset in offsets:
            f.write(f"{offset}\n")
    logging.debug("Offsets saved.")

def check_invalid_reading(raw, ch, alerts, valid_min):
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min}).")
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.")
        return True
    return False

def check_high_temp(calibrated, ch, alerts, high_threshold):
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C).")
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.")

def check_low_temp(calibrated, ch, alerts, low_threshold):
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C).")
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.")

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    abs_dev = abs(calibrated - bank_median)
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%}).")
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s).")
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.")

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C).")
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")

def check_sudden_disconnection(current, previous_temps, ch, alerts):
    previous = previous_temps[ch-1]
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Sudden disconnection.")
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.")

def choose_channel(channel, multiplexer_address):
    logging.debug(f"Switching to I2C channel {channel}.")
    bus.write_byte(multiplexer_address, 1 << channel)

def setup_voltage_meter(settings):
    logging.debug("Configuring voltage meter ADC.")
    config_value = (settings['ContinuousModeConfig'] | 
                    settings['SampleRateConfig'] | 
                    settings['GainConfig'])
    bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)

def read_voltage_with_retry(bank_id, settings):
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
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])
        relay_state = 0
        logging.debug(f"Initial relay state: {bin(relay_state)}")

        if high == low or high < 1 or low < 1:
            logging.debug("No need for relay activation; all relays off.")
            relay_state = 0
        else:
            if high == 2 and low == 1:
                relay_state |= (1 << 0)
                logging.debug("Relay 1 activated for high to low.")
            elif high == 3 and low == 1:
                relay_state |= (1 << 0) | (1 << 1)
                logging.debug("Relays 1 and 2 activated for high to low.")
            elif high == 1 and low == 2:
                relay_state |= (1 << 2)
                logging.debug("Relay 3 activated for low to high.")
            elif high == 1 and low == 3:
                relay_state |= (1 << 2) | (1 << 3)
                logging.debug("Relays 3 and 4 activated for low to high.")
            elif high == 2 and low == 3:
                relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
                logging.debug("Relays 1, 3, and 4 activated for high to low.")
            elif high == 3 and low == 2:
                relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
                logging.debug("Relays 1, 2, and 3 activated for high to low.")

        logging.debug(f"Final relay state: {bin(relay_state)}")
        logging.info(f"Sending relay state command to hardware.")
        bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state)
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}")
    except IOError as e:
        logging.error(f"I/O error while setting up relay: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}")

def control_dcdc_converter(turn_on, settings):
    try:
        GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(message, settings):
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
            server.send_message(msg)
        last_email_time = time.time()
        logging.info(f"Alert email sent: {message}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")

def check_for_issues(voltages, temps_alerts, settings):
    logging.info("Checking for voltage and temp issues.")
    alert_needed = False
    alerts = []
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
    global balance_start_time, last_balance_time, balancing_active
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    logging.info(f"Starting balance from Bank {high} to {low}.")
    balancing_active = True
    voltage_high, _, _ = read_voltage_with_retry(high, settings)
    voltage_low, _, _ = read_voltage_with_retry(low, settings)
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        return
    set_relay_connection(high, low, settings)
    control_dcdc_converter(True, settings)
    balance_start_time = time.time()
    animation_frames = ['|', '/', '-', '\\']
    frame_index = 0
    progress_y = 17 + 6 + 2  # Hardcoded: art_height=17, adc_lines=6 (2 per bank), margin=2
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        stdscr.addstr(progress_y, 0, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
        stdscr.addstr(progress_y + 1, 0, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))
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
    last_balance_time = time.time()

def compute_bank_medians(calibrated_temps, valid_min):
    bank_medians = []
    for start, end in BANK_RANGES:
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None]
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0
        bank_medians.append(bank_median)
    return bank_medians

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    logging.debug("Refreshing TUI.")
    stdscr.clear()
    # Colors
    curses.start_color()
    curses.use_default_colors()
    TITLE_COLOR = curses.color_pair(1)  # Red
    HIGH_V = curses.color_pair(2)  # Red
    LOW_V = curses.color_pair(3)  # Yellow
    OK_V = curses.color_pair(4)  # Green
    ADC_C = curses.color_pair(5)  # Light
    BAL_C = curses.color_pair(6)  # Yellow
    INFO_C = curses.color_pair(7)  # Cyan
    ERR_C = curses.color_pair(8)  # Magenta
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    
    # Total voltage
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = HIGH_V if total_v > total_high else LOW_V if total_v < total_low else OK_V
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    stdscr.addstr(0, 0, "Battery Monitor GUI (3s8p)", TITLE_COLOR)
    for i, line in enumerate(roman_v.splitlines()):
        stdscr.addstr(i + 1, 0, line, v_color)
    
    y_offset = len(roman_v.splitlines()) + 2
    
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
        stdscr.addstr(y_offset + row, 0, full_line, OK_V)
    
    # Overlay content inside each bank
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width
        # Voltage on line 1, centered
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        v_color = ERR_C if voltages[bank_id] == 0.0 else HIGH_V if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else LOW_V if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else OK_V
        v_center = start_pos + (art_width - len(v_str)) // 2
        stdscr.addstr(y_offset + 1, v_center, v_str, v_color)
        
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
            stdscr.addstr(y_offset + 2 + local_ch, t_center, t_str, t_color)
        
        # Median on line 15
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C"
        med_center = start_pos + (art_width - len(med_str)) // 2
        stdscr.addstr(y_offset + 15, med_center, med_str, INFO_C)
    
    y_offset += art_height + 2
    
    # ADC/readings
    for i in range(1, NUM_BANKS + 1):
        voltage, readings, adc_values = read_voltage_with_retry(i, settings)
        logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")
        if voltage is None:
            voltage = 0.0
        stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_C)
        y_offset += 1
        if readings:
            stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_C)
        else:
            stdscr.addstr(y_offset, 0, "  [Readings: No data]", ADC_C)
        y_offset += 1
    
    y_offset += 1
    
    # Startup median, fixed formatting
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", INFO_C)
    y_offset += 2
    
    # Alerts
    stdscr.addstr(y_offset, 0, "Alerts:", INFO_C)
    y_offset += 1
    if alerts:
        for alert in alerts:
            stdscr.addstr(y_offset, 0, alert, ERR_C)
            y_offset += 1
    else:
        stdscr.addstr(y_offset, 0, "No alerts.", OK_V)
    
    if is_startup:
        stdscr.addstr(y_offset + 1, 0, "Press any key to continue...", INFO_C)
        stdscr.getch()
    
    stdscr.refresh()

def main(stdscr):
    stdscr.keypad(True)
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages
    settings = load_config()
    setup_hardware(settings)
    signal.signal(signal.SIGINT, signal_handler)
    logging.basicConfig(filename='battery_monitor.log', level=logging.INFO, format='%(asctime)s - %(message)s')
    
    startup_offsets = load_offsets()
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True
        startup_median = statistics.median(startup_offsets)  # Approx
    previous_temps = None
    previous_bank_medians = [None] * NUM_BANKS
    run_count = 0
    
    while True:
        logging.info("Starting poll cycle.")
        # Read temps
        temp_result = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
        temps_alerts = []
        if isinstance(temp_result, str):
            temps_alerts.append(temp_result)
            calibrated_temps = [None] * settings['num_channels']
            raw_temps = [settings['valid_min']] * settings['num_channels']
            bank_medians = [0.0] * NUM_BANKS
        else:
            valid_count = sum(1 for t in temp_result if t > settings['valid_min'])
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result)
                startup_offsets = [startup_median - raw for raw in temp_result]
                save_offsets(startup_offsets)
                startup_set = True
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
            # Guard for None offsets
            if startup_set and startup_offsets is None:
                startup_set = False
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])]
            raw_temps = temp_result
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min'])
            
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
        
        # Check issues (combined)
        _, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        
        # Balance if needed
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)
            min_v = min(battery_voltages)
            high_b = battery_voltages.index(max_v) + 1
            low_b = battery_voltages.index(min_v) + 1
            current_time = time.time()
            if max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']:
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)
        
        # Draw TUI with is_startup
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0))
        
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks']))

if __name__ == '__main__':
    curses.wrapper(main)