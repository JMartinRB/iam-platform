# Crea el archivo .env a partir de .env.example, pidiendo la clave sin mostrarla.
# La clave nunca se escribe en pantalla ni queda en el historial de la consola.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.env.example")) {
    Write-Host "ERROR: no se encontro .env.example en esta carpeta." -ForegroundColor Red
    Read-Host "Enter para salir"
    exit 1
}

Write-Host ""
Write-Host "=============================================="
Write-Host " Configuracion de la clave del modelo"
Write-Host "=============================================="
Write-Host ""
Write-Host "Pega la clave de Google AI Studio y presiona Enter."
Write-Host "No se va a ver mientras la pegas. Eso es normal."
Write-Host ""

$segura = Read-Host -Prompt "Clave" -AsSecureString
$puntero = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
try {
    $clave = [Runtime.InteropServices.Marshal]::PtrToStringAuto($puntero)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntero)
}

if ([string]::IsNullOrWhiteSpace($clave)) {
    Write-Host ""
    Write-Host "No se recibio ninguna clave. No se creo nada." -ForegroundColor Yellow
    Read-Host "Enter para salir"
    exit 1
}

$texto = Get-Content ".\.env.example" -Raw
$texto = $texto.Replace("LLM_PROVEEDOR=stub", "LLM_PROVEEDOR=gemini")
$texto = $texto.Replace("LLM_API_KEY=", "LLM_API_KEY=" + $clave.Trim())
Set-Content -Path ".\.env" -Value $texto -Encoding UTF8

$clave = $null
[System.GC]::Collect()

Write-Host ""
Write-Host "Archivo .env creado." -ForegroundColor Green
Write-Host ""

# Verificacion: se confirma que la clave esta, sin mostrarla.
$linea = (Get-Content ".\.env" | Where-Object { $_ -like "LLM_API_KEY=*" })
$largo = $linea.Length - "LLM_API_KEY=".Length
Write-Host "Verificacion:"
Write-Host ("  proveedor : " + ((Get-Content ".\.env" | Where-Object { $_ -like "LLM_PROVEEDOR=*" })))
Write-Host ("  clave     : " + $largo + " caracteres guardados")
Write-Host ""

# Confirmacion de que git no lo va a subir.
$ignorado = & git check-ignore ".env" 2>$null
if ($ignorado) {
    Write-Host "git ignora el archivo .env: la clave no se va a subir." -ForegroundColor Green
} else {
    Write-Host "ATENCION: git NO esta ignorando .env. No subas nada y avisale a Claude." -ForegroundColor Red
}

Write-Host ""
Write-Host "Siguiente paso, desde la carpeta backend:"
Write-Host "  python -m herramientas.medir_generacion --casos 3"
Write-Host ""
Read-Host "Enter para cerrar"
