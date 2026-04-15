/**
 * script.js — 品牌伙伴智能客服前端
 *
 * 交互状态机：
 *   idle      → 普通输入
 *   select    → 显示可点击选项卡片（点击回传 option.value）
 *   waiting   → 请求中，禁用所有输入
 */

const API_ORIGIN = window.location.protocol === 'file:' ? 'http://localhost:8000' : 'http://localhost:8000';
const API = `${API_ORIGIN}/api/v1`;
const MOCK_USER_ID = 'bonnie20260412';
const MOCK_JWT_ISS = 'member-ops-agent';
const MOCK_JWT_SECRET = 'change-me-in-production';
const MOCK_TOKEN_KEY = 'member_ops_mock_token';

// ── DOM refs ──────────────────────────────────────────────────
const $messages      = document.getElementById('messages');
const $messageInput  = document.getElementById('messageInput');
const $sendBtn       = document.getElementById('sendBtn');
const $charCount     = document.getElementById('charCount');
const $inputHint     = document.getElementById('inputHint');
const $selectPanel   = document.getElementById('selectPanel');
const $selectLabel   = document.getElementById('selectLabel');
const $optionCards   = document.getElementById('optionCards');
const $statusDot     = document.getElementById('statusDot');
const $statusText    = document.getElementById('statusText');
const $threadLabel   = document.getElementById('threadLabel');
const $memberLevel   = document.getElementById('memberLevel');
const $memberPoints  = document.getElementById('memberPoints');
const $memberName    = document.getElementById('memberName');
const $newChatBtn    = document.getElementById('newChatBtn');

// ── State ─────────────────────────────────────────────────────
let threadId   = null;
let uiState    = 'idle'; // idle | select | waiting

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    syncThreadLabel();
    checkHealth();
    ensureMockToken().catch(err => console.error('mock token init failed', err));
    bindEvents();
});

function bindEvents() {
    $sendBtn.addEventListener('click', onSend);
    $messageInput.addEventListener('input', onInput);
    $messageInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
    });
    $newChatBtn.addEventListener('click', newChat);
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => sendText(btn.dataset.msg));
    });
}

// ── Health check ──────────────────────────────────────────────
async function checkHealth() {
    try {
        const r = await fetch(`${API_ORIGIN}/health`);
        if (r.ok) setOnline(true);
        else      setOnline(false);
    } catch {
        setOnline(false);
    }
}

function setOnline(ok) {
    $statusDot.className  = 'dot ' + (ok ? 'online' : 'offline');
    $statusText.textContent = ok ? '服务正常' : '服务不可用';
    if (ok) {
        $sendBtn.disabled = false;
        $messageInput.disabled = false;
        $messageInput.focus();
        loadUserContext();
    } else {
        $sendBtn.disabled = true;
        $messageInput.placeholder = '后端服务不可用，请检查连接...';
    }
}

// ── Load user context (sidebar member card) ───────────────────
async function loadUserContext() {
    try {
        $memberLevel.textContent  = '黄金会员';
        $memberPoints.textContent = '积分：8500 → 铂金还需 1500';
        $memberName.textContent   = '用户 ID：' + MOCK_USER_ID;
    } catch { /* ignore */ }
}

// ── Input handling ────────────────────────────────────────────
function onInput() {
    const len = $messageInput.value.length;
    $charCount.textContent = len + ' / 500';
    autoGrow($messageInput);
    $sendBtn.disabled = len === 0 || uiState === 'waiting';
}

function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Send message ──────────────────────────────────────────────
function onSend() {
    const text = $messageInput.value.trim();
    if (!text || uiState === 'waiting') return;
    $messageInput.value = '';
    onInput();
    sendText(text);
}

