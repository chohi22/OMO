const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const net = require('net');
const { exec } = require('child_process');
const util = require('util');

const execPromise = util.promisify(exec);

// CLI 인자 파싱 함수
function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    targetPort: null,
    help: false
  };
  
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--target' || arg === '-t') {
      options.targetPort = parseInt(args[++i], 10);
    } else if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (arg.startsWith('--target=')) {
      options.targetPort = parseInt(arg.split('=')[1], 10);
    } else if (!arg.startsWith('-') && !options.targetPort) {
      // 숫자만 입력된 경우 타겟 포트로 간주
      const port = parseInt(arg, 10);
      if (!isNaN(port)) {
        options.targetPort = port;
      }
    }
  }
  
  // 환경변수로도 설정 가능
  if (!options.targetPort && process.env.PROXY_TARGET_PORT) {
    options.targetPort = parseInt(process.env.PROXY_TARGET_PORT, 10);
  }
  
  return options;
}

const cliOptions = parseArgs();

// 도움말 출력
if (cliOptions.help) {
  console.log(`
Usage: node server.js [options] [target-port]

Options:
  -t, --target <port>    Set proxy target port (required)
  -h, --help            Show this help message

Environment Variables:
  PROXY_TARGET_PORT     Set proxy target port

Examples:
  node server.js -t 3000
  node server.js 3000
  PROXY_TARGET_PORT=3000 node server.js
`);
  process.exit(0);
}

// 타겟 포트 필수 체크
if (!cliOptions.targetPort || cliOptions.targetPort < 1 || cliOptions.targetPort > 65535) {
  console.error('Error: Target port is required. Use --target <port> or set PROXY_TARGET_PORT');
  console.error('Run with --help for usage information');
  process.exit(1);
}

const app = express();
const PORT = 8888;

// CLI로 설정된 타겟 포트
const configuredTarget = `http://localhost:${cliOptions.targetPort}`;
let currentTarget = configuredTarget;
let currentTargetName = `Port ${cliOptions.targetPort}`;

console.log(`[Proxy] Target configured: ${currentTarget}`);

// 시스템 예약 포트 (0-1023) 및 일반적인 시스템 서비스 포트
const SYSTEM_RESERVED_PORTS = new Set([
  0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 67, 68,
  69, 70, 79, 80, 88, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 118,
  119, 123, 129, 135, 137, 138, 139, 143, 152, 161, 162, 170, 179, 194, 201,
  209, 210, 213, 220, 443, 445, 515, 631, 3306, 5432, 8443
]);

// 현재 사용자가 소유한 프로세스의 포트 목록을 가져오는 함수
async function getUserOwnedPorts() {
  const userPorts = new Set();
  const uid = process.getuid ? process.getuid() : null;
  
  try {
    // Linux/Mac: lsof 명령어로 현재 사용자의 포트 가져오기
    const { stdout } = await execPromise('lsof -iTCP -sTCP:LISTEN -P -n 2>/dev/null || netstat -tln 2>/dev/null || ss -tln 2>/dev/null');
    const lines = stdout.split('\n');
    
    for (const line of lines) {
      // 포트 번호 추출 (IPv4와 IPv6 모두 처리)
      const match = line.match(/:(\d+).*?\s+\(LISTEN\)|:(\d+).*?\s*$/);
      if (match) {
        const port = parseInt(match[1] || match[2], 10);
        if (port && port > 1023) { // 시스템 예약 포트 제외
          userPorts.add(port);
        }
      }
    }
  } catch (error) {
    console.log('[Warning] Could not get user-owned ports:', error.message);
  }
  
  return userPorts;
}

async function checkPort(host, port, timeout = 500) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(timeout);
    socket.on('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.on('timeout', () => {
      socket.destroy();
      resolve(false);
    });
    socket.on('error', () => {
      socket.destroy();
      resolve(false);
    });
    socket.connect(port, host);
  });
}

