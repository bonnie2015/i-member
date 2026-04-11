// API配置
// 直接访问后端服务，绕过前端服务器的代理
const API_BASE_URL = 'http://localhost:8000/api/v1';

console.log('前端配置:');
console.log('- 当前主机名:', window.location.hostname);
console.log('- API地址:', API_BASE_URL);

// DOM元素
const chatMessages = document.getElementById('chatMessages');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const charCount = document.getElementById('charCount');
const confidenceInfo = document.getElementById('confidenceInfo');
const confidenceScore = document.getElementById('confidenceScore');
const connectionStatus = document.getElementById('connectionStatus');
const currentTime = document.getElementById('currentTime');

// 全局变量
let currentThreadId = generateThreadId();
let isConnected = false;

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    updateCurrentTime();
    setInterval(updateCurrentTime, 60000); // 每分钟更新一次时间

    // 检查后端连接
    checkConnection();

    // 事件监听
    messageInput.addEventListener('input', updateCharCount);
    messageInput.addEventListener('keypress', handleKeyPress);
    sendButton.addEventListener('click', sendMessage);

    // 自动聚焦输入框
    messageInput.focus();
});

// 生成唯一的会话ID
function generateThreadId() {
    return 'web_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// 更新当前时间
function updateCurrentTime() {
    const now = new Date();
    currentTime.textContent = now.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 更新字符计数
function updateCharCount() {
    const count = messageInput.value.length;
    charCount.textContent = `${count}/500`;

    // 字符数警告
    if (count > 450) {
        charCount.style.color = '#ff6b6b';
    } else if (count > 300) {
        charCount.style.color = '#ffa94d';
    } else {
        charCount.style.color = '#868e96';
    }
}

// 处理键盘事件
function handleKeyPress(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// 检查后端连接状态
async function checkConnection() {
    try {
        // 尝试访问健康检查端点或API文档
        const healthUrl = `${API_BASE_URL}/chat`;
        console.log('连接检查: 尝试访问', healthUrl);

        const response = await fetch(healthUrl, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });

        // 如果能够收到响应（即使是405方法不允许），说明后端服务可用
        if (response.status === 405) {
            // GET请求返回405是正常的，说明接口存在
            setConnectionStatus(true);
            console.log('后端连接检查: 成功 (接口存在)');
        } else if (response.ok) {
            setConnectionStatus(true);
            console.log('后端连接检查: 成功');
        } else {
            setConnectionStatus(false);
            console.log('后端连接检查: 失败 - HTTP状态:', response.status);
        }
    } catch (error) {
        // 如果请求失败，说明后端服务不可用
        setConnectionStatus(false);
        console.error('后端连接检查: 网络错误', error);
    }
}

// 设置连接状态
function setConnectionStatus(connected) {
    isConnected = connected;
    const dot = connectionStatus;

    if (connected) {
        dot.style.backgroundColor = '#51cf66';
        dot.title = '后端服务连接正常';
        sendButton.disabled = false;
        messageInput.placeholder = '请输入您的问题...';
    } else {
        dot.style.backgroundColor = '#ff6b6b';
        dot.title = '后端服务连接失败';
        sendButton.disabled = true;
        messageInput.placeholder = '后端服务不可用，请检查连接...';
    }
}

// 发送消息
async function sendMessage() {
    const message = messageInput.value.trim();

    if (!message) {
        showNotification('请输入消息内容', 'warning');
        return;
    }

    if (!isConnected) {
        showNotification('后端服务不可用，请检查连接', 'error');
        return;
    }

    // 添加用户消息到聊天界面
    addMessage(message, 'user');

    // 清空输入框
    messageInput.value = '';
    updateCharCount();

    // 禁用发送按钮
    sendButton.disabled = true;
    messageInput.disabled = true;

    try {
        // 显示加载状态
        const loadingMessage = addMessage('正在思考中...', 'bot', true);

        // 发送请求到后端API
        const response = await fetch(`${API_BASE_URL}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: message,
                thread_id: currentThreadId,
                user_id: 'web_user'
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        // 移除加载消息
        loadingMessage.remove();

        // 添加AI回复
        addMessage(data.reply, 'bot');

        // 显示置信度信息
        if (data.metadata && data.metadata.confidence_score !== undefined) {
            showConfidence(data.metadata.confidence_score);
        }

        // 更新会话ID（如果需要）
        if (data.thread_id && data.thread_id !== currentThreadId) {
            currentThreadId = data.thread_id;
        }

    } catch (error) {
        console.error('发送消息失败:', error);

        // 移除加载消息
        const loadingMessages = document.querySelectorAll('.loading-message');
        loadingMessages.forEach(msg => msg.remove());

        // 显示错误消息
        addMessage('抱歉，处理过程中出现错误，请稍后重试。', 'bot');
        showNotification('网络错误，请检查连接', 'error');
    } finally {
        // 重新启用发送按钮
        sendButton.disabled = false;
        messageInput.disabled = false;
        messageInput.focus();
    }
}

// 添加消息到聊天界面
function addMessage(content, sender, isLoading = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-message ${isLoading ? 'loading-message' : ''}`;

    const messageContent = document.createElement('div');
    messageContent.className = 'message-content';

    const messageHeader = document.createElement('div');
    messageHeader.className = 'message-header';

    const senderSpan = document.createElement('span');
    senderSpan.className = 'sender';
    senderSpan.textContent = sender === 'user' ? '👤 您' : '🤖 智能助手';

    const timestamp = document.createElement('span');
    timestamp.className = 'timestamp';
    timestamp.textContent = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });

    messageHeader.appendChild(senderSpan);
    messageHeader.appendChild(timestamp);

    const messageText = document.createElement('div');
    messageText.className = 'message-text';

    if (isLoading) {
        const loadingDots = document.createElement('div');
        loadingDots.className = 'loading-dots';
        loadingDots.innerHTML = '<span></span><span></span><span></span>';
        messageText.appendChild(loadingDots);
    } else {
        messageText.innerHTML = formatMessage(content);
    }

    messageContent.appendChild(messageHeader);
    messageContent.appendChild(messageText);
    messageDiv.appendChild(messageContent);

    chatMessages.appendChild(messageDiv);

    // 滚动到底部
    chatMessages.scrollTop = chatMessages.scrollHeight;

    return messageDiv;
}

// 格式化消息内容（支持简单的Markdown）
function formatMessage(content) {
    // 处理换行
    content = content.replace(/\n/g, '<br>');

    // 处理列表
    content = content.replace(/^- (.+)$/gm, '<li>$1</li>');

    // 检查是否有列表项
    const listMatches = content.match(/<li>.*<\/li>/g);
    if (listMatches) {
        content = content.replace(/<li>.*<\/li>/g, '<ul>' + listMatches.join('') + '</ul>');
    }

    return content;
}

// 显示置信度信息
function showConfidence(score) {
    confidenceInfo.style.display = 'inline';
    confidenceScore.textContent = (score * 100).toFixed(1) + '%';

    // 根据置信度设置颜色
    if (score >= 0.8) {
        confidenceScore.style.color = '#51cf66';
    } else if (score >= 0.6) {
        confidenceScore.style.color = '#ffa94d';
    } else {
        confidenceScore.style.color = '#ff6b6b';
    }
}

// 显示通知
function showNotification(message, type = 'info') {
    // 创建通知元素
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;

    // 添加到页面
    document.body.appendChild(notification);

    // 显示动画
    setTimeout(() => {
        notification.classList.add('show');
    }, 100);

    // 自动移除
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// 添加通知样式
const notificationStyles = document.createElement('style');
notificationStyles.textContent = `
    .notification {
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 8px;
        color: white;
        font-size: 14px;
        transform: translateX(100%);
        transition: transform 0.3s ease;
        z-index: 1000;
        max-width: 300px;
    }

    .notification.show {
        transform: translateX(0);
    }

    .notification-info { background-color: #339af0; }
    .notification-warning { background-color: #ffa94d; }
    .notification-error { background-color: #ff6b6b; }
    .notification-success { background-color: #51cf66; }
`;

document.head.appendChild(notificationStyles);
