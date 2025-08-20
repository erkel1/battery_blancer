[file name]: setup_bms.sh
[file content begin]
#!/bin/bash
# Setup script for Battery Management System with web interface

echo "Setting up Battery Management System..."

# Update system
echo "Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install required packages
echo "Installing required packages..."
sudo apt-get install -y python3-pip python3-smbus i2c-tools

# Enable I2C interface
echo "Enabling I2C interface..."
sudo raspi-config nonint do_i2c 0

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# Create systemd service
echo "Creating systemd service..."
sudo tee /etc/systemd/system/bms.service > /dev/null <<EOL
[Unit]
Description=Battery Management System
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/bms.py
WorkingDirectory=/home/pi
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
EOL

# Enable and start service
echo "Enabling and starting BMS service..."
sudo systemctl daemon-reload
sudo systemctl enable bms.service
sudo systemctl start bms.service

echo "Setup complete!"
echo "Web interface will be available at http://$(hostname -I | awk '{print $1}'):8080"
echo "Check status with: sudo systemctl status bms.service"
[file content end]