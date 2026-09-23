; ═══════════════════════════════════════════════════════════════════════════
; MiQroForge Desktop 卸载残留清理（issue #1177）
;
; 本文件由 scripts/generate-uninstaller-nsh.mjs 从本模板 +
; src/shared/cleanup-constants.json 生成 —— 不要手工编辑 installers.nsh。
;
; 注入点（electron-builder 26，assisted 安装器 oneClick: false，已核实模板）：
;   customUnWelcomePage   → 复选框页（默认不勾，替换默认卸载欢迎页）
;   customUnInstall       → 卸载 section 内、删除程序文件之前执行清理
;   customUninstallPage   → INSTFILES 与 FINISH 之间的结果摘要页
;
; 安全规则（#1103 教训）：删除目标只能是「已知候选」——注册表显式记录的数据根
; （非主目录/盘根/系统目录）或默认名候选目录（~/.forge | ~/.miqi | ~/.assistant）。
; 解析不出安全目标时跳过并记日志，绝不回退到扩大删除范围。
; ═══════════════════════════════════════════════════════════════════════════

; 本 include 在 installer.nsi 的 MUI2/LogicLib/nsDialogs 引入之前编译（函数体
; 在 include 时立即编译），而 MUI_HEADER_TEXT 等宏来自 MUI2 —— 因此这里先行
; 引入（MUI2.nsh 带 MUI_INCLUDED 守卫，installer.nsi 里的二次引入是 no-op）。
!include "MUI2.nsh"

