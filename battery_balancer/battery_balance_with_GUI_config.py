import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses
import logging
import sys

# Setup logging for detailed debug information
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='battery_balancer.log',
                    filemode='w')

# Configuration reading and parsing
def load_config():
    """Read and parse configuration file."""
    config = configparser.ConfigParser()
    if not config.read('config.ini'):
        logging.error("Failed to read config.ini")
        sys.exit(1)
    
    def parse_hex_or_int(value):
        """Convert string to int, handling hex if prefixed with '0x'."""
        if value.startswith('0x'):
            return int(value, 16)
        return int(value)

    return {
        'General': {
            'NUM_CELLS': config.getint('General', 'NUM_CELLS'),
            'BALANCE_THRESHOLD': config.getfloat('General', 'BALANCE_THRESHOLD'),
            'BALANCE_TIME': config.getint('General', 'BALANCE_TIME'),
            'SLEEP_TIME': config.getint('General', 'SLEEP_TIME'),
            'BALANCE_REST_PERIOD': config.getint('General', 'BALANCE_REST_PERIOD'),
            'ALARM_VOLTAGE_THRESHOLD': config.getfloat('General', 'ALARM_VOLTAGE_THRESHOLD'),
            'I2C_BUS': config.getint('General', 'I2C_BUS'),
            'NUM_SAMPLES': config.getint('General', 'NUM_SAMPLES'),
            'MAX_RETRIES': config.getint('General', 'MAX_RETRIES')
        },
        'I2C': {
            'PAHUB2_ADDR': parse_hex_or_int(config.get('I2C', 'PAHUB2_ADDR')),
            'VMETER_ADDR': parse_hex_or_int(config.get('I2C', 'VMETER_ADDR')),
            'RELAY_ADDR': parse_hex_or_int(config.get('I2C', 'RELAY_ADDR'))
        },
        'GPIO': {
            'DC_DC_RELAY_PIN': config.getint('GPIO', 'DC_DC_RELAY_PIN'),
            'ALARM_RELAY_PIN': config.getint('GPIO', 'ALARM_RELAY_PIN')
        },
        'Email': {
            'SMTP_SERVER': config.get('Email', 'SMTP_SERVER'),
            'SMTP_PORT': config.getint('Email', 'SMTP_PORT'),
            'SENDER_EMAIL': config.get('Email', 'SENDER_EMAIL'),
            'RECIPIENT_EMAIL': config.get('Email', 'RECIPIENT_EMAIL')
        },
        'ADS1115': {
            'REG_CONFIG': parse_hex_or_int(config.get('ADS1115', 'REG_CONFIG')),
            'REG_CONVERSION': parse_hex_or_int(config.get('ADS1115', 'REG_CONVERSION')),
            'CONFIG_CONTINUOUS': parse_hex_or_int(config.get('ADS1115', 'CONFIG_CONTINUOUS')),
            'CONFIG_RATE_8': parse_hex_or_int(config.get('ADS1115', 'CONFIG_RATE_8')),
            'CONFIG_PGA_6144': parse_hex_or_int(config.get('ADS1115', 'CONFIG_PGA_6144'))
        }
    }

# Global config object
config = load_config()

# Initialize I2C bus and GPIO
try:
    bus = smbus.SMBus(config['General']['I2C_BUS'])  # Use bus specified in config
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config['GPIO']['DC_DC_RELAY_PIN'], GPIO.OUT)
    GPIO.setup(config['GPIO']['ALARM_RELAY_PIN'], GPIO.OUT, initial=GPIO.LOW)
except Exception as e:
    logging.error(f"Error initializing I2C or GPIO: {e}")
    sys.exit(1)

# Function to select a channel on PaHUB2
def select_channel(channel):
    """Select the specified channel on the PaHUB2."""
    try:
        bus.write_byte(config['I2C']['PAHUB2_ADDR'], 1 << channel)
        logging.debug(f"Selected channel {channel}")
    except IOError as e:
        logging.error(f"I2C Error while selecting channel {channel}: {e}")

# Function to configure ADS1115 for VMeter reading
def config_vmeter():
    """Configure the VMeter for continuous conversion."""
    config_value = config['ADS1115']['CONFIG_CONTINUOUS'] | config['ADS1115']['CONFIG_RATE_8'] | config['ADS1115']['CONFIG_PGA_6144']
    try:
        bus.write_word_data(config['I2C']['VMETER_ADDR'], config['ADS1115']['REG_CONFIG'], config_value)
        logging.debug("Configured VMeter")
    except IOError as e:
        logging.error(f"Failed to configure VMeter: {e}")

# Function to read voltage with retry and averaging
def read_voltage_with_retry(cell_id):
    """
    Read voltage from a cell with retry mechanism and averaging for stability.
    
    :param cell_id: ID of the cell to read from
    :return: Tuple of calculated voltage and raw ADC value
    """
    max_retries = config['General']['MAX_RETRIES']
    num_samples = config['General']['NUM_SAMPLES']
    
    for attempt in range(max_retries):
        try:
            vmeter_channel = cell_id % 3  # Each VMeter is on channels 0, 1, 2 sequentially
            select_channel(vmeter_channel)
            config_vmeter()
            
            time.sleep(0.2)  # Wait for conversion stability
            adc_samples = []
            for _ in range(num_samples):
                time.sleep(0.01)  # Small delay between samples
                adc_samples.append(bus.read_word_data(config['I2C']['VMETER_ADDR'], config['ADS1115']['REG_CONVERSION']) & 0xFFFF)
            adc_raw = sum(adc_samples) // num_samples  # Average the samples
            
            scaling_factor = 6.144 / 32767  # For PGA_6144
            voltage = adc_raw * scaling_factor
            
            logging.debug(f"Read voltage from Cell {cell_id + 1}: {voltage:.2f}V on attempt {attempt+1}")
            logging.info(f"Raw ADC value for Cell {cell_id + 1}: {adc_raw}")
            return voltage, adc_raw
        except IOError as e:
            logging.warning(f"Voltage reading retry {attempt + 1} for Cell {cell_id + 1}: {e}")
            if attempt + 1 < max_retries:
                time.sleep(1)  # Wait before retrying
    logging.error(f"Failed to read voltage for Cell {cell_id + 1} after {max_retries} attempts")
    return None, None

