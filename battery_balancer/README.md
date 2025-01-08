  Battery Balancer Script Documentation body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; } h1, h2, h3 { color: #2c3e50; } code { background-color: #f4f4f4; padding: 2px 4px; border: 1px solid #ddd; border-radius: 4px; } pre { background-color: #f4f4f4; padding: 10px; border: 1px solid #ddd; border-radius: 4px; overflow: auto; } ul, ol { padding-left: 20px; }

Battery Balancer Script Documentation
=====================================

Overview
--------

This script provides an automated system for balancing lithium-ion battery cells, using a Raspberry Pi with M5 VMeter units for voltage monitoring, 3PDT and 2PDT relays for connection management, and an isolated DC-DC converter for safe balancing. It includes an alarm system that activates if cell voltage exceeds a set threshold, notifying via email and a physical alarm.

Features
--------

*   **Real-time Voltage Monitoring**: Uses M5 VMeter units to continuously monitor cell voltages over an I2C bus.
*   **Automatic Balancing**: Initiates when the voltage difference between cells exceeds a user-defined threshold.
*   **Command-Line Interface**: Provides status updates via a text-based user interface (`curses`).
*   **Configurable Settings**: Parameters like balancing thresholds and alarm settings are managed through an external configuration file.
*   **Safety Mechanisms**: Includes control over the DC-DC converter and an alarm system for overvoltage protection.
*   **Overvoltage Alarm**: Triggers when any cell voltage exceeds a threshold, activating an email alert and a physical relay.

Prerequisites
-------------

### Hardware

*   Raspberry Pi with I2C enabled
*   M5 VMeter Units (3 units for 3 cells)
*   2 x 3PDT Relays (Relays 1 & 4)
*   2 x 2PDT Relays (Relays 2 & 3)
*   Isolated DC-DC Converter
*   PaHUB2 I2C Expandable Hub (I2C address: 0x70)
*   Alarm Relay connected to GPIO

### Software

*   Python 3.x
*   `smbus` for I2C communication
*   `curses` for TUI
*   `configparser` for configuration management
*   `RPi.GPIO` for GPIO control
*   Local SMTP server (`sendmail` or similar) for email notifications

Installation
------------

### Hardware Setup

*   Connect VMeter units to measure each battery cell's voltage.
*   Wire the relays to manage cell connections, with the DC-DC converter placed between the 2PDT relays.
*   Connect the alarm relay to a GPIO pin for physical alert signaling.
*   Ensure your SMTP server is configured for local email delivery.

### Software Setup

1.  **Enable I2C on Raspberry Pi**:
    
        sudo raspi-config
    
    Navigate to `Interface Options` and enable I2C.
2.  **Install Required Libraries**:
    
        sudo apt-get update
        sudo apt-get install python3 python3-smbus python3-curses python3-rpi.gpio
    
3.  **Install Local SMTP Server** (if not already set up):
    
        sudo apt-get install sendmail
    
    Configure `sendmail` for local email delivery.
4.  **Clone or Download This Repository**:
    
        git clone <your-repo-url>
        cd battery-balancer
    
5.  **Edit Configuration**:
    *   Modify `config.ini` to match your hardware setup and email configuration.

Running the Script
------------------

To run the balancer script:

    python3 balancer.py

### User Interaction

*   Observe cell voltages and balancing status through the TUI.
*   Type 'quit' to exit the program.

Configuration File (`config.ini`)
---------------------------------

    [General]
    NUM_CELLS = 3
    BALANCE_THRESHOLD = 0.05
    BALANCE_TIME = 60
    SLEEP_TIME = 5
    BALANCE_REST_PERIOD = 300
    ALARM_VOLTAGE_THRESHOLD = 21.25
    
    [I2C]
    PAHUB2_ADDR = 0x70
    VMETER_ADDR = 0x49
    RELAY_ADDR = 0x26
    
    [GPIO]
    DC_DC_RELAY_PIN = 18
    ALARM_RELAY_PIN = 23
    
    [Email]
    SMTP_SERVER = localhost
    SMTP_PORT = 25
    SENDER_EMAIL = your_local_user@localhost
    SENDER_PASSWORD = 
    RECIPIENT_EMAIL = your_local_user@localhost

*   **NUM\_CELLS**: Number of cells to balance.
*   **BALANCE\_THRESHOLD**: Voltage difference to trigger balancing.
*   **BALANCE\_TIME**: Duration of each balancing action.
*   **SLEEP\_TIME**: Time between voltage checks.
*   **BALANCE\_REST\_PERIOD**: Time for natural stabilization post-balance.
*   **ALARM\_VOLTAGE\_THRESHOLD**: Voltage to trigger the overvoltage alarm.
*   **I2C Addresses**: For hardware components.
*   **GPIO Pins**: For controlling the DC-DC converter and alarm relay.
*   **Email Settings**: For local SMTP server configuration.

Safety Considerations
---------------------

*   **DC-DC Converter**: Ensured to be off during relay switching to prevent short circuits.
*   **Voltage Monitoring**: Continuous to avoid over/under-charging scenarios.
*   **Overvoltage Protection**: Alarm system for safety.

Known Issues
------------

*   Single-threaded operation might cause GUI lag during balancing.
*   This script assumes a very specific hardware setup; changes might require code modification.

Contributing
------------

*   Pull requests are welcome for enhancements or bug fixes.
*   For significant changes, please open an issue to discuss first.

License
-------

\[Insert your license here, e.g., MIT, GPL, etc.\]

Acknowledgments
---------------

*   M5Stack for VMeter and Relay modules.
*   The open-source community for Python libraries used.