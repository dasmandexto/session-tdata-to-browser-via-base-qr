# Telegram Session to Browser via QR

A Python script to automatically authorize a Telegram Web browser profile using an existing `.session` file or `tdata` folder. It uses Playwright to capture the login QR code and Telethon to accept the login token via MTProto.

## Requirements

```bash
pip install telethon playwright opencv-python pillow opentele
playwright install chromium

python3 -m venv venv
source venv/bin/activate
```

## Usage

1. Create an `inbox/` folder in the root of the project.
2. Place your new `.session`, `.json`, `tdata` folders, or `.zip` archives into the `inbox/` folder.
3. Run the script:
   ```bash
   python account_bridge.py
   ```
4. The script will organize the accounts and generate the required browser sessions in the `accounts/` folder:
   ```text
   accounts/
   └── +380991112233/
       ├── +380991112233.session
       ├── +380991112233.json
       └── browser_profile/
   ...
   ```