!ifdef BUILD_UNINSTALLER

  Var MiqiCleanupChecked
  Var MiqiCleanupRan
  Var MiqiCleanupFailures
  Var MiqiCleanupLogPath
  Var MiqiCleanupCheckbox

  ; ── 复选框页（替换默认卸载欢迎页）────────────────────────────────────

  !macro customUnWelcomePage
    UninstPage custom un.CleanupPageCreate un.CleanupPageLeave
  !macroend

  Function un.CleanupPageCreate
    ; MUI_HEADER_TEXT 是运行时宏（SendMessage），必须在页面 create 函数内调用。
    !insertmacro MUI_HEADER_TEXT "卸载 MiQroForge Desktop" "选择是否同时删除应用数据"
    ; 页面清单里优先展示注册表记录的数据根，缺省时展示当前默认目录。
    ReadRegStr $R0 HKCU "@@REG_KEY@@" "@@REG_VALUE@@"
    StrCmp $R0 "" 0 +3
      StrCpy $R0 "$PROFILE\@@ACTIVE_DEFAULT@@"

    nsDialogs::Create 1018
    Pop $R1
    ${If} $R1 == error
      Abort
    ${EndIf}

    ${NSD_CreateLabel} 0 0u 100% 84u "即将卸载 MiQroForge Desktop。$\r$\n勾选下方选项可同时删除全部应用数据（默认不勾选，数据会保留）：$\r$\n$\r$\n· 数据根目录：$R0（含 workspace 用户文档、会话、技能、日志、配置、沙箱状态，将被整体删除）$\r$\n· 应用用户数据：$APPDATA\@@USERDATA_DIR@@$\r$\n· WSL 沙箱发行版：@@DISTRO@@（如存在，可能占用数 GB）$\r$\n· 自动更新缓存：$LOCALAPPDATA\@@UPDATER_DIR@@"
    Pop $R1

    ${NSD_CreateCheckbox} 0 92u 100% 12u "同时删除应用数据（含 WSL 沙箱 @@DISTRO@@）"
    Pop $MiqiCleanupCheckbox

    nsDialogs::Show
  FunctionEnd

  Function un.CleanupPageLeave
    ${NSD_GetState} $MiqiCleanupCheckbox $MiqiCleanupChecked
  FunctionEnd

  ; ── 日志与删除工具宏（$R8/$R9 保留给本组宏使用）─────────────────────

  !macro CleanupLogLine text
    FileOpen $R8 "$MiqiCleanupLogPath" a
    FileSeek $R8 0 END
    FileWrite $R8 "${text}$\r$\n"
    FileClose $R8
  !macroend

  !macro CleanupRemoveDir dir label id
    IfFileExists "${dir}" 0 miqi_skip_${id}
    RMDir /r "${dir}"
    IfFileExists "${dir}" 0 miqi_gone_${id}
    !insertmacro CleanupLogLine "[失败] ${label}：${dir}（删除后仍存在，可能被占用）"
    StrCpy $MiqiCleanupFailures "$MiqiCleanupFailures${label}$\r$\n"
    Goto miqi_end_${id}
    miqi_gone_${id}:
    !insertmacro CleanupLogLine "[已清理] ${label}：${dir}"
    miqi_end_${id}:
    miqi_skip_${id}:
  !macroend

  ; ── WSL 沙箱 distro 注销（只碰 @@DISTRO@@ 一个名字）─────────────────

  !macro CleanupWslDistro
    ; 探测：wsl -l -q 重定向到临时文件（wsl 重定向输出为 UTF-16LE），
    ; FileReadUTF16LE 逐行比对 distro 名；不存在则整段跳过。
    ; 注意：cmd /C 后首个字符若是引号，cmd 会剥掉首尾引号导致路径损坏
    ; （"a" b > "c" → a" b > "c）——$SYSDIR 无空格，exe 路径不加引号。
    StrCpy $R0 "$TEMP\miqi-wsl-list.tmp"
    Delete $R0
    ExecWait '$SYSDIR\cmd.exe /C $SYSDIR\wsl.exe -l -q > "$R0"' $R1
    StrCpy $R2 "0"
    IfFileExists $R0 0 miqi_wsl_nolist
    ClearErrors
    FileOpen $R3 $R0 r
    miqi_wsl_read:
    FileReadUTF16LE $R3 $R4
    IfErrors miqi_wsl_done
    ; 不用 StrContains：其内部 Call 的是安装器函数，卸载器里不能调用。
    ; FileReadUTF16LE 是否剥掉行尾换行随 NSIS 版本而异，三种形态都匹配
    ; （带 $\r$\n、带 $\r、无行尾的最后一行）。
    StrCmp $R4 "@@DISTRO@@" miqi_wsl_found
    StrCmp $R4 "@@DISTRO@@$\r" miqi_wsl_found
    StrCmp $R4 "@@DISTRO@@$\r$\n" miqi_wsl_found
    Goto miqi_wsl_read
    miqi_wsl_found:
    StrCpy $R2 "1"
    miqi_wsl_done:
    FileClose $R3
    miqi_wsl_nolist:
    Delete $R0

    StrCmp $R2 "1" 0 miqi_wsl_end
    ; 先 terminate 释放占用（未运行时报错可忽略），再 unregister。
    ; 无需 cmd 包装：wsl.exe 是真实可执行文件，直接 ExecWait。
    ExecWait '$SYSDIR\wsl.exe --terminate @@DISTRO@@' $R1
    ExecWait '$SYSDIR\wsl.exe --unregister @@DISTRO@@' $R1
    ${If} $R1 == 0
      !insertmacro CleanupLogLine "[已清理] WSL 沙箱发行版：@@DISTRO@@"
    ${Else}
      !insertmacro CleanupLogLine "[失败] WSL 沙箱发行版 @@DISTRO@@ 注销失败（退出码 $R1）。可手动执行：wsl --unregister @@DISTRO@@"
      StrCpy $MiqiCleanupFailures "$MiqiCleanupFailuresWSL 沙箱 @@DISTRO@@（退出码 $R1）$\r$\n"
    ${EndIf}
    miqi_wsl_end:
  !macroend

  ; ── 清理主体（卸载 section 内、删除程序文件之前执行）───────────────

  !macro customUnInstall
    ; 静默卸载（/S 或编译期 SilentInstall，运行时 IfSilent 判定）走环境变量
    ; （e2e 驱动）；交互模式已由复选框页写入 $MiqiCleanupChecked，此处不覆盖。
    IfSilent 0 miqi_cleanup_check_skip
    ReadEnvStr $MiqiCleanupChecked "MIQI_UNINSTALL_CLEANUP"
    miqi_cleanup_check_skip:
    StrCmp $MiqiCleanupChecked "1" 0 miqi_cleanup_end

    StrCpy $MiqiCleanupRan "1"
    StrCpy $MiqiCleanupLogPath "$TEMP\miqi-uninstall.log"
    !insertmacro CleanupLogLine "===== MiQroForge Desktop 卸载残留清理开始 ====="

    ; 1) 数据根：注册表显式值优先，否则默认名候选逐个探测删除。
    ReadRegStr $R0 HKCU "@@REG_KEY@@" "@@REG_VALUE@@"
    StrCmp $R0 "" 0 miqi_cleanup_regroot
