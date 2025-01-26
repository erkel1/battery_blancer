import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses
import logging
import sys
import os
import signal
from art import text2art
from collections import deque
import math

# Load configuration
config = configparser.ConfigParser()
config.read('config.ini')

# Setup logging
logging_level = getattr(logging, config['General']['LoggingLevel'].upper(), logging.INFO)
logging.basicConfig(level=logging_level,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='battery_balancer.log',
                    filemode='a')

# Global state
bus = None
config_values = None
last_email_time = 0
balance_start_time = None
balancing_active = False
last_balance_time = 0
voltage_history = {1: deque(maxlen=40), 2: deque(maxlen=40), 3: deque(maxlen=40)}
balance_progress = 0.0
last_balance_update = 0

# Color constants
COLOR_HEADER = 1
COLOR_NORMAL = 2
COLOR_WARNING = 3
COLOR_CRITICAL = 4
COLOR_STATUS = 5
COLOR_GRAPH_BG = 6

def load_settings():
    try:
        settings = {
            'General': {
                'NumberOfBatteries': config.getint('General', 'NumberOfBatteries'),
                'VoltageDifferenceToBalance': config.getfloat('General', 'VoltageDifferenceToBalance'),
                'BalanceDurationSeconds': config.getint('General', 'BalanceDurationSeconds'),
                'SleepTimeBetweenChecks': config.getfloat('General', 'SleepTimeBetweenChecks'),
                'BalanceRestPeriodSeconds': config.getint('General', 'BalanceRestPeriodSeconds'),
                'LowVoltageThresholdPerBattery': config.getfloat('General', 'LowVoltageThresholdPerBattery'),
                'HighVoltageThresholdPerBattery': config.getfloat('General', 'HighVoltageThresholdPerBattery'),
                'I2C_BusNumber': config.getint('General', 'I2C_BusNumber'),
                'VoltageDividerRatio': config.getfloat('General', 'VoltageDividerRatio')
            },
            'I2C': {
                'MultiplexerAddress': int(config.get('I2C', 'MultiplexerAddress'), 16),
                'VoltageMeterAddress': int(config.get('I2C', 'VoltageMeterAddress'), 16),
                'RelayAddress': int(config.get('I2C', 'RelayAddress'), 16),
            },
            'GPIO': {
                'DC_DC_RelayPin': config.getint('GPIO', 'DC_DC_RelayPin'),
                'AlarmRelayPin': config.getint('GPIO', 'AlarmRelayPin'),
            },
            'Email': {
                'SMTP_Server': config.get('Email', 'SMTP_Server'),
                'SMTP_Port': config.getint('Email', 'SMTP_Port'),
                'SenderEmail': config.get('Email', 'SenderEmail'),
                'RecipientEmail': config.get('Email', 'RecipientEmail'),
            },
            'Calibration': {
                'Sensor1_Calibration': config.getfloat('Calibration', 'Sensor1_Calibration'),
                'Sensor2_Calibration': config.getfloat('Calibration', 'Sensor2_Calibration'),
                'Sensor3_Calibration': config.getfloat('Calibration', 'Sensor3_Calibration'),
            }
        }
        return settings
    except Exception as e:
        logging.error(f"Config error: {e}")
        raise

def setup_hardware():
    global bus, config_values
    try:
        config_values = load_settings()
        bus = smbus.SMBus(config_values['General']['I2C_BusNumber'])
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(config_values['GPIO']['DC_DC_RelayPin'], GPIO.OUT)
        GPIO.setup(config_values['GPIO']['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
        logging.info("Hardware initialized")
    except Exception as e:
        logging.critical(f"Hardware setup failed: {e}")
        raise

def choose_channel(channel):
    try:
        bus.write_byte(config_values['I2C']['MultiplexerAddress'], 1 << channel)
    except IOError as e:
        logging.error(f"Channel select error: {e}")

def setup_voltage_meter():
    config_value = (int(config.get('ADC', 'ContinuousModeConfig'), 16) |
                    int(config.get('ADC', 'SampleRateConfig'), 16) |
                    int(config.get('ADC', 'GainConfig'), 16))
    try:
        bus.write_word_data(config_values['I2C']['VoltageMeterAddress'],
                           int(config.get('ADC', 'ConfigRegister'), 16),
                           config_value)
    except IOError as e:
        logging.error(f"Voltage meter setup error: {e}")

def read_voltage_with_retry(battery_id, samples=2, max_attempts=2):
    ratio = config_values['General']['VoltageDividerRatio']
    sensor_id = (battery_id - 1) % 3 + 1
    calibration = config_values['Calibration'][f'Sensor{sensor_id}_Calibration']
    
    for attempt in range(max_attempts):
        try:
            readings = []
            raw_values = []
            for _ in range(samples):
                channel = (battery_id - 1) % 3
                choose_channel(channel)
                setup_voltage_meter()
                bus.write_byte(config_values['I2C']['VoltageMeterAddress'], 0x01)
                time.sleep(0.05)
                raw_adc = bus.read_word_data(config_values['I2C']['VoltageMeterAddress'],
                                            int(config.get('ADC', 'ConversionRegister'), 16))
                raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)
                
                if raw_adc:
                    voltage = (raw_adc * 6.144 / 32767) / ratio * calibration
                    readings.append(voltage)
                    raw_values.append(raw_adc)
            
            if readings:
                avg = sum(readings) / len(readings)
                return avg, readings, raw_values
        except IOError as e:
            logging.warning(f"Read error battery {battery_id}: {e}")
            time.sleep(0.01)
    
    logging.error(f"Failed reading battery {battery_id}")
    return None, [], []

def set_relay_connection(high, low):
    try:
        choose_channel(3)
        relay_state = 0
        
        if high == 2 and low == 1:
            relay_state |= 1 << 0
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1)
        elif high == 1 and low == 2:
            relay_state |= 1 << 2
        elif high == 1 and low == 3:
            relay_state |= (1 << 2) | (1 << 3)
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
        
        bus.write_byte_data(config_values['I2C']['RelayAddress'], 0x11, relay_state)
    except Exception as e:
        logging.error(f"Relay error: {e}")

