#!/bin/sh
# Cross-compile 羞羞脸变声.exe (needs mingw-w64: apt install gcc-mingw-w64-x86-64)
set -e
cd "$(dirname "$0")"
x86_64-w64-mingw32-windres -c 65001 launcher.rc -O coff -o launcher.res
x86_64-w64-mingw32-gcc -municode -mwindows -O2 -s -o "../羞羞脸变声.exe" launcher.c launcher.res
rm -f launcher.res
echo "built ../羞羞脸变声.exe"
