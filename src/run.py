import subprocess
import sys
import time
import os

def run_messenger():
    """Запуск мессенджера"""
    
    print("=" * 50)
    print("MAXutko Messenger - Запуск")
    print("=" * 50)
    print("\nВыберите действие:")
    print("1. Запустить сервер")
    print("2. Запустить клиент")
    print("3. Запустить и сервер, и клиент")
    print("4. Выход")
    
    choice = input("\nВведите номер действия (1-4): ").strip()
    
    if choice == "1":
        print("\nЗапуск сервера...")
        print("Сервер будет запущен на 127.0.0.1:5555")
        print("Для остановки сервера нажмите Ctrl+C\n")
        subprocess.run([sys.executable, "server.py"])
        
    elif choice == "2":
        print("\nЗапуск клиента...")
        print("Убедитесь, что сервер запущен\n")
        subprocess.run([sys.executable, "client.py"])
        
    elif choice == "3":
        print("\nЗапуск сервера и клиента...")
        print("Сначала запустится сервер, затем клиент")
        print("Для сервера откройте отдельное окно терминала\n")
        
        # Запускаем сервер в отдельном процессе
        server_process = subprocess.Popen([sys.executable, "server.py"])
        
        print("Сервер запущен. Ожидание 2 секунды...")
        time.sleep(2)
        
        print("Запуск клиента...")
        subprocess.run([sys.executable, "client.py"])
        
        # Завершаем сервер после закрытия клиента
        server_process.terminate()
        server_process.wait()
        
    elif choice == "4":
        print("\nВыход...")
        sys.exit(0)
        
    else:
        print("\nНеверный выбор!")

if __name__ == "__main__":
    # Проверяем существование файлов
    if not os.path.exists("server.py"):
        print("Ошибка: файл server.py не найден!")
        print("Убедитесь, что все файлы находятся в одной папке:")
        print("1. server.py")
        print("2. client.py")
        print("3. run.py (этот файл)")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)
    
    if not os.path.exists("client.py"):
        print("Ошибка: файл client.py не найден!")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)
    
    while True:
        run_messenger()
        print("\n" + "=" * 50 + "\n")