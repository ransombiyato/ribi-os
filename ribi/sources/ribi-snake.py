#!/usr/bin/env python3
import curses
import random

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(120)
    max_y, max_x = stdscr.getmaxyx()
    if max_y < 7 or max_x < 12:
        stdscr.addstr(0, 0, "Terminal too small for Ribi Snake.")
        stdscr.getch()
        return
    snake = [(max_y // 2, max_x // 2)]
    direction = (0, 1)
    food = (random.randint(1, max_y - 2), random.randint(1, max_x - 2))
    score = 0
    while True:
        stdscr.erase()
        stdscr.border()
        stdscr.addstr(0, 2, f" Ribi Snake - score {score} (q to quit) ")
        try:
            stdscr.addch(food[0], food[1], ord("*"))
            for y, x in snake:
                stdscr.addch(y, x, ord("#"))
        except curses.error:
            pass
        key = stdscr.getch()
        if key == ord("q"):
            break
        elif key == curses.KEY_UP and direction != (1, 0):
            direction = (-1, 0)
        elif key == curses.KEY_DOWN and direction != (-1, 0):
            direction = (1, 0)
        elif key == curses.KEY_LEFT and direction != (0, 1):
            direction = (0, -1)
        elif key == curses.KEY_RIGHT and direction != (0, -1):
            direction = (0, 1)

        head_y = (snake[0][0] + direction[0] - 1) % (max_y - 2) + 1
        head_x = (snake[0][1] + direction[1] - 1) % (max_x - 2) + 1
        new_head = (head_y, head_x)
        if new_head in snake:
            stdscr.nodelay(False)
            stdscr.addstr(max_y // 2, max_x // 2 - 5, "GAME OVER!")
            stdscr.getch()
            break
        snake.insert(0, new_head)
        if new_head == food:
            score += 1
            food = (random.randint(1, max_y - 2), random.randint(1, max_x - 2))
        else:
            snake.pop()

if __name__ == "__main__":
    curses.wrapper(main)
