#!/usr/bin/env python3
import curses
import sys
import re

KEYWORDS = {
    "def","class","import","from","return","if","elif","else","for","while",
    "try","except","finally","with","as","pass","break","continue","in","is",
    "not","and","or","lambda","yield","global","nonlocal","None","True","False",
    "fi","then","do","done","esac","case","function","echo","local","export",
}
TOKEN_RE = re.compile(r"(#.*$|\".*?\"|'.*?'|\b\w+\b)")

def highlight(win, y, x, line, max_x):
    col = x
    for m in TOKEN_RE.finditer(line):
        tok = m.group(0)
        if col >= max_x:
            break
        attr = curses.A_NORMAL
        if tok.startswith("#"):
            attr = curses.color_pair(2)
        elif tok.startswith('"') or tok.startswith("'"):
            attr = curses.color_pair(3)
        elif tok in KEYWORDS:
            attr = curses.color_pair(1) | curses.A_BOLD
        try:
            win.addnstr(y, col, tok, max_x - col, attr)
        except curses.error:
            pass
        col = x + m.end()

def main(stdscr, path):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.curs_set(1)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        lines = [""]

    cy, cx, top = 0, 0, 0
    modified = False
    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        body_h = max_y - 1
        if cy - top >= body_h:
            top = cy - body_h + 1
        if cy < top:
            top = cy
        for i in range(body_h):
            li = top + i
            if li >= len(lines):
                break
            highlight(stdscr, i, 0, lines[li][:max_x], max_x)
        status = f" ribi-edit: {path} {'[+]' if modified else ''}  ^S save  ^X exit "
        try:
            stdscr.addnstr(max_y - 1, 0, status.ljust(max_x), max_x, curses.A_REVERSE)
        except curses.error:
            pass
        stdscr.move(min(cy - top, body_h - 1), min(cx, max_x - 1))
        ch = stdscr.getch()
        if ch == 24:  # ^X
            break
        elif ch == 19:  # ^S
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            modified = False
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if cx > 0:
                lines[cy] = lines[cy][:cx-1] + lines[cy][cx:]
                cx -= 1
                modified = True
            elif cy > 0:
                cx = len(lines[cy-1])
                lines[cy-1] += lines[cy]
                del lines[cy]
                cy -= 1
                modified = True
        elif ch in (curses.KEY_ENTER, 10, 13):
            lines.insert(cy+1, lines[cy][cx:])
            lines[cy] = lines[cy][:cx]
            cy += 1
            cx = 0
            modified = True
        elif ch == curses.KEY_UP and cy > 0:
            cy -= 1; cx = min(cx, len(lines[cy]))
        elif ch == curses.KEY_DOWN and cy < len(lines) - 1:
            cy += 1; cx = min(cx, len(lines[cy]))
        elif ch == curses.KEY_LEFT and cx > 0:
            cx -= 1
        elif ch == curses.KEY_RIGHT and cx < len(lines[cy]):
            cx += 1
        elif 32 <= ch < 127:
            lines[cy] = lines[cy][:cx] + chr(ch) + lines[cy][cx:]
            cx += 1
            modified = True

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "untitled.txt"
    curses.wrapper(main, target)
