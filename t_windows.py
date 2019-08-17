import sys

import colorama

stream = sys.stdout


def print_col(col):
    print("col:", col)
    print("col.stream:", col.stream)
    print("col.win32_calls:", len(col.win32_calls))
    print("strip, convert:", col.strip, col.convert)

    print('\x1b[34mblue with colorama\x1b[m', file=col.stream)
    print('\x1b[34mblue without colorama\x1b[m')


print("=== default ===")
col = colorama.AnsiToWin32(stream)
print_col(col)

print("=== convert=True, strip=True ===")
col = colorama.AnsiToWin32(stream, convert=True, strip=True)
print_col(col)

print("=== convert=True, strip=False ===")
col = colorama.AnsiToWin32(stream, convert=True, strip=False)
print_col(col)

print("=== convert=False, strip=True ===")
col = colorama.AnsiToWin32(stream, convert=False, strip=True)
print_col(col)

print("=== convert=False, strip=False ===")
col = colorama.AnsiToWin32(stream, convert=False, strip=False)
print_col(col)
