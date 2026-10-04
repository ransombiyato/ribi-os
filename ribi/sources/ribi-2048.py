#!/usr/bin/env python3
import curses
import random

SIZE = 4

def new_tile(board):
    empties = [(r, c) for r in range(SIZE) for c in range(SIZE) if board[r][c] == 0]
    if empties:
        r, c = random.choice(empties)
        board[r][c] = 4 if random.random() < 0.1 else 2

def compress(row):
    vals = [v for v in row if v != 0]
    result = []
    skip = False
    for i in range(len(vals)):
        if skip:
            skip = False
            continue
        if i + 1 < len(vals) and vals[i] == vals[i + 1]:
            result.append(vals[i] * 2)
            skip = True
        else:
            result.append(vals[i])
    return result + [0] * (SIZE - len(result))

def move(board, direction):
    rotated = [row[:] for row in board]
    rotations = {"L": 0, "U": 1, "R": 2, "D": 3}[direction]
    for _ in range(rotations):
        rotated = [list(r) for r in zip(*rotated[::-1])]
    new_rows = [compress(r) for r in rotated]
    for _ in range((4 - rotations) % 4):
        new_rows = [list(r) for r in zip(*new_rows[::-1])]
    changed = new_rows != board
    return new_rows, changed

def has_moves(board):
    if any(0 in row for row in board):
        return True
    for r in range(SIZE):
        for c in range(SIZE):
            if c + 1 < SIZE and board[r][c] == board[r][c + 1]:
                return True
            if r + 1 < SIZE and board[r][c] == board[r + 1][c]:
                return True
    return False

def main(stdscr):
    curses.curs_set(0)
    board = [[0] * SIZE for _ in range(SIZE)]
    new_tile(board)
    new_tile(board)
    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, "Ribi 2048 - arrows to move, q to quit")
        for r in range(SIZE):
            row_str = " ".join(f"{v:5d}" if v else "    ." for v in board[r])
            stdscr.addstr(2 + r, 0, row_str)
        stdscr.refresh()
        key = stdscr.getch()
        direction = {curses.KEY_LEFT: "L", curses.KEY_RIGHT: "R",
                     curses.KEY_UP: "U", curses.KEY_DOWN: "D"}.get(key)
        if key == ord("q"):
            break
        if direction:
            board, changed = move(board, direction)
            if changed:
                new_tile(board)
        if not has_moves(board):
            stdscr.addstr(8, 0, "GAME OVER!")
            stdscr.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            break

if __name__ == "__main__":
    curses.wrapper(main)
