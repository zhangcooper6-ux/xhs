$conn = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "已停止 5000 端口服务 (PID: $($conn.OwningProcess))"
} else {
    Write-Host "未发现 5000 端口占用服务"
}
