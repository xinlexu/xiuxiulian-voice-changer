/*
 * 羞羞脸变声.exe — tiny launcher.
 * Starts  runtime\pythonw.exe -X utf8 app\main.py --exe <this exe> [args]
 * from the folder this exe lives in, without a console window.
 * Build (from Linux): see build.sh
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>

#define BIG 4096

static int file_exists(const wchar_t *path) {
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static void fail(const wchar_t *text) {
    MessageBoxW(NULL, text, L"羞羞脸变声", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst; (void)prev; (void)show;
    static wchar_t exe[BIG], root[BIG], python[BIG], script[BIG], cmd[BIG * 3], msg[BIG];
    DWORD n = GetModuleFileNameW(NULL, exe, BIG);
    if (n == 0 || n >= BIG) {
        fail(L"无法确定程序位置。");
        return 1;
    }
    wcscpy(root, exe);
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;

    _snwprintf(python, BIG, L"%ls\\runtime\\pythonw.exe", root);
    _snwprintf(script, BIG, L"%ls\\app\\main.py", root);
    if (!file_exists(python)) {
        _snwprintf(msg, BIG,
                   L"找不到运行环境：\n%ls\n\n请确认 runtime 文件夹和本程序在同一个目录里。", python);
        fail(msg);
        return 2;
    }
    if (!file_exists(script)) {
        _snwprintf(msg, BIG, L"找不到程序文件：\n%ls", script);
        fail(msg);
        return 3;
    }
    _snwprintf(cmd, BIG * 3, L"\"%ls\" -X utf8 \"%ls\" --exe \"%ls\" %ls", python, script, exe,
               args ? args : L"");

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                        NULL, root, &si, &pi)) {
        _snwprintf(msg, BIG, L"启动失败（错误 %lu）。", GetLastError());
        fail(msg);
        return 4;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