def control_dcdc_converter(enable):
    try:
        GPIO.output(config_values['GPIO']['DC_DC_RelayPin'], GPIO.HIGH if enable else GPIO.LOW)
    except GPIO.GPIOError as e:
        logging.error(f"DC-DC control error: {e}")

def send_alert_email(voltage=None, bid=None, alert_type="high"):
    global last_email_time
    if time.time() - last_email_time < config_values['General']['EmailAlertIntervalSeconds']:
        return
    
    try:
        subject = "Battery Alert - "
        body = ""
        if alert_type == "high":
            subject += f"Overvoltage (Battery {bid})"
            body = f"Battery {bid} voltage {voltage:.2f}V exceeds {config_values['General']['HighVoltageThresholdPerBattery']}V"
        elif alert_type == "low":
            subject += f"Undervoltage (Battery {bid})"
            body = f"Battery {bid} voltage {voltage:.2f}V below {config_values['General']['LowVoltageThresholdPerBattery']}V"
        else:
            subject += f"Critical Error (Battery {bid})"
            body = f"Battery {bid} reading invalid voltage: {voltage}V"
        
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = config_values['Email']['SenderEmail']
        msg['To'] = config_values['Email']['RecipientEmail']
        
        with smtplib.SMTP(config_values['Email']['SMTP_Server'],
                         config_values['Email']['SMTP_Port']) as server:
            server.send_message(msg)
        last_email_time = time.time()
    except Exception as e:
        logging.error(f"Email failed: {e}")

def check_for_voltage_issues(voltages):
    alert = False
    for i, v in enumerate(voltages, 1):
        if v is None or v <= 0:
            send_alert_email(v, i, "zero")
            alert = True
            GPIO.output(config_values['GPIO']['AlarmRelayPin'], GPIO.HIGH)
        elif v > config_values['General']['HighVoltageThresholdPerBattery']:
            send_alert_email(v, i, "high")
            alert = True
            GPIO.output(config_values['GPIO']['AlarmRelayPin'], GPIO.HIGH)
        elif v < config_values['General']['LowVoltageThresholdPerBattery']:
            send_alert_email(v, i, "low")
            alert = True
            GPIO.output(config_values['GPIO']['AlarmRelayPin'], GPIO.HIGH)
    
    if not alert:
        GPIO.output(config_values['GPIO']['AlarmRelayPin'], GPIO.LOW)
    return alert

def balance_battery_voltages(stdscr, high_bat, low_bat):
    global balancing_active, balance_progress, balance_start_time
    try:
        balancing_active = True
        balance_start_time = time.time()
        set_relay_connection(high_bat, low_bat)
        control_dcdc_converter(True)
        
        while time.time() - balance_start_time < config_values['General']['BalanceDurationSeconds']:
            balance_progress = (time.time() - balance_start_time) / config_values['General']['BalanceDurationSeconds']
            stdscr.noutrefresh()
            curses.doupdate()
            time.sleep(0.1)
        
        control_dcdc_converter(False)
        set_relay_connection(0, 0)
    except Exception as e:
        logging.error(f"Balance error: {e}")
    finally:
        balancing_active = False

