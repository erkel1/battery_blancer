  Battery Balancer Script README body { font-family: Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; } h1, h2, h3 { color: #333; } code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; font-family: 'Courier New', Courier, monospace; } pre { background-color: #f4f4f4; padding: 10px; border-radius: 4px; overflow: auto; } ul { list-style-type: square; }

Battery Balancer Script
=======================

A Python script designed for balancing multiple lithium battery cells using Raspberry Pi, I2C communication, and GPIO for hardware control. This script provides a terminal-based user interface (TUI) for real-time monitoring and management of battery cell voltages.

Features
--------

*   **Real-time Voltage Monitoring**: Monitors the voltage of each battery cell in real-time.
*   **Automatic Balancing**: Balances the voltage between cells when disparities exceed a set threshold.
*   **Alarm System**: Alerts via email and hardware relay when cells reach critical voltage levels.
*   **Error Handling**: Robust error detection with logging for troubleshooting.
*   **Watchdog System**: Automatically restarts the script if it becomes unresponsive.
*   **Text-based User Interface**: Uses `curses` for an interactive TUI displaying battery status.

Hardware Requirements
---------------------

*   Raspberry Pi (with Python 3 installed)
*   ADS1115 ADC for voltage measurement
*   M5Stack 4Relay module for relay control
*   DC-DC converter for balancing
*   I2C multiplexer (PaHUB2)
*   Buzzer or LED for physical alarm indication

Software Requirements
---------------------

*   Python 3
*   Libraries:
    *   `smbus`
    *   `RPi.GPIO`
    *   `smtplib`
    *   `curses`
    *   `configparser`
    *   `logging`
    *   `threading`
    *   `os`, `signal`, `sys`

Installation
------------

1.  **Clone the Repository**:
    
        git clone [your-repository-url]
        cd battery-balancer
    
2.  **Install Dependencies**:
    
        sudo apt-get update
        sudo apt-get install -y python3-smbus python3-rpi.gpio python3-curses python3-configparser
    
3.  **Setup Configuration**:
    *   Edit `config.ini` with the correct hardware settings, email configurations, and operational parameters.
4.  **Run the Script**:
    
        python3 battery_balancer.py
    

Configuration
-------------

*   **config.ini**: This file contains all necessary configuration settings:
    *   `General`: Contains operational thresholds and timing.
    *   `I2C`: Addresses for I2C devices.
    *   `GPIO`: GPIO pin numbers for relay control.
    *   `Email`: SMTP settings for email alerts.
    *   `ADS1115`: Configuration for the ADC.

Usage
-----

*   **Monitor**: The script will run, showing a TUI where you can monitor battery voltages.
*   **Balancing**: If a voltage imbalance is detected, the script will initiate balancing automatically.

Troubleshooting
---------------

*   **Check Logs**: All operations are logged in `battery_balancer.log`. Check this for any errors or issues.
*   **Hardware Check**: Ensure all connections are secure and hardware is functioning.
*   **Configuration**: Verify settings in `config.ini` are correct for your setup.

Safety Notes
------------

*   **Do Not Overcharge**: Ensure your `ALARM_VOLTAGE_THRESHOLD` is set appropriately to avoid overcharging cells.
*   **Physical Inspection**: Regularly inspect physical connections and battery health.

License
-------

\[Your License Here\] - e.g., MIT License, GPL, etc.

Acknowledgements
----------------

*   Special thanks to \[Your Name or Team\] for the development of this script.
*   Thanks to the open-source community for the libraries and tools used in this project.

Contact
-------

For any issues or suggestions, please contact \[Your Contact Information\].

* * *

Feel free to contribute to this project by submitting pull requests or raising issues on the repository.