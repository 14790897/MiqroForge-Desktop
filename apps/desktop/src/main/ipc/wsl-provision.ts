/**
 * Flow orchestration behind `WSL_INSTALL_AND_PROVISION`.
 *
 * Extracted from the IPC handler (#1180) so the part that can only be wrong in
 * *sequence* — never letting a later step run after an earlier one failed, never
 * installing a distro onto a platform WSL itself says cannot start, asking for a
 * reboot only when the machine state calls for one, persisting the phase a
 * reboot interrupts — is exercised by tests instead of by a real machine that
 * can be in each of those states once.
 *
 * Everything that touches the system (the probes, the four elevated runs, the
 * stale-marker read, the persisted phase) arrives as an injected dependency, so
 * this module imports nothing Windows-specific and its tests run anywhere.
 * The IPC handler at the other end is wiring only.
 */
import type {
  WslCheckResult,
  WslInstallAndProvisionResult,
  WslInstallProgress,
} from '../../shared/ipc';
import {
  classifyKernelInstall,
  classifyPlatformRepair,
  summarizeElevated,
  type ElevatedRunResult,
  type FeatureStates,
  type StaleOobeState,
} from './wsl-state';

/**
 * The result of asking for a WSL install on a platform that has none.  Shared
 * with the IPC handler, which refuses non-Windows callers before the flow is
 * even entered so that nothing at all is emitted for them.
 */
export const NOT_WINDOWS_RESULT: WslInstallAndProvisionResult = {
  success: false,
  phase: 'error',
  errorCode: 'NOT_WINDOWS',
  error: 'Not on Windows',
  nextStep: '此功能仅适用于 Windows 系统',
};

/**
 * Whether a persisted install phase has been overtaken by the machine.
 *
 * A usable distro is the end state the whole flow works towards, and every
 * step that asks for a reboot can only run while the distro list is empty — so
 * a phase still on disk next to a live distro is stale by definition: it was
 * written when an earlier run asked for the reboot, and no later run reached
 * Done to clear it.  It has to go, because the page's auto-resume gates on
 * exactly that list being empty — a phase left behind here would launch an
 * elevated install the next time the distro list blinks.
 */
export function installPhaseIsObsolete(check: WslCheckResult): boolean {
  return check.distros.length > 0;
}

export interface ProvisionWslDeps {
  /**
   * Re-derive the live machine state (`wsl --status`, distro list, feature
   * state).  Called at the start and again after every step that may have
   * changed the machine — a cached read would describe a machine that no
   * longer exists.
   */
  probe: () => WslCheckResult;
  /** Step 2: the elevated enable of the two optional features. */
  enableFeatures: () => Promise<ElevatedRunResult>;
  /**
   * Step 2 verification: the raw optional-feature states, read unelevated.
   * Deliberately not part of `probe()` — see the comment at its call site.
   */
  readFeatures: () => FeatureStates;
  /** Step 3: the elevated `wsl --install --no-distribution`. */
  installKernel: () => Promise<ElevatedRunResult>;
  /** Step 4: the elevated `wsl --install -d Ubuntu`. */
  installDistro: () => Promise<ElevatedRunResult>;
  /**
   * Step 3 post-check: whether the kernel package registered.  Retries on its
   * own — the Appx registration lags the elevated process by seconds.
   */
  kernelPresent: () => boolean;
  /** Step 3.5: the elevated stale-servicing repair. */
  repairPlatform: () => Promise<ElevatedRunResult>;
  /** Step 3.5 pre- and post-check: the stale OOBE markers that stall it. */
  readStale: () => StaleOobeState;
  /** Progress for the UI, in the order the flow reaches each phase. */
  emit: (p: WslInstallProgress) => void;
  /**
   * The install phase persisted across a reboot, and its cleanup.  `read` is
   * what makes a repeat visible: a phase seen again while the machine is
   * unchanged means the reboot hand-off did not move anything.
   */
  state: {
    read(): { phase: string; at: number } | null;
    write(phase: string): void;
    clear(): void;
  };
}

