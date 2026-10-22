"""Fait défiler X dans Firefox pendant que Zeeschuimer collecte les posts.

Usage:
    python scroll_x.py [--url https://x.com/home] [--minutes 10]

Première fois : connectez-vous à X dans la fenêtre, puis dans le popup de
Zeeschuimer activez la capture pour X/Twitter. Le profil est conservé dans ./profile.
"""
import argparse
import random
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://x.com/home")
    p.add_argument("--minutes", type=float, default=10)
    args = p.parse_args()

    opts = Options()
    opts.add_argument("-profile")
    opts.add_argument(str(HERE / "profile"))
    driver = webdriver.Firefox(options=opts)
    driver.install_addon(str(HERE / "zeeschuimer.xpi"))
    driver.get(args.url)

    input("Connectez-vous à X, activez la capture X/Twitter dans Zeeschuimer, "
          "puis appuyez sur Entrée ici pour lancer le défilement... ")

    end = time.time() + args.minutes * 60
    n = 0
    try:
        while time.time() < end:
            driver.execute_script("window.scrollBy(0, arguments[0]);",
                                  random.randint(500, 1100))
            n += 1
            time.sleep(random.uniform(2.0, 6.0))
            if random.random() < 0.1:  # pause de "lecture"
                time.sleep(random.uniform(8, 20))
            if n % 20 == 0:
                print(f"{n} défilements...")
    except KeyboardInterrupt:
        print("Interrompu.")
    print("Terminé. Exportez les données depuis le popup de Zeeschuimer "
          "avant de fermer la fenêtre.")
    input("Entrée pour fermer Firefox... ")
    driver.quit()


if __name__ == "__main__":
    main()
