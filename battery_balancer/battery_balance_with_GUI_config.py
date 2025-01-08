import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses
import logging
import sys

# Setup logging
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='battery_balancer.log',
                    filemode='w')  # 'w' mode ensures the log file is overwritten each run for cleaner debugging

# Read configuration
config = configparser.ConfigParser()
if not config.read('config.ini'):
    logging.error("Failed to read config.ini")
    sys.exit(1)

# Extract configuration values
try:
    NUM_CELLS = config.getint('General', 'NUM_CELLS')
    BALANCE_THRESHOLD = config.getfloat('General', 'BALANCE_THRESHOLD')
    BALANCE_TIME = config.getint('General', 'BALANCE_TIME')
    SLEEP_TIME = config.getint('General', 'SLEEP_TIME')
    BALANCE_REST_PERIOD = config.getint('General', 'BALANCE_REST_PERIOD')
    ALARM_VOLTAGE_THRESHOLD = config.getfloat('General', 'ALARM_VOLTAGE_THRESHOLD')

    PAHUB2_ADDR = int(config.get('I2C', 'PAHUB2_ADDR'), 16)
    VMETER_ADDR = int(config.get('I2C', 'VMETER_ADDR'), 16)
    RELAY_ADDR = int(config.get('I2C', 'RELAY_ADDR'), 16)

    DC_DC_RELAY_PIN = config.getint('GPIO', 'DC_DC_RELAY_PIN')
    ALARM_RELAY_PIN = config.getint('GPIO', 'ALARM_RELAY_PIN')

    SMTP_SERVER = config.get('Email', 'SMTP_SERVER')
    SMTP_PORT = config.getint('Email', 'SMTP_PORT')
    SENDER_EMAIL = config.get('Email', 'SENDER_EMAIL')
    RECIPIENT_EMAIL = config.get('Email', 'RECIPIENT_EMAIL')
except (configparser.NoOptionError, ValueError) as e:
    logging.error(f"Configuration error: {e}")
    sys.exit(1)

# Initialize I2C bus and GPIO
try:
    bus = smbus.SMBus(1)  # Use bus 1 for newer Raspberry Pi models
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(DC_DC_RELAY_PIN, GPIO.OUT)
    GPIO.setup(ALARM_RELAY_PIN, GPIO.OUT, initial=GPIO.LOW)  # Alarm relay starts off
except Exception as e:
    logging.error(f"Error initializing I2C or GPIO: {e}")
    sys.exit(1)

# ADS1115 configuration settings
REG_CONFIG = 0x01
REG_CONVERSION = 0x00
CONFIG_CONTINUOUS = 0x0000  # Continuous conversion mode
CONFIG_RATE_8 = 0x0000
CONFIG_PGA_2048 = 0x0400  # Gain setting for 32V max input

# Function to select a channel on PaHUB2
def select_channel(channel):
    try:
        bus.write_byte(PAHUB2_ADDR, 1 << channel)
        logging.debug(f"Selected channel {channel}")
    except IOError as e:
        logging.error(f"I2C Error while selecting channel {channel}: {e}")

# Function to configure ADS1115 for VMeter reading
def config_vmeter():
    config = CONFIG_CONTINUOUS | CONFIG_RATE_8 | CONFIG_PGA_2048
    try:
        bus.write_word_data(VMETER_ADDR, REG_CONFIG, config)
        logging.debug("Configured VMeter")
    except IOError as e:
        logging.error(f"Failed to configure VMeter: {e}")

