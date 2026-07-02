from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
import qrcode
import time

api_id = 25874957
api_hash = 'c89ef6fd9ba5c8a479abb1f4d2de248d'

session_name = 'my_session12'


async def main():
    client = TelegramClient(session_name, api_id, api_hash)
    
    await client.connect()

    if await client.is_user_authorized():
        print("✅ Уже авторизованы!")
    else:
        print("📱 Вход через QR-код (с поддержкой 2FA)\n")
        
        while True:
            try:
                qr_login = await client.qr_login()
                
                qr = qrcode.QRCode(version=1, box_size=3, border=2)
                qr.add_data(qr_login.url)
                qr.make(fit=True)

                print("\033c", end="")  # очистка экрана
                print("🔄 Новый QR-код сгенерирован\n")
                print("Отсканируй в Telegram → Настройки → Устройства → Подключить устройство\n")
                
                qr.print_ascii(invert=True)
                print(f"Время: {time.strftime('%H:%M:%S')}")
                
                # Ждём сканирование
                await asyncio.wait_for(qr_login.wait(), timeout=60)
                print("\n✅ QR-код успешно отсканирован!")
                break

            except asyncio.TimeoutError:
                print("⏳ QR-код истёк → генерируем новый...")
                continue
            except Exception as e:
                error_str = str(e)
                if "PASSWORD_HASH_INVALID" in error_str or "password" in error_str.lower():
                    print("\n🔑 Требуется пароль двухэтапной верификации!")
                    password = input("Введите ваш 2FA-пароль: ")
                    await client.sign_in(password=password)
                    print("✅ Пароль принят!")
                    break
                elif "TOKEN_EXPIRED" in error_str or "expired" in error_str.lower():
                    print("⏳ QR устарел → обновляем...")
                    continue
                else:
                    print(f"❌ Ошибка: {e}")
                    await asyncio.sleep(2)
                    continue

    # === После входа ===
    me = await client.get_me()
    print(f"\n👤 Успешный вход!")
    print(f"   Имя: {me.first_name}")
    print(f"   Username: @{me.username}" if me.username else "")

    await client.send_message('me', '✅ Telethon клиент успешно запущен с 2FA!')

    print("\n🚀 Клиент работает. Нажми Ctrl+C для остановки.")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
