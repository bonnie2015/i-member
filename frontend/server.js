import express from 'express';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;

// 静态文件服务
app.use(express.static(__dirname));

// 健康检查端点
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        service: 'member-ops-agent-frontend',
        timestamp: new Date().toISOString(),
        version: '1.0.0'
    });
});

// 默认路由 - 提供前端页面
app.get('/', (req, res) => {
    res.sendFile(join(__dirname, 'index.html'));
});

// 处理所有其他路由（SPA支持）
app.get('*', (req, res) => {
    res.sendFile(join(__dirname, 'index.html'));
});

// 启动服务器
app.listen(PORT, () => {
    console.log(`🚀 Member Ops Agent Frontend 服务已启动`);
    console.log(`📍 本地访问: http://localhost:${PORT}`);
    console.log(`🌐 服务地址: http://0.0.0.0:${PORT}`);
    console.log(`📊 健康检查: http://localhost:${PORT}/health`);
    console.log('');
    console.log('💡 使用说明:');
    console.log('   1. 确保后端服务正在运行 (端口 8000)');
    console.log('   2. 在浏览器中打开上述本地访问地址');
    console.log('   3. 开始与智能客服对话');
});

// 优雅关闭
process.on('SIGINT', () => {
    console.log('\n🛑 正在关闭前端服务...');
    process.exit(0);
});

process.on('SIGTERM', () => {
    console.log('\n🛑 正在关闭前端服务...');
    process.exit(0);
});