# Function to read voltage with retry
def read_voltage_with_retry(cell_id, max_retries=3):
    for attempt in range(max_retries):
        try:
            vmeter_channel = cell_id % 3  # Each VMeter is on channels 0, 1, 2 sequentially
            select_channel(vmeter_channel)
            config_vmeter()
            
            bus.write_byte(VMETER_ADDR, 0x01)  # Start conversion on channel
            time.sleep(0.15)  # Wait for conversion
            adc_raw = bus.read_word_data(VMETER_ADDR, REG_CONVERSION) & 0xFFFF
            
            scaling_factor = 5.0 / 22270 * 32767  # Example scaling, adjust as needed
            voltage = (adc_raw * scaling_factor) / 32767.0
            logging.debug(f"Read voltage from Cell {cell_id + 1}: {voltage:.2f}V on attempt {attempt+1}")
            logging.debug(f"Raw ADC value for Cell {cell_id + 1}: {adc_raw}")
            
            # Also print raw ADC value to console for immediate visibility
            print(f"Raw ADC for Cell {cell_id + 1}: {adc_raw}")
            
            return voltage, adc_raw  # Return both voltage and raw ADC value
        except IOError as e:
            logging.warning(f"Voltage reading retry {attempt + 1} for Cell {cell_id + 1}: {e}")
            time.sleep(1)  # Wait before retrying
    logging.error(f"Failed to read voltage for Cell {cell_id + 1} after {max_retries} attempts")
    return None, None

# Function to control the relays and DC-DC converter
def set_relay(high_cell, low_cell):
    try:
        select_channel(3)  # Switch to the channel where the relays are connected
        relay_state = 0
        if high_cell == low_cell or high_cell < 0 or low_cell < 0:
            relay_state = 0  # All relays off
        else:
            # Relay mapping logic
            if high_cell == 2 and low_cell == 1:  # 2->1
                relay_state |= (1 << 0) | (1 << 2) | (1 << 4)  # Relay 1 Pole 3, Relay 2 Pole 1, Relay 3 Pole 1
            elif high_cell == 3 and low_cell == 1:  # 3->1
                relay_state |= (1 << 1) | (1 << 3) | (1 << 4)  # Relay 4 Pole 2, Relay 2 Pole 2, Relay 3 Pole 1
            elif high_cell == 1 and low_cell == 2:  # 1->2
                relay_state |= (1 << 0) | (1 << 4) | (1 << 2)  # Relay 1 Pole 1, Relay 3 Pole 1, Relay 2 Pole 1
            elif high_cell == 1 and low_cell == 3:  # 1->3
                relay_state |= (1 << 1) | (1 << 5) | (1 << 3)  # Relay 1 Pole 2, Relay 3 Pole 2, Relay 2 Pole 2
            elif high_cell == 2 and low_cell == 2:  # 2->2
                relay_state |= (1 << 0) | (1 << 5)  # Relay 1 Pole 3, Relay 3 Pole 2
            elif high_cell == 2 and low_cell == 3:  # 2->3
                relay_state |= (1 << 2) | (1 << 3) | (1 << 5)  # Relay 4 Pole 1, Relay 2 Pole 2, Relay 3 Pole 2
            elif high_cell == 3 and low_cell == 2:  # 3->2
                relay_state |= (1 << 3) | (1 << 5) | (1 << 2)  # Relay 4 Pole 2, Relay 3 Pole 2, Relay 2 Pole 1
            elif high_cell == 3 and low_cell == 3:  # 3->3
                relay_state |= (1 << 1) | (1 << 5)  # Relay 4 Pole 2, Relay 3 Pole 2

        bus.write_byte_data(RELAY_ADDR, 0x10, relay_state)
        logging.info(f"Set relay state for balancing from Cell {high_cell + 1} to Cell {low_cell + 1}")
    except IOError as e:
        logging.error(f"Relay setting error: {e}")

# Function to control DC-DC converter via GPIO
def control_dc_dc(enable):
    try:
        GPIO.output(DC_DC_RELAY_PIN, GPIO.HIGH if enable else GPIO.LOW)
        logging.info(f"DC-DC Converter {'enabled' if enable else 'disabled'}")
    except GPIO.GPIOError as e:
        logging.error(f"Error controlling DC-DC converter: {e}")

# Function to send an email alarm
def send_email_alarm():
    msg = MIMEText(f'Warning: Cell voltage exceeded {ALARM_VOLTAGE_THRESHOLD}V!')
    msg['Subject'] = 'Battery Alarm'
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.send_message(msg)  
        logging.info("Email alarm sent successfully")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# New function to handle overvoltage alarm
