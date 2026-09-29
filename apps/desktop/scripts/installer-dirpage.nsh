; ============================================================================
; #1176 自定义安装路径：目录页校验
;
; 背景：electron-builder.yml 开启 allowToChangeInstallationDirectory 后，按用户
; （免 UAC）安装时用户可把目录改到 C:\Program Files 等需要管理员权限的位置，
; 后续免 UAC 覆盖升级会复现 #1124 的「Error writing to file」死循环。
; 本脚本在目录页「下一步」时校验：按用户模式 + 受保护目录 → 拦截并提示。
;
; 机制说明（与 NSIS 编译顺序强相关）：
; - 本文件经 electron-builder 的 nsis.include 注入，注入点在模板 body 之前。
; - 因此这里 !define 的 MUI_PAGE_CUSTOMFUNCTION_LEAVE 会被后面的
;   MUI_PAGE_DIRECTORY（目录页）拾取为「下一步」回调。
; - DirectoryLeaveValidate 用到 $installMode，而该 Var 由 multiUser.nsh 声明
;   （晚于本文件）。NSIS 的 Var 不允许前向引用（已实测），所以把该函数放进
;   customPageAfterChangeDir 宏——该宏在模板 body（含 multiUser.nsh）之后才展开，
;   展开时 $installMode 已声明。NSIS 允许对后定义的函数做 Call（已实测）。
; - 升级时目录页被 skipPageIfUpdated 跳过，leave 回调不执行 → 不拦截原地覆盖升级。
;
; 全部内容限定在安装器编译（!ifndef BUILD_UNINSTALLER）内：
; - 卸载器编译时会重新处理本文件；若不加限定，MUI_PAGE_CUSTOMFUNCTION_LEAVE 会
;   泄漏进 MUI_UNPAGE_WELCOME 等卸载页面，生成「在卸载段 Call 非 un. 函数」的非法
;   脚本（编译失败）；而仅定义的函数又会触发 6010「函数未引用」警告（-WX 视为错误）。
; ============================================================================

!ifndef BUILD_UNINSTALLER

  ; 仅本脚本使用的受保护根变量（独特前缀，避免与 electron-builder 的 Var 冲突）。
  Var forgeProtectedRoot

  ; 目录页「下一步」回调函数名（必须早于 MUI_PAGE_DIRECTORY 展开前定义）。
  !define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirectoryLeaveValidate

  ; 判断 $INSTDIR 是否等于 $forgeProtectedRoot，或以「$forgeProtectedRoot\」开头。
  ; StrCmp 天然大小写不敏感，无需手动转小写。
  ; 返回 $0：1 = 受保护，0 = 否。
  Function PathUnderRoot
    StrCpy $0 "0"

    ; 情况 1：恰好等于根（如直接把目录选成 C:\Program Files）
    StrCmp "$INSTDIR" "$forgeProtectedRoot" is_equal
    Goto check_prefix
    is_equal:
      StrCpy $0 "1"
      Return

    check_prefix:
    ; 情况 2：以「根 + 反斜杠」开头（如 C:\Program Files\MiQroForge Desktop）
    StrLen $1 "$forgeProtectedRoot"
    IntOp $1 $1 + 1
    StrCpy $2 "$INSTDIR" $1
    StrCmp $2 "$forgeProtectedRoot\" is_prefix
    Goto done
    is_prefix:
      StrCpy $0 "1"
    done:
    Return
  FunctionEnd

  ; 判断 $INSTDIR 是否落在任一受保护根（Program Files / Windows）之下。
  ; 返回 $0：1 = 需管理员（按用户免 UAC 无法写入），0 = 可写。
  Function IsProtectedDir
    StrCpy $forgeProtectedRoot "$PROGRAMFILES64"
    StrCmp "$forgeProtectedRoot" "" check_pf   ; 32 位系统上 $PROGRAMFILES64 为空，跳过
    Call PathUnderRoot
    StrCmp $0 "1" found check_pf
    check_pf:

    StrCpy $forgeProtectedRoot "$PROGRAMFILES"
    Call PathUnderRoot
    StrCmp $0 "1" found check_win
    check_win:

    StrCpy $forgeProtectedRoot "$WINDIR"
    Call PathUnderRoot
    StrCmp $0 "1" found not_found

    found:
      StrCpy $0 "1"
      Return
    not_found:
      StrCpy $0 "0"
      Return
  FunctionEnd

  ; DirectoryLeaveValidate 定义在 customPageAfterChangeDir 宏里，展开时机在模板 body
  ; 之后（multiUser.nsh 已声明 $installMode），从而绕开 NSIS Var 前向引用限制。
  !macro customPageAfterChangeDir
    Function DirectoryLeaveValidate
      ; 仅「按用户」模式校验；「所有用户」模式（$installMode = "all"）已提权，可写 Program Files。
      StrCmp "$installMode" "CurrentUser" is_currentuser
      Goto done
      is_currentuser:
        Call IsProtectedDir
        StrCmp $0 "1" is_protected
        Goto done
        is_protected:
          MessageBox MB_OK|MB_ICONEXCLAMATION|MB_TOPMOST "无法安装到所选目录：该位置需要管理员权限，按用户模式安装后无法免 UAC 覆盖升级。$\r$\n$\r$\n请返回上一步选择「为所有用户安装」，或改选当前用户可写的位置（如其它盘符）。$\r$\n$\r$\nThis location requires administrator privileges and cannot be auto-updated without UAC in per-user mode. Go back and choose 'Install for all users', or pick a per-user writable directory."
          Abort   ; 留在目录页，让用户改路径或返回上一页改安装模式
      done:
    FunctionEnd
  !macroend

!endif
