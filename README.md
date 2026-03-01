#GENERAL

This script is for SPI GPIO (480x320) Display status output


##INSTALL PREREQS

download proper from hosyond.com
sudo apt update
sudo apt install -y python3-pip fbi fonts-dejavu-core
pip3 install pillow requests --break-system-packages


sudo python3 /home/admin/RPI/status_lcd.py



sudo vim /etc/systemd/system/lcd-dashboard.service :
---------------
[Unit]
Description=SPI LCD Dashboard (Batumi weather + IPs + system stats)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 /home/admin/RPI/status_lcd.py
Restart=always
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
---------------

services:
sudo systemctl daemon-reload
sudo systemctl enable lcd-dashboard.service
sudo systemctl start lcd-dashboard.service

logs:
sudo journalctl -u lcd-dashboard.service -f


restart:
sudo systemctl restart lcd-dashboard.service
