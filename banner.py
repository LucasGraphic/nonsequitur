# banner.py -- NonSequitur startup banner
# Import and call show() from nonsequitur.py instead of _print_splash()
# Usage: from banner import show; show()

def show():
    B = "\033[1m"
    D = "\033[0m"
    G = "\033[38;5;240m"
    W = "\033[37m"
    lines = [
        "",
        G + "  +--------------------------------------------------+" + D,
        G + "  |" + D + "                                                  " + G + "|" + D,
        G + "  |" + D + B + "   _  _              ____                _ __   " + D + G + "|" + D,
        G + "  |" + D + B + "  | \\| |___ _ _  ___/ __| ___ __ _ _  _(_) /_  " + D + G + "|" + D,
        G + "  |" + D + B + "  | .` / _ \\ ' \\(_-<\\__ \\/ -_) _` | || | |  _/ " + D + G + "|" + D,
        G + "  |" + D + B + "  |_|\\_\\___/_||_/__/____/\\___\\__, |\\_,_|_|\\__| " + D + G + "|" + D,
        G + "  |" + D + B + "                               |_|              " + D + G + "|" + D,
        G + "  |" + D + "                                                  " + G + "|" + D,
        G + "  |" + D + W + "  Autonomous Research & Publishing Pipeline     " + D + G + "|" + D,
        G + "  |" + D + G + "  lucasgraphic.com                              " + D + G + "|" + D,
        G + "  |" + D + "                                                  " + G + "|" + D,
        G + "  +--------------------------------------------------+" + D,
        "",
    ]
    for l in lines:
        print(l)