def draw_header(stdscr):
    header = " BATTERY MANAGEMENT SYSTEM "
    stdscr.addstr(0, 0, "╭" + "─"*(curses.COLS-2) + "╮", curses.color_pair(COLOR_HEADER))
    stdscr.addstr(1, 0, "│", curses.color_pair(COLOR_HEADER))
    stdscr.addstr(1, (curses.COLS-len(header))//2, header, curses.color_pair(COLOR_HEADER))
    stdscr.addstr(1, curses.COLS-1, "│", curses.color_pair(COLOR_HEADER))
    stdscr.addstr(2, 0, "╰" + "─"*(curses.COLS-2) + "╯", curses.color_pair(COLOR_HEADER))

def draw_battery(stdscr, y, x, voltage, is_active=False):
    max_v = config_values['General']['HighVoltageThresholdPerBattery']
    fill = min(16, math.ceil(16 * (voltage / max_v)))
    
    color = COLOR_NORMAL
    if voltage > config_values['General']['HighVoltageThresholdPerBattery']:
        color = COLOR_CRITICAL
    elif voltage < config_values['General']['LowVoltageThresholdPerBattery']:
        color = COLOR_WARNING
    
    attr = curses.A_BOLD if is_active else curses.A_NORMAL
    stdscr.addstr(y, x, "╔════════════════╗", curses.color_pair(color) | attr)
    stdscr.addstr(y+1, x, "║", curses.color_pair(color) | attr)
    stdscr.addstr(y+1, x+1, "█"*fill, curses.color_pair(color) | attr)
    stdscr.addstr(y+1, x+1+fill, " "*(16-fill), curses.color_pair(color) | attr)
    stdscr.addstr(y+1, x+17, "║", curses.color_pair(color) | attr)
    stdscr.addstr(y+2, x, "╚════════════════╝", curses.color_pair(color) | attr)
    stdscr.addstr(y+3, x-1, f"{voltage:.2f}V".center(18), curses.color_pair(color))

def draw_graph(stdscr, y, x, history):
    h = 10
    w = 40
    max_v = config_values['General']['HighVoltageThresholdPerBattery']
    min_v = config_values['General']['LowVoltageThresholdPerBattery']
    
    stdscr.addstr(y, x, "╔" + "═"*(w-2) + "╗", curses.color_pair(COLOR_GRAPH_BG))
    for i in range(1, h-1):
        stdscr.addstr(y+i, x, "║" + " "*(w-2) + "║", curses.color_pair(COLOR_GRAPH_BG))
    stdscr.addstr(y+h-1, x, "╚" + "═"*(w-2) + "╯", curses.color_pair(COLOR_GRAPH_BG))
    
    for idx, v in enumerate(history):
        if idx >= w-2: break
        y_pos = y + h-2 - int((v-min_v)/(max_v-min_v)*(h-2))
        if y <= y_pos < y+h-1:
            stdscr.addch(y_pos, x+1+idx, '•', curses.color_pair(COLOR_NORMAL))

def draw_status(stdscr, y, x, voltages, progress):
    status = [
        f"Total: {sum(voltages):.2f}V",
        f"Balance: {'ACTIVE' if balancing_active else 'IDLE'}",
        f"Progress: [{'█'*int(20*progress)}{' '*(20-int(20*progress))}] {int(progress*100)}%" 
        if balancing_active else 
        f"Last: {time.strftime('%H:%M:%S', time.localtime(last_balance_time))}"
    ]
    
    stdscr.addstr(y, x, "╭─ STATUS ────────────────────╮", curses.color_pair(COLOR_STATUS))
    for i, line in enumerate(status):
        stdscr.addstr(y+1+i, x, f"│ {line.ljust(26)} │", curses.color_pair(COLOR_STATUS))
    stdscr.addstr(y+len(status)+1, x, "╰────────────────────────────╯", curses.color_pair(COLOR_STATUS))

def main_program(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(COLOR_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(COLOR_NORMAL, curses.COLOR_GREEN, -1)
    curses.init_pair(COLOR_WARNING, curses.COLOR_YELLOW, -1)
    curses.init_pair(COLOR_CRITICAL, curses.COLOR_RED, -1)
    curses.init_pair(COLOR_STATUS, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(COLOR_GRAPH_BG, curses.COLOR_BLACK, curses.COLOR_WHITE)

    setup_hardware()
    batt_count = config_values['General']['NumberOfBatteries']

    while True:
        stdscr.erase()
        draw_header(stdscr)
        
        # Read voltages
        voltages = []
        for i in range(1, batt_count+1):
            v, _, _ = read_voltage_with_retry(i)
            voltages.append(v if v else 0.0)
            voltage_history[i].append(v)
        
        # Draw batteries
        positions = {}
        for idx, v in enumerate(voltages):
            x = 4 + idx * 22
            positions[idx+1] = x
            draw_battery(stdscr, 4, x, v, balancing_active and (idx+1 == high_bat))
            draw_graph(stdscr, 9, x-2, list(voltage_history[idx+1]))
        
        # Status panel
        draw_status(stdscr, 4, curses.COLS-32, voltages, balance_progress)
        
        # Balance logic
        max_v = max(voltages)
        min_v = min(voltages)
        high_bat = voltages.index(max_v) + 1
        low_bat = voltages.index(min_v) + 1
        
        if (max_v - min_v) > config_values['General']['VoltageDifferenceToBalance']:
            if not balancing_active and (time.time() - last_balance_time) > config_values['General']['BalanceRestPeriodSeconds']:
                balance_battery_voltages(stdscr, high_bat, low_bat)
                last_balance_time = time.time()
        
        check_for_voltage_issues(voltages)
        stdscr.noutrefresh()
        curses.doupdate()
        time.sleep(config_values['General']['SleepTimeBetweenChecks'])

if __name__ == '__main__':
    try:
        setup_hardware()
        curses.wrapper(main_program)
    except Exception as e:
        logging.critical(f"Fatal error: {e}")
    finally:
        GPIO.cleanup()
        logging.info("Clean exit")