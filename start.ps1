# FraudLens – cài đặt và khởi động bằng một lệnh (Windows, chỉ cần Docker Desktop).
#
#   .\start.ps1                    # tạo .env nếu chưa có, huấn luyện model nếu chưa có, khởi động, mở trình duyệt
#   .\start.ps1 -SampleCards 0.5   # huấn luyện trên 50% số thẻ (máy 8 GB RAM)
#   .\start.ps1 -Monitoring        # bật thêm Prometheus + Grafana
#   .\start.ps1 -SkipTrain         # không huấn luyện dù chưa có model
#   .\start.ps1 -Stop              # tắt hệ thống (giữ dữ liệu)
#
# Mọi bước còn lại (duyệt model, nạp lịch sử, chạy simulator, kiểm tra LLM...) làm trên giao diện web.
param(
    [double]$SampleCards = 0,
    [switch]$Monitoring,
    [switch]$SkipTrain,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nLỖI: $msg" -ForegroundColor Red; exit 1 }

if ($Stop) { docker compose --profile monitoring down; exit $LASTEXITCODE }

Step "Kiểm tra Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Chưa cài Docker Desktop. Tải ở https://www.docker.com/products/docker-desktop/ rồi chạy lại."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dd = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) {
        Write-Host "Docker chưa chạy – đang mở Docker Desktop, chờ tối đa 2 phút..."
        Start-Process $dd
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 2
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { break }
        }
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Docker Engine chưa chạy. Mở Docker Desktop, chờ 'Engine running' rồi chạy lại." }
}
Write-Host "Docker OK"

Step "File cấu hình .env"
if (-not (Test-Path .env)) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $hmac = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    $content = (Get-Content .env.example -Raw -Encoding UTF8) -replace "(?m)^HMAC_KEY=.*$", "HMAC_KEY=$hmac"
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $content, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Đã tạo .env với HMAC_KEY ngẫu nhiên (không đổi khóa này sau khi đã có dữ liệu)."
} else {
    Write-Host ".env đã có – giữ nguyên."
}

Step "Dữ liệu Kaggle"
$train = Test-Path "data\fraudTrain.csv"
$test = Test-Path "data\fraudTest.csv"
if (-not ($train -and $test)) {
    Write-Host "Thiếu data\fraudTrain.csv hoặc data\fraudTest.csv." -ForegroundColor Yellow
    Write-Host "Tải bộ 'kartik2112/fraud-detection' trên Kaggle, giải nén và đặt 2 file vào thư mục data\." -ForegroundColor Yellow
    Write-Host "Hệ thống vẫn khởi động, nhưng chưa huấn luyện / phát lại được."
} else { Write-Host "Đủ 2 file dữ liệu." }

Step "Build image"
docker compose build
if ($LASTEXITCODE -ne 0) { Fail "build thất bại (xem log ở trên)" }

$hasModel = (Get-ChildItem registry -Directory -ErrorAction SilentlyContinue | Where-Object { -not $_.Name.StartsWith("_") }).Count -gt 0
if (-not $hasModel -and -not $SkipTrain) {
    if ($train -and $test) {
        if ($SampleCards -le 0) {
            $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
            $SampleCards = if ($ramGb -ge 15) { 1.0 } else { 0.5 }
            Write-Host "RAM máy ~$ramGb GB -> huấn luyện với --sample-cards $SampleCards"
        }
        Step "Huấn luyện model trong Docker (lần đầu 15–30 phút, đừng đóng cửa sổ)"
        docker compose --profile tools run --rm trainer --sample-cards $SampleCards
        if ($LASTEXITCODE -ne 0) { Fail "huấn luyện thất bại (hết RAM? thử .\start.ps1 -SampleCards 0.3)" }
    } else {
        Write-Host "Bỏ qua huấn luyện: chưa có dữ liệu." -ForegroundColor Yellow
    }
} elseif ($hasModel) { Write-Host "`nĐã có model trong registry – bỏ qua huấn luyện." }

Step "Khởi động dịch vụ"
if ($Monitoring) { docker compose --profile monitoring up -d } else { docker compose up -d }
if ($LASTEXITCODE -ne 0) { Fail "docker compose up thất bại" }

Step "Chờ API sẵn sàng"
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod http://localhost:8000/health -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { Write-Host "API chưa trả lời sau 2 phút – xem: docker compose logs api --tail 50" -ForegroundColor Yellow }

Write-Host "`nFraudLens đang chạy: http://localhost:3000" -ForegroundColor Green
Write-Host "Lần đầu: bấm 'Đăng ký' – tài khoản đầu tiên là Admin. Làm theo checklist ở trang 'Hệ thống'."
Write-Host "Tắt hệ thống: .\start.ps1 -Stop"
Start-Process "http://localhost:3000"
