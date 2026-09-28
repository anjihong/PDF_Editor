param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference = 'Stop'
$stage = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Manifest))
$approved = Join-Path $stage 'approved'
$cancelled = Join-Path $stage 'cancelled'
$backup = Join-Path $stage 'previous.exe'
$incoming = Join-Path $stage 'new.exe'
$preserve = $false
$config = $null
function Write-UpdateLog([string]$message) {
    if ($config.log) {
        try {
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($config.log)) | Out-Null
            [IO.File]::AppendAllText($config.log, "$(Get-Date -Format s) $message`r`n")
        } catch { }
    }
}
try {
    $config = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $target = [IO.Path]::GetFullPath($config.target)
    # Restrict all replacement/cleanup paths to this unique sibling staging folder.
    if ((Split-Path $stage -Leaf) -notlike '.pdfeditor-update-*' -or
        [IO.Path]::GetDirectoryName($stage) -ne [IO.Path]::GetDirectoryName($target) -or
        [IO.Path]::GetExtension($target) -ne '.exe') { throw 'Invalid update paths.' }
    $editor = [Diagnostics.Process]::GetProcessById([int]$config.pid)
    $bootloader = $null
    if ($config.parent_pid) {
        try { $bootloader = [Diagnostics.Process]::GetProcessById([int]$config.parent_pid) }
        catch [ArgumentException] { } # The parent may already have exited.
        if ($bootloader) {
            $null = $bootloader.Handle # Keep the original process handle, not just a reusable PID.
            if ([IO.Path]::GetFullPath($bootloader.MainModule.FileName) -ne $target) {
                throw 'The parent process is not the editor bootloader.'
            }
        }
    }
    [IO.File]::WriteAllText((Join-Path $stage 'ready'), '')
    # No approval means no replacement, even when the editor crashes or is closed.
    while (-not [IO.File]::Exists($approved)) {
        if ([IO.File]::Exists($cancelled) -or $editor.HasExited) { exit 0 }
        Start-Sleep -Milliseconds 100
    }
    if ([IO.File]::Exists($cancelled)) { exit 0 }
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    if (-not $editor.WaitForExit(60000)) { throw 'The editor did not exit within 60 seconds.' }
    # Onefile's parent still maps the old EXE while deleting its extracted files.
    if ($bootloader) {
        $remaining = [Math]::Max(0, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (-not $bootloader.WaitForExit($remaining)) { throw 'The editor bootloader did not exit within 60 seconds.' }
    }
    $stream = [IO.File]::OpenRead($incoming)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
    if ((Get-Item -LiteralPath $incoming).Length -ne $config.size -or $digest -ne $config.sha256) {
        throw 'Downloaded executable verification failed.'
    }
    while ($true) {
        try {
            [IO.File]::Replace($incoming, $target, $backup)
            break
        } catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $target
    $start.WorkingDirectory = [IO.Path]::GetDirectoryName($target)
    $start.UseShellExecute = $false
    # A fresh PyInstaller bootloader must not reuse the old extraction directory.
    $start.EnvironmentVariables['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    if ($config.pdf) { $start.Arguments = '"' + $config.pdf + '"' }
    $launched = [Diagnostics.Process]::Start($start)
    if ($null -eq $launched) { throw 'Could not start the updated editor.' }
} catch {
    $message = $_.Exception.Message
    if ([IO.File]::Exists($backup)) {
        try {
            if ([IO.File]::Exists($target)) { [IO.File]::Replace($backup, $target, (Join-Path $stage 'failed.exe')) }
            else { [IO.File]::Move($backup, $target) }
        }
        catch { $preserve = $true; $message += "`nRestore failed. Backup: $backup`n$($_.Exception.Message)" }
    }
    Write-UpdateLog $message
    # Startup failures are reported by the still-running editor, not a second dialog.
    if ([IO.File]::Exists($approved)) {
        Add-Type -AssemblyName System.Windows.Forms
        $notice = '업데이트에 실패했습니다. 기존 실행 파일을 유지하거나 복구했습니다.'
        if ($preserve) { $notice = '업데이트 복구에 실패했습니다. 아래 백업 파일을 보존했습니다.' }
        [Windows.Forms.MessageBox]::Show("$notice`n$message", 'PDF 편집기 업데이트') | Out-Null
    }
    exit 1
} finally {
    if (-not $preserve -and (Split-Path $stage -Leaf) -like '.pdfeditor-update-*' -and
        $config -and [IO.Path]::GetDirectoryName($stage) -eq [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($config.target))) {
        $cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10)
        $cleanupRetried = $false
        while ([IO.Directory]::Exists($stage)) {
            try {
                Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction Stop
                if ($cleanupRetried) { Write-UpdateLog "Cleanup succeeded after retry: $stage" }
                break
            } catch {
                if (-not [IO.Directory]::Exists($stage)) { break }
                if (-not $cleanupRetried) {
                    Write-UpdateLog "Cleanup retry: $stage; $($_.Exception.GetType().FullName); $($_.Exception.Message)"
                    $cleanupRetried = $true
                }
                if ([DateTime]::UtcNow -ge $cleanupDeadline) {
                    Write-UpdateLog "Cleanup failed after 10 seconds: $stage; $($_.Exception.Message)"
                    break
                }
                Start-Sleep -Milliseconds 250
            }
        }
    }
}
