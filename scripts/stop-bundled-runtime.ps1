param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeDirectory,
    [string]$LegacyRuntimeDirectory = (Join-Path $env:LOCALAPPDATA 'instant2\llm-runtime\llama')
)

$runtimePrefixes = @($RuntimeDirectory, $LegacyRuntimeDirectory) |
    Where-Object { $_ } |
    ForEach-Object {
        [System.IO.Path]::GetFullPath($_).TrimEnd('\') + '\'
    }
$matchingProcesses = Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
    Where-Object {
        $executablePath = $_.ExecutablePath
        if (-not $executablePath) { return $false }
        foreach ($runtimePrefix in $runtimePrefixes) {
            if ($executablePath.StartsWith(
                    $runtimePrefix,
                    [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
        return $false
    }

foreach ($matchingProcess in $matchingProcesses) {
    Stop-Process -Id $matchingProcess.ProcessId -Force -ErrorAction SilentlyContinue
}