async function sendText(text) {
    if (uiState === 'waiting') return;

    addUserBubble(text);
    setUiState('waiting');

    const thinkingId = addThinkingBubble('正在处理...');

    try {
        const body = { message: text, channel: 'web' };
        if (threadId) body.thread_id = threadId;
        const token = await ensureMockToken();

        const res = await fetch(`${API}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify(body),
        });

        const data = await safeReadJson(res);
        if (!res.ok) {
            throw new Error(formatApiError(res.status, data));
        }
        const normalized = normalizeChatResponse(data);
        threadId = normalized.thread_id || threadId;
        syncThreadLabel();
        removeThinkingBubble(thinkingId);

        if (normalized.reply) {
            addBotBubble(normalized.reply);
        }
        if (normalized.interaction) {
            handleInteraction(normalized.reply, normalized.interaction);
        } else {
            setUiState('idle');
            if (!normalized.reply) {
                addBotBubble('系统已处理完成，但本轮没有返回可展示内容。您可以继续输入问题。');
            }
        }

    } catch (err) {
        removeThinkingBubble(thinkingId);
        addBotBubble(formatUserError(err));
        setUiState('idle');
        console.error(err);
    }
}

function normalizeChatResponse(payload) {
    const data = isObj(payload) ? payload : {};
    const thread = textOrEmpty(data.thread_id || data.threadId);
    const reply = pickText(data.reply, data.message, data.content);
    const interaction = normalizeInteraction(data.interaction);
    return { thread_id: thread, reply, interaction };
}

function normalizeInteraction(raw) {
    if (!isObj(raw) || !Array.isArray(raw.items)) return null;
    const interactionType = textOrEmpty(raw.interaction_type || raw.type || raw.interactionType);
    const items = raw.items
        .map(normalizeInteractionItem)
        .filter(Boolean);
    if (items.length === 0) return null;
    return {
        interaction_type: interactionType,
        items,
    };
}

function normalizeInteractionItem(item) {
    if (!isObj(item)) return null;
    const detail = isObj(item.detail) ? item.detail : {};
    const key = pickText(item.key, item.value, item.id, detail.action);
    const label = pickText(item.label, item.text, key);
    if (!label || !key) return null;
    return {
        key,
        label,
        selectable: item.selectable !== false,
        detail,
    };
}

function pickText(...values) {
    for (const value of values) {
        const t = textOrEmpty(value);
        if (t) return t;
    }
    return '';
}

function textOrEmpty(value) {
    if (typeof value === 'string') return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return '';
}

function isObj(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

async function safeReadJson(res) {
    try {
        return await res.json();
    } catch {
        return null;
    }
}

function formatApiError(status, payload) {
    const prefix = `HTTP ${status}`;
    if (!isObj(payload)) return prefix;
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
        return `${prefix}: ${payload.detail.trim()}`;
    }
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
        const first = payload.detail[0];
        if (isObj(first)) {
            const msg = pickText(first.msg, first.message, first.type);
            if (msg) return `${prefix}: ${msg}`;
        }
    }
    const msg = pickText(payload.message, payload.error, payload.reply);
    return msg ? `${prefix}: ${msg}` : prefix;
}

function formatUserError(err) {
    const msg = pickText(err && err.message);
    if (!msg) return '抱歉，出了点小问题，请稍后再试。';
    return `请求失败：${msg}`;
}

// ── Mock auth token ──────────────────────────────────────────
function base64urlFromBytes(bytes) {
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function base64urlFromString(str) {
    return base64urlFromBytes(new TextEncoder().encode(str));
}

async function hmacSha256(message, secret) {
    const key = await crypto.subtle.importKey(
        'raw',
        new TextEncoder().encode(secret),
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message));
    return base64urlFromBytes(new Uint8Array(sig));
}

function parseJwtPayload(token) {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    try {
        const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const pad = payload.length % 4 ? '='.repeat(4 - payload.length % 4) : '';
        return JSON.parse(atob(payload + pad));
    } catch {
        return null;
    }
}

async function buildMockToken() {
    const now = Math.floor(Date.now() / 1000);
    const payload = {
        sub: MOCK_USER_ID,
        iss: MOCK_JWT_ISS,
        iat: now,
        exp: now + 7 * 24 * 3600,
    };
    const header = { alg: 'HS256', typ: 'JWT' };
    const h = base64urlFromString(JSON.stringify(header));
    const p = base64urlFromString(JSON.stringify(payload));
    const s = await hmacSha256(`${h}.${p}`, MOCK_JWT_SECRET);
    return `${h}.${p}.${s}`;
}

async function ensureMockToken() {
    const cached = localStorage.getItem(MOCK_TOKEN_KEY);
    if (cached) {
        const payload = parseJwtPayload(cached);
        if (payload && payload.sub === MOCK_USER_ID && payload.exp > Math.floor(Date.now() / 1000) + 60) {
            return cached;
        }
    }
    const token = await buildMockToken();
    localStorage.setItem(MOCK_TOKEN_KEY, token);
    return token;
}

// ── Interaction handling ──────────────────────────────────────
function handleInteraction(reply, interaction) {
    const options = toSelectableOptions(interaction);
    if (options.length > 0) {
        const label = reply || defaultInteractionLabel(interaction.interaction_type);
        setUiState('select', options, label);
    } else {
        setUiState('idle');
        $inputHint.textContent = '请继续回复';
        $messageInput.focus();
    }
}

function toSelectableOptions(interaction) {
    if (!interaction || !Array.isArray(interaction.items)) return [];
    return interaction.items
        .filter(item => item && item.selectable !== false)
        .map(item => ({
            label: String(item.label || item.key || ''),
            value: String(item.key || (item.detail && item.detail.action) || item.label || ''),
        }));
}

function defaultInteractionLabel(interactionType) {
    const labelByType = {
        select_order: '请选择要处理的订单',
        select_product: '请选择要处理的商品',
        select_ticket: '请选择要处理的工单',
        confirm_order: '请确认要处理的订单',
        confirm_product: '请确认要处理的商品',
        confirm_ticket: '请确认要处理的工单',
        confirm: '请确认下一步操作',
    };
    return labelByType[interactionType] || '请选择：';
}

function syncThreadLabel() {
    $threadLabel.textContent = threadId ? `会话ID：${threadId}` : '新会话';
}

// ── UI state machine ──────────────────────────────────────────
function setUiState(state, options = [], label = '请选择：') {
    uiState = state;

    $selectPanel.classList.add('hidden');

    $messageInput.disabled = false;
    $sendBtn.disabled = $messageInput.value.trim().length === 0;
    $inputHint.textContent = '';

    switch (state) {
        case 'waiting':
            $messageInput.disabled = true;
            $sendBtn.disabled = true;
            break;

        case 'select':
            $selectPanel.classList.remove('hidden');
            $selectLabel.textContent = label;
            renderOptionCards(options);
            break;

        case 'idle':
        default:
            $messageInput.focus();
            break;
    }
}

function renderOptionCards(options) {
    $optionCards.innerHTML = '';
    options.forEach(opt => {
        const label = typeof opt === 'string' ? opt : String(opt.label || opt.value || '');
        const value = typeof opt === 'string' ? opt : String(opt.value || opt.label || '');
        const card = document.createElement('button');
        card.className = 'option-card';
        card.textContent = label;
        card.addEventListener('click', () => {
            card.classList.add('selected');
            sendText(value);
        });
        $optionCards.appendChild(card);
    });
}

// ── DOM helpers ───────────────────────────────────────────────
function addUserBubble(text) {
    const row = document.createElement('div');
    row.className = 'msg-row user';
    row.innerHTML = `
        <div class="bubble">${escHtml(text)}</div>
        <div class="msg-avatar">我</div>
    `;
    $messages.appendChild(row);
    scrollBottom();
}

function addBotBubble(text) {
    const row = document.createElement('div');
    row.className = 'msg-row bot';

    const formattedText = formatBotText(text);

    row.innerHTML = `
        <div class="msg-avatar">B</div>
        <div class="bubble">${formattedText}</div>
    `;
    $messages.appendChild(row);
    scrollBottom();
    return row;
}

function addThinkingBubble(text) {
    const id = 'thinking_' + Date.now();
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    row.id = id;
    row.innerHTML = `
        <div class="msg-avatar">B</div>
        <div class="thinking-bubble">
            <span class="thinking-text">${escHtml(text)}</span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
        </div>
    `;
    $messages.appendChild(row);
    scrollBottom();
    return id;
}

function removeThinkingBubble(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function newChat() {
    threadId = null;
    syncThreadLabel();
    setUiState('idle');
    $messageInput.value = '';
    onInput();
    const welcome = $messages.querySelector('.welcome-msg');
    $messages.innerHTML = '';
    if (welcome) $messages.appendChild(welcome);
    $messageInput.focus();
}

// ── Format bot text ───────────────────────────────────────────
function formatBotText(text) {
    const escaped = escHtml(text);
    return escaped
        .replace(/\n/g, '<br>')
        .replace(/(\d+)\.\s+([^<]+)/g, '<span class="numbered-item">$1. $2</span>');
}

function escHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function scrollBottom() {
    $messages.scrollTop = $messages.scrollHeight;
}