async function scanLocalPorts(startPort = 3000, endPort = 9000, options = {}) {
  const { 
    excludeSystemPorts = true,  // 시스템 예약 포트 제외
    userOwnedOnly = false       // 현재 사용자 프로세스만 표시
  } = options;
  
  const activePorts = [];
  const priorityPorts = [3000, 3001, 4000, 5000, 5001, 5173, 5174, 8081, 9000];
  
  // 사용자 소유 포트 목록 가져오기 (userOwnedOnly가 true일 때만)
  let userOwnedPorts = null;
  if (userOwnedOnly) {
    userOwnedPorts = await getUserOwnedPorts();
    console.log(`[Scan] Found ${userOwnedPorts.size} user-owned ports`);
  }
  
  // 포트 필터링 함수
  const shouldIncludePort = (port) => {
    // 자체 포트는 제외
    if (port === PORT) return false;
    
    // 시스템 예약 포트 제외
    if (excludeSystemPorts && SYSTEM_RESERVED_PORTS.has(port)) {
      return false;
    }
    
    // 사용자 소유 포트만 표시
    if (userOwnedOnly && userOwnedPorts && !userOwnedPorts.has(port)) {
      return false;
    }
    
    return true;
  };
  
  // 우선순위 포트 먼저 체크
  for (const port of priorityPorts) {
    if (shouldIncludePort(port) && await checkPort('localhost', port)) {
      activePorts.push(port);
    }
  }
  
  // 나머지 포트 스캔
  for (let port = startPort; port <= endPort; port++) {
    if (!priorityPorts.includes(port) && shouldIncludePort(port)) {
      if (await checkPort('localhost', port)) {
        activePorts.push(port);
      }
    }
  }
  
  return activePorts.sort((a, b) => a - b);
}

function generateStatusHTML() {
  const currentTargetDisplay = currentTarget 
    ? `<div class="current-target" style="background: #f0fdf4; border-color: #10b981;">
        <div style="font-size: 16px; margin-bottom: 8px;">현재 프록시 설정</div>
        <div style="font-size: 24px; font-weight: bold; color: #059669;">${currentTarget}</div>
        <div style="font-size: 14px; color: #059669; margin-top: 8px;">● 설정됨</div>
       </div>`
    : '<div class="current-target none">프록시가 설정되지 않았습니다</div>';

  return `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Local Proxy Selector</title>
  <style>
    .status-info {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .info-item {
      display: flex;
      justify-content: space-between;
      padding: 12px 0;
      border-bottom: 1px solid #e2e8f0;
    }
    .info-item:last-child {
      border-bottom: none;
    }
    .info-label {
      font-weight: 600;
      color: #374151;
    }
    .info-value {
      color: #1e40af;
      font-family: monospace;
      background: #dbeafe;
      padding: 4px 8px;
      border-radius: 4px;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 20px;
    }
    .container {
      background: white;
      border-radius: 20px;
      padding: 40px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
      max-width: 600px;
      width: 100%;
    }
    h1 {
      color: #333;
      margin-bottom: 10px;
      font-size: 28px;
    }
    .subtitle {
      color: #666;
      margin-bottom: 30px;
      font-size: 14px;
    }
    .current-target {
      background: #f0f9ff;
      border: 2px solid #3b82f6;
      border-radius: 10px;
      padding: 15px;
      margin-bottom: 25px;
      text-align: center;
      color: #1e40af;
    }
    .current-target.none {
      background: #f3f4f6;
      border-color: #d1d5db;
      color: #6b7280;
    }
    .clear-btn {
      margin-left: 10px;
      padding: 4px 12px;
      background: #ef4444;
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 12px;
    }
    .clear-btn:hover { background: #dc2626; }
    .ports-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 15px;
      margin-bottom: 25px;
    }
    .port-btn {
      background: #f8fafc;
      border: 2px solid #e2e8f0;
      border-radius: 12px;
      padding: 20px;
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }
    .port-btn:hover {
      background: #eff6ff;
      border-color: #3b82f6;
      transform: translateY(-2px);
    }
    .port-btn.active {
      background: #dbeafe;
      border-color: #3b82f6;
    }
    .port-number {
      font-size: 20px;
      font-weight: bold;
      color: #1e293b;
    }
    .port-status {
      font-size: 12px;
      color: #10b981;
    }
    .no-ports {
      text-align: center;
      color: #9ca3af;
      padding: 40px;
      font-style: italic;
    }
    .actions {
      display: flex;
      gap: 10px;
      margin-top: 20px;
    }
    .refresh-btn, .manual-btn {
      flex: 1;
      padding: 12px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
      transition: background 0.2s;
    }
    .refresh-btn {
      background: #3b82f6;
      color: white;
    }
    .refresh-btn:hover { background: #2563eb; }
    .manual-btn {
      background: #f3f4f6;
      color: #374151;
    }
    .manual-btn:hover { background: #e5e7eb; }
    .manual-input {
      display: none;
      margin-top: 15px;
      padding: 15px;
      background: #f8fafc;
      border-radius: 10px;
    }
    .manual-input.show { display: block; }
    .manual-input input {
      width: 100%;
      padding: 10px;
      border: 2px solid #e2e8f0;
      border-radius: 6px;
      font-size: 16px;
      margin-bottom: 10px;
    }
    .manual-input button {
      width: 100%;
      padding: 10px;
      background: #10b981;
      color: white;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
    }
    .manual-input button:hover { background: #059669; }
    .info {
      margin-top: 20px;
      padding: 15px;
      background: #fef3c7;
      border-radius: 8px;
      font-size: 13px;
      color: #92400e;
    }
    .loading {
      display: none;
      text-align: center;
      padding: 40px;
      color: #6b7280;
    }
    .loading.show { display: block; }
    .spinner {
      border: 3px solid #f3f4f6;
      border-top: 3px solid #3b82f6;
      border-radius: 50%;
      width: 40px;
      height: 40px;
      animation: spin 1s linear infinite;
      margin: 0 auto 15px;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>🚀 Local Proxy Monitor</h1>
    <p class="subtitle">Reverse Proxy Status Dashboard</p>
    ${currentTargetDisplay}
    <div class="status-info">
      <div class="info-item">
        <span class="info-label">프록시 포트:</span>
        <span class="info-value">${PORT}</span>
      </div>
      <div class="info-item">
        <span class="info-label">타겟 서비스:</span>
        <span class="info-value">localhost:${cliOptions.targetPort}</span>
      </div>
      <div class="info-item">
        <span class="info-label">프록시 URL:</span>
        <span class="info-value">http://[서버IP]:${PORT}</span>
      </div>
      <div class="info-item">
        <span class="info-label">상태 페이지:</span>
        <span class="info-value">http://[서버IP]:${PORT}/status</span>
      </div>
    </div>
    <div class="actions">
      <button class="refresh-btn" onclick="refresh()">🔄 상태 새로고침</button>
    </div>
    <div class="info">
      <strong>사용 방법:</strong><br>
      1. CLI로 타겟 포트 설정: <code>node server.js -t ${cliOptions.targetPort}</code><br>
      2. 외부 접속: <code>http://[서버IP]:${PORT}</code> → localhost:${cliOptions.targetPort} (프록시)<br>
      3. 상태 페이지: <code>http://[서버IP]:${PORT}/status</code> (이 페이지)<br>
      4. 외부 접속이 안될 경우 방화벽/라우터 포트 포워딩 확인
    </div>
  </div>
  <script>
    // 5초마다 상태 새로고침
    setInterval(() => {
      fetch('/api/status')
        .then(res => res.json())
        .then(data => {
          console.log('Status:', data);
        });
    }, 5000);
    
    function refresh() {
      window.location.reload();
    }
  </script>
</body>
</html>`;
}

