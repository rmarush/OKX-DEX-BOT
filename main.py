#!/usr/bin/env python3
from __future__ import annotations

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# дії меню
from okx_dex_bot.menu_actions import (
    run_trading_for_all_wallets,
    sell_leftovers_for_all_wallets,
    print_stats_for_all_wallets,
)

# Спробуємо імпортувати InquirerPy для стрілочного меню
try:
    from InquirerPy import inquirer
except Exception:  # fallback на звичайне введення
    inquirer = None


BANNER = r"""
 ██████╗ ██╗  ██╗██╗  ██╗    ██████╗ ███████╗██╗  ██╗    ██████╗  ██████╗ ████████╗
██╔═══██╗██║ ██╔╝╚██╗██╔╝    ██╔══██╗██╔════╝╚██╗██╔╝    ██╔══██╗██╔═══██╗╚══██╔══╝
██║   ██║█████╔╝  ╚███╔╝     ██║  ██║█████╗   ╚███╔╝     ██████╔╝██║   ██║   ██║   
██║   ██║██╔═██╗  ██╔██╗     ██║  ██║██╔══╝   ██╔██╗     ██╔══██╗██║   ██║   ██║   
╚██████╔╝██║  ██╗██╔╝ ██╗    ██████╔╝███████╗██╔╝ ██╗    ██████╔╝╚██████╔╝   ██║   
 ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝    ╚═════╝ ╚══════╝╚═╝  ╚═╝    ╚═════╝  ╚═════╝    ╚═╝                                                                              
"""

def print_banner():
    os.system("cls" if os.name == "nt" else "clear")
    print(BANNER)
    print("✨ Ласкаво просимо! Оберіть дію нижче.\n")


def arrow_menu() -> str:
    """Меню зі стрілками (InquirerPy). Повертає 'run' | 'sell' | 'stats' | 'exit'."""
    return inquirer.select(
        message="🧭 Оберіть дію:",
        choices=[
            {"name": "🚀  Прогнати акаунти", "value": "run"},
            {"name": "💰  Продати залишики", "value": "sell"},
            {"name": "📊  Парсинг статистики", "value": "stats"},
            {"name": "👋  Вихід", "value": "exit"},
        ],
        default="run",
        pointer="👉",
        instruction="Стрілки ↑/↓ для навігації, Enter — підтвердити",
    ).execute()


def fallback_menu() -> str:
    """Запасний варіант без InquirerPy (текстове меню)."""
    print("\n================= OKX DEX BOT =================")
    print("1) 🚀  Прогнати акаунти")
    print("2) 💰  Продати залишики")
    print("3) 📊  Парсинг статистики")
    print("4) 👋  Вихід")
    print("===============================================")
    choice = input("Ваш вибір [1-4]: ").strip().lower()
    mapping = {
        "1": "run", "r": "run", "run": "run",
        "2": "sell", "s": "sell", "sell": "sell",
        "3": "stats", "p": "stats", "stats": "stats",
        "4": "exit", "q": "exit", "quit": "exit", "exit": "exit",
    }
    return mapping.get(choice, "")


def main():
    load_dotenv()  # щоб .env підхопився і для внутрішніх модулів

    while True:
        print_banner()
        try:
            if inquirer is not None:
                choice = arrow_menu()
            else:
                print("⚠️  InquirerPy не знайдено. Використовую звичайний режим вводу.")
                choice = fallback_menu()
        except KeyboardInterrupt:
            print("\n👋 До зустрічі!")
            sys.exit(0)

        if choice == "run":
            print("🚀 Стартую торгівлю для всіх гаманців…")
            run_trading_for_all_wallets()
            input("\n✅ Готово. Натисніть Enter, щоб повернутися в меню…")

        elif choice == "sell":
            print("💰 Продаю залишки токенів з конфігу…")
            sell_leftovers_for_all_wallets()
            input("\n✅ Готово. Натисніть Enter, щоб повернутися в меню…")

        elif choice == "stats":
            print("📊 Збираю статистику…")
            print_stats_for_all_wallets()
            input("\n✅ Готово. Натисніть Enter, щоб повернутися в меню…")

        elif choice == "exit":
            print("👋 До зустрічі!")
            sys.exit(0)

        else:
            print("🤔 Невірний вибір. Спробуйте ще раз.")
            input("Натисніть Enter, щоб продовжити…")


if __name__ == "__main__":
    main()