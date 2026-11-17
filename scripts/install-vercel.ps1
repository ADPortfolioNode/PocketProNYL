Write-Host "Installing Vercel CLI globally (requires Node/npm)..."
npm install -g vercel
if ($LASTEXITCODE -eq 0) {
  Write-Host "Vercel CLI installed. Run 'vercel login' to sign in and 'vercel link' to link the project." -ForegroundColor Green
} else {
  Write-Host "Vercel install failed. Try running the command manually as Administrator or use 'npm i -g vercel'" -ForegroundColor Red
}