# Function to control the relays and DC-DC converter
def set_relay(high_cell, low_cell):
    """Set the relay configuration for battery balancing."""
    try:
        select_channel(3)  # Switch to the channel where relays are connected
        relay_state = 0
        if high_cell == low_cell or high_cell < 0 or low_cell < 0:
            relay_state = 0  # All relays off
        else:
            # Relay mapping logic (implement based on your setup)
            pass
        
        bus.write_byte_data(config['I2C']['RELAY_ADDR'], 0x10, relay_state)
        logging.info(f"Set relay state for balancing from Cell {high_cell + 1} to Cell {low_cell + 1}")
    except IOError as e:
        logging.error(f"Relay setting error: {e}")

# Function to control DC-DC converter via GPIO
def control_dc_dc(enable):
    """Enable or disable the DC-DC converter."""
    try:
        GPIO.output(config['GPIO']['DC_DC_RELAY_PIN'], GPIO.HIGH if enable else GPIO.LOW)
        logging.info(f"DC-DC Converter {'enabled' if enable else 'disabled'}")
    except GPIO.GPIOError as e:
        logging.error(f"Error controlling DC-DC converter: {e}")

# Function to send an email alarm
def send_email_alarm():
    """Send an email alert if voltage exceeds threshold."""
    msg = MIMEText(f'Warning: Cell voltage exceeded {config["General"]["ALARM_VOLTAGE_THRESHOLD"]}V!')
    msg['Subject'] = 'Battery Alarm'
    msg['From'] = config['Email']['SENDER_EMAIL']
    msg['To'] = config['Email']['RECIPIENT_EMAIL']

    try:
        with smtplib.SMTP(config['Email']['SMTP_SERVER'], config['Email']['SMTP_PORT']) as server:
            server.send_message(msg)  
        logging.info("Email alarm sent successfully")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# Function to handle overvoltage alarm
def check_overvoltage_alarm(voltages):
    """Check if any cell voltage exceeds the alarm threshold."""
    for i, voltage in enumerate(voltages):
        if voltage and voltage > config['General']['ALARM_VOLTAGE_THRESHOLD']:
            logging.warning(f"ALARM: Cell {i+1} voltage is {voltage:.2f}V, exceeding threshold!")
            try:
                GPIO.output(config['GPIO']['ALARM_RELAY_PIN'], GPIO.HIGH)
                send_email_alarm()
            except Exception as e:
                logging.error(f"Error during overvoltage alarm: {e}")
            return True
    try:
        GPIO.output(config['GPIO']['ALARM_RELAY_PIN'], GPIO.LOW)
    except Exception as e:
        logging.error(f"Error resetting alarm relay: {e}")
    return False

# Function to balance cells
def balance_cells(stdscr):
    """Perform battery cell balancing based on voltage readings."""
    voltages = []
    adc_raws = []
    for cell in range(config['General']['NUM_CELLS']):
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

    time.sleep(2)  # Delay before balancing for stability

    max_voltage = max(voltages)
    min_voltage = min(voltages)
    high_cell = voltages.index(max_voltage)
    low_cell = voltages.index(min_voltage)

    if max_voltage - min_voltage > config['General']['BALANCE_THRESHOLD']:
        stdscr.addstr(10, 0, f"Balancing Cell {high_cell+1} to Cell {low_cell+1}...")
        set_relay(high_cell, low_cell)
        control_dc_dc(False)
        time.sleep(0.1)
        control_dc_dc(True)
        time.sleep(config['General']['BALANCE_TIME'])
        control_dc_dc(False)
        stdscr.addstr(10, 0, "Balancing completed. Waiting for stabilization...")
        time.sleep(config['General']['BALANCE_REST_PERIOD'])
        stdscr.addstr(10, 0, "Stabilization complete.                     ")
        logging.info(f"Balancing completed: Cell {high_cell+1} to Cell {low_cell+1}")
    else:
        stdscr.addstr(10, 0, "No balancing needed.                      ")
        logging.info("No balancing action taken; voltages within threshold")

# Main function using curses for TUI
def main(stdscr):
    """Main loop for the Battery Balancer TUI."""
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
            for i in range(config['General']['NUM_CELLS']):
                voltage, adc_raw = read_voltage_with_retry(i)
                if voltage is None:
                    stdscr.addstr(i + 2, 0, f"Cell {i+1}: Error reading voltage", curses.color_pair(1))
                else:
                    voltage_color = curses.color_pair(2) if voltage < config['General']['BALANCE_THRESHOLD'] else curses.color_pair(3)
                    stdscr.addstr(i + 2, 0, f"Cell {i+1}: {voltage:.2f}V (Raw: {adc_raw})", voltage_color)

            balance_cells(stdscr)

            time.sleep(config['General']['SLEEP_TIME'])
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