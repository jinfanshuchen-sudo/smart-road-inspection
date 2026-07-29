$ErrorActionPreference = 'Stop'

$source = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.docx' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $source) {
    throw 'Review DOCX was not found.'
}

$outputDir = Join-Path $PSScriptRoot 'word_pdf_review'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$pdfPath = Join-Path $outputDir 'review.pdf'

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0

try {
    $document = $word.Documents.Open($source.FullName, $false, $true)
    $document.ExportAsFixedFormat($pdfPath, 17)
    $document.Close(0)
}
finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}

Write-Output $pdfPath