def check_overvoltage_alarm(voltages):
    for i, voltage in enumerate(voltages):
        if voltage and voltage > ALARM_VOLTAGE_THRESHOLD:
            logging.warning(f"ALARM: Cell {i+1} voltage is {voltage:.2f}V, exceeding threshold!")
            try:
                GPIO.output(ALARM_RELAY_PIN, GPIO.HIGH)
                send_email_alarm()
            except Exception as e:
                logging.error(f"Error during overvoltage alarm: {e}")
            return True
    try:
        GPIO.output(ALARM_RELAY_PIN, GPIO.LOW)
    except Exception as e:
        logging.error(f"Error resetting alarm relay: {e}")
    return False

def balance_cells(stdscr):
    voltages = []
    adc_raws = []  # Collect raw ADC values
    for cell in range(NUM_CELLS):
        voltage, adc_raw = read_voltage_with_retry(cell)
        if voltage is None:
            stdscr.addstr(cell + 2, 0, f"Cell {cell+1}: Error reading voltage", curses.color_pair(1))
        else:
            voltages.append(voltage)
            adc_raws.append(adc_raw)
    
    if not voltages:
        stdscr.addstr(10, 0, "No valid voltage readings. Check hardware.")
        return

    if check_overvoltage_alarm(voltages):
        return

    time.sleep(2)  # Delay before balancing
    logging.info("Delay completed before balancing")

    max_voltage = max(voltages)
    min_voltage = min(voltages)
    high_cell = voltages.index(max_voltage)
    low_cell = voltages.index(min_voltage)

    if max_voltage - min_voltage > BALANCE_THRESHOLD:
        stdscr.addstr(10, 0, f"Balancing Cell {high_cell+1} to Cell {low_cell+1}...")
        set_relay(high_cell, low_cell)
        control_dc_dc(False)
        time.sleep(0.1)
        control_dc_dc(True)
        time.sleep(BALANCE_TIME)
        control_dc_dc(False)
        stdscr.addstr(10, 0, "Balancing completed. Waiting for stabilization...")
        time.sleep(BALANCE_REST_PERIOD)
        stdscr.addstr(10, 0, "Stabilization complete.                     ")
        logging.info(f"Balancing completed: Cell {high_cell+1} to Cell {low_cell+1}")
    else:
        stdscr.addstr(10, 0, "No balancing needed.                      ")
        logging.info("No balancing action taken; voltages within threshold")

def main(stdscr):
    try:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.clear()
        curses.start_color()
        curses.use_default_colors()
        for i in range(1, curses.COLORS):
            curses.init_pair(i, i, -1)

        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, "Battery Balancer TUI", curses.color_pair(1))
            stdscr.hline(1, 0, curses.ACS_HLINE, curses.COLS - 1)
            for i in range(NUM_CELLS):
                voltage, adc_raw = read_voltage_with_retry(i)
                if voltage is None:
                    stdscr.addstr(i + 2, 0, f"Cell {i+1}: Error reading voltage", curses.color_pair(1))
                else:
                    voltage_color = curses.color_pair(2) if voltage < BALANCE_THRESHOLD else curses.color_pair(3)
                    # Display both voltage and raw ADC value
                    stdscr.addstr(i + 2, 0, f"Cell {i+1}: {voltage:.2f}V (Raw: {adc_raw})", voltage_color)

            balance_cells(stdscr)

            time.sleep(SLEEP_TIME)
            stdscr.refresh()  # Ensure screen refreshes
    except Exception as e:
        logging.error(f"Error in main loop: {e}")
        stdscr.addstr(12, 0, f"Error: {e}", curses.color_pair(1))

if __name__ == '__main__':
    try:
        logging.info("Starting the Battery Balancer script")
        curses.wrapper(main)
    except Exception as e:
        logging.error(f"An unexpected error occurred in script execution: {e}")
    finally:
        GPIO.cleanup()
        logging.info("Program terminated. GPIO cleanup completed.")