app.use(express.json());

app.get('/api/status', async (req, res) => {
  const targetPort = cliOptions.targetPort;
  const isReachable = await checkPort('localhost', targetPort);
  
  res.json({
    proxyPort: PORT,
    targetPort: targetPort,
    targetUrl: currentTarget,
    isTargetReachable: isReachable,
    configuredAt: new Date().toISOString()
  });
});

app.get('/status', async (req, res) => {
  res.send(generateStatusHTML());
});

const proxyMiddleware = createProxyMiddleware({
  changeOrigin: true,
  ws: true,
  secure: false,
  logLevel: 'silent',
  router: () => currentTarget,
  onProxyReq: (proxyReq, req) => {
    console.log(`[Proxy] ${req.method} ${req.url} → ${currentTarget}${req.url}`);
  },
  onError: (err, req, res) => {
    console.error('[Proxy Error]', err.message);
    res.status(502).send(`
      <h1>프록시 오류</h1>
      <p>타겟 서버(${currentTarget})에 연결할 수 없습니다.</p>
      <p>서비스가 실행 중인지 확인하세요.</p>
      <a href="/status">← 상태 페이지로 돌아가기</a>
    `);
  }
});

// 모든 요청을 프록시로 (단, /api/*와 /status는 제외)
app.use((req, res, next) => {
  if (req.path.startsWith('/api/') || req.path === '/status') {
    return next();
  }
  
  proxyMiddleware(req, res, next);
});

app.listen(PORT, '0.0.0.0', () => {
  console.log('='.repeat(60));
  console.log('🚀 Local Reverse Proxy Server');
  console.log('='.repeat(60));
  console.log(`\nProxy Server:  http://0.0.0.0:${PORT}`);
  console.log(`Target Server: ${currentTarget}`);
  console.log(`\n📌 Access:`);
  console.log(`   Proxy Target: http://[YOUR_IP]:${PORT}/*  →  ${currentTarget}/*`);
  console.log(`   Status Page:  http://[YOUR_IP]:${PORT}/status`);
  console.log(`\n📌 All paths are forwarded to target except /status and /api/*`);
  console.log(`\n⚠️  Make sure port ${PORT} is open in firewall/router`);
  console.log(`${'─'.repeat(60)}`);
});