export async function provisionWsl(deps: ProvisionWslDeps): Promise<WslInstallAndProvisionResult> {
  const {
    probe,
    enableFeatures,
    readFeatures,
    installKernel,
    installDistro,
    kernelPresent,
    repairPlatform,
    readStale,
    emit,
    state,
  } = deps;

  try {
    // ── Step 1: Check ───────────────────────────────────────────────
    emit({ phase: 'checking', message: '正在检测 WSL 状态...' });

    // Every step below re-derives what is still missing from the live system
    // state rather than from the persisted phase: the machine state is the
    // only thing that stays true across a reboot.
    let check = probe();

    if (!check.isWindows) return NOT_WINDOWS_RESULT;

    // ── Step 2: not-enabled → DISM enable features ──────────────────
    if (check.featureState === 'not-enabled') {
      emit({
        phase: 'enabling_features',
        message: '正在启用 Windows 可选功能 (WSL + 虚拟机平台)...',
      } satisfies WslInstallProgress);

      // The elevated process runs Enable-WindowsOptionalFeature and reports
      // its own output/exit code through the trampoline files: a declined
      // UAC prompt used to be indistinguishable from a DISM failure here.
      const r = await enableFeatures();

      if (r.kind === 'cancelled') {
        emit({
          phase: 'error',
          message: '启用 Windows 功能被取消：管理员权限请求被拒绝',
          error: 'ELEVATION_CANCELLED',
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'ELEVATION_CANCELLED',
          error: '启用 Windows 功能被取消',
          nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
        } satisfies WslInstallAndProvisionResult;
      }

      // Success is judged from the optional features themselves, not from
      // `probe()`:
      // - `wsl --status` cannot answer the question here.  Enabling the
      //   features does not install WSL, so at this point it still fails, and
      //   the probe's feature classification cannot tell the expected outcome
      //   (features on, kernel not installed yet → a reboot is what continues
      //   the flow) apart from the elevated run having changed nothing.  Only
      //   the optional-feature states distinguish those two.
      // - VirtualMachinePlatform is intentionally not required: on machines
      //   with VBS/Core Isolation, WMI keeps VMP reported as Disabled while
      //   it is functional (observed in live testing) — gating on it would
      //   recreate the false-failure bug this step was fixed for.
      // So the enable counts as done only on a successful read with the WSL
      // feature on; everything else keeps the previous step's error path.
      const featuresAfter = readFeatures();
      if (!featuresAfter.ok || !featuresAfter.featureWsl) {
        // A failed DISM cmdlet leaves the exit code at 0, so the captured
        // output is the only place the real reason appears — fall back to the
        // generic text only when the elevated run produced nothing at all.
        const produced = r.kind === 'unknown' || r.exitCode !== 0 || r.output.trim().length > 0;
        const detail = produced ? summarizeElevated(r) : '功能状态未变化';
        emit({
          phase: 'error',
          message: `启用 Windows 功能失败: ${detail}`,
          error: detail,
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'FEATURE_ENABLE_FAILED',
          error: '无法启用 Windows 可选功能',
          nextStep: '以管理员身份打开 PowerShell 并运行: wsl --install',
        } satisfies WslInstallAndProvisionResult;
      }

      state.write('features_enabled');

      emit({
        phase: 'enabling_features',
        rebootRequired: true,
        message: 'Windows 功能已启用。需要重启系统，重启后 MiQroForge 将自动继续安装。',
      } satisfies WslInstallProgress);

      return {
        success: true,
        phase: 'enabling_features',
        rebootRequired: true,
        nextStep: '请重启系统；重启后进入「WSL 状态监控」，安装会自动继续',
      } satisfies WslInstallAndProvisionResult;
    }

    // ── Step 3: not-installed → wsl --install --no-distribution ─────
    if (check.featureState === 'not-installed') {
      emit({
        phase: 'installing_wsl',
        message: '正在安装 WSL2 内核...',
      } satisfies WslInstallProgress);

      const r = await installKernel();

      // Only ask the system when the elevated run itself was inconclusive:
      // exit code 0 already proves the install, and a declined UAC prompt
      // proves nothing was attempted, so probing would just add latency.
      const present = r.kind === 'failed' || r.kind === 'unknown' ? kernelPresent() : false;
      const outcome = classifyKernelInstall(r, present);

      if (outcome.status === 'cancelled') {
        emit({
          phase: 'error',
          message: 'WSL2 内核安装被取消：管理员权限请求被拒绝',
          error: 'ELEVATION_CANCELLED',
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'ELEVATION_CANCELLED',
          error: 'WSL2 内核安装被取消',
          nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
        } satisfies WslInstallAndProvisionResult;
      }

      if (outcome.status === 'failed') {
        emit({
          phase: 'error',
          message: `WSL2 内核安装失败: ${outcome.detail}`,
          error: outcome.detail,
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'KERNEL_INSTALL_FAILED',
          error: `WSL2 内核安装失败: ${outcome.detail}`,
          nextStep: '以管理员身份打开 PowerShell 并运行: wsl --install --no-distribution',
        } satisfies WslInstallAndProvisionResult;
      }

      state.write('kernel_installed');

      emit({
        phase: 'installing_wsl',
        rebootRequired: true,
        message: 'WSL2 内核安装完成。需要重启系统以继续。',
      } satisfies WslInstallProgress);

      return {
        success: true,
        phase: 'installing_wsl',
        rebootRequired: true,
        nextStep: '请重启系统；重启后进入「WSL 状态监控」，安装会自动继续',
      } satisfies WslInstallAndProvisionResult;
    }

    // ── Step 3.5: platform unusable → repair the deferred servicing ──
    // WSL itself reports that WSL2 cannot start.  The one cause this app can
    // repair is the stale OOBE marker set: it makes Windows abort every
    // startup servicing pass, so the queued 「虚拟机平台」 payload never lands.
    // Without those markers (e.g. firmware virtualization switched off) a
    // reboot fixes nothing, and asking for one would only repeat forever.
    if (check.distros.length === 0 && check.featureState !== 'ready' && check.platformIssue) {
      const staleBefore = readStale();
      if (!staleBefore.ok || !staleBefore.stale) {
        emit({
          phase: 'error',
          message: `WSL2 平台无法启动：${check.platformIssue}`,
          error: check.platformIssue,
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'PLATFORM_NOT_READY',
          error: check.platformIssue,
          nextStep:
            '请以管理员身份运行: DISM /Online /Enable-Feature /FeatureName:VirtualMachinePlatform /All 后重启；若固件（BIOS）里虚拟化未开启，请先开启它',
        } satisfies WslInstallAndProvisionResult;
      }

      emit({
        phase: 'enabling_features',
        message: '检测到被推迟的系统组件安装，正在修复...',
      } satisfies WslInstallProgress);

      const repair = await repairPlatform();

      if (repair.kind === 'cancelled') {
        emit({
          phase: 'error',
          message: '修复系统组件安装被取消：管理员权限请求被拒绝',
          error: 'ELEVATION_CANCELLED',
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'ELEVATION_CANCELLED',
          error: '修复系统组件安装被取消',
          nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
        } satisfies WslInstallAndProvisionResult;
      }

      // What decides the next step is whether the platform became usable, not
      // the exit code: the repair can apply the queued payload outright (as
      // observed on the #1171 machine, where DISM finished the pending
      // transaction and `vmcompute` came up) — then no reboot is needed and
      // the flow continues with the distro install.
      check = probe();
      const outcome = classifyPlatformRepair({
        repair,
        platformIssueAfter: check.platformIssue ?? null,
        staleAfter: readStale(),
      });

      if (outcome.status === 'continue') {
        emit({
          phase: 'enabling_features',
          message: '系统组件已安装完成，继续安装发行版...',
        } satisfies WslInstallProgress);
      } else if (outcome.status === 'reboot-required') {
        state.write('platform_repair_pending');

        emit({
          phase: 'enabling_features',
          rebootRequired: true,
          message: '已重新提交「虚拟机平台」安装，需要重启系统完成。',
        } satisfies WslInstallProgress);

        return {
          success: true,
          phase: 'enabling_features',
          rebootRequired: true,
          nextStep: '请重启系统（关机后再开机更稳妥）；重启后进入「WSL 状态监控」，安装会自动继续',
        } satisfies WslInstallAndProvisionResult;
      } else {
        // Nothing verifiable happened and the platform is still unusable:
        // stop here rather than install a distro that cannot register.
        emit({
          phase: 'error',
          message: `WSL2 平台修复失败: ${outcome.detail}`,
          error: outcome.detail,
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'PLATFORM_REPAIR_FAILED',
          error: `WSL2 平台修复失败: ${outcome.detail}`,
          nextStep:
            '请以管理员身份运行: DISM /Online /Enable-Feature /FeatureName:VirtualMachinePlatform /All，然后重启；仍不行请在「设置 → Windows 更新」安装全部更新后重试',
        } satisfies WslInstallAndProvisionResult;
      }
    }

    // ── Step 4: no distro → install Ubuntu ───────────────────────────
    if (check.distros.length === 0 && check.featureState !== 'ready') {
      emit({
        phase: 'installing_distro',
        message: '正在下载并安装 Ubuntu 发行版（网络较慢时可能要十几分钟）...',
      } satisfies WslInstallProgress);

      const r = await installDistro();

      if (r.kind === 'cancelled') {
        emit({
          phase: 'error',
          message: 'Ubuntu 安装被取消：管理员权限请求被拒绝',
          error: 'ELEVATION_CANCELLED',
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'ELEVATION_CANCELLED',
          error: 'Ubuntu 发行版安装被取消',
          nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
        } satisfies WslInstallAndProvisionResult;
      }

      const postCheck = probe();
      if (postCheck.distros.length === 0) {
        // Nothing registered yet — the exit code decides what that means:
        // 0 is "installed, reboot pending" (as with the kernel step), a
        // non-zero code is a real install failure, and no code at all (the run
        // timed out or the trampoline was lost) is only "unconfirmed".
        if (r.kind === 'ok') {
          if (postCheck.platformIssue) {
            // Exit code 0 and still no distro, but WSL itself says the
            // platform cannot start — no reboot will change that, so report
            // what WSL actually said instead of promising one.
            emit({
              phase: 'error',
              message: `WSL2 平台无法启动：${postCheck.platformIssue}`,
              error: postCheck.platformIssue,
            } satisfies WslInstallProgress);
            return {
              success: false,
              phase: 'error',
              errorCode: 'PLATFORM_NOT_READY',
              error: postCheck.platformIssue,
              nextStep:
                '请在「设置 → Windows 更新」安装全部更新后重启；仍不行请以管理员身份运行: DISM /Online /Cleanup-Image /RestoreHealth 后重启',
            } satisfies WslInstallAndProvisionResult;
          }

          // The phase already on disk says the previous round ended in exactly
          // the state this one is about to produce: install reported success,
          // the reboot was taken (the page resumes while the distro list is
          // empty), and the distro still never registered.  Asking for yet
          // another reboot would only repeat the cycle — and because every
          // write refreshes the phase's timestamp, the 24-hour resume limit
          // cannot end it either.  WSL's own docs say `--no-launch` registers
          // the distro once the install completes, so a repeat here points at
          // a real problem rather than at a missing reboot.  Clearing the phase
          // is what actually stops it: the resume fires on an empty distro
          // list alone, so leaving it behind would re-run this elevated
          // install on every visit to the page.
          if (state.read()?.phase === 'distro_installed') {
            state.clear();
            emit({
              phase: 'error',
              message: `Ubuntu 已安装，但发行版始终未注册（上一次尝试同样如此）`,
              error: 'DISTRO_NOT_REGISTERED',
            } satisfies WslInstallProgress);
            return {
              success: false,
              phase: 'error',
              errorCode: 'DISTRO_NOT_REGISTERED',
              error: 'Ubuntu 已安装，但发行版始终未注册',
              nextStep:
                '请以管理员身份打开 PowerShell 运行: wsl --install -d Ubuntu，并按提示完成首次启动（创建用户名与密码）；仍不见发行版请在「设置 → Windows 更新」安装全部更新后重试',
            } satisfies WslInstallAndProvisionResult;
          }

          state.write('distro_installed');

          emit({
            phase: 'installing_distro',
            rebootRequired: true,
            message: 'Ubuntu 已安装，需要重启系统以继续。',
          } satisfies WslInstallProgress);
          return {
            success: true,
            phase: 'installing_distro',
            rebootRequired: true,
            nextStep: '请重启系统；重启后进入「WSL 状态监控」，安装会自动继续',
          } satisfies WslInstallAndProvisionResult;
        }

        const detail = summarizeElevated(r);

        if (r.kind === 'unknown') {
          // No exit code came back at all — the run timed out or was lost, so
          // the install is neither confirmed nor refuted.  It was observed to
          // finish in the background long after the app stopped waiting, which
          // is why this outcome must not wear the failure's wording.
          emit({
            phase: 'error',
            message: `无法确认 Ubuntu 安装结果: ${detail}`,
            error: detail,
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'DISTRO_INSTALL_UNCONFIRMED',
            error: `无法确认 Ubuntu 发行版安装结果: ${detail}`,
            nextStep:
              '安装可能仍在后台进行：稍等片刻后点上方刷新按钮查看；长时间没有变化再以管理员身份运行: wsl --install -d Ubuntu',
          } satisfies WslInstallAndProvisionResult;
        }

        emit({
          phase: 'error',
          message: `Ubuntu 安装失败: ${detail}`,
          error: detail,
        } satisfies WslInstallProgress);
        return {
          success: false,
          phase: 'error',
          errorCode: 'DISTRO_INSTALL_FAILED',
          error: `Ubuntu 发行版安装失败: ${detail}`,
          // A slow download keeps running after the app stops waiting, so the
          // first thing to try is a refresh rather than a manual reinstall.
          nextStep:
            '若网络较慢，安装可能仍在后台进行：稍等片刻后点上方刷新按钮查看；否则以管理员身份运行: wsl --install -d Ubuntu',
        } satisfies WslInstallAndProvisionResult;
      }
    }

    // ── Done ────────────────────────────────────────────────────────
    state.clear();
    emit({
      phase: 'complete',
      message: 'WSL2 安装配置完成！',
    } satisfies WslInstallProgress);

    return { success: true, phase: 'complete' } satisfies WslInstallAndProvisionResult;
  } catch (e: any) {
    emit({
      phase: 'error',
      message: `出错: ${e?.message ?? e}`,
      error: e?.message ?? String(e),
    } satisfies WslInstallProgress);
    return {
      success: false,
      phase: 'error',
      error: e?.message ?? String(e),
      errorCode: 'UNKNOWN',
      nextStep: 'https://learn.microsoft.com/windows/wsl/install',
    } satisfies WslInstallAndProvisionResult;
  }
}
