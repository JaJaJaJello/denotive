# Reads text from pictures with Windows' built-in OCR engine (Windows.Media.Ocr).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File winocr.ps1 -List files.txt
# files.txt holds one picture path per line; prints JSON: [{"file", "text", "lines": [{"text", "words": [{"text",
# "x", "y", "w", "h"}]}]}] in the same order.
param([Parameter(Mandatory = $true)][string]$List)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Wait-Op($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(-1)
    $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw "No OCR language is installed in Windows" }
$out = @()
foreach ($path in Get-Content -LiteralPath $List -Encoding UTF8) {
    if (-not $path) { continue }
    $entry = @{ file = $path; text = ""; lines = @() }
    try {
        $file = Wait-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Wait-Op ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Wait-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Wait-Op ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $entry.text = $result.Text
        $entry.lines = @($result.Lines | ForEach-Object {
                @{ text = $_.Text; words = @($_.Words | ForEach-Object {
                            @{ text = $_.Text; x = $_.BoundingRect.X; y = $_.BoundingRect.Y
                               w = $_.BoundingRect.Width; h = $_.BoundingRect.Height } }) } })
        $stream.Dispose()
    } catch {
        $entry.error = $_.Exception.Message
    }
    $out += $entry
}
ConvertTo-Json -InputObject @($out) -Depth 6 -Compress