@@CANDIDATE_RM_LINES@@
    Goto miqi_cleanup_dataroot_done

    miqi_cleanup_regroot:
    ; 去尾部反斜杠后做安全校验（#1103）：拒绝空值/盘根/主目录及其祖先/
    ; 系统目录/AppData 等，含 ".." 或 "/" 的路径也拒绝——绝不扩大删除范围。
    miqi_trim:
    StrCpy $R5 $R0 1 -1
    StrCmp $R5 "\" 0 miqi_trim_done
    StrCpy $R0 $R0 -1
    Goto miqi_trim
    miqi_trim_done:
    StrCmp $R0 "" miqi_cleanup_regroot_unsafe
    StrCmp $R0 "$PROFILE" miqi_cleanup_regroot_unsafe
    StrCmp $R0 "$WINDIR" miqi_cleanup_regroot_unsafe
    StrCmp $R0 "$APPDATA" miqi_cleanup_regroot_unsafe
    StrCmp $R0 "$LOCALAPPDATA" miqi_cleanup_regroot_unsafe
    StrCmp $R0 "$PROGRAMFILES" miqi_cleanup_regroot_unsafe
    ; 长度 < 4（盘根 "C:" 之类）拒绝；IntCmp 参数序：equal/less/more。
    StrLen $R1 $R0
    IntCmp $R1 4 miqi_cleanup_regroot_len_ok miqi_cleanup_regroot_unsafe miqi_cleanup_regroot_len_ok
    miqi_cleanup_regroot_len_ok:
    ; $PROFILE 的祖先目录（如 C:\Users）也拒绝：$R0 是 $PROFILE 的前缀
    ; 且 $PROFILE 的下一个字符是分隔符才算祖先。
    StrLen $R1 $R0
    StrCpy $R3 "$PROFILE" $R1
    StrCmp $R3 $R0 0 miqi_cleanup_regroot_scan
    StrCpy $R3 "$PROFILE" 1 $R1
    StrCmp $R3 "\" miqi_cleanup_regroot_unsafe
    miqi_cleanup_regroot_scan:
    ; 含 "/" 或 ".." 的路径不可能指向已知数据根，拒绝（游标逐位比对）。
    StrCpy $R2 $R0
    StrLen $R1 $R2
    miqi_scan_chars:
    IntCmp $R1 0 miqi_cleanup_regroot_del 0 0
    StrCpy $R3 $R2 1
    StrCmp $R3 "/" miqi_cleanup_regroot_unsafe
    StrCpy $R3 $R2 2
    StrCmp $R3 ".." miqi_cleanup_regroot_unsafe
    StrCpy $R2 $R2 1
    IntOp $R1 $R1 - 1
    Goto miqi_scan_chars
    miqi_cleanup_regroot_del:
    !insertmacro CleanupRemoveDir "$R0" "数据根（注册表 DataRoot）" regroot
    Goto miqi_cleanup_dataroot_done

    miqi_cleanup_regroot_unsafe:
    !insertmacro CleanupLogLine "[跳过] 注册表 DataRoot 值不安全（主目录/系统目录/盘根）：$R0"

    miqi_cleanup_dataroot_done:

    ; 2) Chromium userData  3) 自动更新缓存  4) WSL 沙箱
    !insertmacro CleanupRemoveDir "$APPDATA\@@USERDATA_DIR@@" "应用用户数据" userdata
    !insertmacro CleanupRemoveDir "$LOCALAPPDATA\@@UPDATER_DIR@@" "自动更新缓存" updater
    !insertmacro CleanupWslDistro

    !insertmacro CleanupLogLine "===== 清理结束 ====="
    miqi_cleanup_end:
  !macroend

  ; ── 结果摘要页（INSTFILES 之后）────────────────────────────────────

  !macro customUninstallPage
    UninstPage custom un.CleanupSummaryCreate
  !macroend

  Function un.CleanupSummaryCreate
    !insertmacro MUI_HEADER_TEXT "卸载完成" "清理结果"
    nsDialogs::Create 1018
    Pop $R1
    ${If} $R1 == error
      Abort
    ${EndIf}

    StrCmp $MiqiCleanupRan "1" 0 miqi_summary_notran
    StrCmp $MiqiCleanupFailures "" 0 miqi_summary_failures
    ${NSD_CreateLabel} 0 0u 100% 24u "卸载完成，全部应用数据已清除。清理日志：$TEMP\miqi-uninstall.log"
    Pop $R1
    Goto miqi_summary_end

    miqi_summary_failures:
    ${NSD_CreateLabel} 0 0u 100% 24u "卸载完成，但以下残留未清除（详情见 $TEMP\miqi-uninstall.log）："
    Pop $R1
    ${NSD_CreateLabel} 0 28u 100% 64u "$MiqiCleanupFailures"
    Pop $R1
    Goto miqi_summary_end

    miqi_summary_notran:
    ${NSD_CreateLabel} 0 0u 100% 24u "卸载完成，应用数据已保留（未勾选删除）。"
    Pop $R1

    miqi_summary_end:
    nsDialogs::Show
  FunctionEnd

!endif
