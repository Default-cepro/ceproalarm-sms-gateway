# Ceproalarm SMS Gateway & Parser 🛰️

A Python-based centralized system for mass management, auditing, and diagnostics of geolocation devices via Industrial SMS communication.

## 📋 Description
This project enables **Ceproalarm** to automate command dispatching across a multi-brand fleet of GPS trackers. The system sends bulk instructions through a GSM modem connected via serial port, intercepts incoming responses, parses the data, and updates device health and location status in a centralized database.

## 🛠️ Key Features
- **Multi-Brand Management:** Command dictionaries tailored by manufacturer and model (Coban, Concox, Queclink, etc.).
- **Serial Communication:** Direct hardware interfacing with GSM modems using AT commands.
- **Intelligent Parsing:** Extraction of coordinates, alerts, and battery status from raw text strings.
- **Historical Logging:** Database integration for tracking response logs and uptime metrics.

## 📂 Project Structure
- `src/drivers/`: Control logic for modem and serial communication.
- `src/parsers/`: Protocol-specific decoding modules.
- `src/database/`: Database schemas and log management.
- `config/`: Configuration files for GPS models and COM ports.

## 🚀 Prerequisites
1. **Hardware:** PC or Raspberry Pi with a GSM/GPRS modem (USB or Serial).
2. **Software:** [Python 3.9+](https://www.python.org)
3. **Libraries:** 
   - [pySerial](https://pyserial.readthedocs.io) for hardware interfacing.
   - [SQLAlchemy](https://www.sqlalchemy.org) for data persistence.

## 🔧 Installation & Setup
1. Clone the repository:
   ```bash
   git clone https://github.com
