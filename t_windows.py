import sys

import colorama

stream = sys.stdout


def print_col(col):
    print("col:", col)
    print("col.stream:", col.stream)
    print("col.win32_calls:", col.win32_calls)
    print("strip, convert:", col.strip, col.convert)


print("=== default ===")
col = colorama.AnsiToWin32(stream)
print_col(col)

print("=== convert=True, strip=True ===")
col = colorama.AnsiToWin32(stream, convert=True, strip=True)
print_col(col)
