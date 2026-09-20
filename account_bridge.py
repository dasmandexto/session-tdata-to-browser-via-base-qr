import asyncio
import base64
import json
import os
import re
import shutil
import urllib.parse
import zipfile
from pathlib import Path

import cv2
from telethon import TelegramClient, functions

# Ваши данные с my.telegram.org (или дефолтные параметры)
API_ID = 2040  # либо ваш API_ID
API_HASH = "b184473d081a79131048721e3a3541de"  # либо ваш API_HASH

BASE_DIR = Path(__file__).parent.resolve()
INBOX_DIR = BASE_DIR / "inbox"
ACCOUNTS_DIR = BASE_DIR / "accounts"


def extract_qr_token(image_path: str) -> bytes | None:
  """Считывает QR-код с изображения и возвращает бинарный токен авторизации."""
  img = cv2.imread(image_path)
  if img is None:
    return None

  detector = cv2.QRCodeDetector()
  data, _, _ = detector.detectAndDecode(img)

  if not data or not data.startswith("tg://login?token="):
    return None

  parsed = urllib.parse.urlparse(data)
  token_b64 = urllib.parse.parse_qs(parsed.query).get("token", [None])[0]
  if not token_b64:
    return None

  # Корректировка паддинга base64
  padding = 4 - (len(token_b64) % 4)
  if padding != 4:
    token_b64 += "=" * padding

  return base64.urlsafe_b64decode(token_b64)


async def organize_inbox():
  """Сканирует папку inbox и распределяет аккаунты по индивидуальным папкам."""
  INBOX_DIR.mkdir(exist_ok=True)
  ACCOUNTS_DIR.mkdir(exist_ok=True)

  # 1. Распаковка zip-архивов (например, tdata в архивах)
  for zip_file in INBOX_DIR.glob("*.zip"):
    extract_target = INBOX_DIR / zip_file.stem
    extract_target.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_file, "r") as z:
      z.extractall(extract_target)
    zip_file.unlink()

  # 2. Обработка .session и .json файлов
  for session_file in list(INBOX_DIR.glob("*.session")):
    name = session_file.stem
    account_dir = ACCOUNTS_DIR / name
    account_dir.mkdir(exist_ok=True)

    # Перемещаем .session
    shutil.move(str(session_file), str(account_dir / session_file.name))

    # Перемещаем сопутствующий .json, если он есть
    json_file = INBOX_DIR / f"{name}.json"
    if json_file.exists():
      shutil.move(str(json_file), str(account_dir / json_file.name))

    print(f"[+] Сессия {name} распределена в {account_dir}")

  # 3. Обработка папок с tdata
  for item in list(INBOX_DIR.iterdir()):
    if item.is_dir() and item.name != "accounts":
      has_tdata = (item / "tdata").exists() or (item / "D877F783D5D3EF8C").exists()
      if has_tdata:
        account_dir = ACCOUNTS_DIR / item.name
        account_dir.mkdir(exist_ok=True)
        target_tdata = account_dir / "tdata"
        if not target_tdata.exists():
          shutil.move(str(item), str(target_tdata))
        print(f"[+] TData {item.name} распределена в {account_dir}")


async def bridge_session_to_browser(account_dir: Path):
  """Авторизует профиль браузера Telegram Web через .session файл."""
  session_files = list(account_dir.glob("*.session"))
  if not session_files:
    # Если лежит tdata, конвертируем её в session через opentele
    tdata_dir = account_dir / "tdata"
    if tdata_dir.exists():
      from opentele.api import UseCurrentSession
      from opentele.td import TDesktop

      tdesk = TDesktop(str(tdata_dir))
      client_tdata = await tdesk.ToTelethon(
          session=str(account_dir / "account.session"), flag=UseCurrentSession
      )
      session_files = [account_dir / "account.session"]
    else:
      print(f"[-] В {account_dir.name} не найдено ни .session, ни tdata.")
      return

  session_path = session_files[0]
  browser_profile_dir = account_dir / "browser_profile"
  browser_profile_dir.mkdir(exist_ok=True)

  # Проверяем, не авторизован ли уже профиль
  if (browser_profile_dir / "Default" / "IndexedDB").exists():
    print(
        f"[~] Профиль браузера для {account_dir.name} уже существует."
        " Пропуск."
    )
    return

  from playwright.async_api import async_playwright

  print(f"[*] Авторизация браузера для {account_dir.name}...")

  client = TelegramClient(str(session_path), API_ID, API_HASH)
  await client.connect()

  if not await client.is_user_authorized():
    print(f"[-] Сессия {session_path.name} не авторизована на сервере Telegram.")
    await client.disconnect()
    return

  async with async_playwright() as p:
    # Запуск браузера с постоянным контекстом (сохраняет куки и IndexedDB)
    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(browser_profile_dir),
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = await context.new_page()

    await page.goto("https://web.telegram.org/a/", wait_until="networkidle")

    # Ждем отрисовки canvas или контейнера с QR-кодом
    qr_locator = page.locator("canvas, .qr-container")
    try:
      await qr_locator.wait_for(timeout=15000)
    except Exception:
      print(f"[-] Не удалось найти QR-код на странице для {account_dir.name}")
      await context.close()
      await client.disconnect()
      return

    # Сохраняем скриншот QR-кода
    qr_img_path = str(account_dir / "temp_qr.png")
    await qr_locator.screenshot(path=qr_img_path)

    # Декодируем токен из QR
    token_bytes = extract_qr_token(qr_img_path)
    if not token_bytes:
      print(f"[-] Ошибка распознавания QR-кода для {account_dir.name}")
      if os.path.exists(qr_img_path):
        os.remove(qr_img_path)
      await context.close()
      await client.disconnect()
      return

    if os.path.exists(qr_img_path):
      os.remove(qr_img_path)

    # Подтверждаем веб-токен через MTProto
    try:
      await client(functions.auth.AcceptLoginTokenRequest(token=token_bytes))
      print(f"[+] Токен успешно принят Telegram для {account_dir.name}!")
    except Exception as e:
      print(f"[-] Ошибка вызова AcceptLoginTokenRequest: {e}")
      await context.close()
      await client.disconnect()
      return

    # Ждем входа в веб-клиент (появление левой панели диалогов)
    try:
      await page.wait_for_selector(
          "#middle-column, .chat-list, .ListItem", timeout=15000
      )
      print(f"[✓] Успешный вход в Telegram Web для {account_dir.name}!")
    except Exception:
      print(
          f"[!] Вход выполнен, сохраняем состояние браузера для"
          f" {account_dir.name}."
      )

    await context.close()
    await client.disconnect()


async def main():
  print("=== Шаг 1: Раскладывание аккаунтов по папкам ===")
  await organize_inbox()

  print("\n=== Шаг 2: Генерация браузерных сессий ===")
  for acc_dir in ACCOUNTS_DIR.iterdir():
    if acc_dir.is_dir():
      await bridge_session_to_browser(acc_dir)

  print("\n[✓] Готово! Все аккаунты подготовлены к минибраузингу.")


if __name__ == "__main__":
  asyncio.run(main())
