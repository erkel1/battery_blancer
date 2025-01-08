import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses

# Read configuration
config = configparser.ConfigParser()
config.read('config.ini')

# Extract configuration values
NUM_CELLS = config.getint('General', 'NUM_CELLS')
BALANCE_THRESHOLD = config.getfloat('General', 'BALANCE_THRESHOLD')
BALANCE_TIME = config.getint('General', 'BALANCE_TIME')
SLEEP_TIME = config.getint('General', 'SLEEP_TIME')
BALANCE_REST_PERIOD = config.getint('General', 'BALANCE_REST_PERIOD')
ALARM_VOLTAGE_THRESHOLD = config.getfloat('General', 'ALARM_VOLTAGE_THRESHOLD')

# I2C addresses for M5 Units
PAHUB2_ADDR = int(config.get('I2C', 'PAHUB2_ADDR'), 16)
VMETER_ADDR = int(config.get('I2C', 'VMETER_ADDR'), 16)  # All VMeter units have the same address
RELAY_ADDR = int(config.get('I2C', 'RELAY_ADDR'), 16)

# GPIO pins
DC_DC_RELAY_PIN = config.getint('GPIO', 'DC_DC_RELAY_PIN')
ALARM_RELAY_PIN = config.getint('GPIO', 'ALARM_RELAY_PIN')

# Email configuration
SMTP_SERVER = config.get('Email', 'SMTP_SERVER')
SMTP_PORT = config.getint('Email', 'SMTP_PORT')
SENDER_EMAIL = config.get('Email', 'SENDER_EMAIL')
SENDER_PASSWORD = config.get('Email', 'SENDER_PASSWORD')  # Not used in this case
RECIPIENT_EMAIL = config.get('Email', 'RECIPIENT_EMAIL')

# Initialize I2C bus and GPIO
bus = smbus.SMBus(1)  # Use bus 1 for newer Raspberry Pi models
GPIO.setmode(GPIO.BCM)
GPIO.setup(DC_DC_RELAY_PIN, GPIO.OUT)
GPIO.setup(ALARM_RELAY_PIN, GPIO.OUT, initial=GPIO.LOW)  # Alarm relay starts off

# ADS1115 configuration settings
REG_CONFIG = 0x01
REG_CONVERSION = 0x00
CONFIG_SINGLE_SHOT = 0x8000
CONFIG_RATE_8 = 0x0000
CONFIG_PGA_2048 = 0x0400  # Gain setting for 32V max input

# Function to select a channel on PaHUB2
def select_channel(channel):
    bus.write_byte(PAHUB2_ADDR, 1 << channel)  # Enable the specific channel

# Function to configure ADS1115 for VMeter reading
def config_vmeter():
    config = CONFIG_SINGLE_SHOT | CONFIG_RATE_8 | CONFIG_PGA_2048
    bus.write_word_data(VMETER_ADDR, REG_CONFIG, config)

# Function to read voltage from a specific cell using one of the M5 VMeter units
def read_voltage(cell_id):
    # Determine which channel the VMeter is on
    vmeter_channel = cell_id % 3  # Each VMeter is on channels 0, 1, 2 sequentially
    select_channel(vmeter_channel)
    
    config_vmeter()  # Configure VMeter before reading
    
    bus.write_byte(VMETER_ADDR, 0x01)  # Start conversion on channel
    time.sleep(0.15)  # Wait for conversion
    adc_raw = bus.read_word_data(VMETER_ADDR, REG_CONVERSION) & 0xFFFF
    
    # Adjust the scaling factor based on the actual reading for your setup
    scaling_factor = 5.0 / 22270 * 32767  # Example scaling, adjust as needed
    voltage = (adc_raw * scaling_factor) / 32767.0
    
    return voltage

# Function to control the relays and DC-DC converter
def set_relay(high_cell, low_cell):
    select_channel(3)  # Switch to the channel where the relays are connected
    
    relay_state = 0
    
    if high_cell == low_cell or high_cell < 0 or low_cell < 0:
        relay_state = 0  # All relays off
    else:
        # Mapping:
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

    # Write to the control register for relays
    bus.write_byte_data(RELAY_ADDR, 0x10, relay_state)  # Assuming 0x10 is the correct control register

# Function to control DC-DC converter via GPIO
def control_dc_dc(enable):
    GPIO.output(DC_DC_RELAY_PIN, GPIO.HIGH if enable else GPIO.LOW)

# Function to send an email alarm
def send_email_alarm():
    msg = MIMEText(f'Warning: Cell voltage exceeded {ALARM_VOLTAGE_THRESHOLD}V!')
    msg['Subject'] = 'Battery Alarm'
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.send_message(msg)

# New function to handle overvoltage alarm
def check_overvoltage_alarm(voltages):
    for i, voltage in enumerate(voltages):
        if voltage > ALARM_VOLTAGE_THRESHOLD:
            print(f"ALARM: Cell {i+1} voltage is {voltage:.2f}V, exceeding threshold!")
            GPIO.output(ALARM_RELAY_PIN, GPIO.HIGH)  # Trigger alarm relay
            send_email_alarm()
            return True
    GPIO.output(ALARM_RELAY_PIN, GPIO.LOW)  # Reset alarm relay if no overvoltage
    return False

def balance_cells(stdscr):
    voltages = []
    for cell in range(NUM_CELLS):
        voltages.append(read_voltage(cell))
    
    if check_overvoltage_alarm(voltages):
        return  # Skip balancing if an alarm has been triggered

    max_voltage = max(voltages)
    min_voltage = min(voltages)
    high_cell = voltages.index(max_voltage)
    low_cell = voltages.index(min_voltage)

    if max_voltage - min_voltage > BALANCE_THRESHOLD:
        stdscr.addstr(10, 0, f"Balancing Cell {high_cell+1} to Cell {low_cell+1}...")
        set_relay(high_cell, low_cell)
        control_dc_dc(False)  # Ensure DC-DC is off before switching
        time.sleep(0.1)  # Brief delay to ensure no voltage is present
        control_dc_dc(True)  # Turn on DC-DC converter
        time.sleep(BALANCE_TIME)
        control_dc_dc(False)  # Turn off DC-DC converter
        stdscr.addstr(10, 0, "Balancing completed. Waiting for stabilization...")
        time.sleep(BALANCE_REST_PERIOD)  # Wait for natural balancing
        stdscr.addstr(10, 0, "Stabilization complete.                     ")
    else:
        stdscr.addstr(10, 0, "No balancing needed.                      ")

def main(stdscr):
    # Setup curses
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    stdscr.clear()
    curses.start_color()
    curses.use_default_colors()
    for i in range(1, curses.COLORS):
        curses.init_pair(i, i, -1)

    # Main loop
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Battery Balancer TUI", curses.color_pair(1))
        stdscr.hline(1, 0, curses.ACS_HLINE, curses.COLS - 1)
        for i in range(NUM_CELLS):
            voltage = read_voltage(i)
            voltage_color = curses.color_pair(2) if voltage < BALANCE_THRESHOLD else curses.color_pair(3)
            stdscr.addstr(i + 2, 0, f"Cell {i+1}: {voltage:.2f}V", voltage_color)

        # Perform balancing in the main loop
        balance_cells(stdscr)

        # User input area is removed, so no 'quit' command

        # Wait before refreshing to not overload the system
        time.sleep(SLEEP_TIME)

if __name__ == '__main__':
    try:
        curses.wrapper(main)
    finally:
        GPIO.cleanup()  # Clean up GPIO on exit